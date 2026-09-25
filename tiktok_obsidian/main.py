"""Точка входа: аргументы → лок → логирование → валидация → бот → роутеры (порядок!) → long polling."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from logging.handlers import RotatingFileHandler

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError

from . import config
from .handlers import capture_router, commands_router, forward_router
from .handlers.forward import DRAIN_TIMEOUT_S, drain_pending
from .instance_lock import InstanceLock, LockTaken
from .middleware import OwnerOnlyMiddleware
from .watchdog import start_watchdog

try:
    from python_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError
except ImportError:  # optional unless a SOCKS-capable proxy is configured
    _PYTHON_SOCKS_ERRORS: tuple[type[Exception], ...] = ()
else:
    _PYTHON_SOCKS_ERRORS = (ProxyError, ProxyConnectionError, ProxyTimeoutError)

try:
    from aiohttp_socks import (
        ProxyConnectionError as AiohttpSocksProxyConnectionError,
        ProxyError as AiohttpSocksProxyError,
        ProxyTimeoutError as AiohttpSocksProxyTimeoutError,
    )
except ImportError:  # optional unless a SOCKS-capable proxy is configured
    _AIOHTTP_SOCKS_ERRORS: tuple[type[Exception], ...] = ()
else:
    _AIOHTTP_SOCKS_ERRORS = (
        AiohttpSocksProxyError,
        AiohttpSocksProxyConnectionError,
        AiohttpSocksProxyTimeoutError,
    )

_NETWORK_ERRORS = (
    TelegramNetworkError,
    aiohttp.ClientError,
    asyncio.TimeoutError,
    OSError,
    *_PYTHON_SOCKS_ERRORS,
    *_AIOHTTP_SOCKS_ERRORS,
)

log = logging.getLogger(__name__)

# Контракт кодов выхода с Obsidian-плагином (внутренняя спека плагина; док не публикуется):
# 0 — чистая остановка; 4 — ошибка конфига, не рестартить;
# 2 — неожиданный краш, рестартить; 3 — уже запущен в другом месте, не рестартить.
# Код 1 reserved: так умирает taskkill /F и падение на импорте — для плагина это краш.
# Любой другой код (внезапная смерть интерпретатора) — тоже краш для плагина.
EXIT_ANOTHER_INSTANCE = 3
EXIT_CONFIG_ERROR = 4
EXIT_CRASH = 2

_BACKOFF_MIN = 5  # секунды; первый повтор после потери сети
_BACKOFF_MAX = 300  # потолок паузы между повторами


def _setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Windows-консоль: принудительно UTF-8 для кириллицы/эмодзи.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(
        config.LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI-флаги. Без флагов — сегодняшнее поведение (ручной запуск из VS Code)."""
    parser = argparse.ArgumentParser(prog="tiktok_obsidian")
    parser.add_argument(
        "--parent-pid",
        type=int,
        default=None,
        help="PID родительского процесса (Obsidian): следить и останавливаться вместе с ним",
    )
    return parser.parse_args(argv)


async def _run(parent_pid: int | None = None) -> None:
    config.validate()
    # Прокси только если задан в .env (иначе прямое соединение).
    session = AiohttpSession(proxy=config.TELEGRAM_PROXY) if config.TELEGRAM_PROXY else None
    if config.TELEGRAM_PROXY:
        log.info("используется прокси для Telegram: %s", config.TELEGRAM_PROXY)
    bot = Bot(
        token=config.TELEGRAM_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.message.middleware(OwnerOnlyMiddleware())
    # Порядок роутеров: команды → форварды → catch-all захват (последним).
    dp.include_router(commands_router)
    dp.include_router(forward_router)
    dp.include_router(capture_router)

    log.info("bot starting; vault=%s", config.VAULT_ROOT)
    shutdown = asyncio.Event()
    watchdog_task = start_watchdog(parent_pid, shutdown, dp, on_shutdown=drain_pending)
    try:
        return await _poll_forever(dp, bot, shutdown)
    finally:
        if watchdog_task is not None:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass
        # Ctrl+C и прочие тихие пути: висящие форварды уже вычитаны с серверов
        # Telegram — даём им шанс досохраниться, пока сессия ещё дышит.
        try:
            await asyncio.shield(asyncio.wait_for(drain_pending(), timeout=DRAIN_TIMEOUT_S))
        except asyncio.TimeoutError:
            log.warning("shutdown drain timed out")
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("shutdown drain failed")


async def _poll_forever(dp: Dispatcher, bot: Bot, shutdown: asyncio.Event) -> None:
    # Сетевые сбои ВО ВРЕМЯ поллинга aiogram ретраит сам, но стартовый bot.me()
    # вне его retry-петли: нет сети в момент запуска → TelegramNetworkError убивает
    # процесс (наблюдалось: WinError 121 «Превышен таймаут семафора»). Поэтому свой
    # ретрай с backoff — бот терпеливо ждёт появления сети.
    backoff = _BACKOFF_MIN
    while True:
        if shutdown.is_set():
            return  # сторож сообщил о смерти родителя
        started = time.monotonic()
        try:
            # drop_pending_updates=False: забираем накопленные апдейты после простоя ПК.
            await dp.start_polling(bot, drop_pending_updates=False)
            return  # чистая остановка поллинга
        except TelegramUnauthorizedError as e:
            raise config.ConfigError(f"Telegram отклонил TELEGRAM_TOKEN: {e}") from e
        except _NETWORK_ERRORS as e:
            # Если связь держалась долго и лишь потом упала — начинаем backoff заново.
            if time.monotonic() - started > 60:
                backoff = _BACKOFF_MIN
            proxy = config._mask_proxy(config.TELEGRAM_PROXY)
            if config.TELEGRAM_PROXY:
                log.warning(
                    "нет связи с Telegram через прокси %s (%s); повтор через %d с. "
                    "прокси недоступен — запусти прокси-клиент или очисти TELEGRAM_PROXY",
                    proxy,
                    e,
                    backoff,
                )
            else:
                log.warning(
                    "нет связи с Telegram напрямую (proxy=%s; %s); повтор через %d с",
                    proxy,
                    e,
                    backoff,
                )
                log.warning(
                    "api.telegram.org недоступен. Проверь подключение к сети "
                    "или пропиши TELEGRAM_PROXY в .env."
                )
            # Ждём через событие, а не sleep: смерть родителя прерывает backoff.
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            if shutdown.is_set():
                return
            backoff = min(backoff * 2, _BACKOFF_MAX)


def main(argv: list[str] | None = None) -> int:
    """Возвращает код выхода для контракта с плагином (0/2/3/4, см. константы выше)."""
    args = parse_args(argv)
    _setup_logging()
    try:
        with InstanceLock(config.LOCK_FILE):
            asyncio.run(_run(parent_pid=args.parent_pid))
    except LockTaken:
        log.error("another bot instance is running")
        return EXIT_ANOTHER_INSTANCE
    except (KeyboardInterrupt, SystemExit):
        log.info("bot stopped")
    except config.ConfigError as e:
        log.error("startup failed: %s", e)
        return EXIT_CONFIG_ERROR
    except Exception:  # noqa: BLE001 — любой сюрприз: код краша, плагин рестартит
        log.exception("bot crashed")
        return EXIT_CRASH
    return 0


if __name__ == "__main__":
    sys.exit(main())
