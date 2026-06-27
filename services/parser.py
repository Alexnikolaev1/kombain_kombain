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

from pathlib import Path

import httpx

logger = logging.getLogger("ai_kombain.parser")


class YouTubeBotCheckError(Exception):
    """YouTube требует captcha/cookies с IP сервера."""

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
            settings = get_settings()
            if settings.TRANSCRIPT_FALLBACK_ENABLED and settings.GEMINI_API_KEY:
                transcript_text = await _try_gemini_transcript_fallback(
                    video_id,
                    max_chars,
                    settings.TRANSCRIPT_MAX_DURATION_SEC,
                )
                if transcript_text:
                    language = "gemini-audio"

        if not transcript_text:
            # Фоллбэк на описание если нет субтитров
            content = description[:max_chars] if description else ""
            if not content:
                return ParsedContent(
                    source_type=SourceType.YOUTUBE,
                    content="",
                    url=url,
                    title=title,
                    error=(
                        "Субтитры недоступны с сервера. "
                        "Обновите YOUTUBE_COOKIES_B64 в Railway (cookies устарели) "
                        "или перешлите текст видео боту."
                    ),
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

    except YouTubeBotCheckError as e:
        return ParsedContent(
            source_type=SourceType.YOUTUBE,
            content="",
            url=url,
            error=str(e),
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


def _is_placeholder_proxy(proxy: str) -> bool:
    """Отсекает примеры из документации, случайно вставленные в Railway."""
    lowered = proxy.strip().lower()
    if not lowered:
        return True

    placeholder_markers = (
        "user:pass@host:port",
        "@host:port",
        "your_",
        "changeme",
    )
    if any(marker in lowered for marker in placeholder_markers):
        return True

    parsed = urlparse(proxy)
    if parsed.scheme not in {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}:
        return True
    hostname = (parsed.hostname or "").lower()
    if not hostname or hostname in {"host", "localhost", "example.com"}:
        return True
    return False


def get_effective_youtube_proxy() -> str:
    """Возвращает рабочий прокси или пустую строку."""
    from config import get_settings

    raw = (get_settings().YOUTUBE_PROXY or "").strip()
    if not raw:
        return ""
    if _is_placeholder_proxy(raw):
        logger.warning(
            "YOUTUBE_PROXY похож на пример/заглушку — игнорируем: %s",
            raw,
        )
        return ""
    return raw


def _is_youtube_bot_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "sign in to confirm" in text or "not a bot" in text


def _normalize_b64(value: str) -> str:
    """Убирает переносы строк — Railway иногда вставляет base64 с разбивкой."""
    return re.sub(r"\s+", "", (value or "").strip())


def _inspect_youtube_cookiefile(path: Path) -> dict[str, bool | int]:
    """Проверяет Netscape cookies на наличие ключевых YouTube-полей."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"youtube_lines": 0, "has_sid": False, "has_login": False, "valid": False}

    lines = [line for line in content.splitlines() if line.strip() and not line.startswith("#")]
    youtube_lines = [line for line in lines if "youtube.com" in line]
    has_sid = any(
        "\tSID\t" in line or "\t__Secure-1PSID\t" in line or "\t__Secure-3PSID\t" in line
        for line in youtube_lines
    )
    has_login = any("\tLOGIN_INFO\t" in line for line in youtube_lines)
    valid = bool(youtube_lines) and has_sid

    return {
        "youtube_lines": len(youtube_lines),
        "has_sid": has_sid,
        "has_login": has_login,
        "valid": valid,
    }


def log_youtube_cookie_status() -> None:
    """Диагностика при старте бота — сразу видно, почему YouTube может не работать."""
    from config import get_settings

    settings = get_settings()
    raw_b64 = settings.YOUTUBE_COOKIES_B64.strip()
    file_path = settings.YOUTUBE_COOKIES_FILE.strip()

    if file_path:
        path = Path(file_path)
        if path.is_file():
            info = _inspect_youtube_cookiefile(path)
            logger.info(
                "YouTube cookies (file): %s строк, SID=%s, LOGIN_INFO=%s",
                info["youtube_lines"],
                info["has_sid"],
                info["has_login"],
            )
            if not info["valid"]:
                logger.warning("YouTube cookies file выглядит неполным — обновите экспорт")
            return
        logger.warning("YOUTUBE_COOKIES_FILE не найден: %s", file_path)

    if not raw_b64:
        logger.warning(
            "YouTube cookies НЕ заданы (YOUTUBE_COOKIES_B64 пуст) — "
            "облачный IP Railway будет получать bot-check"
        )
        return

    cookiefile = _get_youtube_cookiefile()
    if not cookiefile:
        logger.error(
            "YOUTUBE_COOKIES_B64 задан (%s символов), но декодирование не удалось — "
            "проверьте base64 (без кавычек, одной строкой)",
            len(_normalize_b64(raw_b64)),
        )
        return

    info = _inspect_youtube_cookiefile(Path(cookiefile))
    logger.info(
        "YouTube cookies (b64): %s строк, SID=%s, LOGIN_INFO=%s → %s",
        info["youtube_lines"],
        info["has_sid"],
        info["has_login"],
        cookiefile,
    )
    if not info["valid"]:
        logger.warning(
            "YouTube cookies без SID — экспортируйте заново из браузера, залогиненного в YouTube"
        )


def _get_youtube_cookiefile() -> str:
    """Путь к cookies.txt (Netscape) для yt-dlp."""
    import base64
    from pathlib import Path

    from config import get_settings

    settings = get_settings()

    if settings.YOUTUBE_COOKIES_FILE:
        path = Path(settings.YOUTUBE_COOKIES_FILE)
        if path.is_file():
            return str(path)
        logger.warning("YOUTUBE_COOKIES_FILE не найден: %s", path)

    raw_b64 = settings.YOUTUBE_COOKIES_B64.strip()
    if raw_b64:
        normalized = _normalize_b64(raw_b64)
        cookies_path = Path(__file__).resolve().parent.parent / "data" / "youtube_cookies.txt"
        try:
            decoded = base64.b64decode(normalized, validate=False)
        except Exception as exc:
            logger.error("Не удалось декодировать YOUTUBE_COOKIES_B64: %s", exc)
            return ""

        if not decoded.strip():
            logger.error("YOUTUBE_COOKIES_B64 декодировался в пустой файл")
            return ""

        cookies_path.parent.mkdir(parents=True, exist_ok=True)
        cookies_path.write_bytes(decoded)

        info = _inspect_youtube_cookiefile(cookies_path)
        if not info["valid"]:
            logger.warning(
                "Декодированные cookies слабые: youtube_lines=%s, has_sid=%s",
                info["youtube_lines"],
                info["has_sid"],
            )
        return str(cookies_path)

    return ""


def _primary_ytdlp_player_clients() -> list[str]:
    """С cookies — web; без — android (меньше bot-check на облаке)."""
    if _get_youtube_cookiefile():
        return ["web", "mweb"]
    return ["android", "web"]


def _build_ytdlp_client_chain(*, has_cookies: bool) -> list[list[str]]:
    """Цепочка player_client: с cookies сначала web, затем android-fallback."""
    candidates: list[list[str] | None] = [
        _primary_ytdlp_player_clients(),
        ["android", "web"],
        ["ios", "web"],
        ["mweb"],
        ["web"] if not has_cookies else None,
    ]
    chain: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for item in candidates:
        if not item:
            continue
        key = tuple(item)
        if key not in seen:
            seen.add(key)
            chain.append(item)
    return chain


class _YtdlpCaptureLogger:
    """Собирает предупреждения yt-dlp (cookies rotated и т.д.)."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        self.messages.append(str(msg))

    def error(self, msg: str) -> None:
        self.messages.append(str(msg))


def _cookies_marked_invalid(logger: _YtdlpCaptureLogger) -> bool:
    joined = " ".join(logger.messages).lower()
    return "cookies are no longer valid" in joined or "have likely been rotated" in joined


def _ytdlp_options(
    player_clients: list[str] | None = None,
    *,
    use_cookies: bool = True,
    capture_logger: _YtdlpCaptureLogger | None = None,
) -> dict:
    """Опции yt-dlp: метаданные и субтитры без скачивания видео."""
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        # Нужны только субтитры — web-клиент часто падает без форматов видео.
        "ignore_no_formats_error": True,
        "extractor_args": {
            "youtube": {
                "player_client": player_clients or ["android", "web"],
            },
        },
    }
    if capture_logger is not None:
        opts["logger"] = capture_logger

    proxy = get_effective_youtube_proxy()
    if proxy:
        opts["proxy"] = proxy
    if use_cookies:
        cookiefile = _get_youtube_cookiefile()
        if cookiefile:
            opts["cookiefile"] = cookiefile
            logger.debug("yt-dlp cookiefile: %s", cookiefile)
    return opts


# Дополнительные client'ы — только без cookies (иначе усугубляем блокировку IP).
_YTDLP_PLAYER_CLIENTS_FALLBACK: list[list[str]] = [["ios", "web"], ["mweb"]]


def _language_matches(track_lang: str, preferred: str) -> bool:
    track = track_lang.lower().replace("_", "-")
    pref = preferred.lower()
    return track == pref or track.startswith(f"{pref}-")


def _iter_tracks_in_order(
    tracks: dict,
    preferred_languages: list[str],
) -> list[tuple[str, list[dict]]]:
    """Возвращает дорожки субтитров в порядке приоритета языков."""
    ordered: list[tuple[str, list[dict]]] = []
    seen: set[str] = set()

    for pref in preferred_languages:
        for lang, formats in tracks.items():
            if lang in seen or not formats:
                continue
            if _language_matches(lang, pref):
                seen.add(lang)
                ordered.append((lang, formats))

    for lang, formats in tracks.items():
        if lang not in seen and formats:
            ordered.append((lang, formats))

    return ordered


def _pick_subtitle_format(formats: list[dict]) -> Optional[dict]:
    for ext in ("json3", "vtt", "ttml"):
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
        if line.startswith("Kind:") or line.startswith("Language:"):
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


def _download_subtitle_url(url: str) -> str:
    """Запасная загрузка субтитров (без cookies yt-dlp)."""
    import urllib.request

    proxy = get_effective_youtube_proxy()
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ru,en;q=0.9",
        },
    )
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        with opener.open(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")

    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def _download_ytdlp_subtitle(ydl, formats: list[dict]) -> str:
    fmt = _pick_subtitle_format(formats)
    if not fmt or not fmt.get("url"):
        return ""

    url = fmt["url"]
    ext = str(fmt.get("ext", "vtt"))
    raw = ""

    try:
        raw = ydl.urlopen(url).read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.debug("yt-dlp urlopen субтитров не удался: %s", e)
        try:
            raw = _download_subtitle_url(url)
        except Exception as fallback_error:
            logger.debug("urllib fallback субтитров не удался: %s", fallback_error)
            return ""

    text = _parse_subtitle_payload(raw, ext)
    if not text:
        logger.debug("Пустой разбор субтитров ext=%s url=%s", ext, url[:80])
    return text


def _extract_ytdlp_transcript(
    info: dict,
    preferred_languages: list[str],
    ydl,
) -> tuple[str, str]:
    """Ищет субтитры в метаданных yt-dlp."""
    for source_name, auto_label in (("subtitles", ""), ("automatic_captions", " (авто)")):
        tracks: dict = info.get(source_name) or {}
        if not tracks:
            continue

        for lang, formats in _iter_tracks_in_order(tracks, preferred_languages):
            text = _download_ytdlp_subtitle(ydl, formats)
            if text:
                return text, f"{lang}{auto_label}"

        logger.warning(
            "Дорожки %s найдены (%s), но текст не извлечён для %s",
            source_name,
            ", ".join(list(tracks.keys())[:6]),
            info.get("id", "?"),
        )

    return "", ""


def _is_ytdlp_format_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "requested format is not available" in text or "no video formats" in text


def _try_ytdlp_extract(
    video_id: str,
    preferred_languages: list[str],
    max_chars: int,
    *,
    player_clients: list[str],
    use_cookies: bool,
) -> tuple[str, str, str, str, _YtdlpCaptureLogger]:
    """Один проход yt-dlp. Возвращает title, description, transcript, lang, logger."""
    import yt_dlp

    url = f"https://www.youtube.com/watch?v={video_id}"
    capture = _YtdlpCaptureLogger()

    with yt_dlp.YoutubeDL(
        _ytdlp_options(player_clients, use_cookies=use_cookies, capture_logger=capture)
    ) as ydl:
        info = ydl.extract_info(url, download=False)
        title = str(info.get("title") or "")
        description = str(info.get("description") or "")[:1000]
        transcript_text, language = _extract_ytdlp_transcript(
            info,
            preferred_languages,
            ydl,
        )
        if transcript_text:
            transcript_text = _clean_transcript(transcript_text)[:max_chars]
        return title, description, transcript_text, language, capture


def _fetch_via_ytdlp(
    video_id: str,
    preferred_languages: list[str],
    max_chars: int,
) -> tuple[str, str, str, str]:
    title = ""
    description = ""
    last_error: Exception | None = None
    bot_blocked = False
    cookies_invalid = False

    has_cookies = bool(_get_youtube_cookiefile())
    cookie_passes = (True, False) if has_cookies else (False,)

    for use_cookies in cookie_passes:
        if use_cookies:
            client_chain = _build_ytdlp_client_chain(has_cookies=True)
        else:
            logger.info(
                "Повтор yt-dlp для %s без cookies — cookies устарели или не дали субтитры",
                video_id,
            )
            client_chain = [["android", "web"], ["android"], *_YTDLP_PLAYER_CLIENTS_FALLBACK]

        for player_clients in client_chain:
            try:
                (
                    title,
                    description,
                    transcript_text,
                    language,
                    capture,
                ) = _try_ytdlp_extract(
                    video_id,
                    preferred_languages,
                    max_chars,
                    player_clients=player_clients,
                    use_cookies=use_cookies,
                )
                if _cookies_marked_invalid(capture):
                    cookies_invalid = True
                    if use_cookies and not transcript_text:
                        logger.warning(
                            "YouTube cookies устарели для %s — пробуем без cookies",
                            video_id,
                        )
                        break
                if transcript_text:
                    logger.info(
                        "yt-dlp OK: %s client=%s cookies=%s lang=%s",
                        video_id,
                        player_clients,
                        use_cookies,
                        language,
                    )
                    return title, description, transcript_text, language
            except Exception as e:
                last_error = e
                if _is_youtube_bot_error(e):
                    bot_blocked = True
                    logger.warning(
                        "YouTube bot-check для %s (client=%s, cookies=%s)",
                        video_id,
                        player_clients,
                        use_cookies,
                    )
                    if not use_cookies:
                        break
                    break
                if _is_ytdlp_format_error(e):
                    logger.warning(
                        "yt-dlp format error для %s (client=%s) — следующий client",
                        video_id,
                        player_clients,
                    )
                    continue
                logger.warning(
                    "yt-dlp player_client=%s ошибка для %s: %s",
                    player_clients,
                    video_id,
                    e,
                )

        if bot_blocked and not use_cookies:
            break

    if bot_blocked:
        if has_cookies or cookies_invalid:
            raise YouTubeBotCheckError(
                "YouTube cookies устарели или не подходят для IP сервера. "
                "Экспортируйте свежие cookies из браузера (нужен аккаунт YouTube) "
                "и обновите YOUTUBE_COOKIES_B64 в Railway."
            )
        raise YouTubeBotCheckError(
            "YouTube блокирует облачный IP. "
            "Добавьте YOUTUBE_COOKIES_B64 в Railway Variables "
            "или перешлите текст видео боту."
        )

    if cookies_invalid and not title:
        raise YouTubeBotCheckError(
            "YouTube cookies устарели. Экспортируйте новые cookies из браузера "
            "(залогинен в YouTube) и обновите YOUTUBE_COOKIES_B64 в Railway."
        )

    if last_error and not title:
        raise last_error
    return title, description, "", ""


def _fetch_via_transcript_api(
    video_id: str,
    preferred_languages: list[str],
    max_chars: int,
) -> tuple[str, str]:
    from youtube_transcript_api import YouTubeTranscriptApi

    api = YouTubeTranscriptApi()
    try:
        fetched = api.fetch(video_id, languages=preferred_languages)
    except Exception:
        transcript_list = api.list(video_id)
        try:
            transcript = transcript_list.find_transcript(preferred_languages)
        except Exception:
            available = list(transcript_list)
            if not available:
                raise
            transcript = available[0]
        fetched = transcript.fetch()

    text = " ".join(snippet.text for snippet in fetched)
    language = getattr(fetched, "language_code", "") or "unknown"
    cleaned = _clean_transcript(text)[:max_chars]
    if not cleaned:
        raise ValueError("Пустой ответ transcript-api")
    return cleaned, language


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

    bot_blocked = False

    try:
        title, description, transcript_text, language = _fetch_via_ytdlp(
            video_id,
            preferred_languages,
            max_chars,
        )
        if transcript_text:
            logger.info("Субтитры получены через yt-dlp: %s (%s)", video_id, language)
    except YouTubeBotCheckError:
        bot_blocked = True
        raise
    except Exception as e:
        logger.warning("yt-dlp ошибка для %s: %s", video_id, e)

    if not transcript_text and not bot_blocked:
        from config import get_settings

        settings = get_settings()
        if settings.YOUTUBE_USE_TRANSCRIPT_API:
            try:
                transcript_text, language = _fetch_via_transcript_api(
                    video_id,
                    preferred_languages,
                    max_chars,
                )
                if transcript_text:
                    logger.info(
                        "Субтитры получены через transcript-api: %s (%s)",
                        video_id,
                        language,
                    )
            except Exception as e:
                error_text = str(e)
                if "blocking requests from your IP" in error_text:
                    logger.warning(
                        "transcript-api заблокирован YouTube для %s (типично для облака)",
                        video_id,
                    )
                else:
                    logger.warning("Ошибка получения субтитров %s: %s", video_id, e)
        else:
            logger.info("transcript-api отключён, пропускаем fallback для %s", video_id)

    return title, description, transcript_text, language


def _download_youtube_audio_sync(
    video_id: str,
    dest_dir: Path,
    max_duration_sec: int,
) -> Path | None:
    """Скачивает аудиодорожку YouTube для распознавания речи."""
    import yt_dlp

    dest_dir.mkdir(parents=True, exist_ok=True)
    url = f"https://www.youtube.com/watch?v={video_id}"
    out_template = str(dest_dir / f"{video_id}.%(ext)s")

    opts = _ytdlp_options(use_cookies=True)
    opts["skip_download"] = False
    opts["format"] = "bestaudio/best"
    opts["outtmpl"] = out_template
    opts["postprocessors"] = [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "64",
        }
    ]
    opts["postprocessor_args"] = {"ffmpeg": ["-t", str(max_duration_sec)]}

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        logger.warning("Не удалось скачать аудио %s: %s", video_id, exc)
        return None

    for path in sorted(dest_dir.glob(f"{video_id}.*")):
        if path.suffix.lower() in {".mp3", ".m4a", ".wav", ".ogg"}:
            return path
    return None


async def _try_gemini_transcript_fallback(
    video_id: str,
    max_chars: int,
    max_duration_sec: int,
) -> str:
    """Скачивает аудио и распознаёт через Gemini."""
    import shutil
    import tempfile

    from services.transcription import TranscriptionError, transcribe_audio_file

    tmp_dir = Path(tempfile.mkdtemp(prefix="yt_audio_"))
    try:
        loop = asyncio.get_event_loop()
        audio_path = await loop.run_in_executor(
            None,
            _download_youtube_audio_sync,
            video_id,
            tmp_dir,
            max_duration_sec,
        )
        if not audio_path:
            return ""

        raw = await transcribe_audio_file(audio_path)
        cleaned = _clean_transcript(raw)[:max_chars]
        if cleaned:
            logger.info(
                "Транскрипт через Gemini audio: %s (%d символов)",
                video_id,
                len(cleaned),
            )
        return cleaned
    except TranscriptionError as exc:
        logger.warning("Gemini transcript fallback для %s: %s", video_id, exc)
        return ""
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


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
    if "requested format is not available" in error_lower:
        return (
            "YouTube временно не отдаёт метаданные с сервера. "
            "Обновите cookies (YOUTUBE_COOKIES_B64) или попробуйте позже."
        )
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
