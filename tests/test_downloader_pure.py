"""Тесты чистых хелперов downloader (без сети)."""
from tiktok_obsidian.downloader.ytdlp import _url_ext


def test_url_ext_photomode_jpeg():
    u = "https://p16.tiktokcdn-eu.com/tos/a620fb85~tplv-photomode-image.jpeg?x-expires=1&x-signature=z"
    assert _url_ext(u) == "jpeg"


def test_url_ext_png():
    assert _url_ext("https://cdn.example.com/path/pic.PNG?q=1") == "png"


def test_url_ext_fallback_when_no_ext():
    assert _url_ext("https://cdn.example.com/path/noext?q=1") == "jpg"
    assert _url_ext("https://cdn.example.com/path/noext?q=1", default="webp") == "webp"
