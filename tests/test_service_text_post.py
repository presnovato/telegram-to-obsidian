"""Текстовый пост X (без медиа) проходит весь цикл: заметка пишется, дедуп работает."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tiktok_obsidian import service
from tiktok_obsidian.core.models import PostMeta, Source


def _prepare(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    media_dir = tmp_path / "media"
    notes_dir = tmp_path / "notes"
    media_dir.mkdir()
    notes_dir.mkdir()

    meta = PostMeta(
        video_id="x_123",
        author="u",
        caption="текст твита без картинок",
        url="https://x.com/u/status/123",
        source=Source.TWITTER,
        has_media=False,
    )
    monkeypatch.setattr(service.config, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(service.config, "NOTES_DIR_TWITTER", notes_dir)
    monkeypatch.setattr(service.config, "OCR_ENABLED", False)
    monkeypatch.setattr(service, "probe_post", lambda _url: meta)
    # Скачивать нечего: downloader для текстового поста возвращает meta как есть.
    monkeypatch.setattr(service, "download_post", lambda _url, _dir, probed: replace(probed))
    return notes_dir


def test_text_only_post_gets_a_note(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path)

    result = service.process("https://x.com/u/status/123", comment="мысль")

    assert result.status is service.Status.DONE
    assert result.media_paths == []
    note = result.note_path.read_text(encoding="utf-8")
    assert "текст твита без картинок" in note
    assert "![[" not in note  # встраивать нечего
    assert "## Впечатления" in note


def test_text_only_post_is_deduped_by_note(monkeypatch, tmp_path):
    # Файлов в Медиафайлах такой пост не оставляет, поэтому дедуп обязан смотреть в заметки.
    _prepare(monkeypatch, tmp_path)

    first = service.process("https://x.com/u/status/123")
    second = service.process("https://x.com/u/status/123")

    assert first.status is service.Status.DONE
    assert second.status is service.Status.DUPLICATE
    assert second.note_path == first.note_path
