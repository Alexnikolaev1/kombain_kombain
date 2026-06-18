import json

from services.parser import (
    _extract_ytdlp_transcript,
    _parse_subtitle_payload,
    extract_youtube_video_id,
)


def test_extract_youtube_video_id():
    assert extract_youtube_video_id("https://youtu.be/r8B7PPIJkm4") == "r8B7PPIJkm4"
    assert extract_youtube_video_id("https://www.youtube.com/watch?v=JpJ0UVIPePk") == "JpJ0UVIPePk"


def test_parse_vtt_subtitle_payload():
    raw = """WEBVTT

00:00:01.000 --> 00:00:03.000
Hello <b>world</b>

00:00:03.000 --> 00:00:05.000
Hello world
"""
    text = _parse_subtitle_payload(raw, "vtt")
    assert "Hello world" in text
    assert text.count("Hello world") == 1


def test_parse_json3_subtitle_payload():
    raw = json.dumps(
        {
            "events": [
                {"segs": [{"utf8": "Привет"}, {"utf8": " мир"}]},
                {"segs": [{"utf8": "\n"}]},
            ]
        }
    )
    text = _parse_subtitle_payload(raw, "json3")
    assert text == "Привет мир"


def test_extract_ytdlp_transcript_prefers_requested_language():
    class FakeYdl:
        def urlopen(self, url):
            class Resp:
                def read(self):
                    if "ru" in url:
                        return b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nRussian text\n"
                    return b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nEnglish text\n"

            return Resp()

    info = {
        "id": "test",
        "subtitles": {
            "ru": [{"ext": "vtt", "url": "https://example.com/ru.vtt"}],
            "en": [{"ext": "vtt", "url": "https://example.com/en.vtt"}],
        },
    }

    text, lang = _extract_ytdlp_transcript(info, ["ru", "en"], FakeYdl())
    assert "Russian text" in text
    assert lang == "ru"


def test_language_matches_region_codes():
    from services.parser import _language_matches

    assert _language_matches("ru-RU", "ru")
    assert _language_matches("en-US", "en")
    assert not _language_matches("de", "ru")


def test_placeholder_proxy_is_ignored():
    from services.parser import _is_placeholder_proxy

    assert _is_placeholder_proxy("http://user:pass@host:port")
    assert _is_placeholder_proxy("")
    assert not _is_placeholder_proxy("socks5://real:secret@proxy.example.com:1080")


def test_youtube_bot_error_detection():
    from services.parser import YouTubeBotCheckError, _is_youtube_bot_error

    assert _is_youtube_bot_error(
        Exception("Sign in to confirm you're not a bot")
    )
    assert not _is_youtube_bot_error(Exception("format not available"))
    assert issubclass(YouTubeBotCheckError, Exception)


def test_normalize_b64_strips_whitespace():
    from services.parser import _normalize_b64

    assert _normalize_b64("YQ==\n") == "YQ=="
    assert _normalize_b64("  Y W J  ") == "YWJ"


def test_primary_player_clients_with_cookies(tmp_path, monkeypatch):
    from config import get_settings
    from services.parser import _primary_ytdlp_player_clients

    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t0\t__Secure-1PSID\ttest\n",
        encoding="utf-8",
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "YOUTUBE_COOKIES_FILE", str(cookie_file))
    monkeypatch.setattr(settings, "YOUTUBE_COOKIES_B64", "")

    assert _primary_ytdlp_player_clients() == ["web", "mweb"]


def test_inspect_youtube_cookiefile(tmp_path):
    from services.parser import _inspect_youtube_cookiefile

    path = tmp_path / "cookies.txt"
    path.write_text(
        "# Netscape\n"
        ".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tx\n"
        ".youtube.com\tTRUE\t/\tTRUE\t0\tLOGIN_INFO\ty\n",
        encoding="utf-8",
    )
    info = _inspect_youtube_cookiefile(path)
    assert info["valid"] is True
    assert info["has_login"] is True
