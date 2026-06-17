"""
handlers/states.py — Конечный автомат состояний (FSM) для aiogram.

Состояния управляют многошаговыми диалогами:
  - Ожидание ссылки/текста
  - Ожидание выбора действия
  - Обработка контента (лочим повторные запросы)
"""

from aiogram.fsm.state import State, StatesGroup


class ProcessingStates(StatesGroup):
    """Состояния основного пайплайна обработки контента."""

    # Ожидаем ссылку на YouTube/Telegram или текст
    waiting_for_input = State()

    # Контент получен, ждём выбор формата обработки
    waiting_for_action = State()

    # ИИ обрабатывает запрос (блокируем повторные)
    processing = State()

    # Результат готов, ждём следующего действия
    showing_result = State()


class SettingsStates(StatesGroup):
    """Состояния меню настроек."""

    # Главное меню настроек
    main_settings = State()

    # Ввод кастомного API ключа
    waiting_for_api_key = State()


class TextInputStates(StatesGroup):
    """Состояния ввода произвольного текста."""

    waiting_for_text = State()
    waiting_for_action = State()
