from unittest.mock import AsyncMock, MagicMock

import pytest

from handlers.cancel_flow import cancel_workflow
from handlers.states import ProcessingStates
from handlers.task_registry import register_user_task, unregister_user_task


@pytest.mark.asyncio
async def test_cancel_workflow_clears_idle_state():
    state = AsyncMock()
    state.get_state = AsyncMock(return_value=None)

    result = await cancel_workflow(42, state)

    assert result == "cleared"
    state.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_workflow_cancels_registered_task():
    state = AsyncMock()
    state.get_state = AsyncMock(return_value=ProcessingStates.processing.state)

    task = MagicMock()
    register_user_task(7, task)

    try:
        result = await cancel_workflow(7, state)
    finally:
        unregister_user_task(7)

    assert result == "task_cancelled"
    task.cancel.assert_called_once()
    state.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_workflow_busy_without_task():
    state = AsyncMock()
    state.get_state = AsyncMock(return_value=ProcessingStates.processing.state)

    result = await cancel_workflow(99, state)

    assert result == "processing_busy"
    state.clear.assert_awaited_once()
