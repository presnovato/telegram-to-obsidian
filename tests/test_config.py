import logging
import sys
from types import SimpleNamespace

import pytest

from tiktok_obsidian import config


def test_validate_requires_vault_root(monkeypatch):
    """VAULT_ROOT без дефолта: пустое значение — внятный ConfigError, а не тихий Path('.')."""
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", "token")
    monkeypatch.setattr(config, "ALLOWED_USER_ID", 1)
    monkeypatch.setattr(config, "_VAULT_ROOT_RAW", "")
    monkeypatch.setattr(config, "OCR_ENABLED", False)

    with pytest.raises(config.ConfigError, match="VAULT_ROOT"):
        config.validate()


def test_validate_warns_but_does_not_fail_when_tesseract_is_missing(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", "token")
    monkeypatch.setattr(config, "ALLOWED_USER_ID", 1)
    monkeypatch.setattr(config, "_VAULT_ROOT_RAW", str(tmp_path))
    monkeypatch.setattr(config, "VAULT_ROOT", tmp_path)
    monkeypatch.setattr(config, "OCR_ENABLED", True)
    monkeypatch.setattr(config, "TESSERACT_CMD", r"C:\missing\tesseract.exe")

    def unavailable():
        raise OSError("not found")

    fake_pytesseract = SimpleNamespace(
        pytesseract=SimpleNamespace(tesseract_cmd="tesseract"),
        get_tesseract_version=unavailable,
    )
    monkeypatch.setitem(sys.modules, "pytesseract", fake_pytesseract)
    caplog.set_level(logging.WARNING, logger=config.__name__)

    config.validate()

    assert fake_pytesseract.pytesseract.tesseract_cmd == config.TESSERACT_CMD
    assert "Tesseract OCR недоступен" in caplog.text
