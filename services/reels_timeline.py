"""
services/reels_timeline.py — парсинг и экспорт таймлайна монтажа Reels.

Фаза 1: структурированный JSON из сценария + человекочитаемый экспорт для CapCut.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from utils.telegram import escape_html

SECTION_LABELS = {
    "hook": "ХУК",
    "plot": "СЮЖЕТ",
    "climax": "КУЛЬМИНАЦИЯ",
    "cta": "CTA",
}

VALID_SECTIONS = frozenset(SECTION_LABELS)


class TimelineParseError(ValueError):
    """Не удалось разобрать JSON-таймлайн от LLM."""


@dataclass(frozen=True)
class ReelsScene:
    id: int
    timecode: str
    start_sec: int
    end_sec: int
    section: str
    voiceover: str
    on_screen_text: str | None
    broll_query: str
    broll_note: str
    edit_hint: str


@dataclass(frozen=True)
class ReelsTimeline:
    title: str
    total_duration_sec: int
    music_mood: str
    cta: str
    scenes: list[ReelsScene]

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "total_duration_sec": self.total_duration_sec,
            "music_mood": self.music_mood,
            "cta": self.cta,
            "scenes": [
                {
                    "id": scene.id,
                    "timecode": scene.timecode,
                    "start_sec": scene.start_sec,
                    "end_sec": scene.end_sec,
                    "section": scene.section,
                    "voiceover": scene.voiceover,
                    "on_screen_text": scene.on_screen_text,
                    "broll_query": scene.broll_query,
                    "broll_note": scene.broll_note,
                    "edit_hint": scene.edit_hint,
                }
                for scene in self.scenes
            ],
        }


def extract_json_payload(raw: str) -> str:
    """Извлекает JSON из ответа LLM (чистый JSON или markdown-блок)."""
    text = (raw or "").strip()
    if not text:
        raise TimelineParseError("Пустой ответ от ИИ")

    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fence_match:
        return fence_match.group(1).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise TimelineParseError("JSON не найден в ответе")

    return text[start : end + 1]


def _as_str(value: Any, *, field: str) -> str:
    if value is None:
        raise TimelineParseError(f"Поле «{field}» отсутствует")
    result = str(value).strip()
    if not result:
        raise TimelineParseError(f"Поле «{field}» пустое")
    return result


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _as_int(value: Any, *, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise TimelineParseError(f"Поле «{field}» должно быть числом") from exc


def parse_timeline(raw: str) -> ReelsTimeline:
    """Парсит и валидирует JSON-таймлайн."""
    payload = extract_json_payload(raw)

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise TimelineParseError(f"Невалидный JSON: {exc.msg}") from exc

    if not isinstance(data, dict):
        raise TimelineParseError("Корень JSON должен быть объектом")

    scenes_raw = data.get("scenes")
    if not isinstance(scenes_raw, list) or not scenes_raw:
        raise TimelineParseError("Список scenes пуст или отсутствует")

    scenes: list[ReelsScene] = []
    for index, item in enumerate(scenes_raw, start=1):
        if not isinstance(item, dict):
            raise TimelineParseError(f"Сцена {index}: ожидался объект")

        section = _as_str(item.get("section"), field=f"scenes[{index}].section").lower()
        if section not in VALID_SECTIONS:
            raise TimelineParseError(
                f"Сцена {index}: section должен быть hook/plot/climax/cta"
            )

        scenes.append(
            ReelsScene(
                id=_as_int(item.get("id", index), field=f"scenes[{index}].id"),
                timecode=_as_str(item.get("timecode"), field=f"scenes[{index}].timecode"),
                start_sec=_as_int(item.get("start_sec"), field=f"scenes[{index}].start_sec"),
                end_sec=_as_int(item.get("end_sec"), field=f"scenes[{index}].end_sec"),
                section=section,
                voiceover=_as_str(item.get("voiceover"), field=f"scenes[{index}].voiceover"),
                on_screen_text=_as_optional_str(item.get("on_screen_text")),
                broll_query=_as_str(item.get("broll_query"), field=f"scenes[{index}].broll_query"),
                broll_note=_as_str(item.get("broll_note"), field=f"scenes[{index}].broll_note"),
                edit_hint=_as_str(item.get("edit_hint"), field=f"scenes[{index}].edit_hint"),
            )
        )

    return ReelsTimeline(
        title=_as_str(data.get("title"), field="title"),
        total_duration_sec=_as_int(data.get("total_duration_sec"), field="total_duration_sec"),
        music_mood=_as_str(data.get("music_mood"), field="music_mood"),
        cta=_as_str(data.get("cta"), field="cta"),
        scenes=scenes,
    )


def _section_label(section: str) -> str:
    return SECTION_LABELS.get(section, section.upper())


def format_timeline_html(timeline: ReelsTimeline) -> str:
    """Форматирует таймлайн для отображения в Telegram (HTML)."""
    lines = [
        "📋 <b>Таймлайн для монтажа</b>",
        "",
        f"🎬 <b>{escape_html(timeline.title)}</b>",
        f"⏱ Длительность: ~{timeline.total_duration_sec} сек",
        f"🎵 Музыка: <i>{escape_html(timeline.music_mood)}</i>",
        f"🎯 CTA: {escape_html(timeline.cta)}",
        "",
        "─" * 28,
    ]

    for scene in timeline.scenes:
        section = _section_label(scene.section)
        lines.extend(
            [
                "",
                f"<b>Сцена {scene.id}</b> · {escape_html(scene.timecode)} · {section}",
                f"🎙 {escape_html(scene.voiceover)}",
            ]
        )
        if scene.on_screen_text:
            lines.append(f"📺 На экране: <b>{escape_html(scene.on_screen_text)}</b>")
        lines.append(f"🎥 B-roll: <code>{escape_html(scene.broll_query)}</code>")
        lines.append(f"   <i>{escape_html(scene.broll_note)}</i>")
        lines.append(f"✂️ {escape_html(scene.edit_hint)}")

    lines.extend(
        [
            "",
            "─" * 28,
            "",
            "💡 <i>Файлы ниже: JSON для автоматизации и кнопка «Собрать Reels».</i>",
            "🔍 B-roll: pexels.com или pixabay.com по запросу из сцены.",
        ]
    )
    return "\n".join(lines)


def format_capcut_guide(timeline: ReelsTimeline, *, source: str = "") -> str:
    """Текстовый гайд для ручного монтажа в CapCut / аналогах."""
    header = [
        "=== ТАЙМЛАЙН ДЛЯ МОНТАЖА (CapCut / InShot / VN) ===",
        "",
        f"Название: {timeline.title}",
        f"Длительность: ~{timeline.total_duration_sec} сек",
        f"Музыка: {timeline.music_mood}",
        f"CTA: {timeline.cta}",
    ]
    if source:
        header.append(f"Источник: {source}")
    header.extend(
        [
            "",
            "КАК МОНТИРОВАТЬ:",
            "1. Новый проект 9:16 (1080×1920)",
            "2. Импортируйте озвучку (ElevenLabs / CapCut TTS) по сценам",
            "3. Для каждой сцены — клип B-roll с Pexels/Pixabay",
            "4. Добавьте текст на экране крупным шрифтом",
            "5. Субтитры: авто-капшены + стиль word-by-word",
            "6. Музыка на -18 dB под голос",
            "",
            "─" * 40,
            "",
        ]
    )

    body: list[str] = []
    for scene in timeline.scenes:
        section = _section_label(scene.section)
        body.append(f"[{scene.timecode}] {section}")
        body.append(f"  Озвучка: {scene.voiceover}")
        if scene.on_screen_text:
            body.append(f"  Текст на экране: {scene.on_screen_text}")
        body.append(f"  B-roll (Pexels): {scene.broll_query}")
        body.append(f"  Описание кадра: {scene.broll_note}")
        body.append(f"  Монтаж: {scene.edit_hint}")
        body.append("")

    footer = [
        "─" * 40,
        "Сгенерировано ИИ-Комбайном · ai_kombain",
    ]
    return "\n".join(header + body + footer)


def timeline_to_json_bytes(timeline: ReelsTimeline) -> bytes:
    """Сериализует таймлайн в pretty JSON для отправки файлом."""
    return json.dumps(timeline.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")


def timeline_from_dict(data: dict[str, Any]) -> ReelsTimeline:
    """Восстанавливает таймлайн из словаря (FSM / кэш)."""
    return parse_timeline(json.dumps(data, ensure_ascii=False))
