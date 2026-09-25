"""Безопасный текст для чата: усечение в единицах UTF-16.

Telegram считает длину в UTF-16, а бот шлёт чужие тексты (подписи постов, вывод
pip, строки лога). Наивный срез `[:4000]` по кодпоинтам на эмодзи может вылезти
за лимит, а отправка сырого текста с HTML-парсингом падает на любом `<`.
"""
from __future__ import annotations

import re


def truncate_utf16(text: str, limit: int) -> str:
    """Обрезает текст до `limit` единиц UTF-16, не разрывая суррогатную пару."""
    if limit < 0:
        raise ValueError("limit must be non-negative")
    raw = text.encode("utf-16-le")
    if len(raw) // 2 <= limit:
        return text
    cut = raw[: 2 * limit]
    try:
        return cut.decode("utf-16-le")
    except UnicodeDecodeError:
        # Срез попал в середину суррогатной пары — отступаем на единицу.
        return cut[:-2].decode("utf-16-le")


def truncate_utf16_tail(text: str, limit: int) -> str:
    """Хвост текста в `limit` единиц UTF-16, не разрывая суррогатную пару спереди."""
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if limit == 0:
        return ""
    raw = text.encode("utf-16-le")
    if len(raw) // 2 <= limit:
        return text
    cut = raw[-2 * limit :]
    try:
        return cut.decode("utf-16-le")
    except UnicodeDecodeError:
        # Срез рассёк суррогатную пару спереди — пропускаем обломок.
        return cut[2:].decode("utf-16-le")


def utf16_len(text: str) -> int:
    """Длина в единицах UTF-16 — в них считает лимиты Telegram."""
    return len(text.encode("utf-16-le")) // 2


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Убирает ANSI-коды цветов (yt-dlp красит ERROR даже в quiet-режиме).

    Пояс на всякий случай: в опциях yt-dlp стоит `"color": "no_color"`,
    но старые/чужие тексты чистим всё равно — в чате коды видны как мусор.
    """
    return _ANSI.sub("", text)


# Подстроки преходящих сетевых сбоев (регистр не важен). По ним downloader
# повторяет попытку; всё остальное (приват, удалено, Unsupported URL, логин) —
# сразу ошибка без ретрая.
_TRANSIENT_PATTERNS = (
    "unexpected_eof_while_reading",
    "ssl handshake timed out",
    "handshake timeout",
    "curl: (28)",
    "curl: (35)",
    "timed out",
    "timeout",
    "unexpected response from webpage request",
    "connection reset",
    "connection aborted",
    "temporary failure in name resolution",
)


def is_transient(error_text: str) -> bool:
    """Преходящий ли сетевой сбой — стоит ли повторить запрос."""
    low = error_text.lower()
    return any(pattern in low for pattern in _TRANSIENT_PATTERNS)
