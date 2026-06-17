"""
services/gemini_client.py — прямой клиент Google Gemini API (AI Studio).

Используется как запасной провайдер, если OpenRouter недоступен.
Бесплатный ключ: https://aistudio.google.com/apikey
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from config import get_settings
from services.llm_errors import LLMContextTooLargeError, LLMError, LLMRateLimitError

logger = logging.getLogger("ai_kombain.gemini")

_gemini_client: Optional[httpx.AsyncClient] = None

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


def gemini_available() -> bool:
    return bool(get_settings().GEMINI_API_KEY)


def get_gemini_client() -> httpx.AsyncClient:
    global _gemini_client
    if _gemini_client is None or _gemini_client.is_closed:
        _gemini_client = httpx.AsyncClient(
            base_url=GEMINI_API_BASE,
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=5.0),
        )
    return _gemini_client


async def close_gemini_client() -> None:
    global _gemini_client
    if _gemini_client and not _gemini_client.is_closed:
        await _gemini_client.aclose()
        _gemini_client = None


def gemini_model_label() -> str:
    settings = get_settings()
    return f"gemini/{settings.GEMINI_MODEL}"


async def call_gemini_api(
    system_prompt: str,
    user_message: str,
    *,
    max_tokens: int,
    temperature: float,
) -> tuple[str, int]:
    """Вызов Gemini generateContent. Возвращает (текст, токены)."""
    settings = get_settings()
    if not settings.GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY не задан")

    model = settings.GEMINI_MODEL
    client = get_gemini_client()

    payload = {
        "system_instruction": {
            "parts": [{"text": system_prompt}],
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_message}],
            }
        ],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }

    try:
        response = await client.post(
            f"/models/{model}:generateContent",
            params={"key": settings.GEMINI_API_KEY},
            json=payload,
        )
    except httpx.TimeoutException:
        raise LLMError("Таймаут запроса к Gemini API.")
    except httpx.ConnectError:
        raise LLMError("Не удалось подключиться к Gemini API.")

    if response.status_code == 429:
        raise LLMRateLimitError("Лимит Gemini API. Попробуйте позже.")

    if response.status_code == 400:
        detail = _extract_error_message(response)
        lowered = detail.lower()
        if "token" in lowered or "context" in lowered or "too long" in lowered:
            raise LLMContextTooLargeError("Текст слишком длинный для Gemini")
        raise LLMError(f"Неверный запрос к Gemini: {detail}")

    if response.status_code != 200:
        raise LLMError(
            f"Gemini API вернул {response.status_code}: {_extract_error_message(response)}"
        )

    data = response.json()
    candidates = data.get("candidates") or []
    if not candidates:
        block_reason = (data.get("promptFeedback") or {}).get("blockReason")
        if block_reason:
            raise LLMError(f"Gemini заблокировал запрос: {block_reason}")
        raise LLMError("Gemini вернул пустой ответ")

    parts = candidates[0].get("content", {}).get("parts") or []
    text_chunks = [part.get("text", "") for part in parts if part.get("text")]
    response_text = "".join(text_chunks).strip()
    if not response_text:
        raise LLMError("Gemini вернул пустой текст")

    usage = data.get("usageMetadata") or {}
    tokens_used = int(usage.get("totalTokenCount") or 0)

    logger.info(
        "✅ Gemini ответил: модель=%s, токенов=%s, символов=%s",
        model,
        tokens_used,
        len(response_text),
    )
    return response_text, tokens_used


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
        error = payload.get("error") or {}
        return str(error.get("message") or response.text[:200])
    except Exception:
        return response.text[:200]
