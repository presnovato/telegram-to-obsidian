"""Single-instance lock: второй захват падает, после release — ок; main() → 3."""
import os

import pytest

from tiktok_obsidian import config, main as main_module
from tiktok_obsidian.instance_lock import InstanceLock, LockTaken


def test_second_acquire_fails_while_first_held(tmp_path):
    first = InstanceLock(tmp_path / "bot.lock").acquire()
    try:
        with pytest.raises(LockTaken):
            InstanceLock(tmp_path / "bot.lock").acquire()
    finally:
        first.release()


def test_acquire_succeeds_after_release(tmp_path):
    path = tmp_path / "bot.lock"
    InstanceLock(path).acquire().release()
    with InstanceLock(path):
        content = path.read_text(encoding="ascii")
    assert content.strip() == str(os.getpid())


def test_context_manager_releases_on_exception(tmp_path):
    path = tmp_path / "bot.lock"
    with pytest.raises(RuntimeError), InstanceLock(path):
        raise RuntimeError("boom")
    with InstanceLock(path):  # лок снова свободен
        pass


def test_main_returns_3_when_lock_taken(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCK_FILE", tmp_path / "bot.lock")
    monkeypatch.setattr(main_module, "_setup_logging", lambda: None)
    with InstanceLock(config.LOCK_FILE):
        assert main_module.main([]) == main_module.EXIT_ANOTHER_INSTANCE == 3


def test_parse_args_defaults():
    assert main_module.parse_args([]).parent_pid is None
    assert main_module.parse_args(["--parent-pid", "4242"]).parent_pid == 4242
