"""
db/fsm_storage.py — SQLite/SQLAlchemy storage для FSM aiogram.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey
from sqlalchemy import delete, select

from db import database
from db.models import FsmRecord

logger = logging.getLogger("ai_kombain.fsm")


class SqliteStorage(BaseStorage):
    """Хранит FSM в БД — состояния переживают рестарт бота."""

    def _storage_key(self, key: StorageKey) -> str:
        return (
            f"{key.bot_id}:{key.chat_id}:{key.user_id}:"
            f"{key.thread_id}:{key.business_connection_id}:{key.destiny}"
        )

    def _normalize_state(self, state: StateType) -> Optional[str]:
        if state is None:
            return None
        if isinstance(state, State):
            return state.state
        return str(state)

    async def close(self) -> None:
        return None

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        storage_key = self._storage_key(key)
        normalized = self._normalize_state(state)

        async with database.AsyncSessionFactory() as session:
            record = await session.get(FsmRecord, storage_key)
            if record is None:
                if normalized is None:
                    return
                record = FsmRecord(storage_key=storage_key, state=normalized, data_json="{}")
                session.add(record)
            else:
                record.state = normalized
                record.updated_at = datetime.utcnow()
            await session.commit()

    async def get_state(self, key: StorageKey) -> Optional[str]:
        storage_key = self._storage_key(key)
        async with database.AsyncSessionFactory() as session:
            record = await session.get(FsmRecord, storage_key)
            return record.state if record else None

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        storage_key = self._storage_key(key)
        payload = json.dumps(data, ensure_ascii=False)

        async with database.AsyncSessionFactory() as session:
            record = await session.get(FsmRecord, storage_key)
            if record is None:
                record = FsmRecord(storage_key=storage_key, state=None, data_json=payload)
                session.add(record)
            else:
                record.data_json = payload
                record.updated_at = datetime.utcnow()
            await session.commit()

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        storage_key = self._storage_key(key)
        async with database.AsyncSessionFactory() as session:
            record = await session.get(FsmRecord, storage_key)
            if record is None or not record.data_json:
                return {}
            try:
                return json.loads(record.data_json)
            except json.JSONDecodeError:
                logger.warning("Повреждённые FSM-данные для %s", storage_key)
                return {}

    async def clear_key(self, key: StorageKey) -> None:
        storage_key = self._storage_key(key)
        async with database.AsyncSessionFactory() as session:
            await session.execute(delete(FsmRecord).where(FsmRecord.storage_key == storage_key))
            await session.commit()
