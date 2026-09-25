"""F8: преходящие сбои повторяются один раз; остальные — сразу наружу."""

import pytest

from tiktok_obsidian import downloader
from tiktok_obsidian.core.models import PostMeta
from tiktok_obsidian.core.text import is_transient
from tiktok_obsidian.downloader import ytdlp
from tiktok_obsidian.downloader.ytdlp import DownloadError


def _meta() -> PostMeta:
    return PostMeta(video_id="1", author="u", caption="", url="https://x")


def test_transient_probe_retried_once(monkeypatch):
    calls = []

    def flaky(url):
        calls.append(url)
        if len(calls) == 1:
            raise DownloadError("curl: (35) SSL connect error")
        return _meta()

    monkeypatch.setattr(downloader, "_ytdlp_probe", flaky)
    monkeypatch.setattr(downloader, "_RETRY_DELAY_S", 0)
    result = downloader.probe_post("https://www.tiktok.com/@u/video/1")
    assert result.video_id == "1"
    assert len(calls) == 2


def test_non_transient_probe_not_retried(monkeypatch):
    calls = []

    def nope(url):
        calls.append(url)
        raise DownloadError("This post may not be comfortable for some audiences")

    monkeypatch.setattr(downloader, "_ytdlp_probe", nope)
    monkeypatch.setattr(downloader, "_RETRY_DELAY_S", 0)
    with pytest.raises(DownloadError):
        downloader.probe_post("https://www.tiktok.com/@u/video/1")
    assert len(calls) == 1


def test_classifier_table():
    transient = [
        "SSL: UNEXPECTED_EOF_WHILE_READING",
        "curl: (35) SSL connect error",
        "curl: (28) Connection timed out",
        "SSL handshake timed out",
        "Unexpected response from webpage request",
        "Connection reset by peer",
        "TimeoutError: timed out",
    ]
    for text in transient:
        assert is_transient(text), text
    stable = [
        "This post may not be comfortable for some audiences",
        "Unsupported URL",
        "Private video",
        "This post is deleted",
        "Login required",
        "tikwm: не JSON",
    ]
    for text in stable:
        assert not is_transient(text), text


def test_short_url_resolve_retried_once(monkeypatch):
    calls = []

    class FakeResp:
        url = "https://www.tiktok.com/@u/video/1"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def flaky(req, timeout=None):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise OSError("handshake timeout")
        return FakeResp()

    monkeypatch.setattr(ytdlp, "open_url", flaky)
    ytdlp._resolved_cache.clear()
    out = ytdlp._resolve_short_url("https://vt.tiktok.com/ZS123abc/")
    assert out == "https://www.tiktok.com/@u/video/1"
    assert len(calls) == 2
