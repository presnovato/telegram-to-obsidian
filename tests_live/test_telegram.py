from __future__ import annotations

import asyncio

import pytest

from .urls import BY_KEY


PREFIX = "🧪 autotest:"
MAX_MESSAGES = 40


class OwnerChat:
    """Thin aiogram target that stamps each sent message and counts album items."""
    def __init__(self, bot, chat_id):
        self.bot = bot
        self.chat_id = chat_id
        self.messages = 0
        self.accepted_texts = []

    def _reserve(self, count=1):
        assert self.chat_id == _allowed_id()
        self.messages += count
        assert self.messages <= MAX_MESSAGES

    async def answer(self, text, **kwargs):
        from aiogram.methods import SendMessage
        self._reserve()
        value = text if text.startswith(PREFIX) else f"{PREFIX} {text}"
        response = await self.bot(SendMessage(chat_id=self.chat_id, text=value, **kwargs))
        self.accepted_texts.append(value)
        return response

    def answer_photo(self, photo, **kwargs):
        from aiogram.methods import SendPhoto
        self._reserve()
        kwargs.setdefault("caption", PREFIX)
        kwargs.setdefault("parse_mode", None)
        return SendPhoto(chat_id=self.chat_id, photo=photo, **kwargs)

    def answer_video(self, video, **kwargs):
        from aiogram.methods import SendVideo
        self._reserve()
        kwargs.setdefault("caption", PREFIX)
        kwargs.setdefault("parse_mode", None)
        return SendVideo(chat_id=self.chat_id, video=video, **kwargs)

    def answer_animation(self, animation, **kwargs):
        from aiogram.methods import SendAnimation
        self._reserve()
        kwargs.setdefault("caption", PREFIX)
        kwargs.setdefault("parse_mode", None)
        return SendAnimation(chat_id=self.chat_id, animation=animation, **kwargs)

    def answer_media_group(self, media, **kwargs):
        from aiogram.methods import SendMediaGroup
        self._reserve(len(media))
        if media and not media[0].caption:
            media[0].caption = PREFIX
            media[0].parse_mode = None
        return SendMediaGroup(chat_id=self.chat_id, media=media, **kwargs)


def _allowed_id():
    from tiktok_obsidian.config import ALLOWED_USER_ID
    return ALLOWED_USER_ID


@pytest.fixture(scope="module")
def live_bot():
    from aiogram import Bot
    from aiogram.client.session.aiohttp import AiohttpSession
    from aiogram.client.default import DefaultBotProperties
    from tiktok_obsidian.config import ALLOWED_USER_ID, TELEGRAM_PROXY, TELEGRAM_TOKEN
    assert TELEGRAM_TOKEN, "TELEGRAM_TOKEN is absent; cannot run Telegram live acceptance"
    loop = asyncio.new_event_loop()
    session = AiohttpSession(proxy=TELEGRAM_PROXY) if TELEGRAM_PROXY else None
    bot = Bot(token=TELEGRAM_TOKEN, session=session, default=DefaultBotProperties(parse_mode="HTML"))
    runner = loop.run_until_complete
    try:
        yield bot, OwnerChat(bot, ALLOWED_USER_ID), runner
    finally:
        runner(bot.session.close())
        loop.close()


@pytest.mark.live
def test_delivery_large_tiktok_video(live_scratchpad, live_bot):
    from tiktok_obsidian import delivery, service
    case = BY_KEY["tt_large_video"]
    if not case.url:
        pytest.skip(case.reason)
    result = service.process(case.url)
    large = [p for p in result.media_paths if p.stat().st_size > 20 * 1024 * 1024]
    if not large:
        pytest.skip("selected live TikTok URL yielded no video larger than 20 MiB")
    bot, chat, run = live_bot
    report = run(delivery.deliver_files(chat, large))
    assert report.sent == len(large)
    assert chat.messages <= MAX_MESSAGES


@pytest.mark.live
def test_delivery_tiktok_11_photo_carousel(live_scratchpad, live_bot):
    from tiktok_obsidian import delivery, service
    case = BY_KEY["tt_carousel_11"]
    if not case.url:
        pytest.skip(case.reason)
    result = service.process(case.url)
    assert len(result.media_paths) >= 11
    bot, chat, run = live_bot
    report = run(delivery.deliver_files(chat, result.media_paths))
    assert report.sent == len(result.media_paths)
    assert all(2 <= n <= 10 for n in _chunks(len(result.media_paths)))
    assert chat.messages <= MAX_MESSAGES


def _chunks(n):
    from tiktok_obsidian.delivery import _chunk_sizes
    return _chunk_sizes(n, 10)


@pytest.mark.live
def test_delivery_x_photo_video_gif(live_scratchpad, live_bot):
    from tiktok_obsidian import delivery, service
    from tiktok_obsidian.core.twitter import MediaKind
    bot, chat, run = live_bot
    attempted = 0
    for key in ("x_carousel_3", "x_video", "x_gif"):
        case = BY_KEY[key]
        if not case.url:
            continue
        plan = service.plan_chat(case.url)
        assert plan.media
        report = run(delivery.deliver_urls(chat, plan.media))
        assert report.sent == len(plan.media)
        if case.kind == "gif":
            assert any(item.kind is MediaKind.GIF for item in plan.media)
        attempted += 1
        assert chat.messages <= MAX_MESSAGES
    if not attempted:
        pytest.skip("no currently confirmed X carousel/video/GIF URLs were selected")


@pytest.mark.live
def test_verbatim_caption_and_errors_message(live_scratchpad, live_bot):
    from aiogram.exceptions import TelegramBadRequest
    from tiktok_obsidian import config
    from tiktok_obsidian.handlers.capture import _answer_verbatim
    from tiktok_obsidian.handlers.commands import cmd_errors
    bot, chat, run = live_bot

    async def caption():
        await _answer_verbatim(chat, f"{PREFIX} <3 & co")
    try:
        run(caption())
    except TelegramBadRequest as exc:
        pytest.fail(f"verbatim caption rejected: {exc}")

    log_dir = live_scratchpad / "errors_fixture"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / "bot.log"
    log.write_text("ERROR <urlopen error x>\n", encoding="utf-8")
    old = config.LOG_FILE
    config.LOG_FILE = log

    class CommandMessage(OwnerChat):
        text = "/errors"
    command = CommandMessage(bot, chat.chat_id)
    try:
        run(cmd_errors(command))
    except TelegramBadRequest as exc:
        pytest.fail(f"/errors HTML rejected: {exc}")
    finally:
        config.LOG_FILE = old
    assert chat.messages <= MAX_MESSAGES
