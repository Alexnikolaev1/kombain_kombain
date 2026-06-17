"""
utils/telegram.py — утилиты для безопасной отправки сообщений в Telegram.
"""

from __future__ import annotations

import asyncio
import html
from typing import Optional

from aiogram.types import InlineKeyboardMarkup, Message

TELEGRAM_MAX_MESSAGE_LEN = 4000
CONTENT_PREVIEW_MAX_CHARS = 1200

LOADING_FRAMES = ["⏳", "⌛"]
LOADING_MESSAGES = [
    "🔍 Читаю транскрипт...",
    "🧠 Анализирую содержимое...",
    "✍️ Генерирую контент...",
    "⚡ Почти готово...",
]


def escape_html(text: str) -> str:
    """Экранирует текст для parse_mode=HTML."""
    return html.escape(text or "", quote=False)


def split_text(text: str, max_len: int = TELEGRAM_MAX_MESSAGE_LEN) -> list[str]:
    """Разбивает длинный текст на части по границам абзацев."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current = ""

    for paragraph in text.split("\n\n"):
        if len(current) + len(paragraph) + 2 <= max_len:
            current += ("\n\n" if current else "") + paragraph
        else:
            if current:
                chunks.append(current)
            if len(paragraph) > max_len:
                for i in range(0, len(paragraph), max_len):
                    chunks.append(paragraph[i : i + max_len])
                current = ""
            else:
                current = paragraph

    if current:
        chunks.append(current)

    return chunks


def format_generation_message(
    title: str,
    response_text: str,
    *,
    was_cached: bool,
    model: str,
    processing_ms: float,
) -> str:
    """Собирает финальное HTML-сообщение с результатом генерации."""
    if was_cached:
        cache_badge = "💾 <i>Из кэша</i>"
    else:
        model_short = str(model or "").split("/")[-1]
        cache_badge = f"🤖 <i>Модель: {escape_html(model_short)}</i>"

    if processing_ms < 1000:
        time_badge = f"⚡ {processing_ms:.0f}мс"
    else:
        time_badge = f"⏱ {processing_ms / 1000:.1f}с"

    header = (
        f"{title}\n"
        f"{cache_badge} | {time_badge}\n"
        f"{'─' * 30}\n\n"
    )
    return header + escape_html(response_text)


def _truncate_preview(text: str, max_len: int = CONTENT_PREVIEW_MAX_CHARS) -> str:
    """Обрезает превью по границе слова, если текст длиннее лимита."""
    normalized = " ".join((text or "").split())
    if len(normalized) <= max_len:
        return normalized

    cut = normalized[:max_len]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return f"{cut}…"


def format_content_preview(
    *,
    title: str,
    content: str,
    char_count: int,
) -> str:
    """Форматирует превью после успешного парсинга."""
    preview = _truncate_preview(content)
    title_html = escape_html(title) if title else ""
    content_info = f"\n📌 <b>{title_html}</b>" if title else ""

    return (
        f"✅ <b>Контент получен!</b>{content_info}\n\n"
        f"📊 Размер: <code>{char_count:,} символов</code>\n"
        f"📝 Превью: <i>{escape_html(preview)}</i>\n\n"
        f"<b>Выберите формат обработки:</b>"
    )


async def send_long_html(
    anchor: Message,
    text: str,
    *,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    max_len: int = TELEGRAM_MAX_MESSAGE_LEN,
    edit: bool = True,
) -> None:
    """Отправляет длинный HTML-текст частями или редактирует одно сообщение."""
    if len(text) <= max_len:
        if edit:
            await anchor.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
        else:
            await anchor.answer(text, parse_mode="HTML", reply_markup=reply_markup)
        return

    if edit:
        await anchor.delete()

    chunks = split_text(text, max_len)
    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        markup = reply_markup if is_last else None
        if i == 0 and not edit:
            await anchor.answer(chunk, parse_mode="HTML", reply_markup=markup)
        else:
            await anchor.answer(chunk, parse_mode="HTML", reply_markup=markup)


class LoadingAnimator:
    """Контекстный менеджер анимации загрузки в сообщении."""

    def __init__(
        self,
        message: Message,
        frames: list[str] | None = None,
        messages: list[str] | None = None,
        interval_sec: float = 2.5,
    ) -> None:
        self.message = message
        self.frames = frames or LOADING_FRAMES
        self.messages = messages or LOADING_MESSAGES
        self.interval_sec = interval_sec
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> "LoadingAnimator":
        self._task = asyncio.create_task(self._animate())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _animate(self) -> None:
        frame_idx = 0
        msg_idx = 0

        while not self._stop.is_set():
            try:
                frame = self.frames[frame_idx % len(self.frames)]
                text = self.messages[msg_idx % len(self.messages)]
                await self.message.edit_text(
                    f"{frame} <b>{text}</b>",
                    parse_mode="HTML",
                )
                frame_idx += 1
                if frame_idx % 2 == 0:
                    msg_idx += 1
                await asyncio.sleep(self.interval_sec)
            except Exception:
                break
