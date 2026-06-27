"""
services/gemini_tts.py — озвучка сцен через Gemini TTS (AI Studio).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import wave
from pathlib import Path

import httpx

from config import get_settings
from services.gemini_client import get_gemini_client
from services.llm_errors import LLMError, LLMRateLimitError

logger = logging.getLogger("ai_kombain.gemini_tts")

# 2.5 обычно мягче по квоте, чем preview 3.1
TTS_MODEL_FALLBACKS = (
    "gemini-2.5-flash-preview-tts",
    "gemini-3.1-flash-tts-preview",
)

# Строго по одному TTS-запросу — меньше 429 на бесплатном tier
_tts_semaphore = asyncio.Semaphore(1)


class GeminiTTSError(LLMError):
    """Ошибка синтеза речи Gemini TTS."""


def _parse_pcm_from_mime(mime_type: str) -> tuple[int, int]:
    rate = 24000
    channels = 1
    if not mime_type:
        return rate, channels

    rate_match = re.search(r"rate=(\d+)", mime_type, re.IGNORECASE)
    if rate_match:
        rate = int(rate_match.group(1))

    if "stereo" in mime_type.lower():
        channels = 2
    return rate, channels


def write_pcm_as_wav(
    pcm: bytes,
    path: Path,
    *,
    rate: int = 24000,
    channels: int = 1,
    sample_width: int = 2,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(rate)
        wf.writeframes(pcm)


def wav_duration_sec(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        if rate <= 0:
            return 0.0
        return frames / float(rate)


def _build_tts_prompt(text: str) -> str:
    settings = get_settings()
    language = settings.GEMINI_TTS_LANGUAGE
    cleaned = " ".join(text.split())
    return (
        f"Read aloud naturally in {language} with engaging Reels delivery: {cleaned}"
    )


def _tts_models_to_try() -> list[str]:
    settings = get_settings()
    models: list[str] = []
    for candidate in (settings.GEMINI_TTS_MODEL, *TTS_MODEL_FALLBACKS):
        if candidate and candidate not in models:
            models.append(candidate)
    return models


async def _call_tts_model(model: str, prompt: str) -> tuple[bytes, str]:
    settings = get_settings()
    if not settings.GEMINI_API_KEY:
        raise GeminiTTSError("GEMINI_API_KEY не задан — нужен для озвучки")

    payload: dict = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": settings.GEMINI_TTS_VOICE,
                    }
                }
            },
        },
    }

    client = get_gemini_client()
    try:
        response = await client.post(
            f"/models/{model}:generateContent",
            params={"key": settings.GEMINI_API_KEY},
            json=payload,
        )
    except httpx.TimeoutException:
        raise GeminiTTSError("Таймаут Gemini TTS")
    except httpx.ConnectError:
        raise GeminiTTSError("Не удалось подключиться к Gemini TTS")

    if response.status_code == 429:
        raise LLMRateLimitError(
            "Лимит Gemini TTS (бесплатный tier). Бот подождёт и повторит автоматически."
        )

    if response.status_code == 404:
        raise GeminiTTSError(f"Модель TTS недоступна: {model}")

    if response.status_code != 200:
        detail = response.text[:300]
        raise GeminiTTSError(f"Gemini TTS вернул {response.status_code}: {detail}")

    data = response.json()
    candidates = data.get("candidates") or []
    if not candidates:
        block = (data.get("promptFeedback") or {}).get("blockReason")
        raise GeminiTTSError(f"Gemini TTS не вернул аудио{f': {block}' if block else ''}")

    parts = candidates[0].get("content", {}).get("parts") or []
    for part in parts:
        inline = part.get("inlineData") or part.get("inline_data") or {}
        raw_data = inline.get("data")
        if raw_data:
            mime = str(inline.get("mimeType") or inline.get("mime_type") or "")
            return base64.b64decode(raw_data), mime

    raise GeminiTTSError("Gemini TTS: аудио не найдено в ответе")


async def synthesize_speech_to_wav(text: str, output_path: Path) -> float:
    """
    Синтезирует озвучку сцены и сохраняет WAV.
    При 429 — экспоненциальный backoff и смена TTS-модели.
    """
    settings = get_settings()
    prompt = _build_tts_prompt(text)
    models = _tts_models_to_try()
    last_error: Exception | None = None

    async with _tts_semaphore:
        for model in models:
            for attempt in range(settings.GEMINI_TTS_MAX_RETRIES):
                try:
                    pcm, mime = await _call_tts_model(model, prompt)
                    rate, channels = _parse_pcm_from_mime(mime)
                    write_pcm_as_wav(pcm, output_path, rate=rate, channels=channels)
                    duration = wav_duration_sec(output_path)
                    logger.info(
                        "TTS готово: модель=%s, %.1fс, файл=%s",
                        model,
                        duration,
                        output_path.name,
                    )
                    return duration
                except LLMRateLimitError as exc:
                    last_error = exc
                    wait_sec = settings.GEMINI_TTS_RETRY_BASE_SEC * (2 ** attempt)
                    logger.warning(
                        "TTS rate limit: модель=%s, попытка %s/%s, ждём %.0fс",
                        model,
                        attempt + 1,
                        settings.GEMINI_TTS_MAX_RETRIES,
                        wait_sec,
                    )
                    await asyncio.sleep(wait_sec)
                except GeminiTTSError as exc:
                    last_error = exc
                    if "недоступна" in str(exc).lower() or "404" in str(exc):
                        logger.warning("TTS модель %s недоступна, пробуем следующую", model)
                        break
                    raise

    raise GeminiTTSError(
        "Лимит Gemini TTS исчерпан после нескольких попыток. "
        "Подождите 1–2 минуты и нажмите «Собрать Reels» снова. "
        f"Последняя ошибка: {last_error}"
    )
