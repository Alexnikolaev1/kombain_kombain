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
        logger.error(
            "Необработанная ошибка: update=%s exc=%s",
            event.update,
            event.exception,
            exc_info=event.exception,
        )
