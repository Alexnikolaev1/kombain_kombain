"""
handlers/keyboards.py — Все клавиатуры бота (Reply и Inline).
"""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from config import get_settings
from domain.models_catalog import AVAILABLE_MODELS
from prompts.templates import (
    ACTION_SLUG_BY_TYPE,
    PROMPT_MENU_ORDER,
    get_button_label,
)


def get_main_menu() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="📹 Обработать видео"),
        KeyboardButton(text="📝 Анализ текста"),
    )
    builder.row(
        KeyboardButton(text="📊 Мой кэш / Статистика"),
        KeyboardButton(text="⚙️ Настройки"),
    )
    builder.row(KeyboardButton(text="❓ Помощь"))
    return builder.as_markup(
        resize_keyboard=True,
        input_field_placeholder="Вставьте ссылку или введите текст...",
    )


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text="❌ Отмена"))
    return builder.as_markup(resize_keyboard=True)


def _build_prompt_keyboard(prefix: str, *, reprocess: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    row: list[InlineKeyboardButton] = []

    for prompt_type in PROMPT_MENU_ORDER:
        slug = ACTION_SLUG_BY_TYPE[prompt_type]
        label = get_button_label(prompt_type, reprocess=reprocess)
        row.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"{prefix}:{slug}",
            )
        )
        if len(row) == 2:
            builder.row(*row)
            row = []

    if row:
        builder.row(*row)

    if not reprocess:
        builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="action:cancel"))
    else:
        builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="reprocess:back"))

    return builder.as_markup()


def get_action_keyboard(source_type: str = "youtube") -> InlineKeyboardMarkup:
    del source_type  # зарезервировано под будущую кастомизацию по источнику
    return _build_prompt_keyboard("action")


def reels_render_available() -> bool:
    settings = get_settings()
    return settings.REELS_RENDER_ENABLED and bool(settings.GEMINI_API_KEY)


def get_result_keyboard(
    *,
    show_timeline: bool = False,
    show_render: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    action_row: list[InlineKeyboardButton] = []
    if show_timeline:
        action_row.append(
            InlineKeyboardButton(
                text="📋 Таймлайн для монтажа",
                callback_data="result:reels_timeline",
            ),
        )
    if show_render and reels_render_available():
        action_row.append(
            InlineKeyboardButton(
                text="🎬 Собрать Reels",
                callback_data="result:reels_render",
            ),
        )
    if action_row:
        builder.row(*action_row)
    builder.row(
        InlineKeyboardButton(text="🔄 Другой формат", callback_data="result:reprocess"),
        InlineKeyboardButton(text="📋 Скопировать", callback_data="result:copy_hint"),
    )
    builder.row(
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="result:main_menu"),
    )
    return builder.as_markup()


def get_reprocess_keyboard() -> InlineKeyboardMarkup:
    return _build_prompt_keyboard("reprocess", reprocess=True)


def get_settings_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🤖 Сменить модель ИИ", callback_data="settings:change_model"),
    )
    builder.row(
        InlineKeyboardButton(text="🗑 Мой кэш", callback_data="settings:clear_my_cache"),
        InlineKeyboardButton(text="🗑 Весь кэш", callback_data="settings:clear_cache"),
    )
    builder.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data="settings:stats"),
    )
    builder.row(InlineKeyboardButton(text="◀️ В меню", callback_data="settings:back"))
    return builder.as_markup()


def get_model_select_keyboard(current_model: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for label, model_id in AVAILABLE_MODELS:
        prefix = "✅ " if current_model == model_id else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{prefix}{label}",
                callback_data=f"model:{model_id}",
            )
        )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="model:back"))
    return builder.as_markup()


def get_confirm_keyboard(action: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Да, подтверждаю", callback_data=f"confirm:{action}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="confirm:cancel"),
    )
    return builder.as_markup()
