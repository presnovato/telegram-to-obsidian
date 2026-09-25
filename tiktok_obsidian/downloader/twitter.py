"""Источник X (Twitter): probe и download через публичные зеркала эмбедов.

Каскад api.fxtwitter.com → api.vxtwitter.com. Ключей не требуют, `/i/status/{id}` принимают,
медиа лежит на публичном CDN twimg.com — авторизация не нужна ни на одном шаге.
Сеть на urllib, как в tikwm-ветке: лишних зависимостей проект не тянет.

Чистый разбор ответов — в core/twitter.py, здесь только ввод-вывод.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import urllib.request
from pathlib import Path

from ..core import naming
from ..core.models import PostMeta, Source
from ..core.twitter import (
    TweetData,
    TweetMedia,
    TweetParseError,
    TweetRef,
    parse_fx,
    parse_vx,
    tweet_ref_from_url,
)
from .net import open_url

log = logging.getLogger(__name__)

_FX_API = "https://api.fxtwitter.com"
_VX_API = "https://api.vxtwitter.com"
_UA = "tiktok-to-obsidian/1.0 (personal bot)"
_TIMEOUT = 20

# ID постов X и aweme-ID TikTok — числа из одного диапазона, а дедуп и имена файлов
# завязаны на video_id. Префикс разводит их гарантированно.
_ID_PREFIX = "x_"

# probe и download идут подряд по одной ссылке, а каждый поход к зеркалу — сеть.
# Микрокэш последних ответов убирает второй запрос, не заводя состояния на весь процесс.
_CACHE: dict[str, TweetData] = {}
_CACHE_MAX = 8


class TwitterFetchError(RuntimeError):
    """Ни одно зеркало не отдало пригодные данные."""


def is_tweet_url(url: str) -> bool:
    return tweet_ref_from_url(url) is not None


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with open_url(req, _TIMEOUT) as resp:  # noqa: S310 — схема https фиксирована
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _fetch(ref: TweetRef) -> TweetData:
    cached = _CACHE.get(ref.canonical_url)
    if cached is not None:
        return cached

    reasons: list[str] = []
    # Пост без медиа — валидный текстовый твит, а не сбой: сохраняем его как запасной
    # вариант и дочитываем каскад. Второе зеркало могло разобрать медиа там, где первое
    # его не увидело (например, медиа висит в цитате), и такой ответ предпочтительнее.
    text_only: TweetData | None = None
    attempts = (
        ("fxtwitter", f"{_FX_API}/{ref.screen_name}/status/{ref.tweet_id}", parse_fx),
        ("vxtwitter", f"{_VX_API}/{ref.screen_name}/status/{ref.tweet_id}", parse_vx),
    )
    for name, api_url, parser in attempts:
        try:
            data = parser(_get_json(api_url))
        except TweetParseError as e:
            reasons.append(f"{name}: {e}")
            continue
        except Exception as e:  # noqa: BLE001 — HTTPError, таймаут, HTML вместо JSON
            log.warning("%s недоступен: %s", name, e)
            reasons.append(f"{name}: {type(e).__name__}")
            continue
        if not data.media:
            reasons.append(f"{name}: в посте нет медиа")
            if text_only is None:
                text_only = data
            continue
        log.info("метаданные X получены через %s: %d медиа", name, len(data.media))
        return _remember(ref, data)

    if text_only is not None:
        log.info("пост X без медиа — сохраняем как текстовый: %s", ref.canonical_url)
        return _remember(ref, text_only)

    raise TwitterFetchError(
        "; ".join(reasons) or "зеркала не ответили"
    )


def _remember(ref: TweetRef, data: TweetData) -> TweetData:
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[ref.canonical_url] = data
    return data


def _to_meta(data: TweetData, *, media_files: list[str] | None = None) -> PostMeta:
    images = [m for m in data.media if m.kind.is_image]
    return PostMeta(
        video_id=f"{_ID_PREFIX}{data.tweet_id}",
        author=data.author,
        caption=data.text,
        url=data.url,
        upload_date=data.upload_date,
        # Несколько картинок = карусель: включает ту же ветку OCR, что у слайдов TikTok.
        is_carousel=len(images) > 1,
        media_files=media_files or [],
        source=Source.TWITTER,
        has_media=bool(data.media),
    )


def probe_post(url: str) -> PostMeta:
    ref = tweet_ref_from_url(url)
    if ref is None:
        raise TwitterFetchError(f"не похоже на ссылку X: {url}")
    return _to_meta(_fetch(ref))


def fetch_media(url: str) -> list[TweetMedia]:
    """Прямые ссылки на медиа поста — для доставки в чат без скачивания.

    PostMeta несёт только имена уже скачанных файлов, поэтому chat-режиму нужен
    отдельный вход. Сети это не стоит: probe только что положил ответ в _CACHE.
    """
    ref = tweet_ref_from_url(url)
    if ref is None:
        raise TwitterFetchError(f"не похоже на ссылку X: {url}")
    return list(_fetch(ref).media)


def _ext_for(media_url: str, *, default: str) -> str:
    tail = media_url.split("?", 1)[0].rsplit("/", 1)[-1]
    # Без разделителя rpartition отдаёт всё имя как «расширение» — проверяем sep, не ext.
    _, sep, ext = tail.rpartition(".")
    return ext.lower() if sep and 0 < len(ext) <= 4 else default


def download_post(url: str, media_dir: Path, meta: PostMeta) -> PostMeta:
    ref = tweet_ref_from_url(url)
    if ref is None:
        raise TwitterFetchError(f"не похоже на ссылку X: {url}")
    data = _fetch(ref)

    # Текстовый пост: качать нечего, но заметку он всё равно заслуживает.
    if not data.media:
        return _to_meta(data)

    media_dir.mkdir(parents=True, exist_ok=True)
    video_id = f"{_ID_PREFIX}{data.tweet_id}"
    single = len(data.media) == 1

    # Всё или ничего (как _download_images у TikTok): частичный набор файлов
    # плюс заметка — это вечный дедуп «♻️», недостающее уже не восстановить.
    # Качаем во временную папку на диске vault и переносим, только если сошлось всё.
    staged: list[str] = []
    with tempfile.TemporaryDirectory(prefix="xdl_", dir=media_dir.parent) as tmp:
        tmp_dir = Path(tmp)
        for index, item in enumerate(data.media, start=1):
            ext = _ext_for(item.url, default="jpg" if item.kind.is_image else "mp4")
            name = naming.media_basename(video_id, ext, None if single else index)
            try:
                req = urllib.request.Request(item.url, headers={"User-Agent": _UA})
                with open_url(req, _TIMEOUT) as resp:  # noqa: S310
                    (tmp_dir / name).write_bytes(resp.read())
            except Exception as e:  # noqa: BLE001 — HTTPError, таймаут, обрыв
                raise TwitterFetchError(
                    f"не скачалось {index}/{len(data.media)}: {item.url}: {e}"
                ) from e
            staged.append(name)
        try:
            for name in staged:
                os.replace(tmp_dir / name, media_dir / name)
        except OSError as e:
            raise TwitterFetchError(f"не удалось сохранить файлы: {e}") from e
    return _to_meta(data, media_files=staged)
