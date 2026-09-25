"""Сохранение пересланных Telegram-сообщений: файлы → OCR → связанные посты → заметка.

Telegram здесь — НЕ URL-источник (правило «новый источник = новый downloader» на него
не распространяется): файлы забираем через Bot API, а TikTok/X-ссылки внутри форварда
отдаём существующему пайплайну `service` как связанные посты.

Всё тестируемое — через инъекции: `download` качает файл Bot API без привязки к Bot,
`process_link` прогоняет одну ссылку через нужный режим. Сеть и Telegram в тестах
подменяются фейками.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

from . import config, ocr
from .core import fs, naming
from .core.mode import CaptureMode
from .core.models import PostMeta, Source
from .core.note import build_note
from .core.ocr import build_transcript_section
from .core.telegram import (
    ForwardOrigin,
    TgEntity,
    collect_post_urls,
    entities_to_markdown,
    origin_identity,
)
from .service import Status

log = logging.getLogger(__name__)

# Потолок Bot API getFile: файлы большего размера даже не пробуем качать,
# file_size известен заранее. MTProto-клиент не используем (риск бана аккаунта).
FILE_LIMIT_BYTES = 20 * 1024 * 1024

_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"})
_VIDEO_EXTS = frozenset({".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"})
# Что встраивается `![[...]]`, а что даётся ссылкой `[[...]]`.
_EMBED_EXTS = _IMAGE_EXTS | _VIDEO_EXTS | {".pdf"}

# Сериализация обработки: дедуп смотрит на состояние ФС, поэтому параллельные
# форварды и ссылки из capture не должны interleavиться. Тот же лок, что раньше
# жил в handlers/capture как _process_lock.
process_lock = asyncio.Lock()


class LinkStatus(str, Enum):
    DONE = "done"
    DUPLICATE = "duplicate"
    FAILED = "failed"


@dataclass
class TgMediaItem:
    """Один скачиваемый файл пересланного сообщения (уже разобранный в handler)."""

    file_id: str
    ext: str  # с точкой или без, нормализуем через naming.media_basename
    size: int = 0  # file_size из Bot API; 0 = неизвестен
    is_image: bool = False
    label: str = ""  # человекочитаемое имя для предупреждений


@dataclass
class ForwardBundle:
    """Всё, что handler извлёк из одного сообщения или склеенного альбома."""

    origin: ForwardOrigin
    album_min_message_id: int = 0
    text: str = ""
    entities: list[TgEntity] = field(default_factory=list)
    files: list[TgMediaItem] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # фразы про несейвируемое


@dataclass
class LinkedPost:
    url: str
    status: LinkStatus
    note_path: Path | None = None  # файл заметки; каталог = заметка переехала
    error: str = ""
    # Сохранённый пост для both-эха: отправляется ВНЕ process_lock (см. O4),
    # т.к. заливка в чат — минуты, а лок держит все vault-сейвы.
    process_result: object | None = None


@dataclass
class ForwardResult:
    status: Status
    note_path: Path | None
    media_count: int = 0
    linked_done: int = 0
    linked_duplicates: int = 0
    linked_failed: int = 0
    warnings: list[str] = field(default_factory=list)
    ocr_failed: bool = False
    echoes: list = field(default_factory=list)  # ProcessResult связанных постов для эха


# Колбэки, подменяемые в тестах.
DownloadFn = Callable[[str, Path], Awaitable[None]]
ProcessLinkFn = Callable[[str], Awaitable[LinkedPost]]


def _short_reason(error: str, limit: int = 120) -> str:
    first_line = (error or "").strip().splitlines()[0] if error.strip() else "неизвестная ошибка"
    return first_line if len(first_line) <= limit else first_line[:limit].rstrip() + "…"


def _vault_relative(vault_root: Path, note_path: Path) -> str | None:
    try:
        return note_path.relative_to(vault_root).with_suffix("").as_posix()
    except ValueError:
        return None


def related_item(link: LinkedPost, vault_root: Path) -> str:
    """Пункт «Связанных постов» для одного обработанного URL (§5.3)."""
    if link.status is LinkStatus.FAILED:
        return f"{link.url} — не удалось сохранить: {_short_reason(link.error)}"
    if (
        link.note_path is not None
        and link.note_path.suffix == ".md"
        and (rel := _vault_relative(vault_root, link.note_path)) is not None
    ):
        stem = link.note_path.stem
        # Старая заметка с #^[] в имени: вики-ссылка разобьётся — plain text.
        if re.search(r"[#^\[\]|]", stem):
            return f"{link.url} — уже сохранён: {stem}"
        return f"[[{rel}|{stem}]]"
    # Дубликат найден по медиа, а заметка переехала — ссылаться не на что.
    return f"{link.url} — уже сохранён ранее"


async def process_forward(
    bundle: ForwardBundle,
    *,
    comment: str = "",
    mode: CaptureMode = CaptureMode.VAULT,
    download: DownloadFn,
    process_link: ProcessLinkFn,
    media_dir: Path | None = None,
    notes_dir: Path | None = None,
    vault_root: Path | None = None,
    link_limit: int | None = None,
) -> ForwardResult:
    """Полный цикл по §8: дедуп → файлы → OCR → связанные посты → заметка."""
    media_dir = media_dir or config.MEDIA_DIR
    notes_dir = notes_dir or config.NOTES_DIR_TELEGRAM
    vault_root = vault_root or config.VAULT_ROOT
    link_limit = config.TG_MAX_LINKED_POSTS if link_limit is None else link_limit

    identity = origin_identity(bundle.origin, bundle.album_min_message_id)
    post_id = identity.post_id

    if mode is CaptureMode.CHAT:
        # Chat-режим vault не трогает вообще: ни дедупа, ни файлов, ни OCR,
        # ни заметки. Ранняя ветка, чтобы правка ниже не протекла в чат снова.
        # Медиа форварда уже в чате — обрабатываем только связанные посты.
        done = duplicates = failed = 0
        for url in collect_post_urls(bundle.text, bundle.entities, limit=link_limit).urls:
            try:
                link = await process_link(url)
            except Exception as exc:  # noqa: BLE001 — один URL не роняет остальные
                link = LinkedPost(url=url, status=LinkStatus.FAILED, error=str(exc))
            if link.status is LinkStatus.DONE:
                done += 1
            elif link.status is LinkStatus.DUPLICATE:
                duplicates += 1
            else:
                failed += 1
        return ForwardResult(
            status=Status.DONE,
            note_path=None,
            linked_done=done,
            linked_duplicates=duplicates,
            linked_failed=failed,
        )

    existing = fs.find_existing_media(media_dir, post_id)
    # Заметка — источник правды для форварда: проверяем её всегда, а не только
    # когда файлов нет (F6). Иначе форвард, у которого не скачался ни один файл,
    # плодил бы новую заметку при каждом повторе вместо DUPLICATE.
    note = fs.find_note_for(notes_dir, post_id)
    if existing or note:
        log.info("duplicate forward id=%s (%d files), note=%s", post_id, len(existing), note)
        return ForwardResult(
            status=Status.DUPLICATE, note_path=note or notes_dir, media_count=len(existing)
        )

    markdown = entities_to_markdown(bundle.text, bundle.entities)
    links = collect_post_urls(bundle.text, bundle.entities, limit=link_limit)
    warnings: list[str] = []
    skipped_parts = list(bundle.skipped)
    for item in bundle.files:
        if item.size > FILE_LIMIT_BYTES:
            skipped_parts.append(f"файл {item.label or item.file_id} больше 20 МБ")
    if links.overflow:
        warnings.append(
            f"+{links.overflow} ссылок сверх лимита {link_limit} — остались в тексте без обработки"
        )

    # --- Файлы: качаем во временную папку на диске vault, затем os.replace. ---
    saved: list[str] = []  # имена файлов в MEDIA_DIR
    saved_paths: list[Path] = []
    downloadable = [
        item for item in bundle.files if not item.size or item.size <= FILE_LIMIT_BYTES
    ]
    if downloadable:
        with tempfile.TemporaryDirectory(prefix="tg_", dir=media_dir.parent) as tmp:
            tmpdir = Path(tmp)
            staged: list[tuple[TgMediaItem, Path]] = []
            for item in downloadable:
                dest = tmpdir / f"{item.file_id}.bin"
                try:
                    await download(item.file_id, dest)
                except Exception as exc:  # noqa: BLE001 — один файл не роняет заметку
                    log.warning("telegram download failed %s: %s", item.file_id, exc)
                    skipped_parts.append(f"не скачалось: {item.label or item.file_id}")
                    continue
                staged.append((item, dest))
            multi = len(staged) > 1
            for index, (item, dest) in enumerate(staged, start=1):
                name = naming.media_basename(
                    post_id, item.ext, index if multi else None
                )
                try:
                    os.replace(dest, media_dir / name)
                except OSError as exc:
                    log.warning("telegram move failed %s: %s", name, exc)
                    skipped_parts.append(f"не сохранено: {item.label or item.file_id}")
                    continue
                saved.append(name)
                saved_paths.append(media_dir / name)

    if skipped_parts:
        warnings.append("Не сохранено: " + "; ".join(skipped_parts))

    # --- OCR, если картинок 2+ (is_carousel — как у X: больше одной картинки). ---
    transcript_section = ""
    ocr_failed = False
    image_paths = [
        path
        for path, name in zip(saved_paths, saved)
        if Path(name).suffix.lower() in _IMAGE_EXTS
    ]
    if len(image_paths) >= 2 and config.OCR_ENABLED:
        try:
            slides = await asyncio.to_thread(ocr.recognize_images, image_paths)
            transcript_section = build_transcript_section(slides)
        except Exception as exc:  # noqa: BLE001 — OCR никогда не блокирует сохранение
            ocr_failed = True
            log.warning("OCR failed for forward id=%s: %s", post_id, exc, exc_info=True)

    # --- Связанные посты: каждый в своём try/except. ---
    # (chat-режим вернулся раньше: там ни заметки, ни related.)
    # Эхо both собираем, но НЕ отправляем: заливка идёт после снятия лока (O4).
    related: list[str] = []
    echoes: list = []
    done = duplicates = failed = 0
    for url in links.urls:
        try:
            link = await process_link(url)
        except Exception as exc:  # noqa: BLE001 — один URL не роняет остальные
            link = LinkedPost(url=url, status=LinkStatus.FAILED, error=str(exc))
        if link.status is LinkStatus.DONE:
            done += 1
        elif link.status is LinkStatus.DUPLICATE:
            duplicates += 1
        else:
            failed += 1
        related.append(related_item(link, vault_root))
        if (
            mode.sends_chat
            and link.status is LinkStatus.DONE
            and link.process_result is not None
        ):
            echoes.append(link.process_result)

    embeds = [name for name in saved if Path(name).suffix.lower() in _EMBED_EXTS]
    plain = [name for name in saved if name not in embeds]
    meta = PostMeta(
        video_id=post_id,
        author=identity.author,
        caption=markdown,
        url=identity.url,
        upload_date=identity.upload_date,
        source=Source.TELEGRAM,
        media_files=embeds,
        has_media=bool(saved),
    )
    basename = naming.note_basename(
        identity.author,
        bundle.text,
        post_id,
        max_len=config.NOTE_NAME_MAX_LEN,
        words=config.CAPTION_WORDS_IN_NAME,
    )
    note_path = fs.unique_note_path(notes_dir, basename, post_id)
    fs.atomic_write_text(
        note_path,
        build_note(
            meta,
            comment,
            transcript_section=transcript_section,
            related=related,
            warnings=warnings,
            plain_files=plain,
        ),
    )
    log.info("telegram note written: %s", note_path)
    return ForwardResult(
        status=Status.DONE,
        note_path=note_path,
        media_count=len(saved),
        linked_done=done,
        linked_duplicates=duplicates,
        linked_failed=failed,
        warnings=warnings,
        ocr_failed=ocr_failed,
        echoes=echoes,
    )


def build_summary(result: ForwardResult, author: str) -> str:
    """Одна итоговая строка-ответ в чат (§8.6). HTML, всё экранировано."""
    parts: list[str] = []
    if result.status is Status.DUPLICATE:
        return (
            "♻️ Этот пост уже сохранён — новую заметку не создаю.\n"
            f"Заметка: <code>{html.escape(str(result.note_path))}</code>"
        )
    if result.note_path is None:
        parts.append("💬 chat-режим — vault не тронут, заметку не пишу.")
    else:
        parts.append(
            f"✅ Сохранено ({html.escape(author)}): "
            f"медиа — {result.media_count}.\n"
            f"📝 Заметка: <code>{html.escape(str(result.note_path))}</code>"
        )
    linked_total = result.linked_done + result.linked_duplicates + result.linked_failed
    if linked_total:
        parts.append(
            f"🔗 Связанные посты: ✅ {result.linked_done} / "
            f"♻️ {result.linked_duplicates} / ❌ {result.linked_failed}"
        )
    if result.warnings:
        parts.append("⚠️ " + html.escape("; ".join(result.warnings)))
    if result.ocr_failed:
        parts.append("⚠️ Текст со слайдов распознать не удалось — заметка сохранена без транскрипта.")
    return "\n".join(parts)
