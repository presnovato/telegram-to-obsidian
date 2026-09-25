"""Доставка медиа в чат: сначала по прямой ссылке, при отказе — скачать и залить файлом.

Порядок не случаен. Отдавая Telegram ссылку на video.twimg.com, мы не гоняем байты через
домашний канал — это быстро и бесплатно. Но у загрузки по URL жёсткий потолок (≈5 МБ фото,
20 МБ прочее) и Telegram иногда просто отказывается тянуть с twimg. Поэтому любой отказ
API переводит нас во второй режим: качаем сами и заливаем как FSInputFile — там лимит 50 МБ.

Предварительно размер не проверяем: зеркала его не сообщают достоверно (у vx поле `size` —
это размеры кадра, а не байты), так что единственный честный сигнал — отказ Telegram.

Сеть здесь на urllib, как и в downloader/: новых зависимостей проект не тянет.
Блокирующие вызовы уходят в asyncio.to_thread, чтобы не морозить polling.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo, Message

from . import config
from .core.twitter import MediaKind, TweetMedia
from .downloader.net import open_url

log = logging.getLogger(__name__)

_MB = 1024 * 1024
_UA = "tiktok-to-obsidian/1.0 (personal bot)"
_TIMEOUT = 20
_DEFAULT_EXT = {MediaKind.PHOTO: ".jpg", MediaKind.VIDEO: ".mp4", MediaKind.GIF: ".mp4"}
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif")


@dataclass
class DeliveryReport:
    sent: int = 0
    downloaded: bool = False  # пришлось ли уходить в фолбэк
    skipped: list[TweetMedia] = field(default_factory=list)  # не влезли в лимит upload


def _chunk_sizes(n: int, limit: int) -> list[int]:
    """Разбивает n элементов на куски по 2–limit штук, как можно ровнее.

    sendMediaGroup требует 2–10: чанкинг по `limit` давал бы хвост из одного
    (11, 21, 31 …), который API отвергает. Примеры: 11 → 6+5, 21 → 7+7+7.
    """
    if n <= 0:
        return []
    if n <= limit:
        return [n]
    chunks = -(-n // limit)  # ceil
    base, extra = divmod(n, chunks)
    return [base + 1] * extra + [base] * (chunks - extra)


def _chunked(
    items: list[tuple[MediaKind, str | FSInputFile]], limit: int
) -> list[list[tuple[MediaKind, str | FSInputFile]]]:
    """Режет пары на куски по `_chunk_sizes`, порядок сохраняет."""
    sizes = _chunk_sizes(len(items), limit)
    out, pos = [], 0
    for size in sizes:
        out.append(items[pos : pos + size])
        pos += size
    return out


def _filename(item: TweetMedia, index: int) -> str:
    tail = item.url.split("?", 1)[0].rsplit("/", 1)[-1]
    _, sep, ext = tail.rpartition(".")
    suffix = f".{ext.lower()}" if sep and 0 < len(ext) <= 4 else _DEFAULT_EXT[item.kind]
    return f"{index:02d}{suffix}"


def _download(item: TweetMedia, dest: Path) -> Path | None:
    """Качает медиа в dest. None, если файл больше лимита на upload.

    Читаем потоком и обрываемся на превышении: content-length CDN отдаёт не всегда,
    а тянуть 300 МБ ради того, чтобы потом их выбросить, незачем.
    """
    limit = config.UPLOAD_LIMIT_MB * _MB
    req = urllib.request.Request(item.url, headers={"User-Agent": _UA})
    with open_url(req, _TIMEOUT) as resp:  # noqa: S310 — схема https фиксирована
        declared = int(resp.headers.get("content-length") or 0)
        if declared > limit:
            log.warning("пропуск %s: %.1f МБ > лимита", item.url, declared / _MB)
            return None
        written = 0
        with dest.open("wb") as fh:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    log.warning("пропуск %s: превысил лимит в процессе", item.url)
                    fh.close()
                    dest.unlink(missing_ok=True)
                    return None
                fh.write(chunk)
    return dest


async def _call(
    message: Message, method, *, upload_timeout: int | None, is_upload: bool
):
    """Вызов Bot API: для заливки файлов — длинный пер-request таймаут
    (60 с по умолчанию не хватает на 50 МБ на медленном канале), для отправки по URL —
    дефолтный (файл тянет сам Telegram)."""
    if is_upload and upload_timeout:
        return await message.bot(method, request_timeout=upload_timeout)
    return await message.bot(method)


async def _send(
    message: Message,
    pairs: list[tuple[MediaKind, str | FSInputFile]],
    *,
    upload_timeout: int | None = None,
) -> tuple[int, list[tuple[MediaKind, str | FSInputFile]], TelegramAPIError | None]:
    """Отправляет подготовленные пары (тип, источник). Источник — URL или локальный файл.

    Возвращает (отправлено, остаток, ошибка): при отказе API текущий и все
    следующие куски уходят в остаток, чтобы фолбэк досылал только
    недоставленное, а не всё с начала. Остаток пуст = всё ушло.

    GIF-ки в альбом не кладутся (Telegram запрещает animation в media_group) —
    они уходят отдельными сообщениями.
    """
    gifs = [src for kind, src in pairs if kind is MediaKind.GIF]
    album = [(kind, src) for kind, src in pairs if kind is not MediaKind.GIF]

    # Единицы отправки по порядку: одиночка/куски альбома, затем GIF-ки.
    units: list[tuple[str, object]] = []
    if len(album) == 1:
        units.append(("single", album[0]))
    elif album:
        units += [("group", chunk) for chunk in _chunked(album, config.MEDIA_GROUP_MAX)]
    units += [("gif", src) for src in gifs]

    sent = 0
    try:
        for index, (unit_kind, payload) in enumerate(units):
            if unit_kind == "single":
                kind, src = payload
                method = (
                    message.answer_photo(src)
                    if kind is MediaKind.PHOTO
                    else message.answer_video(src)
                )
                await _call(
                    message,
                    method,
                    upload_timeout=upload_timeout,
                    is_upload=not isinstance(src, str),
                )
                sent += 1
            elif unit_kind == "group":
                chunk = payload
                media = [
                    InputMediaPhoto(media=src)
                    if kind is MediaKind.PHOTO
                    else InputMediaVideo(media=src)
                    for kind, src in chunk
                ]
                await _call(
                    message,
                    message.answer_media_group(media),
                    upload_timeout=upload_timeout,
                    is_upload=any(not isinstance(item.media, str) for item in media),
                )
                sent += len(chunk)
            else:
                src = payload
                await _call(
                    message,
                    message.answer_animation(src),
                    upload_timeout=upload_timeout,
                    is_upload=not isinstance(src, str),
                )
                sent += 1
    except TelegramAPIError as e:
        # Остаток — неразосланные пары в исходном порядке. Куски атомарны
        # (sendMediaGroup либо ушёл целиком, либо нет), поэтому срез по юнитам.
        rest: list[tuple[MediaKind, str | FSInputFile]] = []
        for unit_kind, payload in units[index:]:
            if unit_kind == "gif":
                rest.append((MediaKind.GIF, payload))
            elif unit_kind == "single":
                rest.append(payload)
            else:
                rest += payload
        return sent, rest, e
    return sent, [], None


async def deliver_urls(message: Message, items: list[TweetMedia]) -> DeliveryReport:
    """Доставка медиа X по прямым ссылкам с фолбэком на скачивание."""
    report = DeliveryReport()
    if not items:
        return report

    # Режим 1: отдаём Telegram прямые ссылки — байты не идут через домашний канал.
    sent, remaining, err = await _send(message, [(item.kind, item.url) for item in items])
    report.sent = sent
    if err is None:
        return report
    log.info("отправка по URL не прошла (%s) — качаю сам", err)

    # Режим 2: качаем сами и заливаем файлами — только недоставленное.
    report.downloaded = True
    pending_urls = {src for _, src in remaining}
    todo = [item for item in items if item.url in pending_urls]
    with tempfile.TemporaryDirectory(prefix="x2tg_", dir=config.MEDIA_DIR.parent) as tmp:
        tmpdir = Path(tmp)
        pairs: list[tuple[MediaKind, str | FSInputFile]] = []
        for index, item in enumerate(todo, start=1):
            try:
                path = await asyncio.to_thread(_download, item, tmpdir / _filename(item, index))
            except Exception as e:  # noqa: BLE001 — HTTPError, таймаут, обрыв
                log.warning("не скачалось %s: %s", item.url, e)
                path = None
            if path is None:
                report.skipped.append(item)
            else:
                pairs.append((item.kind, FSInputFile(path)))

        if pairs:
            sent, _, err = await _send(
                message, pairs, upload_timeout=config.UPLOAD_TIMEOUT_S
            )
            report.sent = sent
            if err is not None:
                raise err
    return report


async def deliver_files(message: Message, paths: list[Path]) -> DeliveryReport:
    """Доставка уже скачанных файлов (TikTok, а также vault-режим).

    Тип берём по расширению: прямых ссылок и вида медиа здесь уже нет, а пост X может
    смешивать фото и видео — альбом из одних InputMediaPhoto на видео разваливается.
    """
    report = DeliveryReport()
    if not paths:
        return report
    pairs: list[tuple[MediaKind, str | FSInputFile]] = [
        (
            MediaKind.PHOTO if str(p).lower().endswith(_IMAGE_EXTS) else MediaKind.VIDEO,
            FSInputFile(p),
        )
        for p in paths
    ]
    sent, _, err = await _send(message, pairs, upload_timeout=config.UPLOAD_TIMEOUT_S)
    report.sent = sent
    if err is not None:
        raise err
    return report
