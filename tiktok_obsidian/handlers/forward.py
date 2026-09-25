"""Пересланные сообщения: сохранить форвард как Telegram-заметку в vault.

Регистрируется ПОСЛЕ команд, ДО catch-all захвата: любой апдейт с
`forward_origin` разбираем здесь, а не как вставленную ссылку.

Режимы (§3): vault/both — пишем Telegram-заметку (в both медиа форварда в чат
НЕ дублируем — оно уже в чате, а вот медиа СВЯЗАННЫХ постов уходит как в both);
chat — заметки нет, связанные посты идут в чат. `!mode`-токены читаем только из
комментария владельца, никогда из пересланного текста.
"""
from __future__ import annotations

import asyncio
import html
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.types import (
    Message,
    MessageEntity,
    MessageOriginChannel,
    MessageOriginChat,
    MessageOriginHiddenUser,
    MessageOriginUser,
)

from .. import config, mode_store, service
from ..core.mode import CaptureMode
from ..core.telegram import (
    ForwardOrigin,
    TgEntity,
    collect_post_urls,
    is_link_only,
    origin_identity,
)
from ..downloader import DownloadError
from ..telegram_capture import (
    ForwardBundle,
    LinkedPost,
    LinkStatus,
    TgMediaItem,
    build_summary,
    process_forward,
    process_lock,
)
from .burst import PendingComment, buffer
from .capture import _run_chat, _run_vault, deliver_chat_post, echo_saved_to_chat

log = logging.getLogger(__name__)
router = Router(name="forward")

# Graceful shutdown (O3): сколько максимум ждём висящие форварды.
DRAIN_TIMEOUT_S = 30.0

# In-flight обработчики форвардов: aiogram 3.29 при stop_polling задачи апдейтов
# не ждёт, поэтому ждём их сами в drain_pending.
_INFLIGHT: set[asyncio.Task] = set()

# Описания несейвируемого контента для warnings-коллаута (§5.4).
_SKIPPED_LABELS = (
    ("voice", "голосовое сообщение"),
    ("audio", "аудио"),
    ("video_note", "видеосообщение (кружок)"),
    ("sticker", "стикер"),
    ("poll", "опрос"),
    ("location", "геопозиция"),
    ("venue", "место"),
    ("contact", "контакт"),
    ("dice", "кубик"),
    ("game", "игра"),
)


def map_origin(origin) -> ForwardOrigin:
    """aiogram forward_origin → чистый ForwardOrigin (для тестов — напрямую)."""
    date = int(origin.date.timestamp())
    if isinstance(origin, MessageOriginChannel):
        return ForwardOrigin(
            kind="channel",
            date=date,
            message_id=origin.message_id,
            chat_id=origin.chat.id,
            chat_title=origin.chat.title or "",
            chat_username=origin.chat.username or "",
        )
    if isinstance(origin, MessageOriginChat):
        return ForwardOrigin(
            kind="chat",
            date=date,
            chat_id=origin.sender_chat.id,
            chat_title=origin.sender_chat.title or "",
        )
    if isinstance(origin, MessageOriginUser):
        user = origin.sender_user
        return ForwardOrigin(
            kind="user",
            date=date,
            user_id=user.id,
            first_name=user.first_name or "",
            last_name=user.last_name or "",
            username=user.username or "",
        )
    if isinstance(origin, MessageOriginHiddenUser):
        return ForwardOrigin(kind="hidden", date=date, sender_name=origin.sender_user_name)
    raise ValueError(f"неизвестный тип forward_origin: {type(origin).__name__}")


def map_entities(entities: list[MessageEntity] | None) -> list[TgEntity]:
    """aiogram MessageEntity → чистые TgEntity."""
    return [
        TgEntity(
            type=e.type,
            offset=e.offset,
            length=e.length,
            url=e.url or "",
            language=e.language or "",
        )
        for e in entities or []
    ]


def _photo_item(message: Message) -> TgMediaItem:
    biggest = max(message.photo, key=lambda p: (p.file_size or 0, p.width * p.height))
    return TgMediaItem(
        file_id=biggest.file_id,
        ext=".jpg",
        size=biggest.file_size or 0,
        is_image=True,
        label="фото",
    )


def _video_item(message: Message) -> TgMediaItem:
    video = message.video
    name = video.file_name or "video.mp4"
    return TgMediaItem(
        file_id=video.file_id,
        ext=Path(name).suffix or ".mp4",
        size=video.file_size or 0,
        label=name,
    )


def _animation_item(message: Message) -> TgMediaItem:
    animation = message.animation
    name = animation.file_name or "animation.mp4"
    return TgMediaItem(
        file_id=animation.file_id,
        ext=".mp4",  # GIF едет в Obsidian как mp4
        size=animation.file_size or 0,
        label=name,
    )


