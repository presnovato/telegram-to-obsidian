"""Оркестрация: probe → дедуп по ФС → download → заметка. Связывает core и downloader."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

from . import config, ocr
from .core import fs, naming
from .core.models import PostMeta, Source
from .core.note import build_note
from .core.ocr import build_transcript_section
from .core.twitter import TweetMedia
from .downloader import download_post, fetch_media, probe_post

log = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"})


class Status(Enum):
    DONE = auto()
    DUPLICATE = auto()


@dataclass
class ProcessResult:
    status: Status
    meta: PostMeta
    note_path: Path
    media_paths: list[Path] = field(default_factory=list)  # абсолютные, для отправки в чат
    ocr_failed: bool = False


@dataclass
class ChatPlan:
    """Что доставлять в чат. Ровно одно из полей непустое."""

    meta: PostMeta
    media: list[TweetMedia] = field(default_factory=list)  # X: прямые ссылки, качать не надо
    needs_download: bool = False  # TikTok: прямых ссылок нет, придётся скачать во временную


def plan_chat(url: str) -> ChatPlan:
    """probe + разбор, как доставить пост в чат. Vault не трогается, дедуп не проверяется.

    Повторную отправку в чат считаем осознанным действием: дедуп t2o основан на наличии
    файлов в vault, а в chat-режиме vault не пишется — сверяться не с чем.
    """
    meta = probe_post(url)
    if meta.source is Source.TWITTER:
        return ChatPlan(meta=meta, media=fetch_media(url))
    # TikTok: probe отдаёт только метаданные, байты достаёт yt-dlp/tikwm.
    return ChatPlan(meta=meta, needs_download=True)


def download_to(url: str, meta: PostMeta, dest_dir: Path) -> list[Path]:
    """Скачивает медиа поста в произвольную папку (для chat-режима — временную).

    Вызывающий обязан создать dest_dir на том же диске, что и vault: yt-dlp переносит
    файл через os.replace, а тот на переходе C:→D: даёт WinError 17.
    """
    max_bytes = config.UPLOAD_LIMIT_MB * 1024 * 1024
    got = download_post(url, dest_dir, meta, max_bytes=max_bytes)
    paths = [dest_dir / name for name in got.media_files]
    too_large = [path for path in paths if path.stat().st_size > max_bytes]
    if too_large:
        from .downloader import DownloadError

        raise DownloadError(
            f"медиа превышает лимит Telegram upload {config.UPLOAD_LIMIT_MB} МБ: "
            + ", ".join(path.name for path in too_large)
        )
    return paths


def process(url: str, comment: str = "") -> ProcessResult:
    """Полный цикл обработки одной ссылки. Бросает DownloadError при сбое скачивания."""
    meta = probe_post(url)
    if not meta.video_id:
        from .downloader import DownloadError

        raise DownloadError("не удалось определить ID поста")

    log.info(
        "probe ok: source=%s id=%s author=%s carousel=%s",
        meta.source.value,
        meta.video_id,
        meta.author,
        meta.is_carousel,
    )
    notes_dir = config.notes_dir_for(meta.source)

    # Дедуп: файловая система — источник правды.
    existing = fs.find_existing_media(config.MEDIA_DIR, meta.video_id)
    # Текстовый пост X файлов в Медиафайлах не оставляет, поэтому для него единственный
    # след прошлого сохранения — сама заметка. Для поста с медиа, которого в Медиафайлах
    # нет, сканировать заметки незачем: дешёвый glob уже дал ответ.
    note = (
        fs.find_note_for(notes_dir, meta.video_id)
        if existing or not meta.has_media
        else None
    )
    if existing or note:
        log.info("duplicate id=%s (%d files), note=%s", meta.video_id, len(existing), note)
        return ProcessResult(
            status=Status.DUPLICATE,
            meta=meta,
            note_path=note or notes_dir,
            media_paths=existing,
        )

    meta = download_post(url, config.MEDIA_DIR, meta)
    log.info("downloaded id=%s files=%s watermark=%s", meta.video_id, meta.media_files, meta.watermarked)

    media_paths = [config.MEDIA_DIR / name for name in meta.media_files]
    transcript_section = ""
    ocr_failed = False
    if meta.is_carousel and config.OCR_ENABLED:
        image_paths = [path for path in media_paths if path.suffix.lower() in _IMAGE_EXTENSIONS]
        if image_paths:
            try:
                slides = ocr.recognize_images(image_paths)
                transcript_section = build_transcript_section(slides)
            except Exception as exc:  # noqa: BLE001 — OCR никогда не блокирует сохранение
                ocr_failed = True
                log.warning("OCR failed for post id=%s: %s", meta.video_id, exc, exc_info=True)

    basename = naming.note_basename(
        meta.author,
        meta.caption,
        meta.video_id,
        max_len=config.NOTE_NAME_MAX_LEN,
        words=config.CAPTION_WORDS_IN_NAME,
    )
    note_path = fs.unique_note_path(notes_dir, basename, meta.video_id)
    fs.atomic_write_text(
        note_path,
        build_note(meta, comment, transcript_section=transcript_section),
    )
    log.info("note written: %s", note_path)

    return ProcessResult(
        status=Status.DONE,
        meta=meta,
        note_path=note_path,
        media_paths=media_paths,
        ocr_failed=ocr_failed,
    )
