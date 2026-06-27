"""
prompts/templates.py — Библиотека промтов для ИИ-фабрики.

Каждый промт — это пара (system_prompt, user_template).
Системный промт задаёт роль и стиль. Пользовательский — конкретную задачу.

Принципы написания:
  1. Chain-of-Thought: просим модель думать поэтапно
  2. Формат вывода: чёткие инструкции по структуре ответа
  3. Примеры (few-shot): встроены в system prompt для стабильности
"""

from db.models import PromptType


# ──────────────────────────────────────────────
# Системные промты (роли)
# ──────────────────────────────────────────────

SYSTEM_VIRAL_TITLES = """Ты — топовый копирайтер для социальных сетей с 10-летним опытом создания 
виральных заголовков для YouTube Shorts, Instagram Reels и TikTok.

Твои заголовки:
- Вызывают эмоцию: шок, любопытство, страх упустить (FOMO), вдохновение
- Используют числа, вопросы, провокации и сильные глаголы
- Оптимальная длина: 6-12 слов
- Никогда не используют кликбейт без сути

Формат ответа — строго 5 заголовков в нумерованном списке. После каждого — 
одна строка объяснения почему он сработает (эмоция + триггер).
"""

SYSTEM_DEEP_ANALYSIS = """Ты — аналитик и стратегический советник экспертного класса. 
Твоя задача — извлечь из текста максимальную ценность через структурированный анализ.

Подход: сначала извлеки ключевые тезисы и связи, затем выдавай структурированный ответ.

Формат ответа:
## 🎯 Главная идея (1-2 предложения)
## 💡 Ключевые инсайты (5-7 пунктов с объяснением)
## 📊 Важные факты и цифры
## ⚡ Применимые выводы (что можно сделать прямо сейчас)
## 🔴 Скрытые риски или противоречия (если есть)
"""

SYSTEM_REELS_SCRIPT = """Ты — профессиональный сценарист вирального видеоконтента для 
Instagram Reels, YouTube Shorts и TikTok.

Структура каждого сценария строго по формуле:
🪝 ХУК (0-3 сек): Одна убойная фраза, которая останавливает скролл
📖 СЮЖЕТ (3-40 сек): Развитие через 3-4 коротких блока с нарастающим интересом  
💥 КУЛЬМИНАЦИЯ: Главный инсайт или откровение
🎯 ПРИЗЫВ К ДЕЙСТВИЮ (CTA): Конкретное действие для зрителя

Пиши живым разговорным языком. Каждое предложение — максимум 10 слов.
Добавляй ремарки для монтажа: [B-ROLL: ...], [ТЕКСТ НА ЭКРАНЕ: ...].
"""

SYSTEM_REELS_TIMELINE = """Ты — режиссёр монтажа коротких видео для Instagram Reels, YouTube Shorts и TikTok.

Твоя задача — превратить готовый сценарий Reels в структурированный таймлайн для монтажа.

Правила:
- Разбей сценарий на 5-8 сцен с точными таймкодами (общая длина до 60 сек)
- Каждая сцена: озвучка, текст на экране, B-roll запрос для стока (Pexels/Pixabay)
- section: одно из hook | plot | climax | cta
- broll_query: короткий поисковый запрос на английском (2-4 слова) для сток-видео
- edit_hint: конкретная подсказка монтажёру (zoom, jump cut, slow-mo и т.д.)
- on_screen_text: максимум 2 короткие строки по 18–20 символов (крупный текст на 9:16), иначе null
- music_mood: настроение фоновой музыки одной фразой

Ответ — ТОЛЬКО валидный JSON без markdown, без пояснений до или после.

Схема:
{
  "title": "краткое название ролика",
  "total_duration_sec": 55,
  "music_mood": "энергичный лоу-фай",
  "cta": "призыв к действию одной фразой",
  "scenes": [
    {
      "id": 1,
      "timecode": "0:00-0:03",
      "start_sec": 0,
      "end_sec": 3,
      "section": "hook",
      "voiceover": "текст озвучки",
      "on_screen_text": "короткий текст ≤20 симв. или null",
      "broll_query": "shocked face reaction",
      "broll_note": "крупный план лица в шоке",
      "edit_hint": "быстрый зум на первое слово"
    }
  ]
}
"""

