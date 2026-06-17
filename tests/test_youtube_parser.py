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
