"""
handlers/projects.py — история сохранённых проектов пользователя.
"""

from __future__ import annotations

import json
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from db.database import get_session, get_user_project, list_user_projects
from handlers.keyboards import get_main_menu, get_result_keyboard, reels_render_available
from handlers.states import ProcessingStates
from utils.telegram import escape_html

logger = logging.getLogger("ai_kombain.handlers.projects")
router = Router()


def _projects_keyboard(projects: list) -> object:
    builder = InlineKeyboardBuilder()
    for project in projects:
        label = (project.title or "Проект")[:40]
        builder.row(
            InlineKeyboardButton(
                text=f"📁 {label}",
                callback_data=f"project:open:{project.id}",
            )
        )
    builder.row(InlineKeyboardButton(text="◀️ В меню", callback_data="project:back"))
    return builder.as_markup()


@router.message(F.text == "📁 Мои проекты")
async def menu_projects(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    async with get_session() as session:
        projects = await list_user_projects(session, user_id)

    if not projects:
        await message.answer(
            "📁 <b>Мои проекты</b>\n\n"
            "Пока пусто. Сгенерируйте сценарий Reels — проект сохранится автоматически.",
            parse_mode="HTML",
            reply_markup=get_main_menu(),
        )
        return

    await message.answer(
        "📁 <b>Мои проекты</b>\n\nВыберите проект:",
        parse_mode="HTML",
        reply_markup=_projects_keyboard(projects),
    )


@router.callback_query(F.data.startswith("project:open:"))
async def open_project(callback: CallbackQuery, state: FSMContext) -> None:
    project_id = int(callback.data.rsplit(":", 1)[-1])
    user_id = callback.from_user.id

    async with get_session() as session:
        project = await get_user_project(session, user_id, project_id)

    if not project:
        await callback.answer("Проект не найден", show_alert=True)
        return

    await callback.answer()

    data_patch: dict = {
        "content": project.script_text or "",
        "title": project.title,
        "source_url": project.source_url or "",
        "source_type": (project.source_type.value if project.source_type else "text"),
    }
    if project.script_text:
        data_patch["last_result"] = project.script_text
        data_patch["last_prompt_type"] = "reels_script"
    if project.timeline_json:
        try:
            data_patch["last_timeline_json"] = json.loads(project.timeline_json)
        except json.JSONDecodeError:
            pass

    await state.update_data(**data_patch)
    await state.set_state(ProcessingStates.showing_result)

    lines = [
        f"📁 <b>{escape_html(project.title)}</b>",
        "",
    ]
    if project.source_url:
        lines.append(f"🔗 {escape_html(project.source_url)}")
    if project.script_text:
        preview = project.script_text[:500]
        if len(project.script_text) > 500:
            preview += "…"
        lines.append(f"\n<b>Сценарий:</b>\n{escape_html(preview)}")

    has_timeline = bool(project.timeline_json)
    await callback.message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=get_result_keyboard(
            show_timeline=bool(project.script_text),
            show_render=bool(project.script_text) and reels_render_available(),
            show_content_pack=False,
        ),
    )


@router.callback_query(F.data == "project:back")
async def projects_back(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "🏠 Главное меню",
        reply_markup=None,
    )
    await callback.message.answer("Выберите действие:", reply_markup=get_main_menu())
    await callback.answer()
