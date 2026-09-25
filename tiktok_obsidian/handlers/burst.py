"""Буферизация альбомов и спаривание комментариев владельца с форвардами.

Telegram присылает альбом N отдельными апдейтами, а комментарий к форварду —
отдельным текстовым сообщением до/после форварда. aiogram обрабатывает апдейты
конкурентно, поэтому склейка делается здесь, по таймерам.

Модуль без Telegram-зависимостей: время инжектится (`now`), сами ожидания
(`asyncio.sleep`) живут в handlers. Юнит-тесты двигают часы вручную.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..core.mode import CaptureMode


@dataclass
class AlbumGroup:
    items: list = field(default_factory=list)
    last_update: float = 0.0
    generation: int = 0


@dataclass
class PendingComment:
    text: str
    override: CaptureMode | None
    received_at: float
    consumed: bool = False  # хотя бы один форвард уже забрал комментарий


class BurstBuffer:
    """Склейка альбомов по (chat_id, media_group_id) и комментариев по chat_id."""
    def __init__(self, now: Callable[[], float] | None = None) -> None:
        self._now = now or time.monotonic
        self._albums: dict[tuple[int, str], AlbumGroup] = {}
        self._comments: dict[int, PendingComment] = {}

    # --- Альбомы ---

    def album_add(self, key: tuple[int, str], item) -> int:
        """Добавляет элемент альбома. Возвращает generation для задачи-таймера."""
        group = self._albums.get(key)
        if group is None:
            group = AlbumGroup()
            self._albums[key] = group
        group.items.append(item)
        group.last_update = self._now()
        group.generation += 1
        return group.generation

    def album_take_if_due(
        self, key: tuple[int, str], generation: int, wait_s: float
    ) -> list | None:
        """Забирает склеенный альбом, если с последнего элемента прошло wait_s.

        Возвращает None, если приехал элемент новее (чужой generation) или окно
        ещё не вышло — тогда активна другая задача-таймер.
        """
        group = self._albums.get(key)
        if group is None or group.generation != generation:
            return None
        if self._now() - group.last_update < wait_s:
            return None
        del self._albums[key]
        return group.items

    # --- Комментарии владельца ---

    def comment_add(
        self, chat_id: int, text: str, override: CaptureMode | None = None
    ) -> None:
        self._comments[chat_id] = PendingComment(
            text=text, override=override, received_at=self._now()
        )

    def comment_take(self, chat_id: int, wait_s: float) -> PendingComment | None:
        """Забирает комментарий для подошедшего форварда, не удаляя его: один
        комментарий — это «Впечатления» КАЖДОЙ заметки из всплеска форвардов
        внутри окна (и его `!mode`-токен действует на весь всплеск).

        Просроченный комментарий выкидывается и возвращается None.
        """
        pending = self._comments.get(chat_id)
        if pending is None:
            return None
        if self._now() - pending.received_at > wait_s:
            del self._comments[chat_id]
            return None
        pending.consumed = True
        return pending

    def comment_take_if_expired(self, chat_id: int, wait_s: float) -> PendingComment | None:
        """Забирает комментарий для задачи-таймера: только если окно вышло и его
        никто не спарил с форвардом — тогда handler отвечает «Не вижу ссылку»."""
        pending = self._comments.get(chat_id)
        if pending is None:
            return None
        if self._now() - pending.received_at < wait_s:
            return None
        del self._comments[chat_id]
        return pending if not pending.consumed else None

    def flush_all(self) -> list[list]:
        """Забирает ВСЕ недождавшиеся альбомы (для graceful shutdown): чьи-то
        апдейты уже вычитаны с серверов Telegram и иначе потеряются."""
        groups = list(self._albums.values())
        self._albums.clear()
        return [group.items for group in groups if group.items]


# Общий буфер handlers: альбомы клеит forward, комментарии складывает capture,
# забирает forward. Тесты создают свои `BurstBuffer()` с ручными часами.
buffer = BurstBuffer()
