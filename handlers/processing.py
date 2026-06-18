"""
handlers/processing.py — Транспортный слой пайплайна обработки контента.
"""

import asyncio
import logging
import re
import shutil

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from application.dto import ContentSession
from application.use_cases import (
    SOURCE_TYPE_MAP,
    ContentIntakeUseCase,
    GenerationUseCase,
    ReelsRenderUseCase,
)
from handlers.keyboards import (
    get_action_keyboard,
    get_cancel_keyboard,
    get_main_menu,
    get_result_keyboard,
    get_reprocess_keyboard,
    reels_render_available,
)
from handlers.cancel_flow import cancel_workflow
from handlers.states import ProcessingStates
from handlers.task_registry import register_user_task, unregister_user_task
from prompts.templates import get_display_name, parse_action_slug
from services.llm_client import DailyLimitExceededError, LLMError
from services.reels_renderer import ReelsRenderError
from services.reels_timeline import (
    TimelineParseError,
    ReelsTimeline,
    format_capcut_guide,
    format_timeline_html,
    parse_timeline,
    timeline_from_dict,
    timeline_to_json_bytes,
)
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
_reels_render = ReelsRenderUseCase()


def _reels_actions_keyboard(*, has_timeline: bool = False) -> object:
    return get_result_keyboard(
        show_timeline=True,
        show_render=has_timeline or reels_render_available(),
    )


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

    await send_long_html(
        status_msg,
        full_response,
        reply_markup=get_result_keyboard(
            show_timeline=(action == "reels_script"),
            show_render=(action == "reels_script"),
        ),
    )


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


