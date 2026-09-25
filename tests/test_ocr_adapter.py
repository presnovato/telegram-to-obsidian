from pathlib import Path

import pytest

pytest.importorskip("pytesseract")

from tiktok_obsidian import ocr


class _FakeImage:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_broken_slide_becomes_empty_and_other_slides_continue(monkeypatch):
    def fake_open(path: Path):
        if path.name == "broken.jpg":
            raise OSError("broken image")
        return _FakeImage()

    monkeypatch.setattr(ocr.Image, "open", fake_open)
    monkeypatch.setattr(ocr.pytesseract, "image_to_string", lambda *_args, **_kwargs: " Первая\nстрока ")

    result = ocr.recognize_images([Path("good.jpg"), Path("broken.jpg")])

    assert result == ["Первая строка", ""]


def test_engine_failure_is_wrapped_as_ocr_error(monkeypatch):
    monkeypatch.setattr(ocr.Image, "open", lambda _path: _FakeImage())

    def fail_engine(*_args, **_kwargs):
        raise ocr.pytesseract.pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fail_engine)

    with pytest.raises(ocr.OcrError, match="Tesseract недоступен"):
        ocr.recognize_images([Path("slide.jpg")])
