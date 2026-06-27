"""Тесты режимов сборки Reels."""

from services.reels_render_modes import (
    MODE_LABELS,
    ReelsRenderMode,
    ReelsRenderOptions,
    mode_from_callback,
)


def test_classic_shows_on_screen_only():
    opts = ReelsRenderOptions(mode=ReelsRenderMode.CLASSIC)
    assert opts.show_on_screen_text is True
    assert opts.show_subtitles is False
    assert opts.add_music is False


def test_subtitles_mode():
    opts = ReelsRenderOptions(mode=ReelsRenderMode.SUBTITLES)
    assert opts.show_on_screen_text is False
    assert opts.show_subtitles is True
    assert opts.add_music is False


def test_music_mode_keeps_classic_text():
    opts = ReelsRenderOptions(mode=ReelsRenderMode.MUSIC)
    assert opts.show_on_screen_text is True
    assert opts.show_subtitles is False
    assert opts.add_music is True


def test_pro_mode_all_features():
    opts = ReelsRenderOptions(mode=ReelsRenderMode.PRO)
    assert opts.show_subtitles is True
    assert opts.add_music is True


def test_mode_from_callback():
    assert mode_from_callback("reels_render") == ReelsRenderMode.CLASSIC
    assert mode_from_callback("reels_render_pro") == ReelsRenderMode.PRO
    assert mode_from_callback("unknown") is None


def test_mode_labels_cover_all_modes():
    for mode in ReelsRenderMode:
        assert mode in MODE_LABELS
