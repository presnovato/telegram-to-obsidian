"""Catch-all захват ссылок: извлечь URL → обработать по текущему режиму → ответить.

Режимы (см. core/mode.py): vault — только заметка, chat — только в чат,
both — заметка плюс медиа в чат. Разовое переопределение — токеном `!chat` / `!vault`
в том же сообщении.

Регистрируется ПОСЛЕДНИМ роутером (после команд).
"""
from __future__ import annotations

import asyncio
import html
import logging
import tempfile
from pathlib import Path

from aiogram import F, Router
from aiogram.types import Message

from .. import config, delivery, mode_store, service
from ..core.mode import CaptureMode, strip_mode_override
from ..core.models import PostMeta, Source
from ..core.text import truncate_utf16
from ..core.urls import extract_post_url
from ..downloader import DownloadError
from ..service import ProcessResult, Status, process
from ..telegram_capture import process_lock
from .burst import buffer

log = logging.getLogger(__name__)
router = Router(name="capture")

# Лимит Telegram на текст сообщения — в единицах UTF-16, не в кодпоинтах.
_TG_TEXT_LIMIT = 4096
_NO_LINK_REPLY = (
    "Не вижу ссылку. Пришли ссылку на публичный пост TikTok или X (Twitter)."
)


@router.message(F.text | F.caption)
async def on_message(message: Message) -> None:
    text = message.text or message.caption or ""
    override, text = strip_mode_override(text)
    mode = override or mode_store.get_mode()

    parsed = extract_post_url(text)
    if parsed is None:
        # Текст без ссылки — возможно, комментарий к форварду, который ещё
        # летит. Держим его в буфере TG_COMMENT_WAIT_S: если форвард подойдёт —
        # станет «Впечатлениями», если нет — отвечаем как раньше.
        # Команды сюда попадать не должны (их разбирает commands), но на всякий
        # случай неизвестные /команды отвечаем сразу, а не держим.
        if text.startswith("/"):
            await message.answer(_NO_LINK_REPLY)
            return
        # text уже без !mode-токена (снят выше), override — для всего всплеска.
        buffer.comment_add(message.chat.id, text, override)
        asyncio.create_task(_comment_timeout(message))
        return

    status_msg = await message.answer("⏳ Обрабатываю…")
    try:
        if mode is CaptureMode.CHAT:
            await _run_chat(message, status_msg, parsed.url)
        else:
            await _run_vault(message, status_msg, parsed.url, parsed.comment, mode)
    except DownloadError as e:
        log.error("download failed for %s: %s", parsed.url, e)
        # Подсказка про yt-dlp уместна только для TikTok: X идёт через зеркала эмбедов.
        hint = (
            "Если ошибка про экстрактор — обнови: /update_ytdlp"
            if parsed.source is Source.TIKTOK
            else "Пост может быть удалён или приватным."
        )
        await status_msg.edit_text(
            f"❌ Не удалось скачать.\n<pre>{html.escape(str(e)[:500])}</pre>\n{hint}"
        )
    except Exception as e:  # noqa: BLE001 — не роняем процесс на неожиданном сбое
        log.exception("unexpected error for %s", parsed.url)
        await status_msg.edit_text(
            f"❌ Непредвиденная ошибка:\n<pre>{html.escape(str(e)[:500])}</pre>"
        )


async def _comment_timeout(message: Message) -> None:
    """Если за TG_COMMENT_WAIT_S форвард не подошёл — комментарий был просто
    текстом без ссылки: отвечаем как раньше."""
    await asyncio.sleep(config.TG_COMMENT_WAIT_S)
    pending = buffer.comment_take_if_expired(message.chat.id, config.TG_COMMENT_WAIT_S)
    if pending is not None:
        await message.answer(_NO_LINK_REPLY)


async def _run_chat(message: Message, status_msg: Message, url: str) -> None:
    """Только в чат: vault не трогаем, заметку не пишем, дедуп не проверяем."""
    plan = await asyncio.to_thread(service.plan_chat, url)
    await status_msg.edit_text("📨 Отправляю…")
    meta, report = await _deliver_chat_plan(message, plan, url)

    # Что не влезло в лимит — отдаём прямой ссылкой, а не молчим.
    skipped = ""
    if report.skipped:
        links = "\n".join(html.escape(item.url) for item in report.skipped)
        skipped = f"\n⚠️ Не влезло в лимит Telegram ({len(report.skipped)}) — ссылками:\n{links}"

    await status_msg.edit_text(
        f"📨 В чат ({report.sent}) · vault не тронут\n"
        f"👤 <b>{html.escape(meta.author)}</b> · {meta.source.value}\n"
        f"🔗 {html.escape(meta.url)}{skipped}"
    )


