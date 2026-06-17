import pytest
from aiogram.fsm.storage.base import StorageKey
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.fsm_storage import SqliteStorage


@pytest.fixture
async def sqlite_storage(tmp_path, monkeypatch):
    db_path = tmp_path / "test_fsm.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")

    from config import get_settings

    get_settings.cache_clear()

    import db.database as database

    database.engine = database._create_engine()
    database.AsyncSessionFactory = async_sessionmaker(
        bind=database.engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    await database.init_db()
    return SqliteStorage()


@pytest.mark.asyncio
async def test_sqlite_storage_persists_state_and_data(sqlite_storage):
    key = StorageKey(bot_id=1, chat_id=2, user_id=3, destiny="default")

    await sqlite_storage.set_state(key, "ProcessingStates:waiting_for_action")
    await sqlite_storage.set_data(key, {"content": "hello", "title": "Test"})

    storage2 = SqliteStorage()
    assert await storage2.get_state(key) == "ProcessingStates:waiting_for_action"
    assert await storage2.get_data(key) == {"content": "hello", "title": "Test"}
