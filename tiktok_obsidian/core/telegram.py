"""Пересланные Telegram-сообщения: чистая логика без aiogram и сети.

Слой handlers маппит объекты aiogram (`forward_origin`, `MessageEntity`) на датаклассы
этого модуля, а дальше всё считается здесь: `post_id`/`author`/`url` из origin,
Markdown из текста с сущностями, сбор TikTok/X-ссылок (включая скрытые `text_link`).

Важно: смещения сущностей Telegram — в кодовых единицах UTF-16, поэтому наивная
нарезка `str` ломается на эмодзи. Режем байты `utf-16-le` и декодируем обратно.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .urls import extract_post_url, extract_post_urls

# Проверка «остался ли осмысленный текст»: буква (юникод, без _) или цифра.
_LETTER_OR_DIGIT = re.compile(r"[^\W_]|\d")


@dataclass(frozen=True)
class TgEntity:
    """Минимальный слепок aiogram `MessageEntity`: тип и UTF-16-смещение/длина."""

    type: str
    offset: int
    length: int
    url: str = ""  # только для text_link
    language: str = ""  # только для pre


@dataclass(frozen=True)
class ForwardOrigin:
    """Слепок `message.forward_origin`. kind — один из четырёх типов origin."""

    kind: str  # "channel" | "chat" | "user" | "hidden"
    date: int = 0  # unix timestamp (forward_origin.date)
    message_id: int = 0  # только для channel
    chat_id: int = 0  # channel.id / sender_chat.id
    chat_title: str = ""  # channel.title / sender_chat.title
    chat_username: str = ""  # только для channel (без @)
    user_id: int = 0  # только для user
    first_name: str = ""  # только для user
    last_name: str = ""  # только для user
    username: str = ""  # только для user (без @)
    sender_name: str = ""  # только для hidden


@dataclass(frozen=True)
class Identity:
    """post_id/author/url/upload_date пересланного сообщения (или альбома)."""

    post_id: str
    author: str
    url: str
    upload_date: str = ""  # YYYY-MM-DD из forward_origin.date


def _upload_date(unix: int) -> str:
    if not unix:
        return ""
    return datetime.fromtimestamp(unix, tz=timezone.utc).strftime("%Y-%m-%d")


def origin_identity(origin: ForwardOrigin, album_min_message_id: int = 0) -> Identity:
    """Строит идентификаторы из origin. Для альбома — минимальный message_id."""
    upload_date = _upload_date(origin.date)
    if origin.kind == "channel":
        mid = album_min_message_id or origin.message_id
        post_id = f"tg_{abs(origin.chat_id)}_{mid}"
        author = origin.chat_title
        if origin.chat_username:
            author += f" (@{origin.chat_username})"
            url = f"https://t.me/{origin.chat_username}/{mid}"
        else:
            internal = str(abs(origin.chat_id))
            internal = internal[3:] if internal.startswith("100") else internal
            url = f"https://t.me/c/{internal}/{mid}"
        return Identity(post_id=post_id, author=author, url=url, upload_date=upload_date)
    if origin.kind == "chat":
        return Identity(
            post_id=f"tg_{abs(origin.chat_id)}_{origin.date}",
            author=origin.chat_title,
            url="",
            upload_date=upload_date,
        )
    if origin.kind == "user":
        full = f"{origin.first_name} {origin.last_name}".strip()
        author = full + (f" (@{origin.username})" if origin.username else "")
        return Identity(
            post_id=f"tg_u{origin.user_id}_{origin.date}",
            author=author or str(origin.user_id),
            url="",
            upload_date=upload_date,
        )
    # hidden: имени и ID нет — только отображаемое имя + дата, хэшируем.
    digest = hashlib.sha1(f"{origin.sender_name}{origin.date}".encode("utf-8")).hexdigest()[:12]
    return Identity(
        post_id=f"tg_h{digest}",
        author=origin.sender_name,
        url="",
        upload_date=upload_date,
    )


# --- Entities → Markdown ---

# Порядок открытия при совпадающих границах: блочные и ссылки — раньше инлайна,
# чтобы получалось `[**x**](url)`, а не `**[x](url)**`.
_OPEN_PRIORITY = {
    "pre": 0,
    "blockquote": 1,
    "expandable_blockquote": 1,
    "text_link": 2,
}

_INLINE_OPEN = {
    "bold": "**",
    "italic": "*",
    "strikethrough": "~~",
    "code": "`",
    "text_link": "[",
}
_INLINE_CLOSE = {
    "bold": "**",
    "italic": "*",
    "strikethrough": "~~",
    "code": "`",
}
# Сущности без маркеров: либо рендерятся как есть (url/mention/hashtag/...),
# либо сбрасываются в plain text (underline/spoiler/custom_emoji/text_mention).
_PLAIN_TYPES = frozenset(
    {
        "underline",
        "spoiler",
        "custom_emoji",
        "text_mention",
        "url",
        "mention",
        "hashtag",
        "cashtag",
        "email",
        "phone_number",
        "bot_command",
    }
)
_BLOCKQUOTE_TYPES = frozenset({"blockquote", "expandable_blockquote"})


def _open_marker(entity: TgEntity) -> str:
    if entity.type == "pre":
        return f"```{entity.language}\n" if entity.language else "```\n"
    if entity.type == "text_link":
        return "["
    return _INLINE_OPEN.get(entity.type, "")


def _close_marker(entity: TgEntity) -> str:
    if entity.type == "pre":
        return "\n```"
    if entity.type == "text_link":
        return f"]({entity.url})"
    return _INLINE_CLOSE.get(entity.type, "")


# Инлайн-сущности, которые чистим по краям (B5). Блочные (pre/blockquote)
# не трогаем: у них переводы строк — часть формата.
_INLINE_TRIM_TYPES = frozenset({"bold", "italic", "strikethrough", "code", "text_link"})


def _emit_trimmed(
    raw: bytes, out: list[TgEntity], entity: TgEntity, start: int, end: int
) -> None:
    """Кусок [start, end) с обрезанными пробелами по краям (всё в единицах UTF-16)."""
    if end <= start:
        return
    piece = raw[2 * start : 2 * end].decode("utf-16-le")
    if not piece.strip():
        return
    lead = piece[: len(piece) - len(piece.lstrip())]
    trail = piece[len(piece.rstrip()) :]
    start += len(lead.encode("utf-16-le")) // 2
    end -= len(trail.encode("utf-16-le")) // 2
    if end <= start:
        return
    out.append(
        TgEntity(
            type=entity.type,
            offset=start,
            length=end - start,
            url=entity.url,
            language=entity.language,
        )
    )


def _normalize_inline_entities(text: str, entities: list[TgEntity]) -> list[TgEntity]:
    """Чистит инлайн-сущности до рендера: режет по `\\n` на построчные куски,
    сдвигает границы с пробелов (пробел остаётся плейн-текстом), пустые куски
    выбрасывает. Иначе `**` на границе пробела/переноса не замыкается в Obsidian
    и виден как литерал. Всё в единицах UTF-16, плейн-текст не экранируется."""
    raw = text.encode("utf-16-le")
    total = len(raw) // 2
    out: list[TgEntity] = []
    for e in entities:
        if e.type not in _INLINE_TRIM_TYPES:
            out.append(e)
            continue
        start = min(max(e.offset, 0), total)
        end = min(max(e.offset + e.length, 0), total)
        span = raw[2 * start : 2 * end].decode("utf-16-le")
        pos = line_start = start
        for ch in span:
            units = len(ch.encode("utf-16-le")) // 2
            if ch == "\n":
                _emit_trimmed(raw, out, e, line_start, pos)
                pos += units
                line_start = pos
            else:
                pos += units
        _emit_trimmed(raw, out, e, line_start, pos)
    return out


def _clip_entities(text: str, entities: list[TgEntity]) -> tuple[bytes, list[TgEntity]]:
    """UTF-16-байты текста + сущности с границами, обрезанными под текст."""
    raw = text.encode("utf-16-le")
    total = len(raw) // 2
    clipped: list[TgEntity] = []
    for e in entities:
        start = min(max(e.offset, 0), total)
        end = min(max(e.offset + e.length, 0), total)
        if end <= start:
            continue
        clipped.append(
            TgEntity(
                type=e.type,
                offset=start,
                length=end - start,
                url=e.url,
                language=e.language,
            )
        )
    return raw, clipped


def entities_to_markdown(text: str, entities: list[TgEntity]) -> str:
    """Текст с сущностями форматирования → Markdown для Obsidian.

    Плейн-текст не экранируется (хэштеги должны оставаться живыми тегами).
    Вложенность раскрывается стеком открытых сущностей по границам спанов.
    """
    if not text:
        return ""
    if not entities:
        return text
    raw, clipped = _clip_entities(text, _normalize_inline_entities(text, entities))
    if not clipped:
        return text

    bounds = {0, len(raw) // 2}
    for e in clipped:
        bounds.add(e.offset)
        bounds.add(e.offset + e.length)
    points = sorted(bounds)

    def sort_key(e: TgEntity) -> tuple[int, int, int]:
        return (e.offset, -(e.offset + e.length), _OPEN_PRIORITY.get(e.type, 3))

    out: list[str] = []
    stack: list[TgEntity] = []
    at_line_start = True

    def emit(chunk: str) -> None:
        nonlocal at_line_start
        out.append(chunk)
        if chunk:
            at_line_start = chunk.endswith("\n")

    for left, right in zip(points, points[1:]):
        active = [e for e in clipped if e.offset <= left and e.offset + e.length >= right]
        active.sort(key=sort_key)
        depth = 0
        while (
            depth < len(stack) and depth < len(active) and stack[depth] == active[depth]
        ):
            depth += 1
        for e in reversed(stack[depth:]):
            emit(_close_marker(e))
        for e in active[depth:]:
            emit(_open_marker(e))
            if e.type in _BLOCKQUOTE_TYPES and not at_line_start:
                # Цитата началась не с начала строки — переносим на новую,
                # иначе префикс `> ` приклеится к чужому тексту.
                emit("\n")
        stack = active
        # errors="replace" — только страховка от битых границ: валидные сущности
        # Telegram surrogate pairs не рвут, и декодирование точное.
        segment = raw[2 * left : 2 * right].decode("utf-16-le", errors="replace")
        if any(e.type in _BLOCKQUOTE_TYPES for e in stack):
            if at_line_start:
                segment = "> " + segment.replace("\n", "\n> ")
            else:
                segment = segment.replace("\n", "\n> ")
            segment = segment.removesuffix("> ") if segment.endswith("\n> ") else segment
        emit(segment)
    for e in reversed(stack):
        emit(_close_marker(e))
    return "".join(out)


# --- Сбор ссылок и link-only ---


@dataclass
class CollectedLinks:
    urls: list[str]  # в порядке первого появления, без дублей, уже с капом
    overflow: int = 0  # сколько ссылок отрезал кап


def _utf16_offset_to_str_index(text: str, offset: int) -> int:
    raw = text.encode("utf-16-le")
    total = len(raw) // 2
    offset = min(max(offset, 0), total)
    return len(raw[: 2 * offset].decode("utf-16-le"))


def collect_post_urls(
    text: str, entities: list[TgEntity], limit: int | None = 5
) -> CollectedLinks:
    """TikTok/X-ссылки из плейн-текста и из скрытых `text_link`.

    Порядок — по первому появлению в сообщении, дубли схлопываются,
    хвост за `limit` остаётся в тексте и считается в `overflow`.
    """
    if not text:
        return CollectedLinks(urls=[])
    positioned: list[tuple[int, str]] = []
    for parsed in extract_post_urls(text):
        positioned.append((text.find(parsed.url), parsed.url))
    for e in entities:
        if e.type != "text_link" or not e.url:
            continue
        parsed = extract_post_url(e.url)
        if parsed is None:
            continue
        positioned.append((_utf16_offset_to_str_index(text, e.offset), parsed.url))
    positioned.sort(key=lambda pair: pair[0])
    ordered: list[str] = []
    for _, url in positioned:
        if url not in ordered:
            ordered.append(url)
    if limit is not None and len(ordered) > limit:
        return CollectedLinks(urls=ordered[:limit], overflow=len(ordered) - limit)
    return CollectedLinks(urls=ordered)


def is_link_only(text: str, entities: list[TgEntity]) -> bool:
    """В сообщении нет ничего, кроме поддерживаемых ссылок.

    Текст после удаления всех TikTok/X-URL (включая скрытые за text_link) не
    содержит ни букв, ни цифр — такой форвард обрабатываем как вставленную
    ссылку, без материнской Telegram-заметки.
    """
    links = collect_post_urls(text, entities, limit=None)
    rest = text
    for url in links.urls:
        rest = rest.replace(url, " ")
    return _LETTER_OR_DIGIT.search(rest) is None
