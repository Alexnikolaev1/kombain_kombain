from prompts.templates import get_display_name, parse_action_slug
from db.models import PromptType


def test_parse_action_slug_roundtrip():
    assert parse_action_slug("reels_script") == PromptType.REELS_SCRIPT
    assert parse_action_slug("viral_titles") == PromptType.VIRAL_TITLES
    assert parse_action_slug("unknown") is None


def test_display_names_are_human_readable():
    name = get_display_name(PromptType.TLDR_SUMMARY)
    assert "TL;DR" in name


def test_reels_timeline_not_in_main_menu():
    from prompts.templates import PROMPT_MENU_ORDER

    assert PromptType.REELS_TIMELINE not in PROMPT_MENU_ORDER
    assert get_display_name(PromptType.REELS_TIMELINE) == "📋 Таймлайн для монтажа"