async def _deliver_chat_plan(
    message: Message, plan: service.ChatPlan, url: str
) -> tuple[PostMeta, delivery.DeliveryReport]:
    """Доставка готового chat-плана: прямые ссылки X или скачивание TikTok."""
    if plan.needs_download:
        # TikTok: прямых ссылок нет. Временная папка — на диске vault (WinError 17
        # на переносе C:→D:), после отправки удаляется целиком.
        with tempfile.TemporaryDirectory(prefix="chat_", dir=config.MEDIA_DIR.parent) as tmp:
            paths = await asyncio.to_thread(service.download_to, url, plan.meta, Path(tmp))
            report = await delivery.deliver_files(message, paths)
    else:
        report = await delivery.deliver_urls(message, plan.media)

    if plan.meta.caption:
        await _answer_verbatim(message, plan.meta.caption)
    return plan.meta, report


async def deliver_chat_post(message: Message, url: str) -> PostMeta:
    """Chat-поток для одной ссылки без правок статуса: используют форварды для
    связанных постов в chat-режиме. Возвращает мету (caption уже в чате)."""
    plan = await asyncio.to_thread(service.plan_chat, url)
    meta, _ = await _deliver_chat_plan(message, plan, url)
    return meta


async def _answer_verbatim(message: Message, text: str) -> None:
    """Чужой текст в чат дословно: без HTML-парсинга (`<3` роняет весь вызов),
    с усечением по лимиту Telegram в единицах UTF-16.

    Подпись вторична: сбой её отправки не должен менять итог сохранения —
    только предупреждение в лог и короткая реплика.
    """
    try:
        await message.answer(truncate_utf16(text, _TG_TEXT_LIMIT), parse_mode=None)
    except Exception as e:  # noqa: BLE001 — пост уже сохранён/отправлен, подпись вторична
        log.warning("failed to send caption to chat: %s", e)
        await message.answer("⚠️ Подпись не отправилась, остальное сохранено.")


async def echo_saved_to_chat(message: Message, result: ProcessResult) -> None:
    """Both-эхо для уже сохранённого поста: медиа + подпись. Файлы уже в vault,
    чат вторичен — сбои только логируем с предупреждением в чат."""
    try:
        await delivery.deliver_files(message, result.media_paths)
    except Exception as e:  # noqa: BLE001 — файлы уже в vault, чат вторичен
        log.warning("failed to send media to chat: %s", e)
        await message.answer(
            "⚠️ Медиа сохранено в vault, но отправить в чат не удалось.\n"
            f"<pre>{html.escape(str(e)[:500])}</pre>"
        )
    if result.meta.caption:
        await _answer_verbatim(message, result.meta.caption)


async def _run_vault(
    message: Message, status_msg: Message, url: str, comment: str, mode: CaptureMode
) -> None:
    """vault и both: полный цикл с заметкой. В both медиа дополнительно уходит в чат."""
    async with process_lock:
        result = await asyncio.to_thread(process, url, comment)

    meta = result.meta
    if result.status is Status.DUPLICATE:
        await status_msg.edit_text(
            "♻️ Этот пост уже сохранён — новую заметку не создаю.\n"
            f"Заметка: <code>{html.escape(str(result.note_path))}</code>"
        )
        return

    await status_msg.edit_text("✅ Сохранено, отправляю…" if mode.sends_chat else "✅ Сохранено.")

    if mode.sends_chat:
        await echo_saved_to_chat(message, result)

    wm = "\n⚠️ Видео с watermark (без него было недоступно)." if meta.watermarked else ""
    ocr_warning = (
        "\n⚠️ Текст со слайдов распознать не удалось — заметка сохранена без транскрипта."
        if result.ocr_failed
        else ""
    )
    await message.answer(
        f"👤 <b>{html.escape(meta.author)}</b> · {meta.source.value}\n"
        f"🔗 {html.escape(meta.url)}\n"
        f"📝 Заметка: <code>{html.escape(str(result.note_path))}</code>{wm}{ocr_warning}"
    )
