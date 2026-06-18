"""
services/stock_video.py — загрузка B-roll с Pexels (бесплатный API).
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from config import get_settings

logger = logging.getLogger("ai_kombain.stock_video")

PEXELS_API = "https://api.pexels.com/videos"


class StockVideoError(Exception):
    """Не удалось получить сток-видео."""


async def _download_file(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=5.0),
        follow_redirects=True,
    ) as client:
        response = await client.get(url)
        if response.status_code != 200:
            raise StockVideoError(f"Не удалось скачать видео: HTTP {response.status_code}")
        dest.write_bytes(response.content)


def _pick_portrait_file(video_files: list[dict]) -> dict | None:
    portrait = [vf for vf in video_files if vf.get("width", 0) <= vf.get("height", 0)]
    pool = portrait or video_files
    if not pool:
        return None
    return max(pool, key=lambda vf: vf.get("width", 0))


async def fetch_pexels_clip(query: str, dest: Path) -> bool:
    """
    Ищет и скачивает портретное видео с Pexels.
    Возвращает True при успехе, False если API-ключ не задан.
    """
    settings = get_settings()
    if not settings.PEXELS_API_KEY:
        return False

    headers = {"Authorization": settings.PEXELS_API_KEY}
    params = {
        "query": query,
        "orientation": "portrait",
        "per_page": 5,
        "size": "medium",
    }

    async with httpx.AsyncClient(
        base_url=PEXELS_API,
        headers=headers,
        timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0),
    ) as client:
        response = await client.get("/search", params=params)

    if response.status_code == 401:
        raise StockVideoError("Неверный PEXELS_API_KEY")
    if response.status_code != 200:
        raise StockVideoError(f"Pexels API: HTTP {response.status_code}")

    videos = response.json().get("videos") or []
    if not videos:
        logger.warning("Pexels: ничего не найдено по запросу %r", query)
        return False

    for video in videos:
        file_info = _pick_portrait_file(video.get("video_files") or [])
        if not file_info:
            continue
        link = file_info.get("link")
        if not link:
            continue
        await _download_file(link, dest)
        logger.info("Pexels: скачан клип по запросу %r", query)
        return True

    return False
