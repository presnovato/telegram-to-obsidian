"""Single-instance lock: один токен — один polling-процесс.

Второй экземпляр получил бы от Telegram `409 Conflict`, поэтому дубликаты
исключены на старте: эксклюзивный неблокирующий лок `msvcrt.locking` на открытом
хендле `bot.lock`. Хендл живёт весь рантайм, ОС снимает лок при смерти процесса —
зависших локов после краша или килла не бывает. PID внутри файла — только для
людей, источником правды является сам лок.
"""
from __future__ import annotations

import msvcrt
import os
from pathlib import Path


class LockTaken(Exception):
    """Файл уже залочен — другой экземпляр бота работает."""


# Смещение lock-байта: далеко за PID-текстом, т.к. msvcrt-лок мандатный —
# чужой хендл не может даже ЧИТАТЬ залоченный байт. Лок на нуле сделал бы PID
# нечитаемым, пока бот работает, а PID нужен людям и плагину.
_LOCK_OFFSET = 1 << 30


class InstanceLock:
    """Держит эксклюзивный лок файла. Использовать как контекстный менеджер."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fh = None

    def acquire(self) -> InstanceLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self._path, "a+b")  # noqa: PTH123 — нужен живой хендл, не байты
        # msvcrt.locking лочит от ТЕКУЩЕЙ позиции — встаём на общий байт явно.
        fh.seek(_LOCK_OFFSET)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            fh.close()
            raise LockTaken(f"another bot instance is running (lock: {self._path})")
        # Лок наш — фиксируем PID для людей и UX плагина.
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n".encode("ascii"))
        fh.flush()
        self._fh = fh
        return self

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            fh.seek(_LOCK_OFFSET)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        fh.close()

    def __enter__(self) -> InstanceLock:
        return self.acquire()

    def __exit__(self, *exc_info: object) -> None:
        self.release()
