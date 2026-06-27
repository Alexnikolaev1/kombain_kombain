"""Режимы автосборки Reels — классика и расширенные варианты."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ReelsRenderMode(str, Enum):
    """classic — короткий текст на экране (как раньше)."""

    CLASSIC = "classic"
    SUBTITLES = "subtitles"
    MUSIC = "music"
    PRO = "pro"


MODE_LABELS: dict[ReelsRenderMode, str] = {
    ReelsRenderMode.CLASSIC: "🎬 Reels (классика)",
    ReelsRenderMode.SUBTITLES: "💬 Reels + субтитры",
    ReelsRenderMode.MUSIC: "🎵 Reels + музыка",
    ReelsRenderMode.PRO: "✨ Reels Pro",
}


@dataclass(frozen=True)
class ReelsRenderOptions:
    mode: ReelsRenderMode = ReelsRenderMode.CLASSIC

    @property
    def show_on_screen_text(self) -> bool:
        return self.mode in (ReelsRenderMode.CLASSIC, ReelsRenderMode.MUSIC)

    @property
    def show_subtitles(self) -> bool:
        return self.mode in (ReelsRenderMode.SUBTITLES, ReelsRenderMode.PRO)

    @property
    def add_music(self) -> bool:
        return self.mode in (ReelsRenderMode.MUSIC, ReelsRenderMode.PRO)


def mode_from_callback(slug: str) -> ReelsRenderMode | None:
    mapping = {
        "reels_render": ReelsRenderMode.CLASSIC,
        "reels_render_subs": ReelsRenderMode.SUBTITLES,
        "reels_render_music": ReelsRenderMode.MUSIC,
        "reels_render_pro": ReelsRenderMode.PRO,
    }
    return mapping.get(slug)
