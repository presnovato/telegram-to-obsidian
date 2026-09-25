"""Owner-only middleware: пропускает апдейты только от заказчика, прочие молча игнорирует."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from . import config

log = logging.getLogger(__name__)


class OwnerOnlyMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None or user.id != config.ALLOWED_USER_ID:
            if user is not None:
                log.warning("ignored update from user_id=%s", user.id)
            return None
        return await handler(event, data)