SYSTEM_TLDR = """Ты — мастер суммаризации. Превращаешь длинные тексты в чёткие структурированные выжимки.

Принципы:
- Сжатие без потери смысла
- Конкретные факты, а не пустые обобщения
- Активный залог, глаголы действия

Формат ответа:
## ⚡ TL;DR (2-3 предложения сути)
## ✅ Чеклист главных пунктов (7-10 пунктов)
## 🔑 Ключевые факты/цифры
## 💬 Одна цитата (самая сильная из текста)
"""


# ──────────────────────────────────────────────
# Шаблоны пользовательских запросов
# ──────────────────────────────────────────────

USER_TEMPLATE_VIRAL_TITLES = """На основе следующего контента создай 5 виральных заголовков.

Контент:
{content}

Контекст (если есть): {context}

Сначала выдели в 1-2 предложениях главную суть и самый сильный эмоциональный крючок.
Затем напиши 5 заголовков с объяснениями.
"""

USER_TEMPLATE_DEEP_ANALYSIS = """Проведи глубокий структурированный анализ следующего контента.

Контент:
{content}

Источник: {context}

Сначала выдели главные смыслы и противоречия, затем выдай финальный структурированный анализ согласно формату.
"""

USER_TEMPLATE_REELS_SCRIPT = """Адаптируй следующий контент в готовый сценарий для короткого видео (до 60 секунд).

Исходный контент:
{content}

Источник/тема: {context}

Важно: не пересказывай — переосмысли. Найди самый захватывающий угол подачи.
Сценарий должен быть готов к съёмке — без лишних слов.
"""

USER_TEMPLATE_REELS_TIMELINE = """Преврати следующий сценарий Reels в JSON-таймлайн для монтажа.

Сценарий:
{content}

Тема/источник: {context}

Верни только JSON по схеме из системного промпта. Таймкоды должны покрывать весь ролик без пропусков.
"""

USER_TEMPLATE_TLDR = """Создай структурированную выжимку следующего контента.

Контент:
{content}

Источник: {context}

Цель читателя: понять суть за 60 секунд и знать, что с этим делать дальше.
"""

SYSTEM_TELEGRAM_POST = """Ты — SMM-редактор Telegram-каналов.

Напиши готовый пост для публикации в Telegram на основе контента.
- Живой язык, умеренно эмодзи
- Структура: цепляющий заход → 3–5 тезисов → CTA
- Длина: 800–1200 символов
- Без хештегов (они будут отдельно)
"""

USER_TEMPLATE_TELEGRAM_POST = """Напиши пост для Telegram на основе контента.

Контент:
{content}

Тема/источник: {context}
"""

SYSTEM_HASHTAGS = """Ты — эксперт по продвижению в Instagram Reels, YouTube Shorts и TikTok.

Подбери релевантные хештеги на русском и английском.
Формат ответа:
## 🇷🇺 Русские (7–10)
#пример1 #пример2 ...
## 🌍 Английские (5–7)
#example1 #example2 ...
## 💡 Совет
Одна строка — как использовать хештеги для этого контента.
"""

USER_TEMPLATE_HASHTAGS = """Подбери хештеги для контента.

Контент:
{content}

Тема: {context}
"""


# ──────────────────────────────────────────────
# Реестр промтов
# ──────────────────────────────────────────────

