"""Маппинг данных tikwm → PostMeta (без сети, через подмену _tikwm_fetch)."""
from tiktok_obsidian.downloader import ytdlp


def test_tikwm_meta_carousel(monkeypatch):
    fake = {
        "id": "7648756493180914965",
        "title": "Как снизить расход токенов #ai",
        "duration": 0,
        "create_time": 1751328000,  # 2025-07-01 UTC
        "author": {"unique_id": "nawraskader", "nickname": "Nawras Kader"},
        "images": ["u1", "u2", "u3"],
    }
    monkeypatch.setattr(ytdlp, "_tikwm_fetch", lambda url: fake)
    m = ytdlp._tikwm_meta("https://vt.tiktok.com/xxx/")
    assert m.video_id == "7648756493180914965"
    assert m.author == "Nawras Kader"  # nickname в приоритете над unique_id
    assert m.caption == "Как снизить расход токенов #ai"
    assert m.upload_date == "2025-07-01"
    assert m.is_carousel is True


def test_tikwm_meta_video_not_carousel(monkeypatch):
    fake = {
        "id": "999",
        "title": "видео",
        "author": {"unique_id": "user"},  # без nickname → берём unique_id
        "play": "https://cdn/video.mp4",
    }
    monkeypatch.setattr(ytdlp, "_tikwm_fetch", lambda url: fake)
    m = ytdlp._tikwm_meta("https://vt.tiktok.com/yyy/")
    assert m.author == "user"
    assert m.is_carousel is False
    assert m.upload_date == ""  # нет create_time


def test_tikwm_meta_single_photo_is_photo_not_carousel(monkeypatch):
    fake = {
        "id": "123",
        "title": "одиночное фото",
        "author": {"unique_id": "user"},
        "images": ["https://cdn.example/photo.jpg"],
    }
    monkeypatch.setattr(ytdlp, "_tikwm_fetch", lambda url: fake)

    m = ytdlp._tikwm_meta("https://www.tiktok.com/@user/photo/123")

    assert m.is_photo is True
    assert m.is_carousel is False
