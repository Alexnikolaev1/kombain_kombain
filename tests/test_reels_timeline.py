"""Тесты парсинга и экспорта таймлайна Reels."""

import json

import pytest

from services.reels_timeline import (
    TimelineParseError,
    extract_json_payload,
    format_capcut_guide,
    format_timeline_html,
    parse_timeline,
    timeline_to_json_bytes,
)

SAMPLE_JSON = """
{
  "title": "Секрет продуктивности",
  "total_duration_sec": 45,
  "music_mood": "энергичный лоу-фай",
  "cta": "Подпишись за больше лайфхаков",
  "scenes": [
    {
      "id": 1,
      "timecode": "0:00-0:03",
      "start_sec": 0,
      "end_sec": 3,
      "section": "hook",
      "voiceover": "Ты делаешь это каждый день?",
      "on_screen_text": "СТОП",
      "broll_query": "busy office worker",
      "broll_note": "Человек за ноутбуком в спешке",
      "edit_hint": "быстрый зум на лицо"
    },
    {
      "id": 2,
      "timecode": "0:03-0:12",
      "start_sec": 3,
      "end_sec": 12,
      "section": "plot",
      "voiceover": "Вот три правила, которые меняют всё.",
      "on_screen_text": null,
      "broll_query": "checklist planning",
      "broll_note": "Планирование задач",
      "edit_hint": "jump cut каждые 2 сек"
    }
  ]
}
"""


def test_extract_json_from_markdown_fence():
    raw = f"Вот таймлайн:\n```json\n{SAMPLE_JSON.strip()}\n```"
    payload = extract_json_payload(raw)
    data = json.loads(payload)
    assert data["title"] == "Секрет продуктивности"


def test_parse_timeline_success():
    timeline = parse_timeline(SAMPLE_JSON)
    assert timeline.title == "Секрет продуктивности"
    assert timeline.total_duration_sec == 45
    assert len(timeline.scenes) == 2
    assert timeline.scenes[0].section == "hook"
    assert timeline.scenes[0].on_screen_text == "СТОП"


def test_parse_timeline_rejects_invalid_section():
    bad = SAMPLE_JSON.replace('"hook"', '"intro"')
    with pytest.raises(TimelineParseError, match="section"):
        parse_timeline(bad)


def test_parse_timeline_rejects_empty_scenes():
    data = json.loads(SAMPLE_JSON)
    data["scenes"] = []
    with pytest.raises(TimelineParseError, match="scenes"):
        parse_timeline(json.dumps(data))


def test_format_timeline_html_escapes_special_chars():
    timeline = parse_timeline(SAMPLE_JSON.replace("СТОП", "A & B <test>"))
    html = format_timeline_html(timeline)
    assert "A &amp; B &lt;test&gt;" in html
    assert "📋" in html
    assert "busy office worker" in html


def test_format_capcut_guide_contains_instructions():
    timeline = parse_timeline(SAMPLE_JSON)
    guide = format_capcut_guide(timeline, source="YouTube: тест")
    assert "CapCut" in guide
    assert "0:00-0:03" in guide
    assert "YouTube: тест" in guide
    assert "busy office worker" in guide


def test_timeline_to_json_bytes_roundtrip():
    timeline = parse_timeline(SAMPLE_JSON)
    raw = timeline_to_json_bytes(timeline).decode("utf-8")
    data = json.loads(raw)
    assert data["scenes"][0]["broll_query"] == "busy office worker"
