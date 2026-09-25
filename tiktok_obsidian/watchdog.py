"""Сторож родительского процесса для запуска из Obsidian-плагина.

Windows не убивает дочерние процессы при смерти родителя, а `onunload` Obsidian
при выходе ненадёжен — без сторожа закрытие Obsidian оставляло бы бота-сироту,
и следующий запуск упирался бы в `409 Conflict`. Плагин передаёт свой PID флагом
`--parent-pid`, сторож раз в 5 с проверяет родителя через `OpenProcess` +
`WaitForSingleObject` и останавливает polling, когда родителя не стало.

Проверку живости держим за функцией `parent_alive`, чтобы тесты подменяли её
фейком. Важно: `os.kill(pid, 0)` на Windows для проверки НЕ использовать — он не
пробует, а вызывает `TerminateProcess` и **убивает** цель.
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import time
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

CHECK_INTERVAL_S = 5.0
# Хард-фолбэк: родитель мёртв, а процесс всё ещё жив (stop_polling не сработал).
# Заметки атомарны, bot.lock снимает ОС — os._exit безопасен.
FALLBACK_TIMEOUT_S = 30.0
_STOP_RETRY_S = 1.0

_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0x00000000


def parent_alive(pid: int) -> bool:
    """Жив ли процесс с PID. False при закрытом хендле и при signaled-состоянии."""
    kernel32 = ctypes.windll.kernel32  # noqa: SLF001 — WinAPI иначе не вызвать
    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        # WAIT_OBJECT_0: процесс завершён (хендл всегда signaled для мёртвого).
        return kernel32.WaitForSingleObject(handle, 0) != _WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


async def watch_parent(
    parent_pid: int,
    shutdown: asyncio.Event,
    stop_polling: Callable[[], Awaitable[None] | None],
    *,
    check: Callable[[int], bool] = parent_alive,
    interval: float = CHECK_INTERVAL_S,
    stop_retry_interval: float = _STOP_RETRY_S,
    fallback_timeout: float = FALLBACK_TIMEOUT_S,
    exit_func: Callable[[int], None] = os._exit,
    clock: Callable[[], float] = time.monotonic,
    on_shutdown: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Петля сторожа: родитель пропал → выставить shutdown и останавливать polling.

    `stop_polling()` кидает `RuntimeError`, когда polling не запущен (бэкoff без
    сети), и молча возвращается в стартовой гонке — поэтому зовём его повторно
    каждую секунду, пока `_run` не отменит задачу. Если процесс жив через
    `fallback_timeout` после смерти родителя — принудительный `os._exit(0)`.
    Неожиданные исключения логируются с трейсбэком и ведут на тот же путь.
    `on_shutdown` (дренаж висящих форвардов) выполняется один раз, пока polling
    ещё жив и сессия открыта, — до первой попытки stop_polling.
    """
    parent_dead_since: float | None = None
    drained = False
    exception_logged = False
    while True:
        try:
            await asyncio.sleep(interval if parent_dead_since is None else stop_retry_interval)
            if parent_dead_since is None and not check(parent_pid):
                parent_dead_since = clock()
                log.warning("parent exited, stopping")
            if parent_dead_since is not None:
                shutdown.set()
                if not drained:
                    drained = True
                    if on_shutdown is not None:
                        try:
                            await on_shutdown()
                        except Exception:
                            log.exception("shutdown drain failed")
                try:
                    result = stop_polling()
                    if asyncio.iscoroutine(result):
                        await result
                except RuntimeError:
                    log.debug("stop_polling not running (backoff?), retrying")
                if clock() - parent_dead_since >= fallback_timeout:
                    log.error(
                        "parent dead %.0fs ago, still alive — forcing exit",
                        clock() - parent_dead_since,
                    )
                    exit_func(0)
                    return
        except Exception:
            # CancelledError — не Exception, отмена задачи проходит мимо.
            # Взводим тот же shutdown-путь, что при смерти родителя: иначе бот
            # продолжал бы поллить, а лог получал бы трейсбэк каждые 5 с вечно.
            if not exception_logged:
                log.exception("parent watchdog failed")
                exception_logged = True
            if parent_dead_since is None:
                parent_dead_since = clock()
            shutdown.set()


def start_watchdog(
    parent_pid: int | None,
    shutdown: asyncio.Event,
    dispatcher,
    *,
    on_shutdown: Callable[[], Awaitable[None]] | None = None,
) -> asyncio.Task | None:
    """Создаёт задачу сторожа, если флаг `--parent-pid` передан. Иначе None —
    поведение как раньше (ручной запуск из VS Code)."""
    if parent_pid is None:
        return None
    log.info("parent watchdog: following pid %d", parent_pid)
    return asyncio.create_task(
        watch_parent(
            parent_pid, shutdown, dispatcher.stop_polling, on_shutdown=on_shutdown
        )
    )
