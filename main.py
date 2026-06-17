"""
main.py — Точка входа. Запускает бота.
"""

import asyncio

from aiogram import Bot
from aiogram.enums import ParseMode

from config import get_settings, logger
from db.database import init_db
from infrastructure.app import create_dispatcher
from infrastructure.lifecycle import run_bot


async def main() -> None:
    app_settings = get_settings()
    await init_db()

    bot = Bot(token=app_settings.TELEGRAM_TOKEN, parse_mode=ParseMode.HTML)
    dp = create_dispatcher()

    await run_bot(
        bot,
        dp,
        health_enabled=app_settings.HEALTH_ENABLED,
        health_port=app_settings.HEALTH_PORT,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
