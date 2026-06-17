"""infrastructure/storage.py — фабрика FSM storage."""

from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage

from config import get_settings
from db.fsm_storage import SqliteStorage


def create_fsm_storage() -> BaseStorage:
    settings = get_settings()
    if settings.FSM_STORAGE.lower() == "memory":
        return MemoryStorage()
    return SqliteStorage()
