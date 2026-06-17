"""
handlers/settings.py — обработчики меню настроек.
"""

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import delete, update

from db.database import clear_user_cache, get_session, get_user_model
from db.models import AICache, User
from domain.models_catalog import AVAILABLE_MODEL_IDS, get_model_label
from handlers.keyboards import (
    get_confirm_keyboard,
    get_model_select_keyboard,
    get_settings_keyboard,
)

logger = logging.getLogger("ai_kombain.handlers.settings")
router = Router()


@router.callback_query(F.data == "settings:change_model")
async def settings_change_model(callback: CallbackQuery) -> None:
    async with get_session() as session:
        current_model = await get_user_model(session, callback.from_user.id)

    await callback.message.edit_text(
        "🤖 <b>Смена модели</b>\n\nВыберите модель из списка:",
        parse_mode="HTML",
        reply_markup=get_model_select_keyboard(current_model=current_model),
    )
    await callback.answer()


@router.callback_query(F.data == "settings:clear_my_cache")
async def settings_clear_my_cache(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "🗑 <b>Очистить ваш кэш?</b>\n\n"
        "Будут удалены только записи, созданные вашими запросами.",
        parse_mode="HTML",
        reply_markup=get_confirm_keyboard("clear_my_cache"),
    )
    await callback.answer()


@router.callback_query(F.data == "settings:clear_cache")
async def settings_clear_cache(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "🗑 <b>Очистить весь кэш?</b>\n\n"
        "Кэш общий для всех пользователей. Это действие нельзя отменить.",
        parse_mode="HTML",
        reply_markup=get_confirm_keyboard("clear_cache"),
    )
    await callback.answer()


@router.callback_query(F.data == "confirm:clear_my_cache")
async def confirm_clear_my_cache(callback: CallbackQuery) -> None:
    async with get_session() as session:
        deleted = await clear_user_cache(session, callback.from_user.id)

    logger.info("Личный кэш очищен: user=%s deleted=%s", callback.from_user.id, deleted)
    await callback.message.edit_text(
        f"✅ Ваш кэш очищен. Удалено записей: <b>{deleted}</b>.",
        parse_mode="HTML",
        reply_markup=get_settings_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "confirm:clear_cache")
async def confirm_clear_cache(callback: CallbackQuery) -> None:
    async with get_session() as session:
        res = await session.execute(delete(AICache))
        deleted = int(res.rowcount or 0)

    logger.info("Глобальный кэш очищен: удалено записей=%s", deleted)
    await callback.message.edit_text(
        f"✅ Весь кэш очищен. Удалено записей: <b>{deleted}</b>.",
        parse_mode="HTML",
        reply_markup=get_settings_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "confirm:cancel")
async def confirm_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "⚙️ <b>Настройки</b>\n\nВыберите что хотите изменить:",
        parse_mode="HTML",
        reply_markup=get_settings_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "model:back")
async def model_back(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "⚙️ <b>Настройки</b>\n\nВыберите что хотите изменить:",
        parse_mode="HTML",
        reply_markup=get_settings_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("model:"))
async def model_select(callback: CallbackQuery) -> None:
    model_id = callback.data.split(":", 1)[1]
    if model_id not in AVAILABLE_MODEL_IDS:
        await callback.answer("❓ Неизвестная модель", show_alert=True)
        return

    async with get_session() as session:
        await session.execute(
            update(User)
            .where(User.id == callback.from_user.id)
            .values(preferred_model=model_id)
        )

    model_label = get_model_label(model_id)

    await callback.message.edit_text(
        f"✅ Модель обновлена: <b>{model_label}</b>\n"
        f"<i>ID: {model_id}</i>",
        parse_mode="HTML",
        reply_markup=get_settings_keyboard(),
    )
    await callback.answer()
