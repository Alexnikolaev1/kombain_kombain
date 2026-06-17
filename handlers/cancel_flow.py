"""
handlers/cancel_flow.py — единая логика отмены для reply и inline кнопок.
"""

from __future__ import annotations

from typing import Literal

from aiogram.fsm.context import FSMContext

from handlers.states import ProcessingStates
from handlers.task_registry import cancel_user_task

CancelResult = Literal["cleared", "task_cancelled", "processing_busy"]


async def cancel_workflow(user_id: int, state: FSMContext) -> CancelResult:
    """
    Отменяет текущий сценарий пользователя.

    - processing + активная задача → отменяет task (UI обновит обработчик задачи)
    - processing без task → busy (гонка: задача ещё не зарегистрирована)
    - любое другое состояние → просто очищает FSM
    """
    current_state = await state.get_state()

    if current_state == ProcessingStates.processing:
        cancelled = cancel_user_task(user_id)
        await state.clear()
        return "task_cancelled" if cancelled else "processing_busy"

    await state.clear()
    return "cleared"
