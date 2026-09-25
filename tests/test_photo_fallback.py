"""F5: «No video formats found» — это фото-пост, идём фото-веткой."""
from tiktok_obsidian.core.models import PostMeta
from tiktok_obsidian.downloader import ytdlp
from tiktok_obsidian.downloader.ytdlp import DownloadError


def _video_meta() -> PostMeta:
    return PostMeta(
        video_id="123",
        author="user",
        caption="x",
        url="https://www.tiktok.com/@user/video/123",
    )


class _YtFake:
    """YoutubeDL-фейк: конструктор принимает opts, поведение задаётся полями."""

    error_text: str | None = None
    info: dict | None = None

    def __init__(self, _opts):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def extract_info(self, _url, download=False):
        if self.error_text is not None:
            raise ytdlp._YtDownloadError(self.error_text)
        return self.info


def test_probe_no_formats_error_goes_to_web_extraction(monkeypatch):
    monkeypatch.setattr(ytdlp, "_resolve_short_url", lambda u: u)
    monkeypatch.setattr(
        ytdlp,
        "_extract_ytdlp_web",
        lambda _url, _vid: ({"id": "123"}, ["https://cdn.example/p.jpg"]),
    )
    _YtFake.error_text = "[TikTok] 123: No video formats found!"
    _YtFake.info = None
    monkeypatch.setattr(ytdlp, "YoutubeDL", _YtFake)

    meta = ytdlp.probe_post("https://www.tiktok.com/@user/video/123")
    assert meta.is_photo is True


def test_probe_success_without_formats_falls_back_to_tikwm_then_error(monkeypatch):
    monkeypatch.setattr(ytdlp, "_resolve_short_url", lambda u: u)
    monkeypatch.setattr(ytdlp, "_extract_ytdlp_web", lambda _u, _v: None)
    monkeypatch.setattr(
        ytdlp, "_tikwm_fetch", lambda _u: (_ for _ in ()).throw(DownloadError("403"))
    )
    _YtFake.error_text = None
    _YtFake.info = {"id": "123", "uploader": "u", "formats": []}
    monkeypatch.setattr(ytdlp, "YoutubeDL", _YtFake)

    try:
        ytdlp.probe_post("https://www.tiktok.com/@user/video/123")
    except DownloadError as e:
        assert "похоже на фото-пост" in str(e)
        return
    raise AssertionError("expected DownloadError")


def test_download_video_branch_tries_web_photo_before_tikwm(monkeypatch, tmp_path):
    _YtFake.error_text = "[TikTok] 123: No video formats found!"
    _YtFake.info = None
    monkeypatch.setattr(ytdlp, "YoutubeDL", _YtFake)
    monkeypatch.setattr(
        ytdlp, "_images_from_ytdlp_web", lambda _u, _v: ["https://cdn.example/p.jpg"]
    )
    monkeypatch.setattr(
        ytdlp, "_download_images", lambda _u, _d, _v: ["123_01.jpg"]
    )

    result = ytdlp.download_post("https://www.tiktok.com/@user/video/123", tmp_path, _video_meta())
    assert result.is_photo is True
    assert result.media_files == ["123_01.jpg"]
