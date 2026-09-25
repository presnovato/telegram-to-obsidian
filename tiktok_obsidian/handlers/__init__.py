"""Telegram-слой: тонкие обработчики. Порядок роутеров задаётся в main.py."""
from .burst import buffer as burst_buffer
from .capture import router as capture_router
from .commands import router as commands_router
from .forward import router as forward_router

__all__ = ["burst_buffer", "capture_router", "commands_router", "forward_router"]
