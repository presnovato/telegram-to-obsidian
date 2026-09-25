"""Разворот коротких TikTok-ссылок перед подачей в yt-dlp (без сети, через подмену open_url)."""
import pytest

from tiktok_obsidian.downloader import ytdlp


class _Resp:
    def __init__(self, url):
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _clear_cache():
    ytdlp._resolved_cache.clear()


def test_short_url_photo_becomes_video(monkeypatch):
    """Короткая ссылка на фото-пост: разворачиваем и переписываем /photo/ → /video/.

    Без разворота yt-dlp сам идёт по редиректу и падает с «Unsupported URL».
    """
    final = "https://www.tiktok.com/@phoebelilywrites/photo/7659880822371060994?_r=1"
    monkeypatch.setattr(ytdlp, "open_url", lambda req, timeout=0: _Resp(final))
    assert ytdlp._prepare_url("https://www.tiktok.com/t/ZP8nCnE61/") == (
        "https://www.tiktok.com/@phoebelilywrites/video/7659880822371060994?_r=1"
    )


@pytest.mark.parametrize(
    "short",
    [
        "https://www.tiktok.com/t/ZP8nCnE61/",
        "https://vm.tiktok.com/ZSabc123/",
        "https://vt.tiktok.com/ZSabc123",
    ],
)
def test_short_forms_are_resolved(monkeypatch, short):
    calls = []

    def fake(req, timeout=0):
        calls.append(req.full_url)
        return _Resp("https://www.tiktok.com/@u/video/1")

    monkeypatch.setattr(ytdlp, "open_url", fake)
    assert ytdlp._resolve_short_url(short) == "https://www.tiktok.com/@u/video/1"
    assert calls == [short]


def test_full_url_not_resolved(monkeypatch):
    """Полная ссылка уже пригодна — лишнего запроса в сеть не делаем."""

    def boom(*a, **kw):
        raise AssertionError("сеть не должна дёргаться на полной ссылке")

    monkeypatch.setattr(ytdlp, "open_url", boom)
    full = "https://www.tiktok.com/@user/video/123"
    assert ytdlp._resolve_short_url(full) == full


def test_resolve_failure_falls_back_to_original(monkeypatch):
    """Сеть не дала развернуть — отдаём исходную ссылку, пусть пробует сам yt-dlp."""

    def boom(*a, **kw):
        raise OSError("timeout")

    monkeypatch.setattr(ytdlp, "open_url", boom)
    short = "https://vm.tiktok.com/ZSabc123/"
    assert ytdlp._resolve_short_url(short) == short


def test_carousel_prefers_ytdlp_over_tikwm(monkeypatch):
    """Основной источник слайдов — web-данные yt-dlp; tikwm (403 на весь хост) только фолбэк."""
    monkeypatch.setattr(ytdlp, "_images_from_ytdlp_web", lambda url, vid: ["a.jpg", "b.jpg"])

    def boom(url):
        raise AssertionError("tikwm не должен вызываться, пока yt-dlp отдаёт картинки")

    monkeypatch.setattr(ytdlp, "_tikwm_fetch", boom)
    assert ytdlp._carousel_image_urls("https://x", "1") == ["a.jpg", "b.jpg"]


def test_carousel_falls_back_to_tikwm(monkeypatch):
    monkeypatch.setattr(ytdlp, "_images_from_ytdlp_web", lambda url, vid: [])
    monkeypatch.setattr(ytdlp, "_tikwm_fetch", lambda url: {"images": ["c.jpg"]})
    assert ytdlp._carousel_image_urls("https://x", "1") == ["c.jpg"]


def test_carousel_both_sources_dead(monkeypatch):
    """Оба источника молчат — пустой список, download_post превратит его в понятную ошибку."""
    monkeypatch.setattr(ytdlp, "_images_from_ytdlp_web", lambda url, vid: [])

    def dead(url):
        raise ytdlp.DownloadError("403")

    monkeypatch.setattr(ytdlp, "_tikwm_fetch", dead)
    assert ytdlp._carousel_image_urls("https://x", "1") == []