def _safe_export_basename(title: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", title, flags=re.UNICODE)
    slug = re.sub(r"[\s_]+", "_", slug.strip()).lower()[:40]
    return slug or "reels_timeline"


async def _load_or_build_timeline(
    *,
    user_id: int,
    data: dict,
    content_session: ContentSession,
    context: str,
) -> tuple[ReelsTimeline, GenerationOutcome | None]:
    """Берёт таймлайн из FSM или генерирует из сценария Reels."""
    cached = data.get("last_timeline_json")
    if cached:
        return timeline_from_dict(cached), None

    script_text = data.get("last_result", "")
    if not script_text:
        raise TimelineParseError("Сценарий Reels не найден")

    source_type_enum = SOURCE_TYPE_MAP.get(content_session.source_type)
    outcome = await _generation.run_timeline(
        user_id=user_id,
        script_text=script_text,
        context=context,
        source_url=content_session.source_url,
        source_type=source_type_enum,
    )
    timeline = parse_timeline(outcome.response)
    return timeline, outcome


@router.callback_query(F.data == "result:reels_timeline")
async def result_reels_timeline(callback: CallbackQuery, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state == ProcessingStates.processing:
        await callback.answer("⏳ Уже обрабатывается, подождите...")
        return

    data = await state.get_data()
    script_text = data.get("last_result", "")
    last_prompt_type = data.get("last_prompt_type", "")

    if last_prompt_type != "reels_script" or not script_text:
        await callback.answer(
            "Сначала сгенерируйте сценарий Reels 🎬",
            show_alert=True,
        )
        return

    await state.set_state(ProcessingStates.processing)
    await callback.answer("📋 Собираю таймлайн...")

    user_id = callback.from_user.id
    register_user_task(user_id, asyncio.current_task())

    content_session = ContentSession.from_fsm_data(data)
    context = content_session.title or content_session.context or "Reels сценарий"

    status_msg = await callback.message.answer(
        "⏳ <b>Строю таймлайн для монтажа...</b>\n"
        "<i>Разбиваю сценарий на сцены с таймкодами и B-roll</i>",
        parse_mode="HTML",
    )

    source_type_enum = SOURCE_TYPE_MAP.get(content_session.source_type)

    try:
        async with LoadingAnimator(status_msg):
            outcome = await _generation.run_timeline(
                user_id=user_id,
                script_text=script_text,
                context=context,
                source_url=content_session.source_url,
                source_type=source_type_enum,
            )

        timeline = parse_timeline(outcome.response)
    except asyncio.CancelledError:
        await status_msg.edit_text(
            "↩️ <b>Отменено.</b>",
            parse_mode="HTML",
            reply_markup=_reels_actions_keyboard(has_timeline=bool(data.get("last_timeline_json"))),
        )
        await state.set_state(ProcessingStates.showing_result)
        return
    except DailyLimitExceededError as e:
        await status_msg.edit_text(
            f"🚫 <b>Лимит исчерпан</b>\n\n{escape_html(str(e))}",
            parse_mode="HTML",
        )
        await state.set_state(ProcessingStates.showing_result)
        return
    except LLMError as e:
        await status_msg.edit_text(
            f"❌ <b>Ошибка ИИ:</b> {escape_html(str(e))}",
            parse_mode="HTML",
        )
        await state.set_state(ProcessingStates.showing_result)
        return
    except TimelineParseError as e:
        logger.warning("Не удалось разобрать таймлайн: %s", e)
        await status_msg.edit_text(
            "⚠️ <b>ИИ вернул некорректный формат.</b>\n\n"
            f"{escape_html(str(e))}\n\n"
            "Попробуйте ещё раз — обычно со второй попытки срабатывает.",
            parse_mode="HTML",
            reply_markup=_reels_actions_keyboard(has_timeline=bool(data.get("last_timeline_json"))),
        )
        await state.set_state(ProcessingStates.showing_result)
        return
    except Exception as e:
        logger.error("Ошибка генерации таймлайна: %s", e, exc_info=True)
        await status_msg.edit_text("❌ Внутренняя ошибка. Попробуйте позже.")
        await state.set_state(ProcessingStates.showing_result)
        return
    finally:
        unregister_user_task(user_id)

    await state.update_data(last_timeline_json=timeline.to_dict())
    await state.set_state(ProcessingStates.showing_result)

    cache_note = "💾 из кэша" if outcome.was_cached else f"🤖 {outcome.model.split('/')[-1]}"
    header = (
        f"📋 <b>Таймлайн готов</b> · {cache_note} · "
        f"{outcome.processing_ms / 1000:.1f}с\n"
        f"{'─' * 30}\n\n"
    )
    timeline_html = format_timeline_html(timeline)

    await send_long_html(
        status_msg,
        header + timeline_html,
        reply_markup=_reels_actions_keyboard(has_timeline=True),
    )

    basename = _safe_export_basename(timeline.title)
    source_label = content_session.title or content_session.source_url or context
    guide_text = format_capcut_guide(timeline, source=source_label)

    await callback.message.answer_document(
        BufferedInputFile(timeline_to_json_bytes(timeline), filename=f"{basename}.json"),
        caption="📎 JSON-таймлайн — для автоматизации",
    )
    await callback.message.answer_document(
        BufferedInputFile(guide_text.encode("utf-8"), filename=f"{basename}_capcut.txt"),
        caption="📎 Гайд для монтажа в CapCut",
    )


@router.callback_query(F.data == "result:reels_render")
async def result_reels_render(callback: CallbackQuery, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state == ProcessingStates.processing:
        await callback.answer("⏳ Уже обрабатывается, подождите...")
        return

    if not reels_render_available():
        await callback.answer(
            "Нужен GEMINI_API_KEY и FFmpeg на сервере",
            show_alert=True,
        )
        return

    data = await state.get_data()
    last_prompt_type = data.get("last_prompt_type", "")
    if last_prompt_type != "reels_script" or not data.get("last_result"):
        await callback.answer("Сначала сгенерируйте сценарий Reels 🎬", show_alert=True)
        return

    await state.set_state(ProcessingStates.processing)
    await callback.answer("🎬 Собираю видео...")

    user_id = callback.from_user.id
    register_user_task(user_id, asyncio.current_task())

    content_session = ContentSession.from_fsm_data(data)
    context = content_session.title or content_session.context or "Reels сценарий"
    source_type_enum = SOURCE_TYPE_MAP.get(content_session.source_type)

    status_msg = await callback.message.answer(
        "⏳ <b>Собираю Reels...</b>\n"
        "<i>Gemini TTS → B-roll → FFmpeg</i>\n\n"
        "Это может занять 2–5 минут.",
        parse_mode="HTML",
    )

    work_dir: str | None = None

    async def on_progress(message: str) -> None:
        try:
            await status_msg.edit_text(
                f"⏳ <b>Собираю Reels...</b>\n{escape_html(message)}",
                parse_mode="HTML",
            )
        except Exception:
            pass

    try:
        async with LoadingAnimator(status_msg):
            if not data.get("last_timeline_json"):
                await on_progress("📋 Строю таймлайн сцен...")
                timeline, _ = await _load_or_build_timeline(
                    user_id=user_id,
                    data=data,
                    content_session=content_session,
                    context=context,
                )
                await state.update_data(last_timeline_json=timeline.to_dict())
            else:
                timeline = timeline_from_dict(data["last_timeline_json"])

            video_path, elapsed_ms = await _reels_render.run(
                user_id=user_id,
                timeline=timeline,
                source_url=content_session.source_url,
                source_type=source_type_enum,
                on_progress=on_progress,
            )
            work_dir = str(video_path.parent)

        video_bytes = video_path.read_bytes()
        caption = (
            f"🎬 <b>{escape_html(timeline.title)}</b>\n"
            f"⏱ Сборка: {elapsed_ms / 1000:.0f}с · {len(timeline.scenes)} сцен\n"
            f"<i>Черновик Reels — доработайте в CapCut при необходимости</i>"
        )

        await status_msg.edit_text(
            f"✅ <b>Reels собран!</b> · {elapsed_ms / 1000:.0f}с\n"
            f"{'─' * 30}",
            parse_mode="HTML",
            reply_markup=_reels_actions_keyboard(has_timeline=True),
        )

        await callback.message.answer_video(
            BufferedInputFile(video_bytes, filename=video_path.name),
            caption=caption,
            parse_mode="HTML",
            supports_streaming=True,
        )
    except asyncio.CancelledError:
        await status_msg.edit_text(
            "↩️ <b>Отменено.</b>",
            parse_mode="HTML",
            reply_markup=_reels_actions_keyboard(has_timeline=bool(data.get("last_timeline_json"))),
        )
    except DailyLimitExceededError as e:
        await status_msg.edit_text(
            f"🚫 <b>Лимит исчерпан</b>\n\n{escape_html(str(e))}",
            parse_mode="HTML",
        )
    except TimelineParseError as e:
        await status_msg.edit_text(
            f"⚠️ <b>Не удалось подготовить таймлайн:</b> {escape_html(str(e))}",
            parse_mode="HTML",
            reply_markup=_reels_actions_keyboard(has_timeline=False),
        )
    except ReelsRenderError as e:
        await status_msg.edit_text(
            f"❌ <b>Ошибка сборки:</b> {escape_html(str(e))}",
            parse_mode="HTML",
            reply_markup=_reels_actions_keyboard(has_timeline=bool(data.get("last_timeline_json"))),
        )
    except LLMError as e:
        await status_msg.edit_text(
            f"❌ <b>Ошибка ИИ:</b> {escape_html(str(e))}",
            parse_mode="HTML",
            reply_markup=_reels_actions_keyboard(has_timeline=bool(data.get("last_timeline_json"))),
        )
    except Exception as e:
        logger.error("Ошибка сборки Reels: %s", e, exc_info=True)
        await status_msg.edit_text(
            "❌ Внутренняя ошибка при сборке видео.",
            reply_markup=_reels_actions_keyboard(has_timeline=bool(data.get("last_timeline_json"))),
        )
    finally:
        unregister_user_task(user_id)
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
        await state.set_state(ProcessingStates.showing_result)
