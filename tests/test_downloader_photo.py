"""Маршрутизация одиночного фото TikTok."""
from pathlib import Path

from tiktok_obsidian.core.models import PostMeta
from tiktok_obsidian.downloader import ytdlp


def test_single_photo_uses_image_branch(monkeypatch, tmp_path: Path):
    meta = PostMeta(
        video_id="123",
        author="user",
        caption="photo",
        url="https://www.tiktok.com/@user/photo/123",
        is_photo=True,
    )
    monkeypatch.setattr(
        ytdlp,
        "_carousel_image_urls",
        lambda _url, _video_id: ["https://cdn.example/photo.jpg"],
    )
    monkeypatch.setattr(
        ytdlp,
        "_download_images",
        lambda _urls, _media_dir, _video_id: ["123_01.jpg"],
    )

    class FailIfVideoDownload:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            raise AssertionError("single photo must not use yt-dlp video download")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(ytdlp, "YoutubeDL", FailIfVideoDownload)

    result = ytdlp.download_post(meta.url, tmp_path, meta)

    assert result.is_photo is True
    assert result.is_carousel is False
    assert result.media_files == ["123_01.jpg"]


def test_classified_photo_probe_uses_web_image_data(monkeypatch):
    url = "https://www.tiktok.com/t/PHOTO123/"
    expected_id = "7673270241849265422"
    monkeypatch.setattr(
        ytdlp,
        "_resolve_short_url",
        lambda _url: _url,
    )
    monkeypatch.setattr(
        ytdlp,
        "_extract_ytdlp_web",
        lambda _url, video_id: (
            (
                {
                    "id": video_id,
                    "uploader": "user",
                    "description": "classified photo",
                    "formats": [],
                },
                ["https://cdn.example/photo.jpg"],
            )
            if video_id == expected_id
            else None
        ),
    )

    class ClassifiedPost:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            raise ytdlp._YtDownloadError(
                f"[TikTok] {expected_id}: This post may not be comfortable for some audiences. "
                "Log in for access"
            )

    monkeypatch.setattr(ytdlp, "YoutubeDL", ClassifiedPost)

    meta = ytdlp.probe_post(url)

    assert meta.video_id == expected_id
    assert meta.is_photo is True
    assert meta.is_carousel is False
