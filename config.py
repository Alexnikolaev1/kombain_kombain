"""
config.py — Центральный модуль конфигурации ИИ-Комбайна.
Загружает переменные окружения, настраивает логирование и хранит глобальные константы.
"""

import logging
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
import os

# Загружаем .env файл (для локальной разработки)
load_dotenv()


# ──────────────────────────────────────────────
# Настройка логирования
# ──────────────────────────────────────────────

def setup_logging(level: str = "INFO") -> logging.Logger:
    """Настраивает структурированное логирование с цветным выводом в консоль."""
    log_format = "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d — %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Снижаем шум от сторонних библиотек
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    return logging.getLogger("ai_kombain")


logger = setup_logging(os.getenv("LOG_LEVEL", "INFO"))


# ──────────────────────────────────────────────
# Датакласс конфигурации
# ──────────────────────────────────────────────

@dataclass
class Settings:
    """Все настройки приложения в одном месте. Валидируем при старте."""

    # Telegram
    TELEGRAM_TOKEN: str = field(default_factory=lambda: os.getenv("TELEGRAM_TOKEN", ""))

    # LLM API (OpenRouter — агрегатор, поддерживает сотни моделей)
    OPENROUTER_API_KEY: str = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

    # Google Gemini API (бесплатный ключ AI Studio) — запасной провайдер
    GEMINI_API_KEY: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    GEMINI_MODEL: str = field(
        default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    )

    # Gemini TTS (бесплатно через AI Studio — gemini-3.1-flash-tts-preview)
    GEMINI_TTS_MODEL: str = field(
        default_factory=lambda: os.getenv(
            "GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts"
        )
    )
    GEMINI_TTS_VOICE: str = field(
        default_factory=lambda: os.getenv("GEMINI_TTS_VOICE", "Kore")
    )
    GEMINI_TTS_LANGUAGE: str = field(
        default_factory=lambda: os.getenv("GEMINI_TTS_LANGUAGE", "ru-RU")
    )
    GEMINI_TTS_MAX_RETRIES: int = int(os.getenv("GEMINI_TTS_MAX_RETRIES", "5"))
    GEMINI_TTS_RETRY_BASE_SEC: float = float(os.getenv("GEMINI_TTS_RETRY_BASE_SEC", "8"))
    GEMINI_TTS_SCENE_DELAY_SEC: float = float(os.getenv("GEMINI_TTS_SCENE_DELAY_SEC", "2.5"))

    # Pexels — бесплатный сток B-roll (опционально; без ключа — цветной фон)
    PEXELS_API_KEY: str = field(default_factory=lambda: os.getenv("PEXELS_API_KEY", ""))

    # Автосборка Reels (Фаза 2)
    REELS_RENDER_ENABLED: bool = (
        os.getenv("REELS_RENDER_ENABLED", "true").lower() == "true"
    )
    REELS_RENDER_MAX_SCENES: int = int(os.getenv("REELS_RENDER_MAX_SCENES", "6"))
    REELS_VIDEO_MAX_MB: int = int(os.getenv("REELS_VIDEO_MAX_MB", "45"))

    # Модель по умолчанию
    DEFAULT_MODEL: str = field(
        default_factory=lambda: os.getenv("DEFAULT_MODEL", "google/gemini-2.5-flash")
    )

    # Запасная модель если основная недоступна
    FALLBACK_MODEL: str = field(
        default_factory=lambda: os.getenv("FALLBACK_MODEL", "openrouter/free")
    )

    # База данных
    DATABASE_URL: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            f"sqlite+aiosqlite:///{Path(__file__).parent / 'data' / 'kombain.db'}"
        )
    )

    # Кэширование ответов ИИ
    CACHE_TTL_HOURS: int = int(os.getenv("CACHE_TTL_HOURS", "24"))   # Время жизни кэша
    CACHE_ENABLED: bool = os.getenv("CACHE_ENABLED", "true").lower() == "true"

    # Лимиты обработки
    MAX_TRANSCRIPT_CHARS: int = 50_000    # ~30 минут видео
    MAX_INPUT_TEXT_CHARS: int = 20_000    # Максимум текста от пользователя
    LLM_MAX_TOKENS: int = 2048            # Максимум токенов в ответе
    LLM_TEMPERATURE: float = 0.7

    # Лимит запросов на пользователя в сутки (0 = без лимита)
    DAILY_REQUEST_LIMIT: int = int(os.getenv("DAILY_REQUEST_LIMIT", "50"))

    # Healthcheck для Docker/Railway (PORT — стандартная переменная PaaS)
    HEALTH_ENABLED: bool = os.getenv("HEALTH_ENABLED", "true").lower() == "true"
    HEALTH_PORT: int = int(os.getenv("PORT", os.getenv("HEALTH_PORT", "8080")))

    # FSM: sqlite (по умолчанию, переживает рестарт) или memory
    FSM_STORAGE: str = os.getenv("FSM_STORAGE", "sqlite")

    # YouTube
    YOUTUBE_TRANSCRIPT_LANGUAGES: list = field(
        default_factory=lambda: ["ru", "en", "uk"]  # Приоритет языков субтитров
    )
    YOUTUBE_PROXY: str = field(default_factory=lambda: os.getenv("YOUTUBE_PROXY", ""))
    YOUTUBE_USE_TRANSCRIPT_API: bool = (
        os.getenv("YOUTUBE_USE_TRANSCRIPT_API", "true").lower() == "true"
    )
    # Cookies YouTube (Netscape cookies.txt) — обход bot-check на облачных IP
    YOUTUBE_COOKIES_FILE: str = field(default_factory=lambda: os.getenv("YOUTUBE_COOKIES_FILE", ""))
    YOUTUBE_COOKIES_B64: str = field(default_factory=lambda: os.getenv("YOUTUBE_COOKIES_B64", ""))

    # Идентификация приложения для OpenRouter (обязательно для их ToS)
    APP_NAME: str = "AI-Kombain-Bot"
    APP_URL: str = "https://github.com/your-repo/ai-kombain"

    def validate(self) -> None:
        """Проверяет наличие обязательных переменных. Падает при старте если что-то не так."""
        errors = []
        if not self.TELEGRAM_TOKEN:
            errors.append("TELEGRAM_TOKEN не задан")
        if not self.OPENROUTER_API_KEY and not self.GEMINI_API_KEY:
            errors.append("Нужен OPENROUTER_API_KEY или GEMINI_API_KEY")
        if errors:
            for err in errors:
                logger.critical(f"❌ Конфигурация: {err}")
            raise ValueError(f"Ошибки конфигурации: {'; '.join(errors)}")
        providers = []
        if self.OPENROUTER_API_KEY:
            providers.append("OpenRouter")
        if self.GEMINI_API_KEY:
            providers.append("Gemini")
        logger.info(
            "✅ Конфигурация загружена. Модель: %s. Провайдеры: %s",
            self.DEFAULT_MODEL,
            ", ".join(providers),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Синглтон настроек. Используется во всех модулях через dependency injection."""
    settings = Settings()
    settings.validate()
    return settings
