"""
application/use_cases.py — бизнес-сценарии без привязки к Telegram.
"""

from __future__ import annotations

import logging
from typing import Optional

from application.dto import ContentSession, GenerationOutcome
from db.database import get_session, increment_user_stats
from db.models import ContentSource, PromptType
from services.llm_client import process_content
from services.parser import ParsedContent, parse_input

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
