"""
domain/models_catalog.py — единый каталог LLM-моделей для UI и валидации.
"""

# (label, model_id)
AVAILABLE_MODELS: list[tuple[str, str]] = [
    ("⚡ Gemini Flash 1.5 (быстрый)", "google/gemini-flash-1.5"),
    ("🧠 Llama 3.1 70B (мощный)", "meta-llama/llama-3.1-70b-instruct"),
    ("🆓 Llama 3.1 8B (бесплатный)", "meta-llama/llama-3.1-8b-instruct:free"),
    ("💎 Claude Haiku (точный)", "anthropic/claude-haiku"),
]

AVAILABLE_MODEL_IDS: frozenset[str] = frozenset(model_id for _, model_id in AVAILABLE_MODELS)


def get_model_label(model_id: str) -> str:
    for label, mid in AVAILABLE_MODELS:
        if mid == model_id:
            return label
    return model_id
