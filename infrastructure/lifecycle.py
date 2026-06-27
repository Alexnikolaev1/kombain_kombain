"""
infrastructure/lifecycle.py — graceful shutdown и фоновые задачи.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from contextlib import suppress

from aiogram import Bot, Dispatcher

from db.database import close_db
from infrastructure.health import heartbeat_loop, stop_health_server, touch_heartbeat
from services.llm_client import close_http_client
from services.gemini_client import close_gemini_client

logger = logging.getLogger("ai_kombain.lifecycle")


def register_shutdown_signals(shutdown_event: asyncio.Event) -> None:
    """Регистрирует SIGINT/SIGTERM (кроме Windows)."""
    if sys.platform == "win32":
        return

    loop = asyncio.get_running_loop()

    def _handler() -> None:
        logger.info("Получен сигнал остановки")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, _handler)


async def run_bot(
    bot: Bot,
    dp: Dispatcher,
    *,
    health_enabled: bool,
    health_port: int,
) -> None:
    """Запускает polling с healthcheck и корректным завершением."""
    shutdown_event = asyncio.Event()
    register_shutdown_signals(shutdown_event)

    touch_heartbeat()
    background_tasks: list[asyncio.Task] = []

    if health_enabled:
        from infrastructure.health import run_health_server

        background_tasks.append(
            asyncio.create_task(run_health_server(port=health_port), name="health-server")
        )
        background_tasks.append(
            asyncio.create_task(heartbeat_loop(), name="heartbeat-loop")
        )

    polling_task = asyncio.create_task(
        dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            handle_signals=False,
            drop_pending_updates=True,
        ),
        name="telegram-polling",
    )

    logger.info("🚀 Бот запущен (должен быть только 1 экземпляр — иначе Telegram Conflict)")

    try:
        shutdown_wait = asyncio.create_task(shutdown_event.wait(), name="shutdown-wait")
        done, _ = await asyncio.wait(
            [polling_task, shutdown_wait],
            return_when=asyncio.FIRST_COMPLETED,
        )

        if shutdown_event.is_set():
            logger.info("🛑 Останавливаем бота...")
            await dp.stop_polling()
        elif polling_task in done:
            exc = polling_task.exception()
            if exc:
                raise exc
    finally:
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            with suppress(asyncio.CancelledError):
                await task

        await stop_health_server()

        with suppress(asyncio.CancelledError):
            await polling_task

        await close_http_client()
        await close_gemini_client()
        await close_db()
        await bot.session.close()
        logger.info("🛑 Бот остановлен")
