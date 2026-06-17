"""
middleware/user.py — автоматическая регистрация/обновление пользователя.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from db.database import get_or_create_user, get_session
from infrastructure.health import touch_heartbeat


class UserMiddleware(BaseMiddleware):
    """Создаёт или обновляет пользователя на каждом входящем событии."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = None
        if isinstance(event, Message) and event.from_user:
            tg_user = event.from_user
        elif isinstance(event, CallbackQuery) and event.from_user:
            tg_user = event.from_user

        if tg_user:
            async with get_session() as session:
                data["db_user"] = await get_or_create_user(
                    session=session,
                    user_id=tg_user.id,
                    username=tg_user.username,
                    full_name=tg_user.full_name,
                )
            touch_heartbeat()

        return await handler(event, data)
