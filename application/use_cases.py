"""
application/use_cases.py — бизнес-сценарии без привязки к Telegram.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Optional

from application.dto import ContentSession, GenerationOutcome
from config import get_settings
from dataclasses import dataclass, field
from db.database import check_daily_limit, get_session, increment_user_stats, log_usage_stat
from db.models import ContentSource, PromptType
from services.llm_client import DailyLimitExceededError, process_content
from services.parser import ParsedContent, parse_input
from prompts.templates import CONTENT_PACK_TYPES, get_display_name
from services.reels_render_modes import ReelsRenderOptions
from services.reels_renderer import render_reels_video
from services.reels_timeline import ReelsTimeline

logger = logging.getLogger("ai_kombain.application")

SOURCE_TYPE_MAP: dict[str, ContentSource] = {
    "youtube": ContentSource.YOUTUBE,
    "telegram": ContentSource.TELEGRAM,
    "text": ContentSource.TEXT,
}


class ContentIntakeUseCase:
    """Парсинг и нормализация входного контента."""

    async def parse(
        self,
        text: str,
        forwarded_text: Optional[str] = None,
    ) -> ParsedContent:
        return await parse_input(text=text, forwarded_text=forwarded_text)

    def to_session(self, parsed: ParsedContent) -> ContentSession:
        return ContentSession(
            content=parsed.content,
            context=parsed.context_string,
            source_url=parsed.url,
            source_type=parsed.source_type.value,
            title=parsed.title,
        )


class GenerationUseCase:
    """Генерация контента и обновление статистики пользователя."""

    async def run(
        self,
        *,
        user_id: int,
        session: ContentSession,
        prompt_type: PromptType,
        action: str = "",
    ) -> GenerationOutcome:
        source_type = SOURCE_TYPE_MAP.get(session.source_type)

        result = await process_content(
            content=session.content,
            prompt_type=prompt_type,
            context=session.context,
            source_url=session.source_url,
            user_id=user_id,
            source_type=source_type,
        )

        async with get_session() as db_session:
            await increment_user_stats(
                session=db_session,
                user_id=user_id,
                tokens_saved=result.get("tokens_saved", 0),
                was_cache_hit=result.get("was_cached", False),
            )

        outcome = GenerationOutcome.from_result(result, action=action)
        logger.info(
            "Генерация завершена: user=%s type=%s cached=%s ms=%.0f",
            user_id,
            prompt_type.value,
            outcome.was_cached,
            outcome.processing_ms,
        )
        return outcome

    async def run_timeline(
        self,
        *,
        user_id: int,
        script_text: str,
        context: str,
        source_url: str = "",
        source_type: ContentSource | None = None,
    ) -> GenerationOutcome:
        """Генерация таймлайна монтажа из готового сценария Reels."""
        result = await process_content(
            content=script_text,
            prompt_type=PromptType.REELS_TIMELINE,
            context=context,
            source_url=source_url,
            user_id=user_id,
            source_type=source_type,
        )

        async with get_session() as db_session:
            await increment_user_stats(
                session=db_session,
                user_id=user_id,
                tokens_saved=result.get("tokens_saved", 0),
                was_cache_hit=result.get("was_cached", False),
            )

        outcome = GenerationOutcome.from_result(result, action="reels_timeline")
        logger.info(
            "Таймлайн сгенерирован: user=%s cached=%s ms=%.0f",
            user_id,
            outcome.was_cached,
            outcome.processing_ms,
        )
        return outcome


ProgressCallback = Callable[[str], Awaitable[None]]


class ReelsRenderUseCase:
    """Автосборка MP4 Reels из таймлайна (TTS + B-roll + FFmpeg)."""

    async def run(
        self,
        *,
        user_id: int,
        timeline: ReelsTimeline,
        source_url: str = "",
        source_type: ContentSource | None = None,
        on_progress: ProgressCallback | None = None,
        options: ReelsRenderOptions | None = None,
    ) -> tuple[Path, float]:
        settings = get_settings()
        start = time.monotonic()

        async with get_session() as db_session:
            allowed, _ = await check_daily_limit(db_session, user_id)
            if not allowed:
                raise DailyLimitExceededError(
                    f"Дневной лимит ({settings.DAILY_REQUEST_LIMIT}) исчерпан. "
                    "Попробуйте завтра."
                )

        output_path = await render_reels_video(
            timeline,
            on_progress=on_progress,
            options=options,
        )
        elapsed_ms = (time.monotonic() - start) * 1000

        async with get_session() as db_session:
            await increment_user_stats(session=db_session, user_id=user_id)
            await log_usage_stat(
                db_session,
                user_id=user_id,
                prompt_type=PromptType.REELS_RENDER,
                source_type=source_type,
                source_url=source_url or None,
                was_cached=False,
                processing_ms=elapsed_ms,
                model_used=settings.GEMINI_TTS_MODEL,
                success=True,
            )

        logger.info("Reels MP4 собран: user=%s ms=%.0f", user_id, elapsed_ms)
        return output_path, elapsed_ms


@dataclass
class ContentPackResult:
    sections: dict[str, GenerationOutcome] = field(default_factory=dict)
    elapsed_ms: float = 0.0

    def format_html(self) -> str:
        from utils.telegram import escape_html

        lines = ["📦 <b>Content Pack</b>", "─" * 30, ""]
        for prompt_type in CONTENT_PACK_TYPES:
            outcome = self.sections.get(prompt_type.value)
            if not outcome:
                continue
            title = get_display_name(prompt_type)
            lines.append(f"<b>{title}</b>")
            lines.append(escape_html(outcome.response))
            lines.append("")
        return "\n".join(lines).strip()


class ContentPackUseCase:
    """Параллельная генерация набора форматов из одного источника."""

    async def run(
        self,
        *,
        user_id: int,
        session: ContentSession,
        on_progress: ProgressCallback | None = None,
    ) -> ContentPackResult:
        settings = get_settings()
        if not settings.CONTENT_PACK_ENABLED:
            raise ValueError("Content Pack отключён (CONTENT_PACK_ENABLED=false)")

        start = time.monotonic()
        source_type = SOURCE_TYPE_MAP.get(session.source_type)
        semaphore = asyncio.Semaphore(2)
        results: dict[str, GenerationOutcome] = {}

        async def generate_one(prompt_type: PromptType) -> None:
            async with semaphore:
                if on_progress:
                    await on_progress(
                        f"📦 Генерирую: {get_display_name(prompt_type)}..."
                    )
                result = await process_content(
                    content=session.content,
                    prompt_type=prompt_type,
                    context=session.context,
                    source_url=session.source_url,
                    user_id=user_id,
                    source_type=source_type,
                )
                async with get_session() as db_session:
                    await increment_user_stats(
                        session=db_session,
                        user_id=user_id,
                        tokens_saved=result.get("tokens_saved", 0),
                        was_cache_hit=result.get("was_cached", False),
                    )
                results[prompt_type.value] = GenerationOutcome.from_result(
                    result,
                    action=prompt_type.value,
                )

        await asyncio.gather(*(generate_one(pt) for pt in CONTENT_PACK_TYPES))

        elapsed_ms = (time.monotonic() - start) * 1000
        async with get_session() as db_session:
            await log_usage_stat(
                db_session,
                user_id=user_id,
                prompt_type=PromptType.CONTENT_PACK,
                source_type=source_type,
                source_url=session.source_url or None,
                was_cached=False,
                processing_ms=elapsed_ms,
                success=True,
            )

        logger.info("Content Pack: user=%s ms=%.0f", user_id, elapsed_ms)
        return ContentPackResult(sections=results, elapsed_ms=elapsed_ms)
