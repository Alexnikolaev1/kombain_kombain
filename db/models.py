"""
db/models.py — Схема базы данных через SQLAlchemy ORM.

Таблицы:
  - users       : Пользователи бота и их настройки
  - ai_cache    : Кэш ответов ИИ (экономия на API-токенах)
  - usage_stats : Статистика использования для аналитики
"""

import hashlib
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Enum, Float,
    Integer, String, Text, UniqueConstraint, func
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Базовый класс для всех моделей."""
    pass


# ──────────────────────────────────────────────
# Enum-типы
# ──────────────────────────────────────────────

class PromptType(PyEnum):
    """Типы промтов / шаблонов обработки контента."""
    VIRAL_TITLES   = "viral_titles"    # Виральные заголовки
    DEEP_ANALYSIS  = "deep_analysis"   # Смысловой анализ и тезисы
    REELS_SCRIPT   = "reels_script"    # Сценарий Reels/Shorts
    REELS_TIMELINE = "reels_timeline"  # Таймлайн монтажа из сценария Reels
    REELS_RENDER   = "reels_render"    # Автосборка MP4 Reels
    TLDR_SUMMARY   = "tldr_summary"    # Краткая суммаризация (TL;DR)
    TELEGRAM_POST  = "telegram_post"   # Пост для Telegram
    HASHTAGS_PACK  = "hashtags_pack"   # Хештеги для соцсетей
    CONTENT_PACK   = "content_pack"    # Пакетная генерация (мета-тип для статистики)


class ContentSource(PyEnum):
    """Источник контента."""
    YOUTUBE  = "youtube"
    TELEGRAM = "telegram"
    TEXT     = "text"        # Сырой текст от пользователя


# ──────────────────────────────────────────────
# Модели
# ──────────────────────────────────────────────

class User(Base):
    """
    Пользователь бота.
    Хранит настройки и счётчики использования.
    """
    __tablename__ = "users"

    id              = Column(BigInteger, primary_key=True)           # Telegram user_id
    username        = Column(String(64), nullable=True, index=True)
    full_name       = Column(String(256), nullable=True)
    is_active       = Column(Boolean, default=True, nullable=False)

    # Персональная модель (если пользователь хочет другую)
    preferred_model = Column(String(128), nullable=True)

    # Лимиты и счётчики
    requests_today  = Column(Integer, default=0, nullable=False)
    requests_total  = Column(Integer, default=0, nullable=False)
    tokens_saved    = Column(Integer, default=0, nullable=False)     # Сколько токенов сэкономлено кэшем
    cache_hits      = Column(Integer, default=0, nullable=False)

    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username}>"


class AICache(Base):
    """
    Кэш ответов ИИ — главный инструмент экономии токенов.

    Логика: перед отправкой запроса в LLM вычисляем SHA-256 хэш от
    (входной текст + тип промта). Если запись есть в БД и не устарела —
    возвращаем готовый ответ без обращения к API.

    Эффект: повторные запросы на одно и то же видео/текст = 0 токенов.
    """
    __tablename__ = "ai_cache"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    request_hash  = Column(String(64), nullable=False, index=True)   # SHA-256
    prompt_type   = Column(Enum(PromptType), nullable=False, index=True)
    source_type   = Column(Enum(ContentSource), nullable=True)
    source_url    = Column(Text, nullable=True)                       # Оригинальный URL
    user_id       = Column(BigInteger, nullable=True, index=True)     # Кто создал запись кэша

    # Сжатый ключ для поиска (первые 200 символов текста — для дебага)
    input_preview = Column(String(200), nullable=True)

    response_text = Column(Text, nullable=False)                      # Ответ от ИИ
    model_used    = Column(String(128), nullable=True)                # Какая модель ответила
    tokens_used   = Column(Integer, nullable=True)                    # Сколько токенов потрачено
    hit_count     = Column(Integer, default=0, nullable=False)        # Сколько раз достали из кэша

    created_at    = Column(DateTime, default=func.now(), nullable=False)
    last_hit_at   = Column(DateTime, nullable=True)
    expires_at    = Column(DateTime, nullable=True)                   # NULL = бессрочно

    __table_args__ = (
        UniqueConstraint("request_hash", "prompt_type", name="uq_cache_hash_type"),
    )

    @staticmethod
    def compute_hash(
        input_text: str,
        prompt_type: PromptType,
        context: str,
        model_id: str,
    ) -> str:
        """Вычисляет детерминированный хэш для идентификации запроса.

        Ключ кэша должен зависеть от:
        - типа промпта,
        - модели (иначе возможны конфликты при смене модели),
        - контекста (title/url/language влияют на user_message шаблона),
        - входного текста.
        """
        payload = (
            f"{prompt_type.value}::{model_id}::"
            f"{context.strip().lower()}::{input_text.strip().lower()}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def __repr__(self) -> str:
        return f"<AICache hash={self.request_hash[:8]}... type={self.prompt_type} hits={self.hit_count}>"


class UsageStat(Base):
    """
    Детальная статистика каждого запроса.
    Нужна для анализа эффективности и дебага.
    """
    __tablename__ = "usage_stats"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    user_id        = Column(BigInteger, nullable=False, index=True)
    prompt_type    = Column(Enum(PromptType), nullable=False)
    source_type    = Column(Enum(ContentSource), nullable=True)
    source_url     = Column(Text, nullable=True)

    was_cached     = Column(Boolean, default=False, nullable=False)   # Достали из кэша?
    tokens_used    = Column(Integer, nullable=True)
    processing_ms  = Column(Float, nullable=True)                     # Время обработки в мс
    model_used     = Column(String(128), nullable=True)
    success        = Column(Boolean, default=True, nullable=False)
    error_message  = Column(Text, nullable=True)

    created_at     = Column(DateTime, default=func.now(), nullable=False, index=True)


class Project(Base):
    """Сохранённый проект пользователя (сценарий, таймлайн, источник)."""
    __tablename__ = "projects"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    user_id        = Column(BigInteger, nullable=False, index=True)
    title          = Column(String(256), nullable=False, default="Проект")
    source_url     = Column(Text, nullable=True)
    source_type    = Column(Enum(ContentSource), nullable=True)
    content_hash   = Column(String(64), nullable=True, index=True)
    script_text    = Column(Text, nullable=True)
    timeline_json  = Column(Text, nullable=True)
    created_at     = Column(DateTime, default=func.now(), nullable=False)
    updated_at     = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class FsmRecord(Base):
    """Персистентное хранилище FSM-состояний aiogram."""
    __tablename__ = "fsm_states"

    storage_key = Column(String(320), primary_key=True)
    state       = Column(String(128), nullable=True)
    data_json   = Column(Text, nullable=False, default="{}")
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
