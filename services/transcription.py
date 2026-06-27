"""
services/transcription.py — транскрипция аудио через Gemini (fallback для YouTube).
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import httpx

from config import get_settings
from services.gemini_client import get_gemini_client
from services.llm_errors import LLMError, LLMRateLimitError

logger = logging.getLogger("ai_kombain.transcription")

MIME_BY_SUFFIX = {
    ".wav": "audio/wav",
    ".mp3": "audio/mp3",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}


class TranscriptionError(Exception):
    """Не удалось распознать аудио."""


def _mime_for_path(path: Path) -> str:
    return MIME_BY_SUFFIX.get(path.suffix.lower(), "audio/mpeg")


async def transcribe_audio_file(path: Path, *, language_hint: str = "ru") -> str:
    """Распознаёт речь в аудиофайле через Gemini multimodal."""
    settings = get_settings()
    if not settings.GEMINI_API_KEY:
        raise TranscriptionError("GEMINI_API_KEY нужен для распознавания аудио")

    if not path.exists() or path.stat().st_size == 0:
        raise TranscriptionError("Аудиофайл пустой")

    audio_b64 = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    mime = _mime_for_path(path)
    model = settings.GEMINI_MODEL

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            f"Распознай речь в этом аудио. Язык: {language_hint}. "
                            "Верни только текст транскрипта, без пояснений и таймкодов."
                        ),
                    },
                    {
                        "inline_data": {
                            "mime_type": mime,
                            "data": audio_b64,
                        },
                    },
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 8192,
        },
    }

    client = get_gemini_client()
    try:
        response = await client.post(
            f"/models/{model}:generateContent",
            params={"key": settings.GEMINI_API_KEY},
            json=payload,
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=5.0),
        )
    except httpx.TimeoutException as exc:
        raise TranscriptionError("Таймаут распознавания аудио") from exc
    except httpx.ConnectError as exc:
        raise TranscriptionError("Нет связи с Gemini API") from exc

    if response.status_code == 429:
        raise LLMRateLimitError("Лимит Gemini API при распознавании аудио")
    if response.status_code != 200:
        detail = response.text[:300]
        raise TranscriptionError(f"Gemini audio: HTTP {response.status_code}: {detail}")

    data = response.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise TranscriptionError("Gemini вернул пустой транскрипт")

    parts = candidates[0].get("content", {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts if part.get("text")).strip()
    if not text:
        raise TranscriptionError("Пустой транскрипт")

    logger.info("Транскрипт Gemini: %d символов из %s", len(text), path.name)
    return text
