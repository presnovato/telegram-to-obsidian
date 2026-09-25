"""F10: /update_ytdlp не морозит loop и честно называет обе версии."""
import asyncio

from tiktok_obsidian.handlers import commands as commands_module


class FakeMessage:
    def __init__(self):
        self.answers = []

    async def answer(self, text):
        self.answers.append(text)


async def test_update_runs_off_loop_and_reports_both_versions(monkeypatch):
    ticks = []

    async def ticker():
        for _ in range(5):
            ticks.append(True)
            await asyncio.sleep(0.05)

    def slow_update():
        import time

        time.sleep(0.2)
        return True, "ok"

    monkeypatch.setattr(commands_module, "update_ytdlp", slow_update)
    monkeypatch.setattr(commands_module, "installed_ytdlp_version", lambda: "2099.1")
    monkeypatch.setattr(commands_module, "ytdlp_version", lambda: "2024.1")

    msg = FakeMessage()
    await asyncio.gather(commands_module.cmd_update_ytdlp(msg), ticker())
    assert len(ticks) == 5  # loop дышал, пока pip «работал»
    last = msg.answers[-1]
    assert "2099.1" in last and "2024.1" in last
    assert "Restart bot" in last


async def test_same_version_message(monkeypatch):
    monkeypatch.setattr(commands_module, "update_ytdlp", lambda: (True, "ok"))
    monkeypatch.setattr(commands_module, "installed_ytdlp_version", lambda: "2024.1")
    monkeypatch.setattr(commands_module, "ytdlp_version", lambda: "2024.1")

    msg = FakeMessage()
    await commands_module.cmd_update_ytdlp(msg)
    assert "Сейчас: 2024.1." in msg.answers[-1]
    assert "Restart bot" not in msg.answers[-1]


def test_installed_version_reads_fresh_interpreter():
    from tiktok_obsidian.downloader import ytdlp

    version = ytdlp.installed_ytdlp_version()
    assert version and version[0].isdigit()
