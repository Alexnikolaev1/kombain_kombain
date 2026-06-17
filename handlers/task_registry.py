"""
handlers/task_registry.py — хранит ссылки на активные таски обработки.

Нужен, чтобы пользователь мог отменить текущую генерацию/парсинг кнопкой "❌ Отмена".
"""

from __future__ import annotations

import asyncio
from typing import Optional

_task_by_user_id: dict[int, asyncio.Task] = {}


def register_user_task(user_id: int, task: asyncio.Task) -> None:
    _task_by_user_id[user_id] = task


def unregister_user_task(user_id: int) -> None:
    _task_by_user_id.pop(user_id, None)


def cancel_user_task(user_id: int) -> bool:
    task = _task_by_user_id.get(user_id)
    if not task:
        return False
    task.cancel()
    return True


def get_user_task(user_id: int) -> Optional[asyncio.Task]:
    return _task_by_user_id.get(user_id)

