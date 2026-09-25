"""Watchdog: фейк живости → остановка polling; без флага задач нет; B2-кейсы."""
import asyncio
import os

import pytest
from aiogram.exceptions import TelegramNetworkError

from tiktok_obsidian import main as main_module
from tiktok_obsidian.watchdog import parent_alive, start_watchdog, watch_parent


def test_parent_alive_self_and_missing():
    assert parent_alive(os.getpid()) is True
    assert parent_alive(0) is False


async def test_watchdog_requests_stop_when_parent_dead():
    shutdown = asyncio.Event()
    alive = [True, True, False]
    stops = []

    async def fake_stop():
        stops.append(True)

    async def bounded():
        try:
            await asyncio.wait_for(
                watch_parent(
                    4242,
                    shutdown,
                    fake_stop,
                    check=lambda pid: alive.pop(0) if alive else False,
                    interval=0,
                    stop_retry_interval=0,
                    fallback_timeout=60,
                ),
                timeout=0.05,
            )
        except asyncio.TimeoutError:
            pass

    await bounded()
    assert stops and all(s is True for s in stops)
    assert shutdown.is_set()


async def test_watchdog_accepts_sync_stop():
    shutdown = asyncio.Event()
    stops = []

    async def bounded():
        try:
            await asyncio.wait_for(
                watch_parent(
                    4242,
                    shutdown,
                    lambda: stops.append(True),
                    check=lambda pid: False,
                    interval=0,
                    stop_retry_interval=0,
                    fallback_timeout=60,
                ),
                timeout=0.05,
            )
        except asyncio.TimeoutError:
            pass

    await bounded()
    assert stops and all(s is True for s in stops)
    assert shutdown.is_set()


async def test_watchdog_keeps_running_while_parent_alive():
    shutdown = asyncio.Event()
    checks = []

    async def fake_stop():
        raise AssertionError("stop must not be called")

    async def bounded():
        try:
            await asyncio.wait_for(
                watch_parent(
                    4242,
                    shutdown,
                    fake_stop,
                    check=lambda pid: checks.append(pid) or True,
                    interval=0,
                    fallback_timeout=60,
                ),
                timeout=0.05,
            )
        except asyncio.TimeoutError:
            pass

    await bounded()
    assert checks and all(pid == 4242 for pid in checks)
    assert not shutdown.is_set()


async def test_no_flag_no_watchdog_task():
    class FakeDispatcher:
        def stop_polling(self):
            raise AssertionError("must not be called")

    assert start_watchdog(None, asyncio.Event(), FakeDispatcher()) is None


async def test_flag_creates_watchdog_task():
    class FakeDispatcher:
        async def stop_polling(self):
            pass

    task = start_watchdog(4242, asyncio.Event(), FakeDispatcher())
    assert isinstance(task, asyncio.Task)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# --- B2: смерть родителя во время сетевого бэкоффа ---


async def test_watchdog_survives_runtime_error_from_stop():
    """stop_polling кидает RuntimeError (polling не запущен) → событие всё равно
    выставлено, сторож не падает, а зовёт снова."""
    shutdown = asyncio.Event()
    stops = []

    def flaky_stop():
        stops.append(True)
        raise RuntimeError("Polling is not started")

    async def bounded():
        try:
            await asyncio.wait_for(
                watch_parent(
                    4242,
                    shutdown,
                    flaky_stop,
                    check=lambda pid: False,
                    interval=0,
                    stop_retry_interval=0,
                    fallback_timeout=60,
                ),
                timeout=0.05,
            )
        except asyncio.TimeoutError:
            pass

    await bounded()
    assert shutdown.is_set()
    assert len(stops) >= 2


