"""F9: единый MEDIA_PROXY для urllib, socks мимо, yt-dlp своей опцией."""
import os
import urllib.request

from tiktok_obsidian import config
from tiktok_obsidian.downloader import net, ytdlp


def test_opener_has_configured_http_proxy(monkeypatch):
    monkeypatch.setattr(config, "MEDIA_PROXY", "http://proxy:8080")
    handler = net.proxy_handler()
    assert isinstance(handler, urllib.request.ProxyHandler)
    assert handler.proxies == {"http": "http://proxy:8080", "https": "http://proxy:8080"}


def test_socks_proxy_is_ignored(monkeypatch):
    monkeypatch.setattr(config, "MEDIA_PROXY", "socks5://127.0.0.1:1080")
    assert net.proxy_handler() is None


def test_no_proxy_no_handler(monkeypatch):
    monkeypatch.setattr(config, "MEDIA_PROXY", None)
    assert net.proxy_handler() is None


def test_deprecated_http_proxy_fallback(monkeypatch):
    monkeypatch.setattr(os, "environ", {"HTTP_PROXY": "http://legacy:3128"})
    value, deprecated = config._resolve_media_proxy()
    assert (value, deprecated) == ("http://legacy:3128", True)
    monkeypatch.setattr(
        os, "environ", {"MEDIA_PROXY": "http://new:8080", "HTTP_PROXY": "http://legacy:3128"}
    )
    assert config._resolve_media_proxy() == ("http://new:8080", False)


def test_mask_hides_credentials():
    assert config._mask_proxy("http://user:pass@host:8080") == "http://***@host:8080"
    assert config._mask_proxy("socks5://127.0.0.1:1080") == "socks5://127.0.0.1:1080"
    assert config._mask_proxy(None) == "—"


def test_validate_warns_on_socks_and_logs_channels(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", "x")
    monkeypatch.setattr(config, "ALLOWED_USER_ID", 1)
    monkeypatch.setattr(config, "_VAULT_ROOT_RAW", str(tmp_path))
    monkeypatch.setattr(config, "VAULT_ROOT", tmp_path)
    monkeypatch.setattr(config, "OCR_ENABLED", False)
    monkeypatch.setattr(config, "MEDIA_PROXY", "socks5://127.0.0.1:1080")
    monkeypatch.setattr(config, "_MEDIA_PROXY_DEPRECATED", False)
    import logging

    with caplog.at_level(logging.INFO, logger="tiktok_obsidian.config"):
        config.validate()
    assert any("socks" in r.message.lower() for r in caplog.records)
    assert any("прокси:" in r.message for r in caplog.records)


def test_ytdlp_opts_contain_proxy_when_set(monkeypatch):
    captured = {}

    class FakeDL:
        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, _url, download=False):
            raise ytdlp._YtDownloadError("Unsupported URL")

    monkeypatch.setattr(config, "YTDLP_PROXY", "http://proxy:8080")
    monkeypatch.setattr(ytdlp, "YoutubeDL", FakeDL)
    monkeypatch.setattr(
        ytdlp, "_tikwm_meta", lambda _u: (_ for _ in ()).throw(ytdlp.DownloadError("x"))
    )
    try:
        ytdlp.probe_post("https://www.tiktok.com/@u/video/1")
    except ytdlp.DownloadError:
        pass
    assert captured.get("proxy") == "http://proxy:8080"


def test_ytdlp_opts_without_proxy_when_unset(monkeypatch):
    captured = {}

    class FakeDL:
        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, _url, download=False):
            raise ytdlp._YtDownloadError("Unsupported URL")

    monkeypatch.setattr(config, "YTDLP_PROXY", None)
    monkeypatch.setattr(ytdlp, "YoutubeDL", FakeDL)
    monkeypatch.setattr(
        ytdlp, "_tikwm_meta", lambda _u: (_ for _ in ()).throw(ytdlp.DownloadError("x"))
    )
    try:
        ytdlp.probe_post("https://www.tiktok.com/@u/video/1")
    except ytdlp.DownloadError:
        pass
    assert "proxy" not in captured
