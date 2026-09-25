"""O1: link-only форвард с несколькими ссылками — каждая в capture-поток."""
from tiktok_obsidian import config
from tiktok_obsidian.core.telegram import ForwardOrigin
from tiktok_obsidian.telegram_capture import ForwardBundle
from tiktok_obsidian.handlers import forward as forward_module

U1 = "https://www.tiktok.com/@user/video/111"
U2 = "https://x.com/user/status/222"
U3 = "https://www.tiktok.com/@user/video/333"


class FakeStatus:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text):
        self.edits.append(text)


class FakeMessage:
    def __init__(self, chat_id=21):
        self.chat = type("Chat", (), {"id": chat_id})()
        self.answers = []

    async def answer(self, text):
        self.answers.append(text)
        return FakeStatus()


def _bundle(text):
    return ForwardBundle(
        origin=ForwardOrigin(kind="user", date=1, user_id=5, first_name="A"),
        text=text,
    )


async def test_link_only_processes_every_link(monkeypatch):
    calls = []

    async def fake_vault(message, status_msg, url, comment, mode):
        calls.append(url)

    monkeypatch.setattr(forward_module, "_run_vault", fake_vault)
    msg = FakeMessage()
    await forward_module._handle_bundle(msg, _bundle(f"{U1} {U2}"))
    assert calls == [U1, U2]
    assert msg.answers.count("⏳ Обрабатываю…") == 2


async def test_link_only_overflow_reported(monkeypatch):
    calls = []

    async def fake_vault(message, status_msg, url, comment, mode):
        calls.append(url)

    monkeypatch.setattr(forward_module, "_run_vault", fake_vault)
    monkeypatch.setattr(config, "TG_MAX_LINKED_POSTS", 2)
    msg = FakeMessage(chat_id=22)
    await forward_module._handle_bundle(msg, _bundle(f"{U1} {U2} {U3}"))
    assert calls == [U1, U2]
    assert any("сверх лимита" in a for a in msg.answers)
