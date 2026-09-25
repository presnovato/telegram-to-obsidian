"""O3: graceful shutdown — flush альбомов, ожидание in-flight, хук сторожа."""
import asyncio

import pytest

from tiktok_obsidian.handlers import forward as forward_module


async def test_drain_processes_flushed_albums(monkeypatch):
    handled = []

    async def fake_handle(message, bundle):
        handled.append((message, bundle))

    monkeypatch.setattr(forward_module, "_handle_bundle", fake_handle)
    monkeypatch.setattr(forward_module, "bundle_of_album", lambda items: ("bundle", tuple(items)))
    forward_module.buffer.album_add((9, "g1"), "m1")
    forward_module.buffer.album_add((9, "g1"), "m2")
    try:
        await forward_module.drain_pending(timeout=5)
    finally:
        forward_module.buffer.flush_all()
    assert handled == [("m2", ("bundle", ("m1", "m2")))]


async def test_drain_waits_inflight_handlers():
    done = []

    async def slow():
        await asyncio.sleep(0.05)
        done.append(True)

    task = asyncio.create_task(slow())
    forward_module._INFLIGHT.add(task)
    try:
        await forward_module.drain_pending(timeout=5)
    finally:
        forward_module._INFLIGHT.discard(task)
    assert task.done() and done == [True]


async def test_drain_timeout():
    async def stuck():
        await asyncio.sleep(60)

    task = asyncio.create_task(stuck())
    forward_module._INFLIGHT.add(task)
    try:
        with pytest.raises(asyncio.TimeoutError):
            await forward_module.drain_pending(timeout=0.01)
    finally:
        task.cancel()
        forward_module._INFLIGHT.discard(task)
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_watchdog_runs_on_shutdown_hook():
    from tiktok_obsidian.watchdog import watch_parent

    shutdown = asyncio.Event()
    drained = []

    async def fake_drain():
        drained.append(True)

    async def bounded():
        try:
            await asyncio.wait_for(
                watch_parent(
                    4242,
                    shutdown,
                    lambda: None,
                    check=lambda pid: False,
                    interval=0,
                    stop_retry_interval=0,
                    fallback_timeout=60,
                    on_shutdown=fake_drain,
                ),
                timeout=0.05,
            )
        except asyncio.TimeoutError:
            pass

    await bounded()
    assert drained == [True]
    assert shutdown.is_set()


async def test_watchdog_exception_arms_stop_and_fallback():
    """F13: check падает → stop зовём дальше, fallback срабатывает."""
    from tiktok_obsidian.watchdog import watch_parent

    shutdown = asyncio.Event()
    stops = []
    exits = []

    def boom(pid):
        raise OSError("nope")

    await watch_parent(
        4242,
        shutdown,
        lambda: stops.append(True),
        check=boom,
        interval=0,
        stop_retry_interval=0,
        fallback_timeout=0,
        exit_func=lambda code: exits.append(code),
    )
    assert stops == [True]
    assert exits == [0]
    assert shutdown.is_set()


async def test_watchdog_exception_logged_once(caplog):
    """F13: вечно падающий check — один трейсбэк, а не каждые 5 с."""
    import logging

    from tiktok_obsidian.watchdog import watch_parent

    shutdown = asyncio.Event()

    def boom(pid):
        raise OSError("nope")

    async def bounded():
        try:
            await asyncio.wait_for(
                watch_parent(
                    4242,
                    shutdown,
                    lambda: None,
                    check=boom,
                    interval=0,
                    stop_retry_interval=0,
                    fallback_timeout=60,
                ),
                timeout=0.05,
            )
        except asyncio.TimeoutError:
            pass

    with caplog.at_level(logging.ERROR, logger="tiktok_obsidian.watchdog"):
        await bounded()
    tracebacks = [r for r in caplog.records if r.exc_info]
    assert len(tracebacks) == 1
