"""F2: длинные таймауты только для заливок/скачиваний, URL — по дефолту."""
from pathlib import Path

from aiogram.types import FSInputFile

from tiktok_obsidian import config, delivery
from tiktok_obsidian.core.twitter import MediaKind
from tiktok_obsidian.handlers import forward as forward_module


class FakeBot:
    def __init__(self):
        self.calls: list = []
        self.downloads: list = []

    async def __call__(self, method, request_timeout=None):
        self.calls.append(request_timeout)
        return method

    async def download(self, file_id, destination=None, timeout=30):
        self.downloads.append((file_id, timeout))
        Path(destination).write_bytes(b"x")


class FakeMessage:
    def __init__(self):
        self.bot = FakeBot()

    def answer_photo(self, src):
        return ("photo", src)

    def answer_video(self, src):
        return ("video", src)

    def answer_animation(self, src):
        return ("animation", src)

    def answer_media_group(self, media):
        return ("album", media)


async def test_file_upload_gets_long_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_TIMEOUT_S", 300)
    msg = FakeMessage()
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    report = await delivery.deliver_files(msg, [photo])
    assert report.sent == 1
    assert msg.bot.calls == [300]


async def test_url_send_uses_default_timeout(monkeypatch):
    from tiktok_obsidian.core.twitter import TweetMedia

    msg = FakeMessage()
    items = [TweetMedia(kind=MediaKind.PHOTO, url="https://pbs.twimg.com/media/1.jpg")]
    report = await delivery.deliver_urls(msg, items)
    assert report.sent == 1
    assert msg.bot.calls == [None]


async def test_gif_upload_gets_long_timeout():
    msg = FakeMessage()
    await delivery._send(
        msg, [(MediaKind.GIF, FSInputFile(__file__))], upload_timeout=300
    )
    assert msg.bot.calls == [300]


async def test_forward_download_passes_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TG_DOWNLOAD_TIMEOUT_S", 180)
    msg = FakeMessage()
    dest = tmp_path / "f.bin"
    await forward_module._make_download(msg)(file_id="abc", dest=dest)
    assert msg.bot.downloads == [("abc", 180)]
    assert dest.exists()
