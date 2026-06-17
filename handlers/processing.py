"""
handlers/processing.py — Транспортный слой пайплайна обработки контента.
"""

import asyncio
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from application.dto import ContentSession
from application.use_cases import ContentIntakeUseCase, GenerationUseCase
from handlers.keyboards import (
    get_action_keyboard,
    get_cancel_keyboard,
    get_main_menu,
    get_result_keyboard,
    get_reprocess_keyboard,
)
from handlers.cancel_flow import cancel_workflow
from handlers.states import ProcessingStates
from handlers.task_registry import register_user_task, unregister_user_task
from prompts.templates import get_display_name, parse_action_slug
from services.llm_client import DailyLimitExceededError, LLMError
from utils.telegram import (
    LoadingAnimator,
    escape_html,
    format_content_preview,
    format_generation_message,
    send_long_html,
)

logger = logging.getLogger("ai_kombain.handlers.processing")
router = Router()

_content_intake = ContentIntakeUseCase()
_generation = GenerationUseCase()


@router.message(F.text == "📹 Обработать видео")
async def menu_process_video(message: Message, state: FSMContext) -> None:
    await state.set_state(ProcessingStates.waiting_for_input)
    await state.update_data(mode="video")
    await message.answer(
        "📹 <b>Отправьте ссылку на YouTube-видео</b>\n\n"
        "Поддерживаются: youtu.be, youtube.com/watch, youtube.com/shorts\n\n"
        "<i>Также можно отправить ссылку t.me/... или переслать пост</i>",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(F.text == "📝 Анализ текста")
async def menu_process_text(message: Message, state: FSMContext) -> None:
    await state.set_state(ProcessingStates.waiting_for_input)
    await state.update_data(mode="text")
    await message.answer(
        "📝 <b>Вставьте текст для анализа</b>\n\n"
        "Подойдёт: статья, лекция, пост, идея, транскрипт\n"
        "<i>Максимум ~20 000 символов</i>",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(ProcessingStates.waiting_for_input)
async def handle_content_input(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state == ProcessingStates.processing:
        await message.answer("⏳ Уже обрабатывается предыдущий запрос, подождите...")
        return

    await state.set_state(ProcessingStates.processing)
    user_id = message.from_user.id
    register_user_task(user_id, asyncio.current_task())

    text = message.text or ""
    forwarded_text = None
    if message.forward_from or message.forward_from_chat:
        forwarded_text = message.text or message.caption or ""

    status_msg = await message.answer("⏳ <b>Получаю контент...</b>", parse_mode="HTML")

    try:
        async with LoadingAnimator(status_msg):
            parsed = await _content_intake.parse(
                text=text,
                forwarded_text=forwarded_text,
            )
    except asyncio.CancelledError:
        await status_msg.edit_text(
            "↩️ <b>Отменено.</b>\n\nВыберите действие:",
            parse_mode="HTML",
            reply_markup=get_main_menu(),
        )
        await state.clear()
        return
    except Exception as e:
        logger.error("Неожиданная ошибка парсинга: %s", e, exc_info=True)
        await status_msg.edit_text(
            "❌ Внутренняя ошибка при получении контента. Попробуйте позже.",
            parse_mode="HTML",
        )
        await state.clear()
        await message.answer("Выберите действие:", reply_markup=get_main_menu())
        return
    finally:
        unregister_user_task(user_id)

    if not parsed.is_success:
        await status_msg.edit_text(
            f"❌ <b>Ошибка:</b> {escape_html(parsed.error or 'Не удалось извлечь контент')}\n\n"
            "Попробуйте другую ссылку или введите текст вручную.",
            parse_mode="HTML",
        )
        await state.clear()
        await message.answer("Выберите действие:", reply_markup=get_main_menu())
        return

    session = _content_intake.to_session(parsed)
    await state.update_data(**session.to_fsm_data())
    await state.set_state(ProcessingStates.waiting_for_action)

    await status_msg.edit_text(
        format_content_preview(
            title=session.title,
            content=session.content,
            char_count=len(session.content),
        ),
        parse_mode="HTML",
        reply_markup=get_action_keyboard(session.source_type),
    )


async def _run_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    action: str,
    prompt_type,
) -> None:
    current_state = await state.get_state()
    if current_state == ProcessingStates.processing:
        await callback.answer("⏳ Уже обрабатывается, подождите...")
        return

    await state.set_state(ProcessingStates.processing)
    await callback.answer(f"🚀 Запускаю {get_display_name(prompt_type)}...")

    user_id = callback.from_user.id
    register_user_task(user_id, asyncio.current_task())

    content_session = ContentSession.from_fsm_data(await state.get_data())
    if not content_session.content:
        await callback.message.edit_text("❌ Контент не найден. Отправьте ссылку заново.")
        unregister_user_task(user_id)
        await state.clear()
        return

    status_msg = await callback.message.edit_text(
        f"⏳ <b>Обрабатываю через ИИ...</b>\nФормат: {get_display_name(prompt_type)}",
        parse_mode="HTML",
    )

    try:
        async with LoadingAnimator(status_msg):
            outcome = await _generation.run(
                user_id=callback.from_user.id,
                session=content_session,
                prompt_type=prompt_type,
                action=action,
            )
    except asyncio.CancelledError:
        await status_msg.edit_text(
            "↩️ <b>Отменено.</b>\n\nВыберите действие:",
            parse_mode="HTML",
            reply_markup=get_main_menu(),
        )
        await state.clear()
        return
    except DailyLimitExceededError as e:
        await status_msg.edit_text(
            f"🚫 <b>Лимит исчерпан</b>\n\n{escape_html(str(e))}",
            parse_mode="HTML",
        )
        await state.set_state(ProcessingStates.waiting_for_action)
        return
    except LLMError as e:
        await status_msg.edit_text(
            f"❌ <b>Ошибка ИИ:</b> {escape_html(str(e))}\n\n"
            "Попробуйте ещё раз или выберите другую модель в настройках.",
            parse_mode="HTML",
        )
        await state.set_state(ProcessingStates.waiting_for_action)
        return
    except Exception as e:
        logger.error("Неожиданная ошибка обработки: %s", e, exc_info=True)
        await status_msg.edit_text("❌ Внутренняя ошибка. Попробуйте позже.")
        await state.clear()
        return
    finally:
        unregister_user_task(user_id)

    await state.update_data(last_result=outcome.response, last_prompt_type=action)
    await state.set_state(ProcessingStates.showing_result)

    full_response = format_generation_message(
        get_display_name(prompt_type),
        outcome.response,
        was_cached=outcome.was_cached,
        model=outcome.model,
        processing_ms=outcome.processing_ms,
    )

    await send_long_html(status_msg, full_response, reply_markup=get_result_keyboard())


@router.callback_query(F.data.startswith("action:"))
async def handle_action_choice(callback: CallbackQuery, state: FSMContext) -> None:
    action = callback.data.split(":", 1)[1]

    if action == "cancel":
        result = await cancel_workflow(callback.from_user.id, state)

        if result == "task_cancelled":
            await callback.answer("↩️ Отменено")
            return

        if result == "processing_busy":
            await callback.answer("⏳ Обработка уже идёт, подождите...", show_alert=True)
            return

        await callback.message.edit_text("❌ Отменено")
        await callback.message.answer("Выберите действие:", reply_markup=get_main_menu())
        await callback.answer()
        return

    prompt_type = parse_action_slug(action)
    if not prompt_type:
        await callback.answer("❓ Неизвестное действие")
        return

    await _run_prompt(callback, state, action=action, prompt_type=prompt_type)


@router.callback_query(F.data == "result:reprocess")
async def result_reprocess(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_reply_markup(reply_markup=get_reprocess_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("reprocess:"))
async def handle_reprocess(callback: CallbackQuery, state: FSMContext) -> None:
    action = callback.data.split(":", 1)[1]

    if action == "back":
        await callback.message.edit_reply_markup(reply_markup=get_result_keyboard())
        await callback.answer()
        return

    prompt_type = parse_action_slug(action)
    if not prompt_type:
        await callback.answer("❓ Неизвестное действие", show_alert=True)
        return

    await _run_prompt(callback, state, action=action, prompt_type=prompt_type)


@router.message(F.text.regexp(r"https?://"))
async def handle_direct_url(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state == ProcessingStates.processing:
        await message.answer("⏳ Уже обрабатывается предыдущий запрос, подождите...")
        return

    await state.set_state(ProcessingStates.waiting_for_input)
    await handle_content_input(message, state)


@router.callback_query(F.data == "result:copy_hint")
async def result_copy_hint(callback: CallbackQuery) -> None:
    await callback.answer("💡 Зажмите текст сообщения для копирования", show_alert=True)
