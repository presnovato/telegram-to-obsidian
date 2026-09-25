from __future__ import annotations

import re
import time

import pytest

from .urls import CASES, pytest_params


@pytest.mark.live
@pytest.mark.parametrize("case", pytest_params())
def test_service_pipeline_live(case, caplog, monkeypatch):
    from tiktok_obsidian import config, service
    from tiktok_obsidian.core.models import Source

    route = []
    if case.source == "TikTok":
        from tiktok_obsidian.downloader import ytdlp
        for name, label in (("_extract_ytdlp_web", "yt-dlp web"), ("_tikwm_meta", "tikwm metadata"), ("_tikwm_fetch", "TikWM")):
            original = getattr(ytdlp, name)
            def record(*args, _original=original, _label=label, **kwargs):
                route.append(_label)
                return _original(*args, **kwargs)
            monkeypatch.setattr(ytdlp, name, record)

    started = time.monotonic()
    with caplog.at_level("INFO"):
        result = service.process(case.url)
    elapsed = time.monotonic() - started
    assert result.status is service.Status.DONE
    assert result.note_path.is_file()
    expected_source = Source.TWITTER if case.source == "X" else Source.TIKTOK
    assert result.meta.source is expected_source
    assert result.note_path.parent == config.notes_dir_for(expected_source)
    assert not any(char in result.note_path.stem for char in "#^[]")

    note = result.note_path.read_text(encoding="utf-8")
    assert re.search(r"(?m)^post_id:\s*", note)
    assert re.search(r"(?m)^author:\s*", note)
    assert re.search(r"(?m)^url:\s*", note)
    embeds = re.findall(r"!\[\[([^\]]+)\]\]", note)
    assert len(embeds) == len(result.media_paths)
    for name, path in zip(embeds, result.media_paths):
        assert name == path.name
        assert path.is_file()
        assert path.parent == config.MEDIA_DIR
        assert path.name.startswith(result.meta.video_id)
        assert re.fullmatch(re.escape(result.meta.video_id) + r"(?:_\d+)?\.[^.]+", path.name)

    if case.kind == "photo":
        assert len(result.media_paths) == 1
    if case.kind == "large_video":
        assert result.meta.duration is not None and result.meta.duration >= 500
        assert not result.meta.is_photo and not result.meta.is_carousel
        assert len(result.media_paths) == 1
        assert result.media_paths[0].suffix.lower() == ".mp4"
        assert result.media_paths[0].stat().st_size > 20 * 1024 * 1024
        assert "видео через tikwm (hdplay)" in caplog.text
    if case.kind == "photo" and case.source == "TikTok":
        assert result.meta.is_photo or result.meta.is_carousel
        assert result.media_paths
        assert all(path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"} for path in result.media_paths)
    if case.kind == "text":
        assert result.media_paths == []
        assert embeds == []
    if case.kind == "carousel":
        assert result.meta.is_carousel
        assert len(result.media_paths) >= 3
        if result.meta.source is Source.TWITTER:
            from tiktok_obsidian.downloader import fetch_media
            reported = sum(item.kind.value == "photo" for item in fetch_media(case.url))
            assert len(result.media_paths) == reported
        else:
            from tiktok_obsidian.downloader import probe_post
            from tiktok_obsidian.downloader.ytdlp import _carousel_image_urls
            probed = probe_post(case.url)
            reported = len(_carousel_image_urls(case.url, probed.video_id))
            assert len(result.media_paths) == reported
            assert all(path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"} for path in result.media_paths)
            if _tesseract_available():
                assert "Транскрипт" in note

    before = sorted(str(p.relative_to(config.VAULT_ROOT)) for p in config.VAULT_ROOT.rglob("*") if p.is_file())
    duplicate = service.process(case.url)
    after = sorted(str(p.relative_to(config.VAULT_ROOT)) for p in config.VAULT_ROOT.rglob("*") if p.is_file())
    assert duplicate.status is service.Status.DUPLICATE
    assert duplicate.note_path == result.note_path
    assert before == after
    path_used = list(dict.fromkeys(route)) or (_paths(caplog.text) if case.source == "X" else (["yt-dlp"] if not result.meta.is_photo else ["photo route not logged"]))
    print(f"LIVE {case.key}: seconds={elapsed:.2f}; downloader={'/'.join(path_used) or 'unidentified'}; id={result.meta.video_id}")


def _paths(log_text: str) -> list[str]:
    found = []
    for marker in ("yt-dlp", "ytdlp", "tikwm", "web"):
        if marker in log_text.lower():
            found.append(marker)
    return list(dict.fromkeys(found))


def _tesseract_available() -> bool:
    try:
        import pytesseract
        from tiktok_obsidian import config
        if config.TESSERACT_CMD:
            pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_CMD
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


@pytest.mark.live
def test_trailing_url_punctuation_creates_no_impressions(live_scratchpad):
    from tiktok_obsidian import service
    from tiktok_obsidian.core.urls import extract_post_url

    url = next(case.url for case in CASES if case.key == "tt_single_photo" and case.url)
    for wrapped in (f"«{url}»", f"{url}!"):
        extracted = extract_post_url(wrapped)
        assert extracted.url == url
        result = service.process(extracted.url)
        assert "## Впечатления" not in result.note_path.read_text(encoding="utf-8")


@pytest.mark.live
def test_chat_plan_contract(live_scratchpad):
    from tiktok_obsidian import service

    for key in ("tt_short_video", "tt_single_photo", "x_single_photo", "x_video"):
        case = next(item for item in CASES if item.key == key)
        if case.url is None:
            continue
        plan = service.plan_chat(case.url)
        if case.source == "X":
            if case.kind == "text":
                assert plan.media == []
            else:
                assert plan.media
                assert all(item.url.startswith("https://") for item in plan.media)
            assert not plan.needs_download
        else:
            assert plan.needs_download
            assert plan.media == []
