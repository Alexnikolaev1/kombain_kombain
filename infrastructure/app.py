"""infrastructure/app.py — сборка Dispatcher и роутеров."""

from aiogram import Dispatcher

from handlers import common, processing
from handlers import settings as settings_handlers
from infrastructure.storage import create_fsm_storage
from middleware import UserMiddleware, register_error_handler


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=create_fsm_storage())
    dp.update.middleware(UserMiddleware())
    register_error_handler(dp)

    dp.include_router(common.router)
    dp.include_router(processing.router)
    dp.include_router(settings_handlers.router)

    return dp
