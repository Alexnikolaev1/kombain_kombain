"""
db/database.py — Управление подключением к БД и сессиями SQLAlchemy (async).

Используем asyncio-совместимый движок через aiosqlite / asyncpg.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import AsyncGenerator, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import get_settings
from db.models import AICache, Base, ContentSource, PromptType, UsageStat, User

logger = logging.getLogger("ai_kombain.db")


# ──────────────────────────────────────────────
# Инициализация движка
# ──────────────────────────────────────────────

def _create_engine():
    """Создаёт асинхронный движок SQLAlchemy."""
    settings = get_settings()
    db_url = settings.DATABASE_URL

    # Для SQLite добавляем connect_args для правильной работы с async
    connect_args = {}
    if "sqlite" in db_url:
        connect_args["check_same_thread"] = False

    engine = create_async_engine(
        db_url,
        echo=False,              # Включить True для дебага SQL-запросов
        pool_pre_ping=True,      # Проверяем соединение перед использованием
        connect_args=connect_args,
    )
    return engine


engine = _create_engine()

# Фабрика сессий — используем по всему приложению
AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,    # Не сбрасываем атрибуты после commit (важно для async)
    autoflush=False,
)


async def init_db() -> None:
    """
    Создаёт все таблицы при первом запуске.
    Вызывается один раз при старте бота.
    """
    from pathlib import Path

    # Создаём директорию для SQLite файла если нужно
    settings = get_settings()
    if "sqlite" in settings.DATABASE_URL:
        db_path = settings.DATABASE_URL.split("///")[-1]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ База данных инициализирована")


async def close_db() -> None:
    """Закрывает пул соединений при остановке приложения."""
    await engine.dispose()
    logger.info("База данных отключена")


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Контекстный менеджер для работы с сессией.

    Использование:
        async with get_session() as session:
            result = await session.execute(select(User))
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ──────────────────────────────────────────────
# CRUD операции для пользователей
# ──────────────────────────────────────────────

async def get_or_create_user(
    session: AsyncSession,
    user_id: int,
    username: Optional[str] = None,
    full_name: Optional[str] = None,
) -> User:
    """Возвращает пользователя из БД или создаёт нового."""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(id=user_id, username=username, full_name=full_name)
        session.add(user)
        await session.flush()
        logger.info(f"Новый пользователь: {user_id} (@{username})")
    else:
        now = datetime.utcnow()
        if user.last_seen_at and user.last_seen_at.date() < now.date():
            user.requests_today = 0
        user.username = username
        user.full_name = full_name
        user.last_seen_at = now

    return user


async def get_user_model(session: AsyncSession, user_id: int) -> str:
    """Возвращает модель пользователя или дефолтную из конфига."""
    settings = get_settings()
    preferred = await session.scalar(
        select(User.preferred_model).where(User.id == user_id)
    )
    return str(preferred) if preferred else settings.DEFAULT_MODEL


async def check_daily_limit(session: AsyncSession, user_id: int) -> tuple[bool, int]:
    """
    Проверяет дневной лимит запросов.
    Возвращает (разрешено, осталось_запросов).
    """
    settings = get_settings()
    if settings.DAILY_REQUEST_LIMIT <= 0:
        return True, -1

    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        return True, settings.DAILY_REQUEST_LIMIT

    remaining = settings.DAILY_REQUEST_LIMIT - user.requests_today
    return remaining > 0, max(remaining, 0)


async def log_usage_stat(
    session: AsyncSession,
    *,
    user_id: int,
    prompt_type: PromptType,
    source_type: Optional[ContentSource] = None,
    source_url: Optional[str] = None,
    was_cached: bool = False,
    tokens_used: Optional[int] = None,
    processing_ms: Optional[float] = None,
    model_used: Optional[str] = None,
    success: bool = True,
    error_message: Optional[str] = None,
) -> None:
    """Сохраняет детальную статистику одного запроса."""
    session.add(
        UsageStat(
            user_id=user_id,
            prompt_type=prompt_type,
            source_type=source_type,
            source_url=source_url,
            was_cached=was_cached,
            tokens_used=tokens_used,
            processing_ms=processing_ms,
            model_used=model_used,
            success=success,
            error_message=error_message,
        )
    )


async def increment_user_stats(
    session: AsyncSession,
    user_id: int,
    tokens_saved: int = 0,
    was_cache_hit: bool = False,
) -> None:
    """Обновляет счётчики использования для пользователя."""
    values = {
        "requests_total": User.requests_total + 1,
        "tokens_saved": User.tokens_saved + tokens_saved,
        "cache_hits": User.cache_hits + (1 if was_cache_hit else 0),
    }
    if not was_cache_hit:
        values["requests_today"] = User.requests_today + 1

    await session.execute(
        update(User).where(User.id == user_id).values(**values)
    )


# ──────────────────────────────────────────────
# CRUD операции для кэша ИИ
# ──────────────────────────────────────────────

async def get_cached_response(
    session: AsyncSession,
    input_text: str,
    prompt_type: PromptType,
    context: str,
    model_id: str,
) -> Optional[AICache]:
    """
    Ищет кэшированный ответ в БД.
    Возвращает None если кэш пустой или устарел.
    """
    settings = get_settings()
    if not settings.CACHE_ENABLED:
        return None

    request_hash = AICache.compute_hash(
        input_text=input_text,
        prompt_type=prompt_type,
        context=context,
        model_id=model_id,
    )

    result = await session.execute(
        select(AICache).where(
            AICache.request_hash == request_hash,
            AICache.prompt_type == prompt_type,
        )
    )
    cached = result.scalar_one_or_none()

    if cached is None:
        return None

    # Проверяем TTL если задан
    if cached.expires_at and cached.expires_at < datetime.utcnow():
        logger.debug(f"Кэш устарел: {request_hash[:8]}...")
        await session.delete(cached)
        return None

    # Обновляем статистику попаданий
    cached.hit_count += 1
    cached.last_hit_at = datetime.utcnow()

    logger.info(f"💾 Кэш-хит! hash={request_hash[:8]}... тип={prompt_type.value} попаданий={cached.hit_count}")
    return cached


async def save_to_cache(
    session: AsyncSession,
    input_text: str,
    prompt_type: PromptType,
    response_text: str,
    model_used: str,
    context: str,
    tokens_used: Optional[int] = None,
    source_url: Optional[str] = None,
    user_id: Optional[int] = None,
) -> AICache:
    """Сохраняет ответ ИИ в кэш."""
    settings = get_settings()
    request_hash = AICache.compute_hash(
        input_text=input_text,
        prompt_type=prompt_type,
        context=context,
        model_id=model_used,
    )

    expires_at = datetime.utcnow() + timedelta(hours=settings.CACHE_TTL_HOURS)

    result = await session.execute(
        select(AICache).where(
            AICache.request_hash == request_hash,
            AICache.prompt_type == prompt_type,
        )
    )
    cache_entry = result.scalar_one_or_none()

    if cache_entry is None:
        cache_entry = AICache(
            request_hash=request_hash,
            prompt_type=prompt_type,
            source_url=source_url,
            user_id=user_id,
            input_preview=input_text[:200],
            response_text=response_text,
            model_used=model_used,
            tokens_used=tokens_used,
            expires_at=expires_at,
        )
        session.add(cache_entry)
    else:
        cache_entry.source_url = source_url
        cache_entry.user_id = user_id or cache_entry.user_id
        cache_entry.input_preview = input_text[:200]
        cache_entry.response_text = response_text
        cache_entry.model_used = model_used
        cache_entry.tokens_used = tokens_used
        cache_entry.expires_at = expires_at

    await session.flush()

    logger.debug(f"💾 Сохранено в кэш: hash={request_hash[:8]}... тип={prompt_type.value}")
    return cache_entry


async def clear_user_cache(session: AsyncSession, user_id: int) -> int:
    """Удаляет кэш-записи, созданные конкретным пользователем."""
    result = await session.execute(
        delete(AICache).where(AICache.user_id == user_id)
    )
    return int(result.rowcount or 0)
