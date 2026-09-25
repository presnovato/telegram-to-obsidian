"""post_id / url / author для всех типов forward_origin + альбомы (§4, §10)."""
import hashlib
from datetime import datetime, timezone

from aiogram.types import Chat, MessageOriginChannel, User

from tiktok_obsidian.core.telegram import ForwardOrigin, origin_identity
from tiktok_obsidian.handlers.forward import map_origin

DATE = int(datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc).timestamp())


def test_channel_public():
    origin = ForwardOrigin(
        kind="channel", date=DATE, message_id=842,
        chat_id=-1001001234567, chat_title="Канал про дизайн", chat_username="design_ch",
    )
    ident = origin_identity(origin)
    assert ident.post_id == "tg_1001001234567_842"
    assert ident.url == "https://t.me/design_ch/842"
    assert ident.author == "Канал про дизайн (@design_ch)"
    assert ident.upload_date == "2026-09-20"


def test_channel_private():
    origin = ForwardOrigin(
        kind="channel", date=DATE, message_id=15,
        chat_id=-1001001234567, chat_title="Приватка",
    )
    ident = origin_identity(origin)
    assert ident.post_id == "tg_1001001234567_15"
    assert ident.url == "https://t.me/c/1001234567/15"
    assert ident.author == "Приватка"


def test_channel_album_uses_smallest_message_id():
    origin = ForwardOrigin(
        kind="channel", date=DATE, message_id=900,
        chat_id=-1001001234567, chat_title="Канал", chat_username="ch",
    )
    ident = origin_identity(origin, album_min_message_id=897)
    assert ident.post_id == "tg_1001001234567_897"
    assert ident.url == "https://t.me/ch/897"


def test_chat_anonymous_admin():
    origin = ForwardOrigin(kind="chat", date=DATE, chat_id=-555, chat_title="Группа")
    ident = origin_identity(origin)
    assert ident.post_id == f"tg_555_{DATE}"
    assert ident.url == ""
    assert ident.author == "Группа"
    assert ident.upload_date == "2026-09-20"


def test_user():
    origin = ForwardOrigin(
        kind="user", date=DATE, user_id=123,
        first_name="Иван", last_name="Петров", username="ivan",
    )
    ident = origin_identity(origin)
    assert ident.post_id == f"tg_u123_{DATE}"
    assert ident.url == ""
    assert ident.author == "Иван Петров (@ivan)"


def test_user_without_username():
    origin = ForwardOrigin(kind="user", date=DATE, user_id=7, first_name="Аня")
    ident = origin_identity(origin)
    assert ident.post_id == f"tg_u7_{DATE}"
    assert ident.author == "Аня"


def test_hidden_user():
    origin = ForwardOrigin(kind="hidden", date=DATE, sender_name="Скрытный")
    ident = origin_identity(origin)
    expected = hashlib.sha1(f"Скрытный{DATE}".encode("utf-8")).hexdigest()[:12]
    assert ident.post_id == f"tg_h{expected}"
    assert ident.author == "Скрытный"
    assert ident.url == ""


def test_map_origin_channel_aiogram():
    chat = Chat(id=-1001001234567, type="channel", title="Канал", username="design_ch")
    aiogram_origin = MessageOriginChannel(
        type="channel",
        date=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc),
        chat=chat,
        message_id=842,
    )
    origin = map_origin(aiogram_origin)
    assert origin.kind == "channel"
    assert origin.message_id == 842
    assert origin.chat_username == "design_ch"
    ident = origin_identity(origin)
    assert ident.url == "https://t.me/design_ch/842"


def test_map_origin_user_aiogram():
    from aiogram.types import MessageOriginUser

    user = User(id=123, is_bot=False, first_name="Иван", username="ivan")
    aiogram_origin = MessageOriginUser(
        type="user",
        date=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc),
        sender_user=user,
    )
    origin = map_origin(aiogram_origin)
    assert origin.kind == "user"
    assert origin_identity(origin).author == "Иван (@ivan)"
