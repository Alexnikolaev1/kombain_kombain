from db.models import AICache, PromptType


def test_cache_hash_depends_on_model_and_context():
    content = "один и тот же текст"
    context_a = "Название: Видео A | URL: https://youtu.be/a"
    context_b = "Название: Видео B | URL: https://youtu.be/b"
    model_a = "google/gemini-flash-1.5"
    model_b = "meta-llama/llama-3.1-8b-instruct:free"

    hash_base = AICache.compute_hash(content, PromptType.TLDR_SUMMARY, context_a, model_a)
    hash_other_context = AICache.compute_hash(content, PromptType.TLDR_SUMMARY, context_b, model_a)
    hash_other_model = AICache.compute_hash(content, PromptType.TLDR_SUMMARY, context_a, model_b)
    hash_other_prompt = AICache.compute_hash(content, PromptType.DEEP_ANALYSIS, context_a, model_a)

    assert hash_base != hash_other_context
    assert hash_base != hash_other_model
    assert hash_base != hash_other_prompt


def test_cache_hash_is_deterministic():
    content = "текст"
    context = "ctx"
    model = "google/gemini-flash-1.5"

    first = AICache.compute_hash(content, PromptType.REELS_SCRIPT, context, model)
    second = AICache.compute_hash(content, PromptType.REELS_SCRIPT, context, model)

    assert first == second
