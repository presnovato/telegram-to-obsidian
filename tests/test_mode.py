"""Чистый слой режима захвата: разбор значения и разового переопределения в сообщении."""
from tiktok_obsidian.core.mode import CaptureMode, parse_mode, strip_mode_override


def test_parse_mode_accepts_known_values_case_insensitively():
    assert parse_mode("chat") is CaptureMode.CHAT
    assert parse_mode("  VAULT ") is CaptureMode.VAULT
    assert parse_mode("both") is CaptureMode.BOTH


def test_parse_mode_rejects_junk():
    assert parse_mode("obsidian") is None
    assert parse_mode("") is None


def test_mode_flags():
    assert CaptureMode.VAULT.writes_vault and not CaptureMode.VAULT.sends_chat
    assert CaptureMode.CHAT.sends_chat and not CaptureMode.CHAT.writes_vault
    assert CaptureMode.BOTH.writes_vault and CaptureMode.BOTH.sends_chat


def test_override_before_url_is_stripped():
    mode, rest = strip_mode_override("!chat https://x.com/u/status/123")
    assert mode is CaptureMode.CHAT
    assert rest == "https://x.com/u/status/123"


def test_override_after_url_is_stripped():
    # Пересланную ссылку чаще комментируют после неё, чем до.
    mode, rest = strip_mode_override("https://x.com/u/status/123 !vault")
    assert mode is CaptureMode.VAULT
    assert rest == "https://x.com/u/status/123"


def test_no_override_leaves_text_intact():
    text = "просто ссылка https://x.com/u/status/123"
    assert strip_mode_override(text) == (None, text)


def test_exclamation_inside_word_is_not_an_override():
    # «Ого!chat» и хэштег-подобные хвосты не должны переключать режим.
    mode, rest = strip_mode_override("ого!chat https://x.com/u/status/1")
    assert mode is None
    assert rest == "ого!chat https://x.com/u/status/1"


def test_unknown_token_is_consumed_but_yields_no_mode():
    # `!foo` регуляркой не ловится вовсе — текст остаётся как есть.
    assert strip_mode_override("!foo https://x.com/u/status/1")[0] is None
