"""
services/stock_music.py — фоновая музыка для Reels (Pixabay Audio API).
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from config import get_settings

logger = logging.getLogger("ai_kombain.stock_music")

PIXABAY_API = "https://pixabay.com/api/"

MOOD_SEARCH: list[tuple[str, ...]] = [
    ("энерг", "upbeat", "energetic", "motivation"),
    ("спокой", "calm", "ambient", "relax"),
    ("драм", "cinematic", "epic", "dramatic"),
    ("лоу", "lofi", "chill", "lo-fi"),
    ("корп", "corporate", "business", "tech"),
]


def _mood_to_query(music_mood: str) -> str:
    lowered = (music_mood or "").lower()
    for keywords in MOOD_SEARCH:
        if any(word in lowered for word in keywords[:1]):
            return keywords[1]
    for keywords in MOOD_SEARCH:
        if any(word in lowered for word in keywords[2:]):
            return keywords[1]
    return "background music"


async def _download_file(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=5.0),
        follow_redirects=True,
    ) as client:
        response = await client.get(url)
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        dest.write_bytes(response.content)


async def fetch_background_music(music_mood: str, dest: Path) -> bool:
    """
    Скачивает короткий royalty-free трек с Pixabay.
    Возвращает False, если ключ не задан или трек не найден.
    """
    settings = get_settings()
    if not settings.PIXABAY_API_KEY:
        return False

    query = _mood_to_query(music_mood)
    params = {
        "key": settings.PIXABAY_API_KEY,
        "q": query,
        "audio_type": "music",
        "per_page": 8,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=30.0)) as client:
        response = await client.get(PIXABAY_API, params=params)

    if response.status_code != 200:
        logger.warning("Pixabay music: HTTP %s", response.status_code)
        return False

    hits = response.json().get("hits") or []
    for hit in hits:
        url = hit.get("previewURL") or hit.get("url")
        if not url:
            continue
        try:
            await _download_file(url, dest)
            logger.info("Pixabay: музыка по mood %r → %r", music_mood, query)
            return True
        except Exception as exc:
            logger.warning("Pixabay: не скачать %s: %s", url, exc)

    logger.warning("Pixabay: музыка не найдена для mood %r", music_mood)
    return False
