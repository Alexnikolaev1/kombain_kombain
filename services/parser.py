"""
services/parser.py — Модуль извлечения контента из внешних источников.

Поддерживает:
  - YouTube: метаданные + транскрипт (субтитры) на русском/английском
  - Telegram: текст из пересланных сообщений и постов
  - Сырой текст: базовая очистка и нормализация

Всё асинхронно — не блокируем event loop бота.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from enum import Enum
from html import unescape
from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx

logger = logging.getLogger("ai_kombain.parser")

TELEGRAM_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; AIKombainBot/1.0; "
        "+https://github.com/your-repo/ai-kombain)"
    ),
    "Accept-Language": "ru,en;q=0.9",
}

# Публичный пост: t.me/channel/123 или t.me/s/channel/123
_TELEGRAM_PUBLIC_POST_RE = re.compile(
    r"(?:https?://)?(?:t(?:elegram)?\.me)/(?:s/)?"
    r"([a-zA-Z][a-zA-Z0-9_]{3,})/(\d+)",
    re.IGNORECASE,
)

# Приватный пост: t.me/c/1234567890/123
_TELEGRAM_PRIVATE_POST_RE = re.compile(
    r"(?:https?://)?(?:t(?:elegram)?\.me)/c/(\d+)/(\d+)",
    re.IGNORECASE,
)


# ──────────────────────────────────────────────
# Типы результата
# ──────────────────────────────────────────────

class SourceType(Enum):
    YOUTUBE  = "youtube"
    TELEGRAM = "telegram"
    TEXT     = "text"
    UNKNOWN  = "unknown"


@dataclass
class ParsedContent:
    """Результат парсинга контента из любого источника."""
    source_type: SourceType
    content: str           # Основной текст (транскрипт / текст поста)
    title: str = ""        # Название видео/поста
    description: str = ""  # Описание
    url: str = ""          # Оригинальный URL
    language: str = ""     # Язык контента
    duration_sec: int = 0  # Длительность видео (для YouTube)
    error: Optional[str] = None  # Сообщение об ошибке если что-то пошло не так

    @property
    def is_success(self) -> bool:
        return self.error is None and bool(self.content)

    @property
    def context_string(self) -> str:
        """Строка контекста для промтов."""
        parts = []
        if self.title:
            parts.append(f"Название: {self.title}")
        if self.url:
            parts.append(f"URL: {self.url}")
        if self.language:
            parts.append(f"Язык: {self.language}")
        return " | ".join(parts) if parts else ""


# ──────────────────────────────────────────────
# Определение типа ссылки
# ──────────────────────────────────────────────

def detect_source_type(text: str) -> SourceType:
    """Определяет тип источника по тексту/ссылке."""
    text = text.strip()

    # YouTube patterns
    youtube_patterns = [
        r"youtube\.com/watch",
        r"youtu\.be/",
        r"youtube\.com/shorts/",
        r"youtube\.com/live/",
        r"m\.youtube\.com",
    ]
    if any(re.search(p, text, re.I) for p in youtube_patterns):
        return SourceType.YOUTUBE

    # Telegram patterns
    telegram_patterns = [
        r"t\.me/",
        r"telegram\.me/",
        r"telegram\.org/",
    ]
    if any(re.search(p, text, re.I) for p in telegram_patterns):
        return SourceType.TELEGRAM

    # URL без конкретного типа
    if re.match(r"https?://", text):
        return SourceType.UNKNOWN

    return SourceType.TEXT


def extract_youtube_video_id(url: str) -> Optional[str]:
    """Извлекает video ID из YouTube URL любого формата."""
    patterns = [
        r"(?:v=|/v/|youtu\.be/|/embed/|/shorts/|/live/)([A-Za-z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    # Попытка через parse_qs
    parsed = urlparse(url)
    if "youtube" in parsed.netloc:
        qs = parse_qs(parsed.query)
        if "v" in qs:
            return qs["v"][0]

    return None


# ──────────────────────────────────────────────
# YouTube Parser
# ──────────────────────────────────────────────

async def parse_youtube(url: str, max_chars: int = 50_000) -> ParsedContent:
    """
    Асинхронно извлекает транскрипт и метаданные YouTube-видео.

    Приоритет языков: ru → uk → en → любой доступный.
    Если субтитры недоступны — возвращает описание видео.
    """
    from config import get_settings
    settings = get_settings()

    video_id = extract_youtube_video_id(url)
    if not video_id:
        return ParsedContent(
            source_type=SourceType.YOUTUBE,
            content="",
            url=url,
            error="Не удалось извлечь ID видео из ссылки",
        )

    logger.info(f"Парсим YouTube: {video_id}")

    # Запускаем блокирующие операции в thread pool
    loop = asyncio.get_event_loop()

    try:
        title, description, transcript_text, language = await loop.run_in_executor(
            None,
            _fetch_youtube_data_sync,
            video_id,
            settings.YOUTUBE_TRANSCRIPT_LANGUAGES,
            max_chars,
        )

        if not transcript_text:
            # Фоллбэк на описание если нет субтитров
            content = description[:max_chars] if description else ""
            if not content:
                return ParsedContent(
                    source_type=SourceType.YOUTUBE,
                    content="",
                    url=url,
                    title=title,
                    error="Субтитры недоступны для этого видео. Попробуйте другое.",
                )

            logger.warning(f"Субтитры не найдены для {video_id}, используем описание")
            return ParsedContent(
                source_type=SourceType.YOUTUBE,
                content=content,
                title=title,
                url=url,
                language="unknown",
                error=None,
            )

        return ParsedContent(
            source_type=SourceType.YOUTUBE,
            content=transcript_text,
            title=title,
            description=description[:500],
            url=url,
            language=language,
        )

    except Exception as e:
        error_msg = _humanize_youtube_error(str(e))
        logger.error(f"Ошибка парсинга YouTube {video_id}: {e}")
        return ParsedContent(
            source_type=SourceType.YOUTUBE,
            content="",
            url=url,
            error=error_msg,
        )


def _ytdlp_options() -> dict:
    """Опции yt-dlp: без скачивания видео, с обходом типичных ошибок форматов."""
    return {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
            },
        },
    }


def _pick_subtitle_format(formats: list[dict]) -> Optional[dict]:
    for ext in ("vtt", "srv3", "ttml", "json3"):
        for fmt in formats:
            if fmt.get("ext") == ext:
                return fmt
    return formats[0] if formats else None


def _parse_subtitle_payload(raw: str, ext: str) -> str:
    ext = (ext or "vtt").lower()
    if ext == "json3":
        import json

        data = json.loads(raw)
        parts: list[str] = []
        for event in data.get("events", []):
            for seg in event.get("segs") or []:
                chunk = seg.get("utf8", "")
                if chunk and chunk != "\n":
                    parts.append(chunk)
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    lines: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line == "WEBVTT" or "-->" in line:
            continue
        if re.fullmatch(r"\d+", line):
            continue
        if line.startswith("NOTE") or line.startswith("STYLE"):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        line = unescape(line).strip()
        if line:
            lines.append(line)

    deduped: list[str] = []
    for line in lines:
        if not deduped or deduped[-1] != line:
            deduped.append(line)
    return " ".join(deduped)


def _download_ytdlp_subtitle(ydl, formats: list[dict]) -> str:
    fmt = _pick_subtitle_format(formats)
    if not fmt or not fmt.get("url"):
        return ""

    raw = ydl.urlopen(fmt["url"]).read().decode("utf-8", errors="replace")
    return _parse_subtitle_payload(raw, str(fmt.get("ext", "vtt")))


def _extract_ytdlp_transcript(
    info: dict,
    ydl,
    preferred_languages: list[str],
) -> tuple[str, str]:
    """Ищет субтитры в метаданных yt-dlp."""
    for source_name, auto_label in (("subtitles", ""), ("automatic_captions", " (авто)")):
        tracks: dict = info.get(source_name) or {}
        if not tracks:
            continue

        for lang in preferred_languages:
            if lang in tracks:
                text = _download_ytdlp_subtitle(ydl, tracks[lang])
                if text:
                    return text, f"{lang}{auto_label}"

        for lang, formats in tracks.items():
            text = _download_ytdlp_subtitle(ydl, formats)
            if text:
                return text, f"{lang}{auto_label}"

    return "", ""


def _fetch_via_ytdlp(
    video_id: str,
    preferred_languages: list[str],
    max_chars: int,
) -> tuple[str, str, str, str]:
    import yt_dlp

    url = f"https://www.youtube.com/watch?v={video_id}"
    with yt_dlp.YoutubeDL(_ytdlp_options()) as ydl:
        info = ydl.extract_info(url, download=False)

    title = str(info.get("title") or "")
    description = str(info.get("description") or "")[:1000]
    transcript_text, language = _extract_ytdlp_transcript(info, ydl, preferred_languages)
    if transcript_text:
        transcript_text = _clean_transcript(transcript_text)[:max_chars]
    return title, description, transcript_text, language


def _fetch_via_transcript_api(
    video_id: str,
    preferred_languages: list[str],
    max_chars: int,
) -> tuple[str, str]:
    from youtube_transcript_api import YouTubeTranscriptApi

    api = YouTubeTranscriptApi()
    fetched = api.fetch(video_id, languages=preferred_languages)
    text = " ".join(snippet.text for snippet in fetched)
    language = getattr(fetched, "language_code", "") or "unknown"
    return _clean_transcript(text)[:max_chars], language


def _fetch_youtube_data_sync(
    video_id: str,
    preferred_languages: list[str],
    max_chars: int,
) -> tuple[str, str, str, str]:
    """
    Синхронная функция для запуска в thread pool.
    Возвращает (title, description, transcript_text, language).
    """
    title = ""
    description = ""
    transcript_text = ""
    language = ""

    try:
        title, description, transcript_text, language = _fetch_via_ytdlp(
            video_id,
            preferred_languages,
            max_chars,
        )
        if transcript_text:
            logger.info("Субтитры получены через yt-dlp: %s (%s)", video_id, language)
    except Exception as e:
        logger.warning("yt-dlp ошибка для %s: %s", video_id, e)

    if not transcript_text:
        try:
            transcript_text, language = _fetch_via_transcript_api(
                video_id,
                preferred_languages,
                max_chars,
            )
            if transcript_text:
                logger.info("Субтитры получены через transcript-api: %s (%s)", video_id, language)
        except Exception as e:
            logger.warning("Ошибка получения субтитров %s: %s", video_id, e)

    return title, description, transcript_text, language


def _clean_transcript(text: str) -> str:
    """Очищает транскрипт от артефактов форматирования."""
    # Убираем теги субтитров
    text = re.sub(r"<[^>]+>", "", text)
    # Убираем повторяющиеся пробелы и переносы
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    # Убираем типичные артефакты YouTube: [Музыка], [Аплодисменты]
    text = re.sub(r"\[(?:Музыка|Music|Applause|Аплодисменты|♪[^♪]*♪)\]", "", text, flags=re.I)
    return text.strip()


def _humanize_youtube_error(error: str) -> str:
    """Превращает технические ошибки в понятные пользователю сообщения."""
    error_lower = error.lower()
    if "private" in error_lower:
        return "Это приватное видео — субтитры недоступны"
    if "not available" in error_lower or "unavailable" in error_lower:
        return "Видео недоступно в вашем регионе или удалено"
    if "age" in error_lower:
        return "Видео с возрастным ограничением — субтитры недоступны без авторизации"
    if "disabled" in error_lower:
        return "Субтитры для этого видео отключены автором"
    if "blocked" in error_lower or "too many requests" in error_lower:
        return (
            "YouTube временно блокирует запросы с сервера. "
            "Попробуйте позже или перешлите текст/транскрипт вручную."
        )
    return f"Не удалось получить субтитры: {error[:100]}"


# ──────────────────────────────────────────────
# Telegram Parser
# ──────────────────────────────────────────────

@dataclass(frozen=True)
class TelegramPostRef:
    """Ссылка на конкретный пост Telegram."""
    url: str
    channel: str
    message_id: int
    is_private: bool = False


def _strip_url_trailing_punctuation(url: str) -> str:
    return url.rstrip(").,;!?")


def extract_telegram_post_url(text: str) -> Optional[TelegramPostRef]:
    """
    Ищет в тексте ссылку на конкретный пост Telegram.
    Поддерживает t.me, telegram.me, публичные и приватные (/c/) ссылки.
    """
    text = text.strip()
    if not text:
        return None

    private_match = _TELEGRAM_PRIVATE_POST_RE.search(text)
    if private_match:
        channel_id, message_id = private_match.groups()
        url = _strip_url_trailing_punctuation(private_match.group(0))
        if not url.startswith("http"):
            url = f"https://{url}"
        return TelegramPostRef(
            url=url,
            channel=channel_id,
            message_id=int(message_id),
            is_private=True,
        )

    public_match = _TELEGRAM_PUBLIC_POST_RE.search(text)
    if public_match:
        channel, message_id = public_match.groups()
        url = _strip_url_trailing_punctuation(public_match.group(0))
        if not url.startswith("http"):
            url = f"https://{url}"
        # Нормализуем к каноническому виду без /s/
        canonical = f"https://t.me/{channel}/{message_id}"
        return TelegramPostRef(
            url=canonical,
            channel=channel,
            message_id=int(message_id),
            is_private=False,
        )

    return None


def build_telegram_fetch_url(ref: TelegramPostRef) -> str:
    """URL для загрузки HTML-превью поста."""
    if ref.is_private:
        return f"https://t.me/c/{ref.channel}/{ref.message_id}"
    return f"https://t.me/{ref.channel}/{ref.message_id}"


def _html_fragment_to_text(html_fragment: str) -> str:
    """Конвертирует HTML-фрагмент текста поста в plain text."""
    text = html_fragment
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

    def _link_replacer(match: re.Match[str]) -> str:
        href = unescape(match.group(1))
        label = re.sub(r"<[^>]+>", "", match.group(2))
        label = unescape(label).strip()
        if label and label != href:
            return f"{label} ({href})"
        return href

    text = re.sub(
        r'<a href="([^"]+)"[^>]*>(.*?)</a>',
        _link_replacer,
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_meta_content(html: str, property_name: str) -> str:
    match = re.search(
        rf'<meta\s+property="{re.escape(property_name)}"\s+content="([^"]*)"',
        html,
        flags=re.IGNORECASE,
    )
    if match:
        return unescape(match.group(1)).strip()
    return ""


def _extract_message_text_for_post(html: str, ref: TelegramPostRef) -> str:
    """
    Извлекает текст нужного поста из HTML t.me.
    Сначала ищет блок с data-post, затем fallback на единственный виджет.
    """
    post_key = (
        f"c/{ref.channel}/{ref.message_id}"
        if ref.is_private
        else f"{ref.channel}/{ref.message_id}"
    )

    targeted = re.search(
        rf'data-post="{re.escape(post_key)}"[\s\S]*?'
        r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if targeted:
        return _html_fragment_to_text(targeted.group(1))

    generic = re.search(
        r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if generic:
        return _html_fragment_to_text(generic.group(1))

    return ""


def parse_telegram_post_html(html: str, ref: TelegramPostRef) -> tuple[str, str]:
    """Парсит HTML превью t.me. Возвращает (title, content)."""
    content = _extract_message_text_for_post(html, ref)

    channel_title = _extract_meta_content(html, "og:site_name")
    og_title = _extract_meta_content(html, "og:title")
    og_description = _extract_meta_content(html, "og:description")

    title = channel_title or og_title or f"Telegram: {ref.channel}"

    if not content:
        # Пост только с медиа или приватный канал без доступа
        if og_description and "tgme_page" not in og_description.lower():
            content = og_description
        elif "tgme_page_post_not_found" in html or "Post not found" in html:
            raise ValueError("Пост не найден — проверьте ссылку")
        elif ref.is_private and "tgme_widget_message_text" not in html:
            raise ValueError(
                "Приватный пост недоступен. Перешлите сообщение боту напрямую."
            )
        else:
            raise ValueError(
                "В посте нет текста (возможно, только медиа). "
                "Перешлите сообщение боту напрямую."
            )

    return title, content


async def fetch_telegram_post(ref: TelegramPostRef, max_chars: int = 50_000) -> ParsedContent:
    """Загружает публичный пост Telegram по ссылке t.me."""
    fetch_url = build_telegram_fetch_url(ref)
    logger.info("Парсим Telegram: %s", fetch_url)

    try:
        async with httpx.AsyncClient(
            headers=TELEGRAM_FETCH_HEADERS,
            follow_redirects=True,
            timeout=httpx.Timeout(15.0),
        ) as client:
            response = await client.get(fetch_url)
            response.raise_for_status()
            html = response.text
    except httpx.TimeoutException:
        return ParsedContent(
            source_type=SourceType.TELEGRAM,
            content="",
            url=ref.url,
            error="Таймаут при загрузке поста Telegram. Попробуйте позже.",
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            error = "Пост не найден — проверьте ссылку"
        else:
            error = f"Не удалось загрузить пост (HTTP {exc.response.status_code})"
        return ParsedContent(
            source_type=SourceType.TELEGRAM,
            content="",
            url=ref.url,
            error=error,
        )
    except httpx.HTTPError as exc:
        logger.error("Ошибка сети при загрузке Telegram %s: %s", fetch_url, exc)
        return ParsedContent(
            source_type=SourceType.TELEGRAM,
            content="",
            url=ref.url,
            error="Не удалось загрузить пост Telegram. Проверьте ссылку или перешлите пост.",
        )

    try:
        title, content = parse_telegram_post_html(html, ref)
    except ValueError as exc:
        return ParsedContent(
            source_type=SourceType.TELEGRAM,
            content="",
            url=ref.url,
            title=f"Telegram: {ref.channel}",
            error=str(exc),
        )

    cleaned = _clean_telegram_text(content)[:max_chars]
    return ParsedContent(
        source_type=SourceType.TELEGRAM,
        content=cleaned,
        title=title,
        url=ref.url,
    )


async def parse_telegram_message(
    text: str,
    forwarded_text: Optional[str] = None,
    max_chars: int = 50_000,
) -> ParsedContent:
    """
    Обрабатывает контент из Telegram.
    Принимает пересланные сообщения или ссылки на посты t.me.
    """
    # Если есть пересланный текст — используем его напрямую
    if forwarded_text:
        cleaned = _clean_telegram_text(forwarded_text)
        return ParsedContent(
            source_type=SourceType.TELEGRAM,
            content=cleaned,
            title="Telegram пост",
        )

    post_ref = extract_telegram_post_url(text)
    if post_ref:
        return await fetch_telegram_post(post_ref, max_chars=max_chars)

    # Просто текст
    cleaned = _clean_telegram_text(text)
    return ParsedContent(
        source_type=SourceType.TEXT,
        content=cleaned,
        title="Текст пользователя",
    )


def _clean_telegram_text(text: str) -> str:
    """Очищает Telegram-разметку и нормализует текст."""
    # Убираем HTML-теги Telegram
    text = re.sub(r"<[^>]+>", "", text)
    # Убираем markdown-разметку
    text = re.sub(r"[*_`]", "", text)
    # Нормализуем пробелы
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ──────────────────────────────────────────────
# Главная точка входа
# ──────────────────────────────────────────────

async def parse_input(
    text: str,
    forwarded_text: Optional[str] = None,
    max_chars: int = 50_000,
) -> ParsedContent:
    """
    Универсальный парсер. Определяет тип контента и вызывает нужный обработчик.

    Args:
        text: Текст или ссылка от пользователя
        forwarded_text: Текст пересланного сообщения (если есть)
        max_chars: Максимальный размер контента

    Returns:
        ParsedContent с заполненными полями
    """
    from config import get_settings
    settings = get_settings()

    text = text.strip()
    source_type = detect_source_type(text)

    logger.info(f"Парсинг контента типа: {source_type.value} | длина: {len(text)}")

    if source_type == SourceType.YOUTUBE:
        return await parse_youtube(text, max_chars=settings.MAX_TRANSCRIPT_CHARS)

    elif source_type == SourceType.TELEGRAM:
        return await parse_telegram_message(
            text,
            forwarded_text,
            max_chars=settings.MAX_TRANSCRIPT_CHARS,
        )

    elif source_type == SourceType.TEXT:
        cleaned = _clean_telegram_text(text)[:settings.MAX_INPUT_TEXT_CHARS]
        return ParsedContent(
            source_type=SourceType.TEXT,
            content=cleaned,
            title="Введённый текст",
        )

    else:
        # Неизвестный URL — пробуем как текст
        return ParsedContent(
            source_type=SourceType.UNKNOWN,
            content=text[:settings.MAX_INPUT_TEXT_CHARS],
            url=text,
            error="Неподдерживаемый тип ссылки. Поддерживаются YouTube и Telegram.",
        )
