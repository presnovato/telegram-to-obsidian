"""BurstBuffer: альбомы, спаривание комментариев, таймаут; capture-маршрутизация."""
import asyncio

from tiktok_obsidian import config
from tiktok_obsidian.handlers import burst as burst_module
from tiktok_obsidian.handlers import capture
from tiktok_obsidian.handlers.burst import BurstBuffer


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def test_album_grouping_and_flush_timing():
    clock = Clock()
    buf = BurstBuffer(now=clock)
    key = (1, "g1")
    g1 = buf.album_add(key, "a")
    clock.advance(1.0)
    g2 = buf.album_add(key, "b")
    assert g2 != g1
    # Старый generation уже невалиден, новый — ещё не вышел по времени.
    assert buf.album_take_if_due(key, g1, 1.5) is None
    assert buf.album_take_if_due(key, g2, 1.5) is None
    clock.advance(1.0)  # 1.0 с последнего элемента — рано
    assert buf.album_take_if_due(key, g2, 1.5) is None
    clock.advance(1.0)  # 2.0 с — пора
    assert buf.album_take_if_due(key, g2, 1.5) == ["a", "b"]
    assert buf.album_take_if_due(key, g2, 1.5) is None  # уже забрали


def test_album_keys_independent():
    buf = BurstBuffer(now=Clock())
    buf.album_add((1, "g1"), "a")
    g = buf.album_add((1, "g2"), "b")
    assert buf.album_take_if_due((1, "g2"), g, 0) == ["b"]
    assert buf.album_take_if_due((1, "g1"), 1, 0) == ["a"]


def test_comment_attached_to_all_forwards_in_window():
    clock = Clock()
    buf = BurstBuffer(now=clock)
    buf.comment_add(7, "моё впечатление", None)
    clock.advance(1.0)
    first = buf.comment_take(7, 3.0)
    clock.advance(1.0)
    second = buf.comment_take(7, 3.0)
    assert first is not None and second is not None
    assert first.text == second.text == "моё впечатление"


def test_comment_take_marks_consumed_for_timeout():
    clock = Clock()
    buf = BurstBuffer(now=clock)
    buf.comment_add(7, "x", None)
    assert buf.comment_take(7, 3.0) is not None
    clock.advance(5.0)
    assert buf.comment_take_if_expired(7, 3.0) is None  # consumed — молчим


def test_comment_timeout_falls_back_when_nothing_arrived():
    clock = Clock()
    buf = BurstBuffer(now=clock)
    buf.comment_add(7, "просто текст", None)
    clock.advance(5.0)
    assert buf.comment_take(7, 3.0) is None  # просрочен — выкинут
    buf.comment_add(7, "просто текст", None)
    clock.advance(5.0)
    pending = buf.comment_take_if_expired(7, 3.0)
    assert pending is not None and pending.text == "просто текст"


def test_flush_all_returns_and_clears():
    buf = BurstBuffer(now=Clock())
    buf.album_add((1, "g1"), "a")
    buf.album_add((1, "g1"), "b")
    buf.album_add((1, "g2"), "c")
    assert buf.flush_all() == [["a", "b"], ["c"]]
    assert buf.flush_all() == []


def test_comment_override_stored():
    from tiktok_obsidian.core.mode import CaptureMode

    buf = BurstBuffer(now=Clock())
    buf.comment_add(7, "", CaptureMode.CHAT)
    pending = buf.comment_take(7, 3.0)
    assert pending is not None and pending.override is CaptureMode.CHAT


def test_resolve_comment_uses_pending_override():
    from tiktok_obsidian.core.mode import CaptureMode
    from tiktok_obsidian.handlers.burst import PendingComment
    from tiktok_obsidian.handlers.forward import resolve_comment

    pending = PendingComment(text="круто", override=CaptureMode.CHAT, received_at=0)
    assert resolve_comment(pending, CaptureMode.VAULT) == ("круто", CaptureMode.CHAT)


def test_resolve_comment_token_only_applies_mode_without_impressions():
    from tiktok_obsidian.core.mode import CaptureMode
    from tiktok_obsidian.handlers.burst import PendingComment
    from tiktok_obsidian.handlers.forward import resolve_comment

    pending = PendingComment(text="", override=CaptureMode.VAULT, received_at=0)
    assert resolve_comment(pending, CaptureMode.CHAT) == ("", CaptureMode.VAULT)


def test_resolve_comment_no_pending_keeps_default():
    from tiktok_obsidian.core.mode import CaptureMode
    from tiktok_obsidian.handlers.forward import resolve_comment

    assert resolve_comment(None, CaptureMode.BOTH) == ("", CaptureMode.BOTH)


class FakeMessage:
    def __init__(self, text, chat_id=7):
        self.text = text
        self.caption = None
        self.chat = type("Chat", (), {"id": chat_id})()
        self.answers = []

    async def answer(self, text):
        self.answers.append(text)
        return self


async def test_link_message_unaffected_by_comment_buffer(monkeypatch):
    from tiktok_obsidian.core.mode import CaptureMode

    monkeypatch.setattr(capture.mode_store, "get_mode", lambda: CaptureMode.VAULT)
    called = []

    async def fake_vault(message, status_msg, url, comment, mode):
        called.append((url, comment))

    monkeypatch.setattr(capture, "_run_vault", fake_vault)
    msg = FakeMessage("https://www.tiktok.com/@u/video/111 круто")
    await capture.on_message(msg)
    assert len(called) == 1 and called[0][1] == "круто"
    assert burst_module.buffer.comment_take(7, 9999) is None


async def test_linkless_text_held_then_no_link_reply(monkeypatch):
    monkeypatch.setattr(config, "TG_COMMENT_WAIT_S", 0)
    msg = FakeMessage("просто мысли вслух")
    await capture.on_message(msg)
    await asyncio.sleep(0.05)  # даём задаче-таймеру отработать
    assert msg.answers == [
        "Не вижу ссылку. Пришли ссылку на публичный пост TikTok или X (Twitter)."
    ]
    assert burst_module.buffer.comment_take(7, 9999) is None
