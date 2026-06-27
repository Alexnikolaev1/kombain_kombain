"""
services/reels_renderer.py — автосборка Reels: TTS + B-roll + FFmpeg.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from config import get_settings
from services.gemini_tts import GeminiTTSError, synthesize_speech_to_wav
from services.llm_errors import LLMRateLimitError
from services.reels_timeline import ReelsScene, ReelsTimeline
from services.stock_video import StockVideoError, fetch_pexels_clip

logger = logging.getLogger("ai_kombain.reels_renderer")

ProgressCallback = Callable[[str], Awaitable[None]]

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FFMPEG_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
WINDOWS_FFMPEG_FONT = "C:/Windows/Fonts/arial.ttf"


class ReelsRenderError(Exception):
    """Ошибка сборки видео."""


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _resolve_font_path() -> str | None:
    candidates = [
        FFMPEG_FONT,
        WINDOWS_FFMPEG_FONT,
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def _escape_drawtext(text: str) -> str:
    escaped = text.replace("\\", "\\\\")
    escaped = escaped.replace(":", "\\:")
    escaped = escaped.replace("'", "\\'")
    escaped = escaped.replace("%", "\\%")
    return escaped


async def _run_ffmpeg(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = (stderr or b"").decode("utf-8", errors="replace")[-500:]
        raise ReelsRenderError(f"FFmpeg ошибка: {detail}")


async def _create_color_clip(path: Path, duration: float, color: str = "0x16213e") -> None:
    await _run_ffmpeg(
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r=30",
            "-t",
            f"{duration:.2f}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )


async def _build_scene_clip(
    *,
    scene: ReelsScene,
    work_dir: Path,
    index: int,
) -> Path:
    scene_dir = work_dir / f"scene_{index:02d}"
    scene_dir.mkdir(parents=True, exist_ok=True)

    wav_path = scene_dir / "voice.wav"
    stock_path = scene_dir / "stock.mp4"
    output_path = scene_dir / "clip.mp4"

    duration = await synthesize_speech_to_wav(scene.voiceover, wav_path)
    duration = max(duration, 1.2)
    duration = min(duration, 20.0)

    has_stock = False
    try:
        has_stock = await fetch_pexels_clip(scene.broll_query, stock_path)
    except StockVideoError as exc:
        logger.warning("Pexels для сцены %s: %s", index, exc)

    if not has_stock:
        await _create_color_clip(stock_path, duration)

    vf_parts = [
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase",
        f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}",
        "setsar=1",
        f"trim=duration={duration:.3f}",
        "setpts=PTS-STARTPTS",
    ]

    font_path = _resolve_font_path()
    if scene.on_screen_text and font_path:
        text = _escape_drawtext(scene.on_screen_text[:80])
        font = font_path.replace("\\", "/").replace(":", "\\:")
        vf_parts.append(
            "drawtext="
            f"fontfile='{font}':"
            f"text='{text}':"
            "fontsize=64:fontcolor=white:"
            "x=(w-text_w)/2:y=h*0.78:"
            "box=1:boxcolor=black@0.45:boxborderw=16"
        )

    filter_chain = ",".join(vf_parts)

    await _run_ffmpeg(
        [
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(stock_path),
            "-i",
            str(wav_path),
            "-filter_complex",
            f"[0:v]{filter_chain}[v]",
            "-map",
            "[v]",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            "-t",
            f"{duration:.3f}",
            str(output_path),
        ]
    )
    return output_path


async def _concat_clips(clip_paths: list[Path], output_path: Path) -> None:
    list_file = output_path.parent / "concat_list.txt"
    lines = [f"file '{path.resolve().as_posix()}'" for path in clip_paths]
    list_file.write_text("\n".join(lines), encoding="utf-8")

    await _run_ffmpeg(
        [
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )


def _safe_slug(title: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", title, flags=re.UNICODE)
    slug = re.sub(r"[\s_]+", "_", slug.strip()).lower()[:40]
    return slug or "reels"


async def render_reels_video(
    timeline: ReelsTimeline,
    *,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """
    Собирает вертикальное MP4 из таймлайна.
    Возвращает путь к готовому файлу во временной директории.
    """
    settings = get_settings()
    if not settings.REELS_RENDER_ENABLED:
        raise ReelsRenderError("Автосборка Reels отключена (REELS_RENDER_ENABLED=false)")
    if not ffmpeg_available():
        raise ReelsRenderError(
            "FFmpeg не найден. На сервере нужен ffmpeg в PATH (см. Dockerfile)."
        )
    if not settings.GEMINI_API_KEY:
        raise ReelsRenderError("GEMINI_API_KEY нужен для озвучки сцен")

    scenes = timeline.scenes[: settings.REELS_RENDER_MAX_SCENES]
    if not scenes:
        raise ReelsRenderError("Таймлайн не содержит сцен")

    work_dir = Path(tempfile.mkdtemp(prefix="reels_render_"))
    clip_paths: list[Path] = []

    async def report(message: str) -> None:
        if on_progress:
            await on_progress(message)

    try:
        for index, scene in enumerate(scenes, start=1):
            if index > 1 and settings.GEMINI_TTS_SCENE_DELAY_SEC > 0:
                await report(
                    f"⏳ Пауза {settings.GEMINI_TTS_SCENE_DELAY_SEC:.0f}с перед сценой "
                    f"{index}/{len(scenes)}..."
                )
                await asyncio.sleep(settings.GEMINI_TTS_SCENE_DELAY_SEC)

            await report(f"🎙 Озвучка и монтаж сцены {index}/{len(scenes)}...")
            clip = await _build_scene_clip(scene=scene, work_dir=work_dir, index=index)
            clip_paths.append(clip)

        await report("🎬 Склеиваю финальный ролик...")
        slug = _safe_slug(timeline.title)
        output_path = work_dir / f"{slug}.mp4"
        await _concat_clips(clip_paths, output_path)

        size_mb = output_path.stat().st_size / (1024 * 1024)
        if size_mb > settings.REELS_VIDEO_MAX_MB:
            raise ReelsRenderError(
                f"Видео слишком большое ({size_mb:.1f} МБ). "
                f"Лимит Telegram: {settings.REELS_VIDEO_MAX_MB} МБ."
            )

        logger.info("Reels собран: %s (%.1f МБ)", output_path.name, size_mb)
        return output_path
    except (GeminiTTSError, LLMRateLimitError) as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise ReelsRenderError(str(exc)) from exc
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
