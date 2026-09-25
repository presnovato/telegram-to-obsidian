"""Единый HTTP-доступ downloaders и доставки: opener с MEDIA_PROXY.

Только http/https: urllib SOCKS не понимает, поэтому socks-значение тихо
пропускается здесь (предупреждение — один раз при старте в `config.validate`),
а yt-dlp получает прокси своей опцией (SOCKS он умеет нативно).
"""
from __future__ import annotations

import urllib.request

from .. import config


def proxy_handler() -> urllib.request.ProxyHandler | None:
    """ProxyHandler под MEDIA_PROXY или None (нет прокси / только socks)."""
    proxy = config.MEDIA_PROXY
    if not proxy or proxy.lower().startswith("socks"):
        return None
    return urllib.request.ProxyHandler({"http": proxy, "https": proxy})


def open_url(req: urllib.request.Request, timeout: float):
    """Как `urlopen`, но через настроенный прокси."""
    handler = proxy_handler()
    if handler is not None:
        return urllib.request.build_opener(handler).open(req, timeout=timeout)
    return urllib.request.build_opener().open(req, timeout=timeout)