async def test_watchdog_retries_ineffective_stop():
    """stop без эффекта → зовём снова каждый тик."""
    shutdown = asyncio.Event()
    stops = []

    async def bounded():
        try:
            await asyncio.wait_for(
                watch_parent(
                    4242,
                    shutdown,
                    lambda: stops.append(True),
                    check=lambda pid: False,
                    interval=0,
                    stop_retry_interval=0,
                    fallback_timeout=60,
                ),
                timeout=0.05,
            )
        except asyncio.TimeoutError:
            pass

    await bounded()
    assert len(stops) >= 2


async def test_watchdog_fallback_exit():
    """Процесс жив через fallback_timeout после смерти родителя → exit_func(0)."""
    shutdown = asyncio.Event()
    exits = []
    await watch_parent(
        4242,
        shutdown,
        lambda: None,
        check=lambda pid: False,
        interval=0,
        stop_retry_interval=0,
        fallback_timeout=0,
        exit_func=lambda code: exits.append(code),
    )
    assert shutdown.is_set()
    assert exits == [0]


async def test_poll_forever_returns_promptly_on_shutdown_during_backoff():
    """Сеть лежит (бэкофф 5 с) → событие → возврат за ~мгновение, а не через 5 с."""
    import time

    async def failing_polling(*args, **kwargs):
        raise TelegramNetworkError("getMe", "no network")

    class FakeDp:
        start_polling = failing_polling

    shutdown = asyncio.Event()
    started = time.monotonic()
    task = asyncio.create_task(main_module._poll_forever(FakeDp(), object(), shutdown))
    await asyncio.sleep(0.2)  # дать войти в бэкофф
    shutdown.set()
    await asyncio.wait_for(task, timeout=2)
    assert time.monotonic() - started < 5


# --- F16: connection-level failures use the same interruptible backoff ---


def _network_error_cases():
    import aiohttp
    from aiohttp.client_reqrep import ConnectionKey

    cases = [
        (aiohttp.ClientConnectorError(
            ConnectionKey("127.0.0.1", 9, True, True, None, None, None, None),
            ConnectionRefusedError(111, "connection refused"),
        ), "127.0.0.1"),
        (ConnectionRefusedError(111, "connection refused"), "connection refused"),
    ]
    try:
        import python_socks
    except ImportError:
        pass
    else:
        cases.extend([
            (python_socks.ProxyConnectionError("proxy connection refused"), "proxy connection refused"),
            (python_socks.ProxyTimeoutError("proxy timeout"), "proxy timeout"),
            (python_socks.ProxyError("proxy error"), "proxy error"),
        ])
    try:
        import aiohttp_socks
    except ImportError:
        pass
    else:
        cases.extend([
            (aiohttp_socks.ProxyConnectionError("aiohttp-socks proxy connection refused"), "aiohttp-socks proxy connection refused"),
            (aiohttp_socks.ProxyTimeoutError("aiohttp-socks proxy timeout"), "aiohttp-socks proxy timeout"),
            (aiohttp_socks.ProxyError("aiohttp-socks proxy error"), "aiohttp-socks proxy error"),
        ])
    return cases


@pytest.mark.parametrize("network_error, message", _network_error_cases())
async def test_poll_forever_network_errors_back_off_and_shutdown_interrupts(
    network_error, message, monkeypatch, caplog
):
    import logging
    import time

    monkeypatch.setattr(main_module.config, "TELEGRAM_PROXY", "http://user:secret@127.0.0.1:9")
    calls = []

    class FakeDp:
        async def start_polling(self, *_args, **_kwargs):
            calls.append(True)
            raise network_error

    shutdown = asyncio.Event()
    async def request_shutdown():
        await asyncio.sleep(0.05)
        shutdown.set()

    stopper = asyncio.create_task(request_shutdown())
    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger=main_module.__name__):
        await asyncio.wait_for(main_module._poll_forever(FakeDp(), object(), shutdown), timeout=1)
    await stopper

    assert calls == [True]
    assert time.monotonic() - started < 1
    assert "http://***@127.0.0.1:9" in caplog.text
    assert "прокси недоступен — запусти прокси-клиент или очисти TELEGRAM_PROXY" in caplog.text
    assert "secret" not in caplog.text
