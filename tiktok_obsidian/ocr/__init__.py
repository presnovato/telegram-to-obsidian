"""Адаптер локального Tesseract OCR для изображений карусели."""
from __future__ import annotations

import logging
from pathlib import Path

import pytesseract
from PIL import Image

from .. import config
from ..core.ocr import clean_text

log = logging.getLogger(__name__)


class OcrError(Exception):
    """Ошибка уровня OCR-движка, при которой обработку карусели надо прервать."""


_ENGINE_ERRORS = (
    pytesseract.pytesseract.TesseractNotFoundError,
    pytesseract.pytesseract.TesseractError,
)


def recognize_images(paths: list[Path]) -> list[str]:
    """Распознаёт изображения по порядку; локально сбойный слайд становится пустым."""
    if config.TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_CMD

    slides: list[str] = []
    for path in paths:
        try:
            with Image.open(path) as image:
                raw = pytesseract.image_to_string(image, lang=config.OCR_LANGS)
        except _ENGINE_ERRORS as exc:
            raise OcrError(f"Tesseract недоступен: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — один битый слайд не ломает карусель
            log.warning("OCR failed for slide %s: %s", path, exc)
            slides.append("")
            continue
        slides.append(clean_text(raw))

    return slides
