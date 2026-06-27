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


def _wrap_on_screen_text(
    text: str,
    *,
    max_chars_per_line: int = 20,
    max_lines: int = 2,
) -> list[str]:
    """Разбивает длинный текст на 1–2 строки под ширину 9:16."""
    cleaned = text.strip()
    if not cleaned:
        return []
    if len(cleaned) <= max_chars_per_line:
        return [cleaned]

    words = cleaned.split()
    lines: list[str] = []
    current = ""

    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= max_chars_per_line:
            current = candidate
            continue
        if current:
            lines.append(current)
            if len(lines) >= max_lines:
                return lines
        current = word[:max_chars_per_line]

    if current and len(lines) < max_lines:
        lines.append(current[:max_chars_per_line])
    return lines or [cleaned[:max_chars_per_line]]


def _fontsize_for_display(lines: list[str]) -> int:
    max_len = max((len(line) for line in lines), default=0)
    if max_len > 24:
        return 34
    if max_len > 18:
        return 42
    if len(lines) >= 2:
        return 44
    return 52


def _drawtext_y(lines_count: int) -> str:
    if lines_count >= 3:
        return "h*0.64"
    if lines_count == 2:
        return "h*0.70"
    return "h*0.76"


def _format_drawtext_multiline(lines: list[str]) -> str:
    return "\\n".join(_escape_drawtext(line) for line in lines)


async def _probe_media_duration(path: Path) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return 0.0
    try:
        return float(stdout.decode().strip())
    except ValueError:
        return 0.0


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
    ]

    font_path = _resolve_font_path()
    if scene.on_screen_text and font_path:
        lines = _wrap_on_screen_text(scene.on_screen_text)
        fontsize = _fontsize_for_display(lines)
        text = _format_drawtext_multiline(lines)
        font = font_path.replace("\\", "/").replace(":", "\\:")
        vf_parts.append(
            "drawtext="
            f"fontfile='{font}':"
            f"text='{text}':"
            f"fontsize={fontsize}:fontcolor=white:"
            "x=(w-text_w)/2:"
            f"y={_drawtext_y(len(lines))}:"
            "line_spacing=8:"
            "borderw=2:bordercolor=black@0.75:"
            "box=1:boxcolor=black@0.45:boxborderw=12"
        )

    filter_chain = ",".join(vf_parts)

    ffmpeg_args: list[str] = ["-y"]
    if has_stock:
        ffmpeg_args.extend(["-stream_loop", "-1"])
    ffmpeg_args.extend(
        [
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
            "-t",
            f"{duration:.3f}",
            str(output_path),
        ]
    )
    await _run_ffmpeg(ffmpeg_args)

    actual = await _probe_media_duration(output_path)
    if abs(actual - duration) > 0.75:
        logger.warning(
            "Сцена %s: ожидали %.1fс, ffprobe=%.1fс",
            index,
            duration,
            actual,
        )
    return output_path


async def _concat_clips(clip_paths: list[Path], output_path: Path) -> None:
    if len(clip_paths) == 1:
        shutil.copy2(clip_paths[0], output_path)
        return

    inputs: list[str] = []
    for clip_path in clip_paths:
        inputs.extend(["-i", str(clip_path)])

    n = len(clip_paths)
    concat_inputs = "".join(f"[{i}:v][{i}:a]" for i in range(n))
    filter_complex = f"{concat_inputs}concat=n={n}:v=1:a=1[vout][aout]"

    await _run_ffmpeg(
        [
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
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

        video_duration = await _probe_media_duration(output_path)
        logger.info(
            "Reels собран: %s (%.1f МБ, %.1fс, %d сцен)",
            output_path.name,
            size_mb,
            video_duration,
            len(scenes),
        )
        return output_path
    except (GeminiTTSError, LLMRateLimitError) as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise ReelsRenderError(str(exc)) from exc
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
