"""Чистый разбор постов X (Twitter): ссылка → ref, JSON зеркала → домен.

Метаданные берём из публичных зеркал эмбедов X (fxtwitter, vxtwitter) — ключей и аккаунта
не требуют, ссылки ведут на публичный CDN twimg.com. HTML-превью fixupx.com не парсим:
у того же проекта есть JSON-эндпоинт, а og:video отдаёт лишь одно видео и разваливается
на карусели. yt-dlp для x.com не годится — требует cookies залогиненного браузера.

Модуль чистый: ни сети, ни telegram. Сетевой слой — downloader/twitter.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# Принимаем и «сырые» ссылки, и уже подменённые вручную зеркала — старая привычка
# подменять домен на fixupx.com должна продолжать работать.
_HOSTS = r"(?:x|twitter|fixupx|fxtwitter|vxtwitter|fixvx|twittpr)\.com"
TWEET_URL = re.compile(
    rf"https?://(?:www\.|mobile\.|m\.)?{_HOSTS}/(?P<user>[A-Za-z0-9_]+|i)/status(?:es)?/(?P<id>\d+)[^\s«»\"<>]*",
    re.IGNORECASE,
)

# Картинка ссылочной карточки — превью чужого сайта, а не медиа поста. fx её в media не
# кладёт, vx кладёт: без фильтра пост-ссылка приезжал бы «картинкой» вместо «медиа нет».
_CARD_IMAGE = "/card_img/"


class TweetParseError(ValueError):
    """Ответ зеркала получен, но пригодных данных в нём нет."""


class MediaKind(str, Enum):
    PHOTO = "photo"
    VIDEO = "video"
    GIF = "gif"  # в X это беззвучный mp4

    @property
    def is_image(self) -> bool:
        return self is MediaKind.PHOTO


@dataclass(frozen=True)
class TweetMedia:
    kind: MediaKind
    url: str


@dataclass(frozen=True)
class TweetRef:
    tweet_id: str
    screen_name: str  # "i", если имени в ссылке не было — зеркала это принимают

    @property
    def canonical_url(self) -> str:
        return f"https://x.com/{self.screen_name}/status/{self.tweet_id}"


@dataclass(frozen=True)
class TweetData:
    tweet_id: str
    url: str
    author: str  # @screen_name — он стабильнее отображаемого имени и годится в имя файла
    text: str
    upload_date: str = ""  # ISO YYYY-MM-DD или "" если неизвестно
    media: list[TweetMedia] = field(default_factory=list)


def tweet_ref_from_url(url: str) -> TweetRef | None:
    """Извлекает id и имя автора из ссылки на пост X. None, если это не такая ссылка."""
    match = TWEET_URL.search(url or "")
    if not match:
        return None
    return TweetRef(tweet_id=match.group("id"), screen_name=match.group("user"))


def _kind(raw: str | None) -> MediaKind | None:
    """Нормализация типа: у fx это photo/video/gif, у vx — image/video/gif."""
    match (raw or "").lower():
        case "photo" | "image":
            return MediaKind.PHOTO
        case "video":
            return MediaKind.VIDEO
        case "gif" | "animated_gif":
            return MediaKind.GIF
        case _:
            return None


def _media_items(raw_list: Any, *, drop_cards: bool) -> list[TweetMedia]:
    items: list[TweetMedia] = []
    for raw in raw_list or []:
        if not isinstance(raw, dict):
            continue
        kind = _kind(raw.get("type"))
        url = raw.get("url")
        if kind is None or not url:
            continue
        if drop_cards and _CARD_IMAGE in url:
            continue
        items.append(TweetMedia(kind=kind, url=url))
    return items


def _date_from_timestamp(value: Any) -> str:
    if not isinstance(value, (int, float)) or value <= 0:
        return ""
    return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()


def _fx_media_all(tweet: dict[str, Any]) -> Any:
    """media.all сохраняет исходный порядок фото и видео в посте."""
    media = tweet.get("media")
    return media.get("all") if isinstance(media, dict) else None


def parse_fx(payload: dict[str, Any]) -> TweetData:
    """Ответ api.fxtwitter.com: {"code":200,"tweet":{...}}.

    Если своих медиа у поста нет, но это цитата — берём медиа цитируемого поста:
    пользователь кидает ссылку на цитату, а хочет ровно те байты.
    """
    tweet = payload.get("tweet")
    if not isinstance(tweet, dict):
        # code 404 с tweet:null или type "tombstone" — удалён / приватный / NSFW.
        raise TweetParseError(payload.get("message") or "пост недоступен")
    if tweet.get("type") == "tombstone":
        raise TweetParseError(tweet.get("message") or "пост недоступен")

    raw_media = _fx_media_all(tweet)
    if not raw_media:
        quote = tweet.get("quote")
        if isinstance(quote, dict):
            raw_media = _fx_media_all(quote)

    author = tweet.get("author") or {}
    screen_name = author.get("screen_name") or "i"
    tweet_id = str(tweet.get("id") or "")
    return TweetData(
        tweet_id=tweet_id,
        url=tweet.get("url") or f"https://x.com/{screen_name}/status/{tweet_id}",
        author=screen_name,
        text=tweet.get("text") or "",
        upload_date=_date_from_timestamp(tweet.get("created_timestamp")),
        media=_media_items(raw_media, drop_cards=False),
    )


def parse_vx(payload: dict[str, Any]) -> TweetData:
    """Ответ api.vxtwitter.com: плоский объект с media_extended[]."""
    if not isinstance(payload, dict) or "media_extended" not in payload:
        raise TweetParseError("пост недоступен")

    screen_name = payload.get("user_screen_name") or "i"
    tweet_id = str(payload.get("tweetID") or "")
    return TweetData(
        tweet_id=tweet_id,
        url=payload.get("tweetURL") or f"https://x.com/{screen_name}/status/{tweet_id}",
        author=screen_name,
        text=payload.get("text") or "",
        upload_date=_date_from_timestamp(payload.get("date_epoch")),
        media=_media_items(payload.get("media_extended"), drop_cards=True),
    )
