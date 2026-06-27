"""
middleware/errors.py — глобальный обработчик необработанных ошибок.
"""

import logging

from aiogram import Dispatcher
from aiogram.types import ErrorEvent

logger = logging.getLogger("ai_kombain.errors")


def register_error_handler(dp: Dispatcher) -> None:
    @dp.errors()
    async def on_error(event: ErrorEvent) -> None:
        exc = event.exception
        exc_name = type(exc).__name__

        if exc_name == "TelegramConflictError":
            logger.critical(
                "⚠️ ДВА экземпляра бота! Остановите локальный main.py и проверьте "
                "Railway Replicas=1. Иначе сборка Reels обрывается. exc=%s",
                exc,
            )
            return

        logger.error(
            "Необработанная ошибка: update=%s exc=%s",
            event.update,
            exc,
            exc_info=exc,
        )
