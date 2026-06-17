"""
domain/models_catalog.py — единый каталог LLM-моделей для UI и валидации.
"""

# (label, model_id)
AVAILABLE_MODELS: list[tuple[str, str]] = [
    ("⚡ Gemini 2.5 Flash (рекомендуется)", "google/gemini-2.5-flash"),
    ("🔀 Gemini Flash Latest (авто-версия)", "google/gemini-flash-latest"),
    ("🆓 Бесплатные модели (авто)", "openrouter/free"),
    ("🧠 Llama 3.3 70B (бесплатно)", "meta-llama/llama-3.3-70b-instruct:free"),
    ("💎 Gemma 4 31B (бесплатно)", "google/gemma-4-31b-it:free"),
]

AVAILABLE_MODEL_IDS: frozenset[str] = frozenset(model_id for _, model_id in AVAILABLE_MODELS)

# Устаревшие slug OpenRouter → актуальные
MODEL_ALIASES: dict[str, str] = {
    "google/gemini-flash-1.5": "google/gemini-2.5-flash",
    "google/gemini-flash-1.5-8b": "google/gemini-2.5-flash",
    "meta-llama/llama-3.1-8b-instruct:free": "openrouter/free",
    "meta-llama/llama-3.1-70b-instruct:free": "meta-llama/llama-3.3-70b-instruct:free",
}


def resolve_model_id(model_id: str) -> str:
    """Подставляет актуальный slug, если модель переименована на OpenRouter."""
    current = model_id
    seen: set[str] = set()
    while current in MODEL_ALIASES and current not in seen:
        seen.add(current)
        current = MODEL_ALIASES[current]
    return current


def get_model_label(model_id: str) -> str:
    resolved = resolve_model_id(model_id)
    for label, mid in AVAILABLE_MODELS:
        if mid == resolved:
            return label
    return resolved
