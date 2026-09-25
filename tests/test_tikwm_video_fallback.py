from __future__ import annotations

import logging

import pytest

from tiktok_obsidian.core.models import PostMeta
from tiktok_obsidian.downloader import ytdlp
from tiktok_obsidian.downloader.ytdlp import DownloadError


URL = "https://www.tiktok.com/@user/video/7684014525762342164"


class _YtFake:
    def __init__(self, _opts):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def extract_info(self, _url, download=False):
        raise ytdlp._YtDownloadError("[TikTok] 7684014525762342164: No video formats found!")


class _Response:
    def __init__(self, body: bytes):
        self.body = body
        self.offset = 0
        self.headers = {"content-length": str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        if size < 0:
            size = len(self.body) - self.offset
        result = self.body[self.offset : self.offset + size]
        self.offset += len(result)
        return result


def _video_meta() -> PostMeta:
    return PostMeta(
        video_id="7684014525762342164",
        author="user",
        caption="long video",
        url=URL,
        duration=508,
    )


def _setup(monkeypatch, data):
    monkeypatch.setattr(ytdlp, "YoutubeDL", _YtFake)
    monkeypatch.setattr(ytdlp, "_resolve_short_url", lambda url: url)
    monkeypatch.setattr(ytdlp, "_images_from_ytdlp_web", lambda _url, _video_id: [])
    monkeypatch.setattr(ytdlp, "_tikwm_fetch", lambda _url: data)


def test_no_formats_downloads_hdplay_and_logs_route(monkeypatch, tmp_path, caplog):
    _setup(monkeypatch, {"hdplay": "https://cdn.example/hd.mp4"})
    monkeypatch.setattr(ytdlp, "open_url", lambda *_args, **_kwargs: _Response(b"hd-video"))

    with caplog.at_level(logging.INFO, logger=ytdlp.__name__):
        result = ytdlp.download_post(URL, tmp_path, _video_meta())

    assert result.media_files == ["7684014525762342164.mp4"]
    assert (tmp_path / result.media_files[0]).read_bytes() == b"hd-video"
    assert result.watermarked is False
    assert "видео через tikwm (hdplay)" in caplog.text


def test_hdplay_failure_falls_back_to_play(monkeypatch, tmp_path):
    _setup(monkeypatch, {
        "hdplay": "https://cdn.example/hd.mp4",
        "play": "https://cdn.example/play.mp4",
    })
    requested = []

    def open_fake(req, *_args, **_kwargs):
        requested.append(req.full_url)
        if req.full_url.endswith("hd.mp4"):
            raise OSError("hd unavailable")
        return _Response(b"play-video")

    monkeypatch.setattr(ytdlp, "open_url", open_fake)
    result = ytdlp.download_post(URL, tmp_path, _video_meta())

    assert requested == ["https://cdn.example/hd.mp4", "https://cdn.example/play.mp4"]
    assert result.media_files == ["7684014525762342164.mp4"]
    assert (tmp_path / result.media_files[0]).read_bytes() == b"play-video"
    assert result.watermarked is False


def test_only_wmplay_sets_watermarked(monkeypatch, tmp_path):
    _setup(monkeypatch, {"wmplay": "https://cdn.example/wm.mp4"})
    monkeypatch.setattr(ytdlp, "open_url", lambda *_args, **_kwargs: _Response(b"wm-video"))

    result = ytdlp.download_post(URL, tmp_path, _video_meta())

    assert result.media_files == ["7684014525762342164.mp4"]
    assert result.watermarked is True


def test_tikwm_failure_reports_both_yt_dlp_and_tikwm_causes(monkeypatch, tmp_path):
    monkeypatch.setattr(ytdlp, "YoutubeDL", _YtFake)
    monkeypatch.setattr(ytdlp, "_resolve_short_url", lambda url: url)
    monkeypatch.setattr(ytdlp, "_images_from_ytdlp_web", lambda _url, _video_id: [])
    monkeypatch.setattr(
        ytdlp, "_tikwm_fetch",
        lambda _url: (_ for _ in ()).throw(DownloadError("TikWM API unavailable")),
    )

    with pytest.raises(DownloadError) as exc_info:
        ytdlp.download_post(URL, tmp_path, _video_meta())

    message = str(exc_info.value)
    assert "No video formats found" in message
    assert "TikWM API unavailable" in message


def test_chat_limit_skips_oversized_hdplay_but_vault_has_no_limit(monkeypatch, tmp_path):
    data = {
        "hdplay": "https://cdn.example/hd.mp4",
        "play": "https://cdn.example/play.mp4",
    }
    _setup(monkeypatch, data)
    requested = []

    def open_fake(req, *_args, **_kwargs):
        requested.append(req.full_url)
        return _Response(b"12345" if req.full_url.endswith("hd.mp4") else b"123")

    monkeypatch.setattr(ytdlp, "open_url", open_fake)
    chat_dir = tmp_path / "chat"
    chat_dir.mkdir()
    chat_result = ytdlp.download_post(URL, chat_dir, _video_meta(), max_bytes=4)
    assert chat_result.media_files == ["7684014525762342164.mp4"]
    assert (chat_dir / chat_result.media_files[0]).read_bytes() == b"123"
    assert requested == ["https://cdn.example/hd.mp4", "https://cdn.example/play.mp4"]

    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    vault_result = ytdlp.download_post(URL, vault_dir, _video_meta())
    assert (vault_dir / vault_result.media_files[0]).read_bytes() == b"12345"


def test_probe_tikwm_video_keeps_duration_when_no_images(monkeypatch):
    monkeypatch.setattr(ytdlp, "YoutubeDL", _YtFake)
    monkeypatch.setattr(ytdlp, "_resolve_short_url", lambda url: url)
    monkeypatch.setattr(ytdlp, "_extract_ytdlp_web", lambda _url, _video_id: None)
    monkeypatch.setattr(ytdlp, "_tikwm_fetch", lambda _url: {
        "id": "7684014525762342164",
        "title": "long video",
        "duration": 508,
        "author": {"unique_id": "user"},
        "play": "https://cdn.example/video.mp4",
    })

    result = ytdlp.probe_post(URL)

    assert result.video_id == "7684014525762342164"
    assert result.duration == 508
    assert result.is_photo is False
    assert result.is_carousel is False
