"""Отображение TweetData → PostMeta и выбор имён файлов. Без сети."""
import urllib.error

import pytest

from tiktok_obsidian.core.models import Source
from tiktok_obsidian.core.twitter import MediaKind, TweetData, TweetMedia
from tiktok_obsidian.downloader import twitter as dl_twitter
from tiktok_obsidian.downloader.twitter import _ext_for, _to_meta


def _data(*media: TweetMedia) -> TweetData:
    return TweetData(
        tweet_id="123",
        url="https://x.com/u/status/123",
        author="u",
        text="текст твита",
        upload_date="2026-08-01",
        media=list(media),
    )


def test_meta_prefixes_id_to_avoid_collision_with_tiktok():
    # ID постов X и aweme-ID TikTok — числа одного диапазона, а дедуп завязан на video_id.
    meta = _to_meta(_data(TweetMedia(MediaKind.VIDEO, "https://video.twimg.com/a.mp4")))
    assert meta.video_id == "x_123"
    assert meta.source is Source.TWITTER


def test_single_image_is_not_a_carousel():
    meta = _to_meta(_data(TweetMedia(MediaKind.PHOTO, "a.jpg")))
    assert meta.is_carousel is False


def test_several_images_enable_carousel_ocr_branch():
    meta = _to_meta(_data(TweetMedia(MediaKind.PHOTO, "a.jpg"), TweetMedia(MediaKind.PHOTO, "b.jpg")))
    assert meta.is_carousel is True


def test_video_pair_is_not_a_carousel():
    # Карусель включает OCR — гонять его по видео незачем.
    meta = _to_meta(_data(TweetMedia(MediaKind.VIDEO, "a.mp4"), TweetMedia(MediaKind.VIDEO, "b.mp4")))
    assert meta.is_carousel is False


def test_ext_from_url_ignores_query():
    assert _ext_for("https://video.twimg.com/vid/x.mp4?tag=12", default="mp4") == "mp4"
    assert _ext_for("https://pbs.twimg.com/media/a.jpg?name=orig", default="jpg") == "jpg"


def test_ext_falls_back_when_url_has_none():
    assert _ext_for("https://pbs.twimg.com/media/abc", default="jpg") == "jpg"
    assert _ext_for("https://video.twimg.com/x", default="mp4") == "mp4"


def test_meta_marks_media_presence():
    # has_media — сигнал дедупу: текстовый пост файлов в Медиафайлах не оставляет.
    assert _to_meta(_data()).has_media is False
    assert _to_meta(_data(TweetMedia(MediaKind.PHOTO, "a.jpg"))).has_media is True


# --- каскад зеркал (сеть замокана) ---

_FX_TEXT_ONLY = {"code": 200, "tweet": {"id": "123", "author": {"screen_name": "u"}, "text": "текст"}}
_VX_TEXT_ONLY = {"tweetID": "123", "user_screen_name": "u", "text": "текст", "media_extended": []}
_VX_WITH_MEDIA = {
    "tweetID": "123",
    "user_screen_name": "u",
    "text": "текст",
    "media_extended": [{"type": "image", "url": "https://pbs.twimg.com/a.jpg"}],
}


@pytest.fixture(autouse=True)
def _clear_cache():
    dl_twitter._CACHE.clear()
    yield
    dl_twitter._CACHE.clear()


def _ref():
    from tiktok_obsidian.core.twitter import tweet_ref_from_url

    return tweet_ref_from_url("https://x.com/u/status/123")


def test_fetch_returns_text_only_post_instead_of_failing(monkeypatch):
    # Твит без картинок и видео — валидный пост, а не сбой: он тоже должен доехать до vault.
    monkeypatch.setattr(
        dl_twitter,
        "_get_json",
        lambda url: _FX_TEXT_ONLY if "fxtwitter" in url else _VX_TEXT_ONLY,
    )
    data = dl_twitter._fetch(_ref())
    assert data.media == []
    assert data.text == "текст"


def test_fetch_prefers_mirror_that_found_media(monkeypatch):
    # Первое зеркало медиа не увидело, второе увидело — берём ответ с медиа.
    monkeypatch.setattr(
        dl_twitter,
        "_get_json",
        lambda url: _FX_TEXT_ONLY if "fxtwitter" in url else _VX_WITH_MEDIA,
    )
    data = dl_twitter._fetch(_ref())
    assert [m.url for m in data.media] == ["https://pbs.twimg.com/a.jpg"]


def test_fetch_still_fails_when_post_is_unavailable(monkeypatch):
    def dead(_url):
        raise OSError("timeout")

    monkeypatch.setattr(dl_twitter, "_get_json", dead)
    with pytest.raises(dl_twitter.TwitterFetchError):
        dl_twitter._fetch(_ref())


def test_download_post_of_text_only_tweet_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        dl_twitter,
        "_get_json",
        lambda url: _FX_TEXT_ONLY if "fxtwitter" in url else _VX_TEXT_ONLY,
    )
    media_dir = tmp_path / "media"
    meta = dl_twitter.download_post("https://x.com/u/status/123", media_dir, _to_meta(_data()))

    assert meta.media_files == []
    assert meta.has_media is False
    assert meta.caption == "текст"
    # Папку под медиа для текстового поста не создаём — класть в неё нечего.
    assert not media_dir.exists()


def test_download_post_is_all_or_nothing(monkeypatch, tmp_path):
    """Второй из трёх файлов не скачался → ошибка и пустая медиапапка."""
    import urllib.error

    data = _data(
        TweetMedia(MediaKind.PHOTO, "https://pbs.twimg.com/1.jpg"),
        TweetMedia(MediaKind.PHOTO, "https://pbs.twimg.com/2.jpg"),
        TweetMedia(MediaKind.PHOTO, "https://pbs.twimg.com/3.jpg"),
    )
    monkeypatch.setattr(dl_twitter, "_fetch", lambda ref: data)

    class FakeResp:
        def __init__(self, url):
            self.url = url

        def read(self):
            if self.url.endswith("/2.jpg"):
                raise urllib.error.HTTPError(self.url, 500, "boom", {}, None)
            return b"jpeg"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        dl_twitter, "open_url", lambda req, timeout=None: FakeResp(req.full_url)
    )
    media_dir = tmp_path / "media"
    with pytest.raises(dl_twitter.TwitterFetchError):
        dl_twitter.download_post(
            "https://x.com/u/status/123", media_dir, _to_meta(data)
        )
    assert list(media_dir.iterdir()) == []