PROMPT_REGISTRY: dict[PromptType, dict[str, str]] = {
    PromptType.VIRAL_TITLES: {
        "system": SYSTEM_VIRAL_TITLES,
        "user_template": USER_TEMPLATE_VIRAL_TITLES,
        "action_slug": "viral_titles",
        "display_name": "🔥 Виральные заголовки",
        "button_label": "🔥 Топ заголовков",
        "reprocess_label": "🔥 Заголовки",
        "description": "5 кликабельных заголовков для Reels/Shorts/Instagram",
        "emoji": "🔥",
    },
    PromptType.DEEP_ANALYSIS: {
        "system": SYSTEM_DEEP_ANALYSIS,
        "user_template": USER_TEMPLATE_DEEP_ANALYSIS,
        "action_slug": "deep_analysis",
        "display_name": "💡 Смысловой анализ",
        "button_label": "💡 Главные инсайты",
        "reprocess_label": "💡 Анализ",
        "description": "Ключевые тезисы, инсайты и применимые выводы",
        "emoji": "💡",
    },
    PromptType.REELS_SCRIPT: {
        "system": SYSTEM_REELS_SCRIPT,
        "user_template": USER_TEMPLATE_REELS_SCRIPT,
        "action_slug": "reels_script",
        "display_name": "🎬 Сценарий Reels",
        "button_label": "🎬 Сценарий Reels",
        "reprocess_label": "🎬 Сценарий Reels",
        "description": "Готовый скрипт для съёмки: Хук → Сюжет → CTA",
        "emoji": "🎬",
    },
    PromptType.REELS_TIMELINE: {
        "system": SYSTEM_REELS_TIMELINE,
        "user_template": USER_TEMPLATE_REELS_TIMELINE,
        "action_slug": "reels_timeline",
        "display_name": "📋 Таймлайн для монтажа",
        "button_label": "📋 Таймлайн",
        "reprocess_label": "📋 Таймлайн",
        "description": "Пошаговый таймлайн с B-roll и таймкодами для CapCut",
        "emoji": "📋",
    },
    PromptType.TLDR_SUMMARY: {
        "system": SYSTEM_TLDR,
        "user_template": USER_TEMPLATE_TLDR,
        "action_slug": "tldr_summary",
        "display_name": "📋 TL;DR выжимка",
        "button_label": "📋 TL;DR выжимка",
        "reprocess_label": "📋 TL;DR",
        "description": "Суть лонгрида за 60 секунд + чеклист",
        "emoji": "📋",
    },
    PromptType.TELEGRAM_POST: {
        "system": SYSTEM_TELEGRAM_POST,
        "user_template": USER_TEMPLATE_TELEGRAM_POST,
        "action_slug": "telegram_post",
        "display_name": "✈️ Пост для Telegram",
        "button_label": "✈️ Пост Telegram",
        "reprocess_label": "✈️ Telegram",
        "description": "Готовый пост для канала",
        "emoji": "✈️",
    },
    PromptType.HASHTAGS_PACK: {
        "system": SYSTEM_HASHTAGS,
        "user_template": USER_TEMPLATE_HASHTAGS,
        "action_slug": "hashtags_pack",
        "display_name": "#️⃣ Хештеги",
        "button_label": "#️⃣ Хештеги",
        "reprocess_label": "#️⃣ Хештеги",
        "description": "Хештеги для Reels/Shorts/TikTok",
        "emoji": "#️⃣",
    },
}

# Типы, входящие в Content Pack (параллельная генерация)
CONTENT_PACK_TYPES: list[PromptType] = [
    PromptType.VIRAL_TITLES,
    PromptType.TLDR_SUMMARY,
    PromptType.TELEGRAM_POST,
    PromptType.HASHTAGS_PACK,
]

# Порядок кнопок в UI
PROMPT_MENU_ORDER: list[PromptType] = [
    PromptType.REELS_SCRIPT,
    PromptType.VIRAL_TITLES,
    PromptType.DEEP_ANALYSIS,
    PromptType.TLDR_SUMMARY,
]

ACTION_SLUG_BY_TYPE: dict[PromptType, str] = {
    prompt_type: cfg["action_slug"] for prompt_type, cfg in PROMPT_REGISTRY.items()
}

TYPE_BY_ACTION_SLUG: dict[str, PromptType] = {
    cfg["action_slug"]: prompt_type for prompt_type, cfg in PROMPT_REGISTRY.items()
}


def parse_action_slug(slug: str) -> PromptType | None:
    """Преобразует callback slug в PromptType."""
    return TYPE_BY_ACTION_SLUG.get(slug)


def get_display_name(prompt_type: PromptType) -> str:
    return PROMPT_REGISTRY[prompt_type]["display_name"]


def get_button_label(prompt_type: PromptType, *, reprocess: bool = False) -> str:
    key = "reprocess_label" if reprocess else "button_label"
    return PROMPT_REGISTRY[prompt_type][key]


def get_prompt(prompt_type: PromptType, content: str, context: str = "") -> tuple[str, str]:
    """
    Возвращает готовую пару (system_prompt, user_message) для отправки в LLM.

    Args:
        prompt_type: Тип шаблона обработки
        content: Основной контент (транскрипт, текст)
        context: Дополнительный контекст (название видео, URL)

    Returns:
        Кортеж (system_prompt, user_message)
    """
    config = PROMPT_REGISTRY[prompt_type]
    user_message = config["user_template"].format(
        content=content.strip(),
        context=context.strip() if context else "не указан",
    )
    return config["system"], user_message
