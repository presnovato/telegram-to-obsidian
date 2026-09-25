"""B3/F12/R1: коды выхода main — 4 конфиг, 2 краш, 3 второй экземпляр."""
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tiktok_obsidian import config
from tiktok_obsidian import main as main_module
from tiktok_obsidian.instance_lock import InstanceLock


def _harness(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOCK_FILE", tmp_path / "bot.lock")
    monkeypatch.setattr(main_module, "_setup_logging", lambda: None)


async def _raise(exc):
    raise exc


def test_crash_returns_2(monkeypatch, tmp_path):
    _harness(monkeypatch, tmp_path)
    monkeypatch.setattr(main_module, "_run", lambda parent_pid=None: _raise(ValueError("boom")))
    assert main_module.main([]) == main_module.EXIT_CRASH == 2


def test_config_error_returns_4(monkeypatch, tmp_path):
    _harness(monkeypatch, tmp_path)
    monkeypatch.setattr(
        main_module, "_run", lambda parent_pid=None: _raise(config.ConfigError("no token"))
    )
    assert main_module.main([]) == main_module.EXIT_CONFIG_ERROR == 4


def test_second_instance_returns_3(monkeypatch, tmp_path):
    _harness(monkeypatch, tmp_path)
    with InstanceLock(config.LOCK_FILE):
        assert main_module.main([]) == main_module.EXIT_ANOTHER_INSTANCE == 3


def test_config_error_is_runtime_error():
    assert issubclass(config.ConfigError, RuntimeError)


def test_int_env_falls_back_and_records_problem(monkeypatch):
    """R1: мусор в числовом env на импорте не бросает — дефолт + запись в список."""
    monkeypatch.setattr(os, "environ", {"X_BAD": "abc"})
    before = len(config._ENV_PARSE_PROBLEMS)
    try:
        assert config._int_env("X_BAD", "7") == 7
        assert any("X_BAD" in p for p in config._ENV_PARSE_PROBLEMS[before:])
    finally:
        del config._ENV_PARSE_PROBLEMS[before:]


def test_bad_allowed_user_id_reported_by_validate(monkeypatch):
    """R1: ALLOWED_USER_ID=abc — импорт жив, validate() называет переменную."""
    monkeypatch.setenv("ALLOWED_USER_ID", "abc")
    importlib.reload(config)
    try:
        assert config.ALLOWED_USER_ID == 0  # откат к дефолту
        with pytest.raises(config.ConfigError, match="ALLOWED_USER_ID"):
            config.validate()
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_bad_numeric_env_exits_4_and_names_variable(tmp_path):
    """R1: живой процесс с ALLOWED_USER_ID=abc → код 4, переменная в выводе."""
    env = dict(os.environ)
    env["ALLOWED_USER_ID"] = "abc"
    env["LOCK_FILE"] = str(tmp_path / "bot.lock")
    env["LOG_DIR"] = str(tmp_path / "logs")
    env["STATE_FILE"] = str(tmp_path / "state.json")
    repo = Path(config.__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, "-m", "tiktok_obsidian"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        cwd=repo,
        env=env,
    )
    assert proc.returncode == 4
    assert "ALLOWED_USER_ID" in (proc.stdout + proc.stderr)


async def _unauthorized_poll():
    import asyncio
    from aiogram.methods import GetMe
    from aiogram.exceptions import TelegramUnauthorizedError

    class FakeDp:
        async def start_polling(self, *_args, **_kwargs):
            raise TelegramUnauthorizedError(GetMe(), "Unauthorized")

    await main_module._poll_forever(FakeDp(), object(), asyncio.Event())


def test_unauthorized_token_maps_to_config_exit_4(monkeypatch, tmp_path, caplog):
    _harness(monkeypatch, tmp_path)
    monkeypatch.setattr(main_module, "_run", lambda parent_pid=None: _unauthorized_poll())

    assert main_module.main([]) == main_module.EXIT_CONFIG_ERROR == 4
    assert "Telegram отклонил TELEGRAM_TOKEN" in caplog.text
