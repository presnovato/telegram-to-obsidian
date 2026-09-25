"""O4: заливки в чат — вне process_lock."""
import asyncio
from types import SimpleNamespace

from tiktok_obsidian import config
from tiktok_obsidian.core.mode import CaptureMode
from tiktok_obsidian.core.telegram import ForwardOrigin
from tiktok_obsidian.handlers import forward as forward_module
from tiktok_obsidian.telegram_capture import ForwardBundle, LinkedPost, LinkStatus

U = "https://www.tiktok.com/@u/video/111"


class FakeStatus:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text):
        self.edits.append(text)


class FakeMessage:
    def __init__(self, chat_id=31):
        self.chat = type("Chat", (), {"id": chat_id})()
        self.answers = []

    async def answer(self, text):
        self.answers.append(text)
        return FakeStatus()


def _bundle():
    return ForwardBundle(
        origin=ForwardOrigin(kind="user", date=1, user_id=5, first_name="A"),
        text=f"смотри {U} круто",
    )


def _patch_dirs(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    media = vault / "Media"
    notes = vault / "NotesTg"
    media.mkdir(parents=True)
    notes.mkdir(parents=True)
    monkeypatch.setattr(config, "MEDIA_DIR", media)
    monkeypatch.setattr(config, "NOTES_DIR_TELEGRAM", notes)
    monkeypatch.setattr(config, "VAULT_ROOT", vault)


async def test_both_echo_runs_after_unlock(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(
        forward_module.mode_store, "get_mode", lambda: CaptureMode.BOTH
    )
    locked_during_echo = []

    async def fake_link(url):
        return LinkedPost(
            url=url, status=LinkStatus.DONE, process_result=SimpleNamespace()
        )

    async def fake_echo(message, result):
        locked_during_echo.append(forward_module.process_lock.locked())

    monkeypatch.setattr(forward_module, "echo_saved_to_chat", fake_echo)
    monkeypatch.setattr(
        forward_module, "_make_process_link", lambda message, mode: fake_link
    )
    msg = FakeMessage()
    await forward_module._handle_bundle(msg, _bundle())
    assert locked_during_echo == [False]


async def test_chat_mode_needs_no_lock(monkeypatch, tmp_path):
    """Лок занят тестом — chat-форвард всё равно проходит (раньше был бы дедлок)."""
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(
        forward_module.mode_store, "get_mode", lambda: CaptureMode.CHAT
    )
    calls = []

    async def fake_link(url):
        calls.append(url)
        return LinkedPost(url=url, status=LinkStatus.DONE)

    monkeypatch.setattr(
        forward_module, "_make_process_link", lambda message, mode: fake_link
    )
    msg = FakeMessage(chat_id=32)
    await forward_module.process_lock.acquire()
    try:
        await asyncio.wait_for(
            forward_module._handle_bundle(msg, _bundle()), timeout=5
        )
    finally:
        forward_module.process_lock.release()
    assert calls == [U]
