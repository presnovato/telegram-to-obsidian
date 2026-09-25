"""F1: чужой текст в чат — дословно, без HTML-парсинга; эхо не роняет результат."""
import re
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest

from tiktok_obsidian import config
from tiktok_obsidian.core.text import truncate_utf16, truncate_utf16_tail, utf16_len
from tiktok_obsidian.handlers import capture as capture_module
from tiktok_obsidian.handlers import commands as commands_module
from tiktok_obsidian.handlers import forward as forward_module
from tiktok_obsidian.service import Status


def test_truncate_ascii():
    assert truncate_utf16("abc", 10) == "abc"
    assert truncate_utf16("abcdef", 4) == "abcd"


def test_truncate_emoji_stays_in_limit_and_decodes():
    text = "😀" * 3000  # 6000 единиц UTF-16
    out = truncate_utf16(text, 4096)
    assert len(out.encode("utf-16-le")) // 2 <= 4096
    assert out == "😀" * 2048


def test_truncate_steps_back_from_split_surrogate():
    assert truncate_utf16("a😀", 2) == "a"


def test_truncate_negative_limit():
    try:
        truncate_utf16("x", -1)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_truncate_utf16_tail_keeps_newest():
    assert truncate_utf16_tail("abcdef", 4) == "cdef"
    assert truncate_utf16_tail("abc", 10) == "abc"
    assert truncate_utf16_tail("anything", 0) == ""
    try:
        truncate_utf16_tail("x", -1)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_truncate_utf16_tail_steps_over_split_surrogate():
    assert truncate_utf16_tail("😀a", 2) == "a"
    out = truncate_utf16_tail("😀" * 3000, 4096)
    assert len(out.encode("utf-16-le")) // 2 <= 4096
    assert out == "😀" * 2048


class FakeMessage:
    def __init__(self, fail_text=""):
        self.calls = []
        self.fail_text = fail_text

    async def answer(self, text, **kwargs):
        if self.fail_text and self.fail_text in text:
            raise TelegramBadRequest("sendMessage", "can't parse entities")
        self.calls.append((text, kwargs))
        return text


def _echo_result(caption):
    return SimpleNamespace(
        meta=SimpleNamespace(caption=caption), media_paths=[], ocr_failed=False
    )


async def test_caption_sent_verbatim_without_parse_mode():
    msg = FakeMessage()
    await capture_module.echo_saved_to_chat(msg, _echo_result("i <3 this & <0.11"))
    assert msg.calls == [("i <3 this & <0.11", {"parse_mode": None})]


async def test_caption_failure_does_not_raise_and_notifies():
    msg = FakeMessage(fail_text="<3")
    await capture_module.echo_saved_to_chat(msg, _echo_result("i <3 boom"))
    assert any("Подпись не отправилась" in text for text, _ in msg.calls)


async def test_cmd_errors_keeps_newest_and_entities_intact(monkeypatch, tmp_path):
    """R2: 15 строк × ~300 символов — новейшая на месте, старейшая выкинута,
    сущностей пополам нет, итог в лимите Telegram."""
    lines = [
        f"2026-09-01 ERROR line {i:02d} " + "<tag>" * 30 + "x" * 100 for i in range(15)
    ]
    log = tmp_path / "bot.log"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(config, "LOG_FILE", log)
    msg = FakeMessage()
    await commands_module.cmd_errors(msg)
    (sent, _kwargs) = msg.calls[0]
    assert "line 14" in sent
    assert "line 00" not in sent
    assert re.search(r"&(?!(amp|lt|gt|quot|#\d+);)", sent) is None
    assert utf16_len(sent) <= 4096


async def test_cmd_errors_escapes_log_lines(monkeypatch, tmp_path):
    log = tmp_path / "bot.log"
    log.write_text("2026-09-01 ERROR <urlopen error x> boom\n", encoding="utf-8")
    monkeypatch.setattr(config, "LOG_FILE", log)
    msg = FakeMessage()
    await commands_module.cmd_errors(msg)
    (sent, _kwargs) = msg.calls[0]
    assert "&lt;urlopen" in sent
    assert "<urlopen" not in sent


async def test_linked_post_done_when_echo_raises(monkeypatch):
    from tiktok_obsidian.core.mode import CaptureMode

    def fake_process(url, comment=""):
        return SimpleNamespace(
            status=Status.DONE, note_path="/vault/note.md", meta=None, media_paths=[]
        )

    async def boom_echo(message, result):
        raise TelegramBadRequest("sendMessage", "can't parse entities")

    monkeypatch.setattr(forward_module.service, "process", fake_process)
    monkeypatch.setattr(forward_module, "echo_saved_to_chat", boom_echo)
    msg = FakeMessage()
    link = await forward_module._make_process_link(msg, CaptureMode.BOTH)(
        "https://x.com/u/status/1"
    )
    assert link.status is forward_module.LinkStatus.DONE
