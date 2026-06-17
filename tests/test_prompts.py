from prompts.templates import get_display_name, parse_action_slug
from db.models import PromptType


def test_parse_action_slug_roundtrip():
    assert parse_action_slug("reels_script") == PromptType.REELS_SCRIPT
    assert parse_action_slug("viral_titles") == PromptType.VIRAL_TITLES
    assert parse_action_slug("unknown") is None


def test_display_names_are_human_readable():
    name = get_display_name(PromptType.TLDR_SUMMARY)
    assert "TL;DR" in name
