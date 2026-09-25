"""Доменные модели, общие для core и downloader (без внешних зависимостей)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Source(str, Enum):
    """Откуда пост. Значение идёт прямо во frontmatter заметки (`source:`)."""

    TIKTOK = "TikTok"
    TWITTER = "Twitter"
    TELEGRAM = "Telegram"

    @property
    def tag(self) -> str:
        """Тег для frontmatter — нижним регистром, рядом с `inbox`."""
        return self.value.lower()


@dataclass(frozen=True)
class PostMeta:
    """Нормализованные метаданные поста + имена уже скачанных медиафайлов."""

    video_id: str
    author: str
    caption: str
    url: str
    upload_date: str = ""  # ISO YYYY-MM-DD или "" если неизвестно
    duration: int | None = None  # секунды, только для видео
    is_carousel: bool = False
    # TikTok photo post, including one-image posts. This must stay separate
    # from is_carousel because a single photo is not a video.
    is_photo: bool = False
    watermarked: bool = False  # видео скачано с watermark (fallback)
    # Имена файлов в папке вложений для встраивания через ![[...]] (без пути).
    media_files: list[str] = field(default_factory=list)
    source: Source = Source.TIKTOK
    # Есть ли у поста медиа вообще. False только для текстовых постов X: они не оставляют
    # файлов в Медиафайлах, а дедуп по ФС смотрит именно туда — ему нужен этот сигнал,
    # чтобы для таких постов свериться с заметками. У TikTok пост без медиа не бывает.
    has_media: bool = True
