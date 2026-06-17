# 🤖 ИИ-Комбайн — Telegram бот вирального контента

## Быстрый старт

1. Скопируй `.env.example` в `.env` и заполни токены
2. `pip install -r requirements.txt`
3. `python main.py`

## Тесты

```bash
pip install -r requirements-dev.txt
pytest
```

## Деплой на Railway

1. Подключи репозиторий к Railway
2. Добавь переменные из `.env.example` в Railway Variables
3. Railway автоматически соберёт Docker-образ
4. Healthcheck: `GET /health` на порту `PORT`

## Получение API ключей

- **Telegram**: [@BotFather](https://t.me/BotFather) → /newbot
- **OpenRouter**: [openrouter.ai/keys](https://openrouter.ai/keys) — есть бесплатные модели

## Структура проекта

```
ai_kombain/
├── main.py                 # Точка входа
├── config.py               # Конфигурация и .env
├── application/            # Use-cases (бизнес-логика)
│   ├── dto.py
│   └── use_cases.py
├── infrastructure/         # Сборка приложения, lifecycle, health
│   ├── app.py
│   ├── lifecycle.py
│   ├── health.py
│   └── storage.py
├── middleware/             # User tracking, error handler
├── domain/                 # Каталоги моделей и константы
├── utils/                  # Telegram-утилиты
├── db/
│   ├── models.py
│   ├── database.py
│   └── fsm_storage.py
├── services/
│   ├── parser.py
│   └── llm_client.py
├── handlers/
│   ├── common.py
│   ├── processing.py
│   ├── settings.py
│   ├── cancel_flow.py
│   └── keyboards.py
├── prompts/
│   └── templates.py
├── tests/
├── Dockerfile
├── requirements.txt
└── .env.example
```

## Переменные окружения

| Переменная | Описание |
|------------|----------|
| `TELEGRAM_TOKEN` | Токен бота |
| `OPENROUTER_API_KEY` | API ключ OpenRouter |
| `FSM_STORAGE` | `sqlite` (по умолчанию) или `memory` |
| `DAILY_REQUEST_LIMIT` | Лимит LLM-запросов в сутки (0 = без лимита) |
| `HEALTH_ENABLED` | HTTP healthcheck для Docker/Railway |
