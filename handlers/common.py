"""
handlers/common.py — Базовые хендлеры: /start, /help, главное меню.
"""

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from db.database import get_or_create_user, get_session
from handlers.keyboards import get_main_menu, get_settings_keyboard

logger = logging.getLogger("ai_kombain.handlers.common")
router = Router()

# ──────────────────────────────────────────────
# Тексты сообщений
# ──────────────────────────────────────────────

WELCOME_TEXT = """
🤖 <b>ИИ-Комбайн — машина вирального контента</b>

Я превращаю любой YouTube-ролик или текст в готовый контент за секунды.

<b>Что умею:</b>
🎬 <b>Сценарий Reels</b> — готовый скрипт со структурой Хук → Сюжет → CTA
📋 <b>Таймлайн монтажа</b> — таймкоды, B-roll и экспорт для CapCut
🎥 <b>Собрать Reels</b> — автоматический MP4 (Gemini TTS + B-roll + FFmpeg)
🔥 <b>Виральные заголовки</b> — 5 кликабельных заголовков с объяснением
💡 <b>Глубокий анализ</b> — тезисы, инсайты, применимые выводы
📋 <b>TL;DR</b> — суть лонгрида за 60 секунд

<b>Как начать:</b>
Просто вставьте YouTube-ссылку или введите текст 👇

<i>💾 Умный кэш экономит ваши токены — повторные запросы мгновенны!</i>
"""

HELP_TEXT = """
<b>📖 Как пользоваться ИИ-Комбайном</b>

<b>1. Отправьте контент:</b>
• YouTube-ссылку (любого формата: youtu.be, youtube.com/watch, /shorts)
• Telegram-ссылку (t.me/channel/123) или перешлите пост
• Любой текст (статья, лекция, идея)

<b>2. Выберите формат обработки:</b>
• 🎬 <b>Reels-сценарий</b> — для съёмки
• 🔥 <b>Заголовки</b> — для публикации
• 💡 <b>Анализ</b> — для глубокого понимания
• 📋 <b>TL;DR</b> — для быстрого усвоения

<b>3. Получите результат!</b>
• После сценария Reels — <b>Таймлайн</b> или <b>Собрать Reels</b> (авто-MP4)
• Для видео нужен <code>GEMINI_API_KEY</code> и FFmpeg на сервере
• B-roll: бесплатный ключ <code>PEXELS_API_KEY</code> (опционально)

<b>💡 Советы:</b>
• Кэш сохраняет ответы — повторные запросы бесплатны
• Длинные видео (1-2ч) обрабатываются дольше — наберитесь терпения
• В настройках можно выбрать модель ИИ под ваши нужды

<b>Команды:</b>
/start — главное меню
/help — эта справка
/stats — ваша статистика
/settings — настройки
"""


# ──────────────────────────────────────────────
# Хендлеры
# ──────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Приветствие и регистрация пользователя."""
    await state.clear()

    # Регистрируем/обновляем пользователя в БД
    async with get_session() as session:
        await get_or_create_user(
            session=session,
            user_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )

    await message.answer(
        WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=get_main_menu(),
    )
    logger.info(f"Пользователь {message.from_user.id} запустил бота")


@router.message(Command("help"))
@router.message(F.text == "❓ Помощь")
async def cmd_help(message: Message) -> None:
    """Справка по использованию бота."""
    await message.answer(HELP_TEXT, parse_mode="HTML")


@router.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Отмена текущего действия и возврат в главное меню."""
    from handlers.cancel_flow import cancel_workflow

    result = await cancel_workflow(message.from_user.id, state)

    if result == "task_cancelled":
        # Сообщение об отмене покажет обработчик прерванной задачи.
        return

    if result == "processing_busy":
        await message.answer(
            "⚠️ Обработка уже идёт, подождите немного...",
            reply_markup=get_main_menu(),
        )
        return

    await message.answer(
        "↩️ Отменено. Выберите действие:",
        reply_markup=get_main_menu(),
    )


@router.message(Command("settings"))
@router.message(F.text == "⚙️ Настройки")
async def cmd_settings(message: Message, state: FSMContext) -> None:
    """Меню настроек."""
    await state.clear()
    await message.answer(
        "⚙️ <b>Настройки</b>\n\nВыберите что хотите изменить:",
        parse_mode="HTML",
        reply_markup=get_settings_keyboard(),
    )


@router.message(Command("stats"))
@router.message(F.text == "📊 Мой кэш / Статистика")
async def cmd_stats(message: Message) -> None:
    """Статистика использования для пользователя."""
    from sqlalchemy import select
    from db.models import User
    from config import get_settings

    app_settings = get_settings()

    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.id == message.from_user.id)
        )
        user = result.scalar_one_or_none()
        current_model = await get_user_model(session, message.from_user.id) if user else app_settings.DEFAULT_MODEL

    if not user:
        await message.answer("📊 Статистика пуста — вы ещё не делали запросов!")
        return

    cost_saved = (user.tokens_saved / 1000) * 0.001
    daily_limit_line = ""
    if app_settings.DAILY_REQUEST_LIMIT > 0:
        remaining = max(app_settings.DAILY_REQUEST_LIMIT - user.requests_today, 0)
        daily_limit_line = (
            f"• Сегодня: <b>{user.requests_today}</b> / {app_settings.DAILY_REQUEST_LIMIT} "
            f"(осталось {remaining})\n"
        )

    stats_text = f"""
📊 <b>Ваша статистика</b>

👤 <b>Пользователь:</b> @{user.username or 'анонимный'}
🤖 <b>Модель:</b> {current_model.split('/')[-1]}

📈 <b>Использование:</b>
• Всего запросов: <b>{user.requests_total}</b>
{daily_limit_line}• Кэш-хитов: <b>{user.cache_hits}</b>
• Процент кэша: <b>{int(user.cache_hits / max(user.requests_total, 1) * 100)}%</b>

💾 <b>Экономия:</b>
• Токенов сэкономлено: <b>{user.tokens_saved:,}</b>
• В деньгах ~: <b>${cost_saved:.4f}</b>

📅 Первый запуск: {user.created_at.strftime('%d.%m.%Y')}
🕒 Последний визит: {user.last_seen_at.strftime('%d.%m.%Y %H:%M') if user.last_seen_at else '—'}
"""

    await message.answer(stats_text, parse_mode="HTML")


# ──────────────────────────────────────────────
# Коллбэки настроек
# ──────────────────────────────────────────────

@router.callback_query(F.data == "settings:back")
async def settings_back(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.delete()
    await callback.message.answer("🏠 Главное меню:", reply_markup=get_main_menu())
    await callback.answer()


@router.callback_query(F.data == "settings:stats")
async def settings_stats(callback: CallbackQuery) -> None:
    await callback.answer("Открываю статистику...")
    await cmd_stats(callback.message)


@router.callback_query(F.data == "result:main_menu")
async def result_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("🏠 Главное меню:", reply_markup=get_main_menu())
    await callback.answer()
