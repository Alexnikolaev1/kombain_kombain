import pytest

from services.parser import (
    TelegramPostRef,
    _html_fragment_to_text,
    extract_telegram_post_url,
    parse_telegram_post_html,
)


SAMPLE_POST_HTML = """
<!DOCTYPE html>
<html>
<head>
  <meta property="og:site_name" content="Pavel Durov">
  <meta property="og:title" content="Pavel Durov">
  <meta property="og:description" content="Fallback description">
</head>
<body>
  <div class="tgme_widget_message_wrap" data-post="durov/193">
    <div class="tgme_widget_message_text js-message_text" dir="auto">
      Hello <b>world</b><br>Read <a href="https://example.com">more</a>
    </div>
  </div>
</body>
</html>
"""


def test_extract_public_telegram_url():
    ref = extract_telegram_post_url("Смотри https://t.me/durov/193")
    assert ref is not None
    assert ref.channel == "durov"
    assert ref.message_id == 193
    assert ref.url == "https://t.me/durov/193"
    assert ref.is_private is False


def test_extract_public_telegram_url_with_s_prefix():
    ref = extract_telegram_post_url("https://t.me/s/durov/193")
    assert ref is not None
    assert ref.channel == "durov"
    assert ref.message_id == 193


def test_extract_private_telegram_url():
    ref = extract_telegram_post_url("https://t.me/c/1234567890/42")
    assert ref is not None
    assert ref.channel == "1234567890"
    assert ref.message_id == 42
    assert ref.is_private is True


def test_extract_ignores_channel_without_message_id():
    assert extract_telegram_post_url("https://t.me/durov") is None


def test_html_fragment_to_text():
    html = 'Line 1<br>Visit <a href="https://example.com">site</a>'
    text = _html_fragment_to_text(html)
    assert "Line 1" in text
    assert "site (https://example.com)" in text


def test_parse_telegram_post_html():
    ref = TelegramPostRef(
        url="https://t.me/durov/193",
        channel="durov",
        message_id=193,
    )
    title, content = parse_telegram_post_html(SAMPLE_POST_HTML, ref)
    assert title == "Pavel Durov"
    assert "Hello world" in content
    assert "more (https://example.com)" in content


def test_parse_telegram_post_html_media_only_raises():
    ref = TelegramPostRef(
        url="https://t.me/durov/1",
        channel="durov",
        message_id=1,
    )
    html = '<html><body><div data-post="durov/1"></div></body></html>'
    with pytest.raises(ValueError, match="нет текста"):
        parse_telegram_post_html(html, ref)


@pytest.mark.asyncio
async def test_fetch_telegram_post_uses_http_client(monkeypatch):
    ref = TelegramPostRef(
        url="https://t.me/durov/193",
        channel="durov",
        message_id=193,
    )

    class FakeResponse:
        status_code = 200
        text = SAMPLE_POST_HTML

        def raise_for_status(self):
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            assert url == "https://t.me/durov/193"
            return FakeResponse()

    monkeypatch.setattr("services.parser.httpx.AsyncClient", lambda **kwargs: FakeClient())

    from services.parser import fetch_telegram_post

    result = await fetch_telegram_post(ref)
    assert result.is_success
    assert result.source_type.value == "telegram"
    assert "Hello world" in result.content
    assert result.title == "Pavel Durov"
