"""Хранение выбранного режима захвата между перезапусками.

Отдельный state.json, а не перезапись .env: .env — это ручной конфиг с комментариями,
переписывать его из кода значит однажды потерять чужую строку. Здесь же файл целиком
принадлежит боту, и его не жалко удалить — тогда режим вернётся к CAPTURE_MODE.

Модуль знает про ФС, но не про telegram.
"""
from __future__ import annotations

import json
import logging

from . import config
from .core.fs import atomic_write_text
from .core.mode import CaptureMode, parse_mode

log = logging.getLogger(__name__)

_cached: CaptureMode | None = None


def _default() -> CaptureMode:
    # validate() уже отвергла бы мусор в CAPTURE_MODE, но /mode может вызываться
    # и в тестах без validate — подстраховываемся историческим поведением.
    return parse_mode(config.DEFAULT_MODE_RAW) or CaptureMode.BOTH


def get_mode() -> CaptureMode:
    """Текущий режим. Читает state.json один раз, дальше отдаёт из памяти."""
    global _cached
    if _cached is not None:
        return _cached

    mode = _default()
    try:
        raw = json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
        stored = parse_mode(str(raw.get("mode", "")))
        if stored is not None:
            mode = stored
        else:
            log.warning("в %s непонятный режим — беру %s", config.STATE_FILE, mode.value)
    except FileNotFoundError:
        pass  # первый запуск
    except (OSError, ValueError) as e:
        # Битый json не должен мешать боту стартовать: молча откатываемся на дефолт.
        log.warning("не прочитать %s (%s) — режим %s", config.STATE_FILE, e, mode.value)

    _cached = mode
    return mode


def set_mode(mode: CaptureMode) -> None:
    """Запоминает режим в памяти и на диске. Сбой записи не отменяет переключение."""
    global _cached
    _cached = mode
    try:
        atomic_write_text(config.STATE_FILE, json.dumps({"mode": mode.value}, indent=2) + "\n")
    except OSError as e:
        log.warning("режим %s не сохранён на диск (%s) — до перезапуска", mode.value, e)


def reset_cache() -> None:
    """Сбрасывает кэш — нужно тестам, подменяющим STATE_FILE."""
    global _cached
    _cached = None
