"""Доставка в чат: без сети и без Telegram — на фейковом Message.

Проверяем то, ради чего слой вообще переносился из twitter-to-telegram:
прямые ссылки вместо байт, GIF мимо альбома, честный отчёт о непролезшем.
"""
from pathlib import Path

import pytest
from aiogram.exceptions import TelegramAPIError

from tiktok_obsidian import config, delivery
from tiktok_obsidian.core.twitter import MediaKind, TweetMedia


class FakeMessage:
    """Собирает вызовы вместо обращения к Telegram."""

    def __init__(self, fail_on_url: bool = False):
        self.fail_on_url = fail_on_url
        self.photos: list = []
        self.videos: list = []
        self.animations: list = []
        self.albums: list[list] = []
        self.bot = FakeBot(self)

    def _guard(self, src):
        # Отказ Telegram воспроизводим только для отправки по ссылке (src — str).
        if self.fail_on_url and isinstance(src, str):
            raise TelegramAPIError(method=None, message="wrong file identifier")

    # aiogram 3.29: answer_* возвращают объект метода, исполнение — через bot().
    def answer_photo(self, src):
        return ("photo", src)

    def answer_video(self, src):
        return ("video", src)

    def answer_animation(self, src):
        return ("animation", src)

    def answer_media_group(self, media):
        return ("album", media)


class FakeBot:
    def __init__(self, msg: FakeMessage):
        self.msg = msg
        self.calls: list = []  # (method, request_timeout)

    async def __call__(self, method, request_timeout=None):
        self.calls.append((method, request_timeout))
        kind, payload = method
        if kind == "photo":
            self.msg._guard(payload)
            self.msg.photos.append(payload)
        elif kind == "video":
            self.msg._guard(payload)
            self.msg.videos.append(payload)
        elif kind == "animation":
            self.msg._guard(payload)
            self.msg.animations.append(payload)
        elif kind == "album":
            for item in payload:
                self.msg._guard(item.media)
            self.msg.albums.append(payload)
        return self.msg


def _photo(n: int = 1) -> TweetMedia:
    return TweetMedia(kind=MediaKind.PHOTO, url=f"https://pbs.twimg.com/media/{n}.jpg")


def test_filename_takes_extension_from_url():
    item = TweetMedia(kind=MediaKind.VIDEO, url="https://video.twimg.com/vid/x.mp4?tag=12")
    assert delivery._filename(item, 1) == "01.mp4"


def test_filename_falls_back_by_kind():
    assert delivery._filename(TweetMedia(MediaKind.PHOTO, "https://pbs.twimg.com/a"), 3) == "03.jpg"
    assert delivery._filename(TweetMedia(MediaKind.GIF, "https://video.twimg.com/g"), 4) == "04.mp4"


def test_chunk_sizes_never_leave_a_single():
    for n in range(1, 41):
        sizes = delivery._chunk_sizes(n, 10)
        assert sum(sizes) == n
        if n == 1:
            assert sizes == [1]
        else:
            assert all(2 <= s <= 10 for s in sizes), (n, sizes)
    assert delivery._chunk_sizes(11, 10) == [6, 5]
    assert delivery._chunk_sizes(21, 10) == [7, 7, 7]
    assert delivery._chunk_sizes(0, 10) == []


def test_chunked_preserves_order():
    items = [(MediaKind.PHOTO, f"u{i}") for i in range(11)]
    chunks = delivery._chunked(items, 10)
    assert [len(c) for c in chunks] == [6, 5]
    assert [src for c in chunks for _, src in c] == [f"u{i}" for i in range(11)]


async def test_single_photo_goes_by_direct_url_without_download():
    msg = FakeMessage()
    report = await delivery.deliver_urls(msg, [_photo()])
    assert report.sent == 1
    assert report.downloaded is False  # байты через домашний канал не гнали
    assert msg.photos == ["https://pbs.twimg.com/media/1.jpg"]


async def test_carousel_goes_as_one_album():
    msg = FakeMessage()
    report = await delivery.deliver_urls(msg, [_photo(1), _photo(2), _photo(3)])
    assert report.sent == 3
    assert len(msg.albums) == 1 and len(msg.albums[0]) == 3


