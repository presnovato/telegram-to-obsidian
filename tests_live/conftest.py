"""Live acceptance environment: isolate every application path before imports."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest


_base = Path(os.environ.get("T2O_LIVE_SCRATCH_ROOT", Path(os.environ.get("TEMP", ".")) / "tiktok-obsidian-live"))
RUN_ROOT = _base / f"run-{uuid.uuid4().hex}"
VAULT = RUN_ROOT / "vault"
LOGS = RUN_ROOT / "logs"
for _path in (VAULT, LOGS, VAULT / "Meta" / "Медиафайлы", VAULT / "1. Входящие" / "TikTok", VAULT / "1. Входящие" / "Twitter"):
    _path.mkdir(parents=True, exist_ok=True)

os.environ["VAULT_ROOT"] = str(VAULT)
os.environ["LOG_DIR"] = str(LOGS)
os.environ["STATE_FILE"] = str(RUN_ROOT / "state.json")
os.environ["LOCK_FILE"] = str(RUN_ROOT / "bot.lock")
os.environ["OCR_ENABLED"] = "true"


@pytest.fixture(scope="session")
def live_scratchpad() -> Path:
    return RUN_ROOT
