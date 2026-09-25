from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("pytesseract")

from tiktok_obsidian import service
from tiktok_obsidian.core.models import PostMeta


def _prepare_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    is_carousel: bool,
    media_files: list[str],
) -> tuple[PostMeta, Path, Path]:
    media_dir = tmp_path / "media"
    notes_dir = tmp_path / "notes"
    media_dir.mkdir()
    notes_dir.mkdir()

    meta = PostMeta(
        video_id="7412345",
        author="author",
        caption="caption",
        url="https://www.tiktok.com/@author/video/7412345",
        is_carousel=is_carousel,
    )
    monkeypatch.setattr(service.config, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(service.config, "NOTES_DIR", notes_dir)
    monkeypatch.setattr(service.config, "OCR_ENABLED", True)
    monkeypatch.setattr(service, "probe_post", lambda _url: meta)

    def fake_download(_url: str, target: Path, probed: PostMeta) -> PostMeta:
        for name in media_files:
            (target / name).write_bytes(b"media")
        return replace(probed, media_files=media_files)

    monkeypatch.setattr(service, "download_post", fake_download)
    return meta, media_dir, notes_dir


def test_carousel_ocr_is_added_to_note(monkeypatch, tmp_path):
    _prepare_process(
        monkeypatch,
        tmp_path,
        is_carousel=True,
        media_files=["7412345_01.jpg", "7412345_02.png", "7412345.mp4"],
    )
    seen_paths: list[Path] = []

    def fake_ocr(paths: list[Path]) -> list[str]:
        seen_paths.extend(paths)
        return ["Первый\nслайд", ""]

    monkeypatch.setattr(service.ocr, "recognize_images", fake_ocr)

    result = service.process("https://example.test", comment="комментарий")

    assert result.status is service.Status.DONE
    assert result.ocr_failed is False
    assert [path.suffix for path in seen_paths] == [".jpg", ".png"]
    note = result.note_path.read_text(encoding="utf-8")
    assert "**Слайд 1:** Первый слайд" in note
    assert "**Слайд 2:**" not in note
    assert note.index("## Впечатления") < note.index("**Слайд 1:**")


def test_ocr_failure_does_not_block_note(monkeypatch, tmp_path):
    _prepare_process(
        monkeypatch,
        tmp_path,
        is_carousel=True,
        media_files=["7412345_01.jpg"],
    )

    def fail_ocr(_paths: list[Path]) -> list[str]:
        raise service.ocr.OcrError("engine unavailable")

    monkeypatch.setattr(service.ocr, "recognize_images", fail_ocr)

    result = service.process("https://example.test")

    assert result.status is service.Status.DONE
    assert result.ocr_failed is True
    assert result.note_path.exists()
    assert "**Слайд" not in result.note_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(("is_carousel", "ocr_enabled"), [(False, True), (True, False)])
def test_ocr_is_skipped_for_video_or_when_disabled(monkeypatch, tmp_path, is_carousel, ocr_enabled):
    suffix = ".jpg" if is_carousel else ".mp4"
    _prepare_process(
        monkeypatch,
        tmp_path,
        is_carousel=is_carousel,
        media_files=[f"7412345{suffix}"],
    )
    monkeypatch.setattr(service.config, "OCR_ENABLED", ocr_enabled)
    monkeypatch.setattr(
        service.ocr,
        "recognize_images",
        lambda _paths: pytest.fail("OCR must not run"),
    )

    result = service.process("https://example.test")

    assert result.status is service.Status.DONE
    assert result.ocr_failed is False


def test_duplicate_skips_download_and_ocr(monkeypatch, tmp_path):
    meta, media_dir, _notes_dir = _prepare_process(
        monkeypatch,
        tmp_path,
        is_carousel=True,
        media_files=["7412345_01.jpg"],
    )
    (media_dir / "7412345.jpg").write_bytes(b"existing")
    monkeypatch.setattr(service, "probe_post", lambda _url: meta)
    monkeypatch.setattr(
        service,
        "download_post",
        lambda *_args: pytest.fail("download must not run for duplicate"),
    )
    monkeypatch.setattr(
        service.ocr,
        "recognize_images",
        lambda _paths: pytest.fail("OCR must not run for duplicate"),
    )

    result = service.process("https://example.test")

    assert result.status is service.Status.DUPLICATE
    assert result.ocr_failed is False
