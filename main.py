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
    try:
        app_settings = get_settings()
    except ValueError as exc:
        logger.critical(
            "Не заданы обязательные переменные окружения. "
            "На Railway: Service → Variables → добавьте TELEGRAM_TOKEN и OPENROUTER_API_KEY"
        )
        raise SystemExit(1) from exc

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
