"""Персистентность режима: выбор переживает перезапуск, битый файл не роняет бота."""
import pytest

from tiktok_obsidian import config, mode_store
from tiktok_obsidian.core.mode import CaptureMode


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Каждому тесту свой state.json и чистый кэш модуля."""
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(config, "DEFAULT_MODE_RAW", "both")
    mode_store.reset_cache()
    yield
    mode_store.reset_cache()


def test_defaults_to_env_value_when_no_state_file():
    assert mode_store.get_mode() is CaptureMode.BOTH


def test_set_mode_survives_restart():
    mode_store.set_mode(CaptureMode.CHAT)
    mode_store.reset_cache()  # эмулируем перезапуск процесса
    assert mode_store.get_mode() is CaptureMode.CHAT


def test_stored_mode_wins_over_env_default(monkeypatch):
    mode_store.set_mode(CaptureMode.VAULT)
    mode_store.reset_cache()
    monkeypatch.setattr(config, "DEFAULT_MODE_RAW", "chat")
    assert mode_store.get_mode() is CaptureMode.VAULT


def test_corrupt_state_falls_back_to_default_without_raising():
    config.STATE_FILE.write_text("{не json", encoding="utf-8")
    mode_store.reset_cache()
    assert mode_store.get_mode() is CaptureMode.BOTH


def test_unknown_mode_in_state_falls_back_to_default():
    config.STATE_FILE.write_text('{"mode": "obsidian"}', encoding="utf-8")
    mode_store.reset_cache()
    assert mode_store.get_mode() is CaptureMode.BOTH


def test_write_failure_still_switches_for_this_session(monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError("диск только на чтение")

    monkeypatch.setattr(mode_store, "atomic_write_text", boom)
    mode_store.set_mode(CaptureMode.CHAT)
    assert mode_store.get_mode() is CaptureMode.CHAT
