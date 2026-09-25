"""Служебные команды: /start, /status, /update_ytdlp, /errors."""
from __future__ import annotations

import asyncio
import html
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from .. import config, mode_store
from ..core.mode import CaptureMode, parse_mode
from ..core.text import truncate_utf16_tail, utf16_len
from ..downloader import installed_ytdlp_version, update_ytdlp, ytdlp_version

log = logging.getLogger(__name__)
router = Router(name="commands")

_MODE_HELP = {
    CaptureMode.VAULT: "только заметка и медиа в vault, в чат ничего не шлю",
    CaptureMode.CHAT: "только в чат, vault не трогаю и заметку не пишу",
    CaptureMode.BOTH: "заметка в vault + медиа в чат",
}


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 Бот TikTok / X → Obsidian.\n\n"
        "Пришли ссылку на публичный пост TikTok или X (Twitter).\n"
        "Ссылки X можно слать как есть: подменять домен на fixupx.com не нужно.\n"
        "Комментарий в том же сообщении попадёт в раздел «Впечатления» заметки.\n\n"
        f"<b>Режим</b> (<code>/mode</code>): сейчас <b>{mode_store.get_mode().value}</b>.\n"
        "• <code>vault</code> — только в Obsidian\n"
        "• <code>chat</code> — только в чат\n"
        "• <code>both</code> — и туда, и туда\n"
        "Разово переопределить: <code>!chat</code> или <code>!vault</code> в сообщении со ссылкой.\n"
        "Оговорка: для X режим <code>chat</code> отдаёт медиа прямой ссылкой, "
        "а для TikTok всё равно приходится скачать во временную папку — она стирается сразу.\n\n"
        "Команды: /mode, /status, /update_ytdlp, /errors"
    )


@router.message(Command("mode"))
async def cmd_mode(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""

    if not arg:
        current = mode_store.get_mode()
        lines = "\n".join(
            f"{'▶' if m is current else '·'} <code>{m.value}</code> — {_MODE_HELP[m]}"
            for m in CaptureMode
        )
        await message.answer(f"Режим захвата:\n{lines}\n\nСменить: <code>/mode chat</code>")
        return

    mode = parse_mode(arg)
    if mode is None:
        allowed = ", ".join(m.value for m in CaptureMode)
        await message.answer(f"Не знаю режим «{html.escape(arg)}». Допустимо: {allowed}.")
        return

    mode_store.set_mode(mode)
    log.info("режим переключён на %s", mode.value)
    await message.answer(f"✅ Режим: <b>{mode.value}</b> — {_MODE_HELP[mode]}.\nПереживёт перезапуск.")


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    await message.answer(
        "✅ Бот работает.\n"
        f"режим: {mode_store.get_mode().value}\n"
        f"yt-dlp: {ytdlp_version()}\n"
        f"vault: {config.VAULT_ROOT}\n"
        f"заметки TikTok: {config.NOTES_DIR}\n"
        f"заметки X: {config.NOTES_DIR_TWITTER}\n"
        f"заметки Telegram: {config.NOTES_DIR_TELEGRAM}\n"
        f"медиа: {config.MEDIA_DIR}"
    )


@router.message(Command("update_ytdlp"))
async def cmd_update_ytdlp(message: Message) -> None:
    await message.answer("⏳ Обновляю yt-dlp…")
    # pip идёт в потоке: синхронные 10–60 с морозили бы весь event loop
    # (поллинг, handlers и сторож родителя).
    try:
        ok, tail = await asyncio.to_thread(update_ytdlp)
        installed = await asyncio.to_thread(installed_ytdlp_version)
    except Exception as e:  # noqa: BLE001 — поток тоже может упасть
        await message.answer(
            f"⚠️ Не удалось обновить:\n<pre>{html.escape(str(e)[:500])}</pre>"
        )
        return
    running = ytdlp_version()
    prefix = "✅ Готово" if ok else "⚠️ Не удалось обновить"
    # Хвост pip — чужой текст: экранируем, иначе `<` роняет весь вызов.
    body = f"{prefix}:\n<pre>{html.escape(tail)}</pre>"
    if installed and installed != running:
        body += (
            f"\nУстановлено {installed}, работает {running} — "
            "перезапусти бота (Obsidian: t2o → Restart bot)."
        )
    else:
        body += f"\nСейчас: {running}."
    await message.answer(body)


_ERRORS_RAW_LIMIT = 3500  # единиц UTF-16; после escape режем ещё раз под лимит
_ERRORS_OUT_LIMIT = 4096


@router.message(Command("errors"))
async def cmd_errors(message: Message) -> None:
    if not config.LOG_FILE.exists():
        await message.answer("Лог пуст.")
        return
    lines = config.LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = [ln for ln in lines if "ERROR" in ln or "WARNING" in ln][-15:]
    if not tail:
        tail = lines[-15:]
    body = "\n".join(tail) or "Лог пуст."
    # Новейшее важнее старого: держим хвост, а не голову (как было body[-3500:]).
    body = truncate_utf16_tail(body, _ERRORS_RAW_LIMIT)
    # Экранируем целыми строками и скидываем старые целиком, пока не влезет:
    # escape раздувает (`<` → `&lt;`), а рез по символам мог бы рассечь сущность.
    escaped = [html.escape(ln) for ln in body.split("\n")]
    budget = _ERRORS_OUT_LIMIT - 11  # 11 = len("<pre></pre>")
    while len(escaped) > 1 and utf16_len("\n".join(escaped)) > budget:
        escaped.pop(0)
    # Последний рубеж на патологию (одна строка больше всего бюджета) — хвост.
    out = truncate_utf16_tail("\n".join(escaped), budget)
    await message.answer(f"<pre>{out}</pre>")
