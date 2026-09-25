from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


@pytest.mark.live
def test_section_c17_proxy_backoff_stops_with_parent(live_scratchpad):
    """A closed Telegram proxy should back off, then the watchdog should stop the bot."""
    repo = Path(__file__).resolve().parents[1]
    scratch = live_scratchpad / "section-c-17"
    vault = scratch / "vault"
    logs = scratch / "logs"
    for folder in (
        vault,
        logs,
        vault / "Meta" / "Медиафайлы",
        vault / "1. Входящие" / "TikTok",
        vault / "1. Входящие" / "Twitter",
    ):
        folder.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    # Load credentials from this checkout's .env; path and proxy values stay test-local.
    for name in ("TELEGRAM_TOKEN", "ALLOWED_USER_ID", "MEDIA_PROXY", "HTTP_PROXY"):
        env.pop(name, None)
    env.update(
        {
            "VAULT_ROOT": str(vault),
            "LOG_DIR": str(logs),
            "LOCK_FILE": str(scratch / "bot.lock"),
            "STATE_FILE": str(scratch / "state.json"),
            "OCR_ENABLED": "false",
            "TELEGRAM_PROXY": "http://127.0.0.1:9",
        }
    )

    output_path = scratch / "bot-stdout.log"
    output: queue.Queue[str] = queue.Queue()
    captured: list[str] = []
    dummy = None
    bot = None
    reader = None

    def drain_stdout(proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        with output_path.open("w", encoding="utf-8") as stream:
            for raw in proc.stdout:
                line = raw.rstrip("\r\n")
                captured.append(line)
                stream.write(line + "\n")
                stream.flush()
                output.put(line)

    def wait_for(pattern: str, timeout: float) -> str:
        matcher = re.compile(pattern)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = output.get(timeout=min(0.2, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                if bot is not None and bot.poll() is not None:
                    raise AssertionError(
                        f"bot exited {bot.returncode} before /{pattern}/; output={captured[-20:]}"
                    )
                continue
            if matcher.search(line):
                return line
        raise AssertionError(f"timed out waiting for /{pattern}/; output={captured[-20:]}")

    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        dummy = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)"],
            cwd=repo,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=create_no_window,
        )
        bot = subprocess.Popen(
            [sys.executable, "-m", "tiktok_obsidian", "--parent-pid", str(dummy.pid)],
            cwd=repo,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=create_no_window,
        )
        reader = threading.Thread(target=drain_stdout, args=(bot,), daemon=True)
        reader.start()

        backoff_warning = wait_for(
            r"прокси недоступен — запусти прокси-клиент или очисти TELEGRAM_PROXY",
            timeout=30,
        )
        assert "http://127.0.0.1:9" in backoff_warning

        dummy.terminate()
        dummy.wait(timeout=5)
        try:
            exit_code = bot.wait(timeout=40)
        except subprocess.TimeoutExpired:
            raise AssertionError("bot did not exit within 40 seconds after dummy parent stopped")
        assert exit_code == 0, f"expected clean watchdog exit 0, got {exit_code}; output={captured[-20:]}"
        assert any("parent exited, stopping" in line for line in captured)
    finally:
        for proc in (bot, dummy):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
        if reader is not None:
            reader.join(timeout=5)
