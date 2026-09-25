"""Режим захвата: куда уходит пост — в vault, в чат или туда и туда.

Модуль чистый: ни telegram, ни ФС. Хранение выбранного режима — в `mode_store.py`.
"""
from __future__ import annotations

import re
from enum import Enum


class CaptureMode(str, Enum):
    """Куда доставляем пост."""

    VAULT = "vault"  # только заметка + медиа в vault
    CHAT = "chat"  # только в чат, vault не трогаем
    BOTH = "both"  # заметка в vault + медиа в чат (историческое поведение)

    @property
    def writes_vault(self) -> bool:
        return self is not CaptureMode.CHAT

    @property
    def sends_chat(self) -> bool:
        return self is not CaptureMode.VAULT


def parse_mode(value: str) -> CaptureMode | None:
    """Строка из .env или из команды → режим. None, если значение не распознано."""
    try:
        return CaptureMode((value or "").strip().lower())
    except ValueError:
        return None


# Разовое переопределение режима в том же сообщении: «!chat <ссылка>».
# Токен ищем отдельным словом в любом месте текста — ссылку часто пересылают
# и приписка оказывается после неё, а не перед.
_MODE_TOKEN = re.compile(r"(?<!\S)!(vault|chat|both)(?!\S)", re.IGNORECASE)


def strip_mode_override(text: str) -> tuple[CaptureMode | None, str]:
    """Вырезает токен `!chat` / `!vault` / `!both` из текста.

    Возвращает (режим или None, текст без токена). Текст возвращаем очищенным,
    чтобы токен не утёк в комментарий и оттуда в раздел «Впечатления» заметки.
    """
    if not text:
        return None, text
    match = _MODE_TOKEN.search(text)
    if match is None:
        return None, text
    rest = (text[: match.start()] + " " + text[match.end() :]).strip()
    return parse_mode(match.group(1)), rest
