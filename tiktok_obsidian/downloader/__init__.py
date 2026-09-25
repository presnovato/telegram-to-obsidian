"""Изолированный движок скачивания: маршрутизация по источникам.

Доменная логика (core/) от движков не зависит. Профили источников:
- TikTok — yt-dlp (видео) + tikwm (фото/карусели), модуль `ytdlp`;
- X/Twitter — публичные зеркала эмбедов, модуль `twitter`.

Сбои любого источника наружу выходят одним типом — DownloadError, чтобы telegram-слой
не знал про их внутренние исключения.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from ..core.models import PostMeta, Source
from ..core.text import is_transient
from ..core.twitter import TweetMedia
from . import twitter as _twitter
from .ytdlp import DownloadError, installed_ytdlp_version, update_ytdlp, ytdlp_version
from .ytdlp import download_post as _ytdlp_download
from .ytdlp import probe_post as _ytdlp_probe

log = logging.getLogger(__name__)

# Пауза перед единичным повтором преходящего сбоя (тесты обнуляют).
_RETRY_DELAY_S = 3.0

__all__ = [
    "DownloadError",
    "download_post",
    "fetch_media",
    "installed_ytdlp_version",
    "probe_post",
    "update_ytdlp",
    "ytdlp_version",
]


def fetch_media(url: str) -> list[TweetMedia]:
    """Прямые ссылки на медиа поста X (chat-режим). Для TikTok неприменимо."""
    try:
        return _twitter.fetch_media(url)
    except _twitter.TwitterFetchError as e:
        raise DownloadError(str(e)) from e


def _with_transient_retry(label: str, fn, *args, **kwargs):
    """Один повтор через паузу, если сбой преходящий (сеть). Остальное — сразу наружу."""
    try:
        return fn(*args, **kwargs)
    except DownloadError as e:
        if not is_transient(str(e)):
            raise
        log.info("%s: преходящий сбой (%s) — повтор через %s с", label, e, _RETRY_DELAY_S)
        time.sleep(_RETRY_DELAY_S)
        return fn(*args, **kwargs)


def probe_post(url: str) -> PostMeta:
    """Метаданные поста. Источник определяется по самой ссылке."""
    if _twitter.is_tweet_url(url):
        try:
            return _with_transient_retry(
                "probe", _twitter_probe_guarded, url
            )
        except _twitter.TwitterFetchError as e:
            raise DownloadError(str(e)) from e
    return _with_transient_retry("probe", _ytdlp_probe, url)


def _twitter_probe_guarded(url: str) -> PostMeta:
    try:
        return _twitter.probe_post(url)
    except _twitter.TwitterFetchError as e:
        raise DownloadError(str(e)) from e


def download_post(
    url: str,
    media_dir: Path,
    meta: PostMeta,
    *,
    max_bytes: int | None = None,
) -> PostMeta:
    """Скачивание медиа поста. max_bytes применяется только вызывающим chat-путём."""
    if meta.source is Source.TWITTER:
        return _with_transient_retry("download", _twitter_download_guarded, url, media_dir, meta)
    if max_bytes is None:
        return _with_transient_retry("download", _ytdlp_download, url, media_dir, meta)
    return _with_transient_retry(
        "download", _ytdlp_download, url, media_dir, meta, max_bytes=max_bytes
    )


def _twitter_download_guarded(url: str, media_dir: Path, meta: PostMeta) -> PostMeta:
    try:
        return _twitter.download_post(url, media_dir, meta)
    except _twitter.TwitterFetchError as e:
        raise DownloadError(str(e)) from e