def _document_item(message: Message) -> TgMediaItem:
    doc = message.document
    name = doc.file_name or f"file_{doc.file_id[-6:]}.bin"
    return TgMediaItem(
        file_id=doc.file_id,
        ext=Path(name).suffix or ".bin",
        size=doc.file_size or 0,
        label=name,
    )


def extract_files(message: Message) -> tuple[list[TgMediaItem], list[str]]:
    """Файлы для скачивания + фразы про несейвируемое (§5.4)."""
    files: list[TgMediaItem] = []
    if message.photo:
        files.append(_photo_item(message))
    elif message.video:
        files.append(_video_item(message))
    elif message.animation:
        files.append(_animation_item(message))
    elif message.document:
        files.append(_document_item(message))
    skipped = [
        label for field, label in _SKIPPED_LABELS if getattr(message, field, None) is not None
    ]
    return files, skipped


def message_text(message: Message) -> tuple[str, list[TgEntity]]:
    """Текст/подпись форварда вместе с его сущностями."""
    if message.caption is not None:
        return message.caption, map_entities(message.caption_entities)
    return message.text or "", map_entities(message.entities)


def bundle_of(message: Message) -> ForwardBundle:
    """Бандл из одиночного пересланного сообщения."""
    text, entities = message_text(message)
    files, skipped = extract_files(message)
    return ForwardBundle(
        origin=map_origin(message.forward_origin),
        text=text,
        entities=entities,
        files=files,
        skipped=skipped,
    )


def bundle_of_album(items: list[Message]) -> ForwardBundle:
    """Один бандл из склеенного альбома: минимальный message_id, подпись с любого
    элемента, файлы в порядке сообщений."""
    first = items[0]
    origin = map_origin(first.forward_origin)
    album_min = 0
    if origin.kind == "channel":
        album_min = min(map_origin(m.forward_origin).message_id for m in items)
    text, entities = "", []
    for item in items:
        candidate, candidate_entities = message_text(item)
        if candidate:
            text, entities = candidate, candidate_entities
            break
    files: list[TgMediaItem] = []
    skipped: list[str] = []
    for item in items:
        item_files, item_skipped = extract_files(item)
        files.extend(item_files)
        for label in item_skipped:
            if label not in skipped:
                skipped.append(label)
    return ForwardBundle(
        origin=origin,
        album_min_message_id=album_min,
        text=text,
        entities=entities,
        files=files,
        skipped=skipped,
    )


@router.message(F.forward_origin, F.media_group_id)
async def on_album_item(message: Message) -> None:
    key = (message.chat.id, str(message.media_group_id))
    generation = buffer.album_add(key, message)
    await asyncio.sleep(config.TG_ALBUM_WAIT_S)
    items = buffer.album_take_if_due(key, generation, config.TG_ALBUM_WAIT_S)
    if items is None:
        return  # приехал элемент новее — ждёт активная задача-таймер
    await _handle_tracked(message, bundle_of_album(items))


@router.message(F.forward_origin)
async def on_forward(message: Message) -> None:
    await _handle_tracked(message, bundle_of(message))


def resolve_comment(
    pending: PendingComment | None, default_mode: CaptureMode
) -> tuple[str, CaptureMode]:
    """Комментарий владельца + режим для всплеска форвардов (чистая функция).

    Текст в буфере уже без `!mode`-токена (снят в capture), override лежит рядом
    в `pending.override` — дважды не стрипаем. Пустой текст после токена
    (`!chat` alone) режим применяет, а «Впечатления» не создаёт (build_note
    пропускает пустой comment).
    """
    if pending is None:
        return "", default_mode
    return pending.text, pending.override or default_mode


async def _handle_tracked(message: Message, bundle: ForwardBundle) -> None:
    """_handle_bundle + учёт в _INFLIGHT для graceful shutdown (см. drain_pending)."""
    task = asyncio.current_task()
    if task is not None:
        _INFLIGHT.add(task)
    try:
        await _handle_bundle(message, bundle)
    finally:
        if task is not None:
            _INFLIGHT.discard(task)


