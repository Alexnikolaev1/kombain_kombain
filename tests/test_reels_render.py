"""Тесты Gemini TTS и сборщика Reels."""

import json
import wave
from pathlib import Path

import pytest

from services.gemini_tts import (
    _parse_pcm_from_mime,
    write_pcm_as_wav,
    wav_duration_sec,
)
from services.reels_renderer import ReelsRenderError, _escape_drawtext, _safe_slug
from services.reels_timeline import timeline_from_dict

SAMPLE_TIMELINE = {
    "title": "Тест",
    "total_duration_sec": 10,
    "music_mood": "энергичный",
    "cta": "Подпишись",
    "scenes": [
        {
            "id": 1,
            "timecode": "0:00-0:05",
            "start_sec": 0,
            "end_sec": 5,
            "section": "hook",
            "voiceover": "Привет мир",
            "on_screen_text": "ВАУ",
            "broll_query": "city night",
            "broll_note": "ночной город",
            "edit_hint": "zoom",
        }
    ],
}


def test_parse_pcm_mime_rate():
    rate, channels = _parse_pcm_from_mime("audio/L16;codec=pcm;rate=48000")
    assert rate == 48000
    assert channels == 1


def test_wav_duration_roundtrip(tmp_path: Path):
    pcm = b"\x00\x00" * 24000  # 1 sec silence at 24kHz mono 16-bit
    wav_path = tmp_path / "test.wav"
    write_pcm_as_wav(pcm, wav_path, rate=24000)
    assert abs(wav_duration_sec(wav_path) - 1.0) < 0.05


def test_escape_drawtext_special_chars():
    assert "\\:" in _escape_drawtext("A: B")
    assert "\\'" in _escape_drawtext("it's")


def test_safe_slug_cyrillic():
    slug = _safe_slug("Секрет успеха 2024!")
    assert slug
    assert " " not in slug


def test_timeline_from_dict():
    timeline = timeline_from_dict(SAMPLE_TIMELINE)
    assert timeline.title == "Тест"
    assert timeline.scenes[0].broll_query == "city night"


@pytest.mark.asyncio
async def test_render_disabled_raises(monkeypatch):
    from config import get_settings
    from services.reels_renderer import render_reels_video

    settings = get_settings()
    monkeypatch.setattr(settings, "REELS_RENDER_ENABLED", False)

    timeline = timeline_from_dict(SAMPLE_TIMELINE)
    with pytest.raises(ReelsRenderError, match="отключена"):
        await render_reels_video(timeline)
