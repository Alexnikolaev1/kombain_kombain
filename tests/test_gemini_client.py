import pytest

from services.gemini_client import call_gemini_api, gemini_model_label


@pytest.mark.asyncio
async def test_call_gemini_api_parses_response(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "candidates": [
                    {"content": {"parts": [{"text": "Готово"}]}},
                ],
                "usageMetadata": {"totalTokenCount": 42},
            }

    class FakeClient:
        is_closed = False

        async def post(self, path, params=None, json=None):
            assert path.endswith(":generateContent")
            assert params["key"] == "test-gemini-key"
            return FakeResponse()

    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.0-flash")
    from config import get_settings

    get_settings.cache_clear()

    monkeypatch.setattr("services.gemini_client.get_gemini_client", lambda: FakeClient())

    text, tokens = await call_gemini_api(
        "system",
        "user",
        max_tokens=100,
        temperature=0.5,
    )
    assert text == "Готово"
    assert tokens == 42
    assert gemini_model_label() == "gemini/gemini-2.0-flash"