async def drain_pending(timeout: float = DRAIN_TIMEOUT_S) -> None:
    """Graceful stop: обработать висящие в буфере альбомы и дождаться in-flight
    форвардов, максимум `timeout` секунд.

    Вызывать, пока polling ещё жив (сессия открыта): aiogram 3.29 при остановке
    задачи апдейтов не ждёт — уже вычитанные альбомы иначе потеряются, а
    in-flight handlers умрут о закрывшуюся сессию.
    """

    async def _drain() -> None:
        for items in buffer.flush_all():
            await _handle_bundle(items[-1], bundle_of_album(items))
        pending = [task for task in list(_INFLIGHT) if not task.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    await asyncio.wait_for(_drain(), timeout=timeout)


async def _handle_bundle(message: Message, bundle: ForwardBundle) -> None:
    comment, mode = resolve_comment(
        buffer.comment_take(message.chat.id, config.TG_COMMENT_WAIT_S),
        mode_store.get_mode(),
    )

    # Форвард без медиа, состоящий из одних ссылок, — не материнская заметка:
    # каждая ссылка идёт в существующий capture-поток отдельно, со своим статусом.
    if not bundle.files and not bundle.skipped and is_link_only(bundle.text, bundle.entities):
        links = collect_post_urls(
            bundle.text, bundle.entities, limit=config.TG_MAX_LINKED_POSTS
        )
        for url in links.urls:
            log.info("link-only forward, delegating to capture flow: %s", url)
            status_msg = await message.answer("⏳ Обрабатываю…")
            try:
                if mode is CaptureMode.CHAT:
                    await _run_chat(message, status_msg, url)
                else:
                    await _run_vault(message, status_msg, url, "", mode)
            except Exception as e:  # noqa: BLE001 — mirroring capture.on_message
                if isinstance(e, DownloadError):
                    log.error("download failed for %s: %s", url, e)
                    await status_msg.edit_text(f"❌ Не удалось скачать.\n{html.escape(str(e)[:500])}")
                else:
                    log.exception("unexpected error for %s", url)
                    await status_msg.edit_text(
                        f"❌ Непредвиденная ошибка:\n<pre>{html.escape(str(e)[:500])}</pre>"
                    )
        if links.overflow:
            await message.answer(
                f"⚠️ Ещё {links.overflow} ссылок сверх лимита "
                f"({config.TG_MAX_LINKED_POSTS}) — пропустил."
            )
        return

    identity = origin_identity(bundle.origin, bundle.album_min_message_id)
    status_msg = await message.answer("⏳ Обрабатываю…")
    try:
        if mode is CaptureMode.CHAT:
            # Chat не трогает vault — лок не нужен вообще.
            result = await process_forward(
                bundle,
                comment=comment,
                mode=mode,
                download=_make_download(message),
                process_link=_make_process_link(message, mode),
            )
        else:
            async with process_lock:
                result = await process_forward(
                    bundle,
                    comment=comment,
                    mode=mode,
                    download=_make_download(message),
                    process_link=_make_process_link(message, mode),
                )
            # Заливка связанных постов — после снятия лока (O4): она не пишет
            # в vault, а висит минутами. Как _run_vault для обычных ссылок.
            for echo_result in result.echoes:
                await echo_saved_to_chat(message, echo_result)
    except Exception as e:  # noqa: BLE001 — не роняем процесс на неожиданном сбое
        log.exception("unexpected error for forward %s", identity.post_id)
        await status_msg.edit_text(
            f"❌ Непредвиденная ошибка:\n<pre>{html.escape(str(e)[:500])}</pre>"
        )
        return
    await status_msg.edit_text(build_summary(result, identity.author))


def _make_download(message: Message):
    async def _download(file_id: str, dest: Path) -> None:
        await message.bot.download(
            file_id, destination=dest, timeout=config.TG_DOWNLOAD_TIMEOUT_S
        )

    return _download


def _make_process_link(message: Message, mode: CaptureMode):
    """Связанный пост — через существующий пайплайн в режиме материнского."""
    if mode is CaptureMode.CHAT:

        async def _chat(url: str) -> LinkedPost:
            try:
                await deliver_chat_post(message, url)
            except Exception as exc:  # noqa: BLE001 — один URL не роняет остальные
                return LinkedPost(url=url, status=LinkStatus.FAILED, error=str(exc))
            return LinkedPost(url=url, status=LinkStatus.DONE)

        return _chat

    send_to_chat = mode.sends_chat  # both: заметка + медиа в чат; vault: тихо

    async def _vault(url: str) -> LinkedPost:
        try:
            result = await asyncio.to_thread(service.process, url, "")
        except Exception as exc:  # noqa: BLE001 — DownloadError и прочие
            return LinkedPost(url=url, status=LinkStatus.FAILED, error=str(exc))
        if result.status is service.Status.DUPLICATE:
            return LinkedPost(
                url=url, status=LinkStatus.DUPLICATE, note_path=result.note_path
            )
        # Эхо — позже, вне process_lock (см. O4): собираем, не отправляем.
        if send_to_chat:
            return LinkedPost(
                url=url,
                status=LinkStatus.DONE,
                note_path=result.note_path,
                process_result=result,
            )
        return LinkedPost(url=url, status=LinkStatus.DONE, note_path=result.note_path)

    return _vault


# Re-export for tests.
__all__ = [
    "router",
    "map_origin",
    "map_entities",
    "extract_files",
    "message_text",
    "bundle_of",
    "bundle_of_album",
    "resolve_comment",
    "drain_pending",
    "DRAIN_TIMEOUT_S",
]
