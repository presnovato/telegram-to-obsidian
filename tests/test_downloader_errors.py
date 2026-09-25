"""F7: контракт ошибок — всё становится DownloadError; ANSI чистятся."""
import urllib.error

import pytest

from tiktok_obsidian.core.text import strip_ansi
from tiktok_obsidian.downloader import ytdlp
from tiktok_obsidian.downloader.ytdlp import DownloadError


class _HtmlResp:
    def read(self):
        return "<html>Attention Required! | Cloudflare</html>".encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_tikwm_html_body_becomes_download_error(monkeypatch):
    monkeypatch.setattr(
        ytdlp, "open_url", lambda req, timeout=None: _HtmlResp()
    )
    with pytest.raises(DownloadError, match="не JSON"):
        ytdlp._tikwm_fetch("https://www.tiktok.com/@u/video/1")


class _FailResp:
    headers = {"Content-Type": "image/jpeg"}

    def read(self):
        raise urllib.error.HTTPError(
            "https://cdn.example/1.jpg", 500, "boom", {}, None
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_download_images_wraps_http_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ytdlp, "open_url", lambda req, timeout=None: _FailResp()
    )
    with pytest.raises(DownloadError, match="слайд 1/2"):
        ytdlp._download_images(
            ["https://cdn.example/1.jpg", "https://cdn.example/2.jpg"],
            tmp_path,
            "123",
        )


def test_strip_ansi_on_real_log_sample():
    assert (
        strip_ansi("\x1b[0;31mERROR:\x1b[0m [TikTok] 1: x")
        == "ERROR: [TikTok] 1: x"
    )
    assert strip_ansi("plain") == "plain"


def test_ytdlp_opts_disable_color():
    import inspect

    src = inspect.getsource(ytdlp)
    assert src.count('"color": "no_color"') >= 3
