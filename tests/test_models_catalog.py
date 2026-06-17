from domain.models_catalog import resolve_model_id


def test_resolve_model_id_maps_deprecated_gemini():
    assert resolve_model_id("google/gemini-flash-1.5") == "google/gemini-2.5-flash"


def test_resolve_model_id_maps_deprecated_free_llama():
    assert resolve_model_id("meta-llama/llama-3.1-8b-instruct:free") == "openrouter/free"