async def test_gif_never_lands_in_album():
    msg = FakeMessage()
    gif = TweetMedia(kind=MediaKind.GIF, url="https://video.twimg.com/tweet_video/g.mp4")
    report = await delivery.deliver_urls(msg, [_photo(1), _photo(2), gif])
    assert report.sent == 3
    assert len(msg.albums[0]) == 2  # только фото
    assert msg.animations == ["https://video.twimg.com/tweet_video/g.mp4"]


async def test_empty_media_is_a_noop():
    msg = FakeMessage()
    report = await delivery.deliver_urls(msg, [])
    assert report.sent == 0 and not msg.albums


async def test_eleven_photos_go_as_six_plus_five():
    msg = FakeMessage()
    report = await delivery.deliver_urls(msg, [_photo(n) for n in range(1, 12)])
    assert report.sent == 11
    assert [len(a) for a in msg.albums] == [6, 5]


async def test_url_fallback_uploads_only_undelivered(monkeypatch, tmp_path):
    """Отказ на втором куске: фолбэк качает и заливает только его предметы."""
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
    (tmp_path / "media").mkdir()
    downloaded: list = []

    def fake_download(item, dest: Path):
        downloaded.append(item.url)
        dest.write_bytes(b"jpeg")
        return dest

    monkeypatch.setattr(delivery, "_download", fake_download)

    real_bot_call = FakeBot.__call__

    async def flaky_call(self, method, request_timeout=None):
        kind, payload = method
        urls = [
            item.media for item in payload if isinstance(getattr(item, "media", None), str)
        ]
        if kind == "album" and any(u.endswith("/7.jpg") for u in urls):
            raise TelegramAPIError(method=None, message="bad group")
        return await real_bot_call(self, method, request_timeout)

    monkeypatch.setattr(FakeBot, "__call__", flaky_call)
    msg = FakeMessage()
    report = await delivery.deliver_urls(msg, [_photo(n) for n in range(1, 13)])
    assert report.downloaded is True
    assert report.sent == 6  # второй кусок долетел файлами
    assert downloaded == [f"https://pbs.twimg.com/media/{n}.jpg" for n in range(7, 13)]


async def test_api_refusal_falls_back_to_download(monkeypatch, tmp_path):
    msg = FakeMessage(fail_on_url=True)
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
    (tmp_path / "media").mkdir()

    def fake_download(item, dest: Path):
        dest.write_bytes(b"jpeg")
        return dest

    monkeypatch.setattr(delivery, "_download", fake_download)
    report = await delivery.deliver_urls(msg, [_photo()])
    assert report.downloaded is True
    assert report.sent == 1
    assert not report.skipped


async def test_oversized_item_is_reported_not_swallowed(monkeypatch, tmp_path):
    msg = FakeMessage(fail_on_url=True)
    monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
    (tmp_path / "media").mkdir()
    # None от _download = «не влез в лимит upload».
    monkeypatch.setattr(delivery, "_download", lambda item, dest: None)

    report = await delivery.deliver_urls(msg, [_photo()])
    assert report.sent == 0
    assert [i.url for i in report.skipped] == ["https://pbs.twimg.com/media/1.jpg"]


async def test_deliver_files_types_by_extension(tmp_path):
    msg = FakeMessage()
    photo = tmp_path / "a.jpg"
    video = tmp_path / "b.mp4"
    photo.write_bytes(b"x")
    video.write_bytes(b"x")

    report = await delivery.deliver_files(msg, [photo, video])
    assert report.sent == 2
    kinds = [type(item).__name__ for item in msg.albums[0]]
    assert kinds == ["InputMediaPhoto", "InputMediaVideo"]


def test_download_stops_when_content_length_exceeds_limit(monkeypatch, tmp_path):
    """Заявленный размер больше лимита — не качаем ни байта."""
    monkeypatch.setattr(config, "UPLOAD_LIMIT_MB", 1)

    class FakeResp:
        headers = {"content-length": str(5 * 1024 * 1024)}

        def read(self, _n):  # pragma: no cover — сюда дойти не должны
            raise AssertionError("не должны были начать чтение")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(delivery, "open_url", lambda req, timeout: FakeResp())
    item = TweetMedia(kind=MediaKind.VIDEO, url="https://video.twimg.com/big.mp4")
    assert delivery._download(item, tmp_path / "big.mp4") is None
