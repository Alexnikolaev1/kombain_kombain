"""
services/llm_client.py — Асинхронный клиент для OpenRouter API.

Фичи:
  - Интеграция с кэшем БД (сначала БД, потом API)
  - Автоматический фоллбэк на резервную модель
  - Retry логика с экспоненциальным откатом
  - Подсчёт токенов и логирование стоимости
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx

from config import get_settings
from db.database import (
    check_daily_limit,
    get_cached_response,
    get_session,
    get_user_model,
    log_usage_stat,
    save_to_cache,
)
from db.models import ContentSource, PromptType
from domain.models_catalog import resolve_model_id
from prompts.templates import get_prompt

logger = logging.getLogger("ai_kombain.llm")


from services.llm_errors import (
    DailyLimitExceededError,
    LLMContextTooLargeError,
    LLMError,
    LLMModelNotFoundError,
    LLMRateLimitError,
)

_http_client: Optional[httpx.AsyncClient] = None


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    settings = get_settings()
    if not settings.OPENROUTER_API_KEY:
        raise LLMError("OPENROUTER_API_KEY не задан")

    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            base_url=settings.OPENROUTER_BASE_URL,
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": settings.APP_URL,
                "X-Title": settings.APP_NAME,
            },
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=5.0),
        )
    return _http_client


async def close_http_client() -> None:
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


async def _call_openrouter_api(
    system_prompt: str,
    user_message: str,
    model: str,
    max_tokens: int,
    temperature: float,
) -> tuple[str, int]:
    client = get_http_client()

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    try:
        response = await client.post("/chat/completions", json=payload)

        if response.status_code == 429:
            raise LLMRateLimitError("Rate limit. Попробуйте через минуту.")

        if response.status_code == 400:
            detail = response.json().get("error", {}).get("message", "")
            if "context" in detail.lower() or "token" in detail.lower():
                raise LLMContextTooLargeError("Текст слишком длинный для этой модели")
            raise LLMError(f"Неверный запрос: {detail}")

        if response.status_code != 200:
            detail = response.text[:200]
            if response.status_code == 404:
                raise LLMModelNotFoundError(f"Модель недоступна: {model}. {detail}")
            raise LLMError(f"API вернул {response.status_code}: {detail}")

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            raise LLMError("API вернул пустой список choices")

        response_text = choices[0].get("message", {}).get("content", "").strip()
        if not response_text:
            raise LLMError("API вернул пустой ответ")

        tokens_used = data.get("usage", {}).get("total_tokens", 0)
        logger.info(
            "✅ LLM ответил: модель=%s, токенов=%s, символов=%s",
            model,
            tokens_used,
            len(response_text),
        )
        return response_text, tokens_used

    except (LLMRateLimitError, LLMContextTooLargeError, LLMModelNotFoundError, LLMError):
        raise
    except httpx.TimeoutException:
        raise LLMError("Таймаут запроса к LLM. Попробуйте снова.")
    except httpx.ConnectError:
        raise LLMError("Не удалось подключиться к LLM API. Проверьте интернет.")
    except Exception as e:
        raise LLMError(f"Неожиданная ошибка: {e}")


async def _call_with_retry(
    system_prompt: str,
    user_message: str,
    model: str,
    max_tokens: int,
    temperature: float,
    max_retries: int = 3,
) -> tuple[str, int, str]:
    """Возвращает (ответ, токены, фактическая_модель)."""
    settings = get_settings()
    models_to_try: list[str] = []
    if settings.OPENROUTER_API_KEY:
        for candidate in (model, settings.FALLBACK_MODEL):
            resolved = resolve_model_id(candidate)
            if resolved not in models_to_try:
                models_to_try.append(resolved)

    last_error: Exception | None = None

    for model_attempt in models_to_try:
        for attempt in range(max_retries):
            try:
                response_text, tokens_used = await _call_openrouter_api(
                    system_prompt,
                    user_message,
                    model_attempt,
                    max_tokens,
                    temperature,
                )
                return response_text, tokens_used, model_attempt
            except LLMModelNotFoundError as e:
                last_error = e
                logger.warning("Модель %s недоступна, пробуем следующую", model_attempt)
                break
            except LLMRateLimitError as e:
                last_error = e
                wait_sec = 2 ** attempt * 5
                logger.warning(
                    "RateLimit на %s, ждём %ss (попытка %s)",
                    model_attempt,
                    wait_sec,
                    attempt + 1,
                )
                await asyncio.sleep(wait_sec)
            except LLMContextTooLargeError as e:
                last_error = e
                logger.warning(
                    "Контекст слишком большой для %s, пробуем fallback",
                    model_attempt,
                )
                break
            except LLMError as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_sec = 2 ** attempt
                    logger.warning(
                        "Ошибка LLM (попытка %s/%s): %s",
                        attempt + 1,
                        max_retries,
                        e,
                    )
                    await asyncio.sleep(wait_sec)

    if settings.GEMINI_API_KEY:
        from services.gemini_client import call_gemini_api, gemini_model_label

        try:
            logger.info("OpenRouter недоступен, пробуем Gemini API")
            response_text, tokens_used = await call_gemini_api(
                system_prompt,
                user_message,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            used_model = gemini_model_label()
            return response_text, tokens_used, used_model
        except LLMError as e:
            last_error = e
            logger.warning("Gemini fallback не сработал: %s", e)

    raise LLMError(
        "Не удалось получить ответ от ИИ. Проверьте модель в настройках "
        "или добавьте GEMINI_API_KEY / кредиты OpenRouter. "
        f"Последняя ошибка: {last_error}"
    )


async def process_content(
    content: str,
    prompt_type: PromptType,
    context: str = "",
    source_url: str = "",
    user_id: Optional[int] = None,
    source_type: Optional[ContentSource] = None,
) -> dict:
    """Главная функция обработки контента через кэш и LLM."""
    settings = get_settings()
    start_time = time.monotonic()

    async with get_session() as session:
        chosen_model = resolve_model_id(settings.DEFAULT_MODEL)
        if user_id is not None:
            chosen_model = await get_user_model(session, user_id)
            allowed, remaining = await check_daily_limit(session, user_id)
            if not allowed:
                raise DailyLimitExceededError(
                    f"Дневной лимит ({settings.DAILY_REQUEST_LIMIT}) исчерпан. "
                    "Попробуйте завтра."
                )

        cached = await get_cached_response(
            session=session,
            input_text=content,
            prompt_type=prompt_type,
            context=context,
            model_id=chosen_model,
        )

    if cached:
        elapsed_ms = (time.monotonic() - start_time) * 1000
        result = {
            "response": cached.response_text,
            "was_cached": True,
            "tokens_used": 0,
            "tokens_saved": cached.tokens_used or 0,
            "model": cached.model_used,
            "processing_ms": elapsed_ms,
            "cache_hits": cached.hit_count,
        }
        if user_id is not None:
            async with get_session() as session:
                await log_usage_stat(
                    session,
                    user_id=user_id,
                    prompt_type=prompt_type,
                    source_type=source_type,
                    source_url=source_url or None,
                    was_cached=True,
                    tokens_used=0,
                    processing_ms=elapsed_ms,
                    model_used=cached.model_used,
                    success=True,
                )
        return result

    system_prompt, user_message = get_prompt(prompt_type, content, context)
    logger.info(
        "Запрос к LLM: тип=%s, контент=%s символов, модель=%s",
        prompt_type.value,
        len(content),
        chosen_model,
    )

    try:
        response_text, tokens_used, used_model = await _call_with_retry(
            system_prompt=system_prompt,
            user_message=user_message,
            model=chosen_model,
            max_tokens=settings.LLM_MAX_TOKENS,
            temperature=settings.LLM_TEMPERATURE,
        )
    except LLMError as e:
        if user_id is not None:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            async with get_session() as session:
                await log_usage_stat(
                    session,
                    user_id=user_id,
                    prompt_type=prompt_type,
                    source_type=source_type,
                    source_url=source_url or None,
                    was_cached=False,
                    processing_ms=elapsed_ms,
                    model_used=chosen_model,
                    success=False,
                    error_message=str(e),
                )
        raise

    async with get_session() as session:
        await save_to_cache(
            session=session,
            input_text=content,
            prompt_type=prompt_type,
            context=context,
            response_text=response_text,
            model_used=used_model,
            tokens_used=tokens_used,
            source_url=source_url or None,
            user_id=user_id,
        )
        if user_id is not None:
            await log_usage_stat(
                session,
                user_id=user_id,
                prompt_type=prompt_type,
                source_type=source_type,
                source_url=source_url or None,
                was_cached=False,
                tokens_used=tokens_used,
                processing_ms=(time.monotonic() - start_time) * 1000,
                model_used=used_model,
                success=True,
            )

    elapsed_ms = (time.monotonic() - start_time) * 1000
    return {
        "response": response_text,
        "was_cached": False,
        "tokens_used": tokens_used,
        "tokens_saved": 0,
        "model": used_model,
        "processing_ms": elapsed_ms,
        "cache_hits": 0,
    }
