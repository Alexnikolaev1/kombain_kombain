"""application/dto.py — объекты передачи данных между слоями."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContentSession:
    """Сохранённый в FSM контент, готовый к генерации."""

    content: str
    context: str
    source_url: str
    source_type: str
    title: str

    def to_fsm_data(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "context": self.context,
            "source_url": self.source_url,
            "source_type": self.source_type,
            "title": self.title,
        }

    @classmethod
    def from_fsm_data(cls, data: dict[str, Any]) -> "ContentSession":
        return cls(
            content=data.get("content", ""),
            context=data.get("context", ""),
            source_url=data.get("source_url", ""),
            source_type=data.get("source_type", ""),
            title=data.get("title", ""),
        )


@dataclass(frozen=True)
class GenerationOutcome:
    """Результат генерации контента через LLM."""

    response: str
    was_cached: bool
    model: str
    processing_ms: float
    tokens_saved: int
    action: str = ""

    @classmethod
    def from_result(cls, result: dict[str, Any], *, action: str = "") -> "GenerationOutcome":
        return cls(
            response=result["response"],
            was_cached=result["was_cached"],
            model=str(result.get("model", "")),
            processing_ms=float(result["processing_ms"]),
            tokens_saved=int(result.get("tokens_saved", 0)),
            action=action,
        )
