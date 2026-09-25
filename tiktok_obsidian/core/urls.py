"""Извлечение ссылки на пост и пользовательского комментария из текста сообщения."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Source
from .twitter import TWEET_URL

# Все поддерживаемые формы TikTok-ссылок: полная, mobile, короткие vm./vt.
# Матч стопается на пробеле и символах, которых в этих URL не бывает («»"<>).
_TIKTOK_URL = re.compile(
    r"https?://(?:www\.|m\.|vm\.|vt\.)?tiktok\.com/[^\s«»\"<>]+",
    re.IGNORECASE,
)

# Хвостовая пунктуация/скобки, прилипающие при копипасте из текста.
_TAIL_STRIP = ")]}>.,;:!?»\"'…"

# Запятая, за которой идёт не-URL-символ («.../ZS123/,круто»): дальше не ссылка.
_TIKTOK_COMMA_CUT = re.compile(r",(?![A-Za-z0-9_/?#&=\%.~:+\-])")


def _clean_url(raw: str, source: Source) -> str:
    """URL без прилипшего хвоста: обрезка по запятой-клею (только TikTok) и
    срез замыкающей пунктуации. Полную грамматику URL не валидируем."""
    if source is Source.TIKTOK:
        cut = _TIKTOK_COMMA_CUT.search(raw)
        if cut is not None:
            raw = raw[: cut.start()]
    return raw.rstrip(_TAIL_STRIP)

# Числовой ID из полной ссылки на видео/фото-пост (для коротких ссылок ID даёт yt-dlp).
_VIDEO_ID = re.compile(r"/(?:video|photo)/(\d+)")


@dataclass(frozen=True)
class ParsedMessage:
    url: str
    comment: str  # текст сообщения без ссылки; идёт в раздел «Впечатления»
    source: Source = Source.TIKTOK


def _parse(text: str, match: re.Match, source: Source) -> ParsedMessage:
    raw = match.group(0)
    url = _clean_url(raw, source)
    # Комментарий — всё вне сырого спэна ссылки: срезанный хвост («!», «)», «.»)
    # это пунктуация копипасты, а не слова заказчика. Исключение — запятая-клей
    # («.../ZS123/,круто»): её хвост принадлежит комментарию, клеим обратно.
    glued = ""
    if source is Source.TIKTOK:
        cut = _TIKTOK_COMMA_CUT.search(raw)
        if cut is not None:
            glued = raw[cut.start() + 1 :]
    comment = (text[: match.start()] + " " + glued + text[match.end() :]).strip()
    comment = comment.lstrip(",;:").strip()
    if not re.search(r"[^\W_]", comment, re.UNICODE):
        comment = ""
    return ParsedMessage(url=url, comment=comment, source=source)


def extract_tiktok_url(text: str) -> ParsedMessage | None:
    """Находит первую TikTok-ссылку в тексте. Остаток текста — комментарий заказчика.

    Возвращает None, если валидной TikTok-ссылки нет.
    """
    if not text:
        return None
    match = _TIKTOK_URL.search(text)
    return _parse(text, match, Source.TIKTOK) if match else None


def extract_post_url(text: str) -> ParsedMessage | None:
    """Находит ссылку на пост любого поддерживаемого источника: TikTok или X (Twitter).

    Если в сообщении есть обе — берём ту, что встретилась раньше, чтобы поведение
    совпадало с ожиданием «бот берёт первую ссылку».
    """
    if not text:
        return None
    candidates = [
        (match, source)
        for match, source in (
            (_TIKTOK_URL.search(text), Source.TIKTOK),
            (TWEET_URL.search(text), Source.TWITTER),
        )
        if match is not None
    ]
    if not candidates:
        return None
    match, source = min(candidates, key=lambda pair: pair[0].start())
    return _parse(text, match, source)


def extract_post_urls(text: str) -> list[ParsedMessage]:
    """Все ссылки на посты (TikTok и X) в порядке первого появления, без дублей.

    В отличие от `extract_post_url` (одна ссылка + комментарий), возвращает каждую
    найденную ссылку отдельным `ParsedMessage` с пустым `comment` — нужно для
    пересланных Telegram-сообщений, где ссылок может быть несколько.
    """
    if not text:
        return []
    matches = [
        (match, source)
        for match, source in (
            (_TIKTOK_URL.finditer(text), Source.TIKTOK),
            (TWEET_URL.finditer(text), Source.TWITTER),
        )
        for match in match
    ]
    matches.sort(key=lambda pair: pair[0].start())
    seen: set[str] = set()
    out: list[ParsedMessage] = []
    for match, source in matches:
        parsed = _parse(text, match, source)
        if parsed.url not in seen:
            seen.add(parsed.url)
            out.append(ParsedMessage(url=parsed.url, comment="", source=source))
    return out


def video_id_from_url(url: str) -> str | None:
    """Числовой video ID из полной ссылки, если он там есть (иначе None — брать из метаданных)."""
    match = _VIDEO_ID.search(url)
    return match.group(1) if match else None


_PHOTO_PATH = re.compile(r"(tiktok\.com/@[^/]+/)photo/", re.IGNORECASE)


def normalize_for_ytdlp(url: str) -> str:
    """Готовит ссылку для yt-dlp: /photo/ → /video/ (тот же aweme id).

    Экстрактор TikTok в yt-dlp не матчит фото-посты по пути /photo/ и падает с
    «Unsupported URL». ID у фото-поста тот же, что у видео-пути, поэтому переписываем.
    """
    return _PHOTO_PATH.sub(r"\1video/", url)
