"""Имена файлов: очистка под Windows, человекочитаемое имя заметки, имена медиа."""
from __future__ import annotations

import re

# Запрещённые в именах файлов Windows символы.
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WS = re.compile(r"\s+")
# Символы, ломающие вики-ссылки Obsidian: # — якорь, ^ — блок, [] — скобки.
_WIKILINK_BREAKERS = re.compile(r"[#^\[\]]")


def sanitize_filename(name: str) -> str:
    """Убирает запрещённые Windows-символы, схлопывает пробелы, срезает хвостовые точки/пробелы.

    Кириллица и эмодзи сохраняются. Возвращает пустую строку, если чистить нечего.
    """
    cleaned = _FORBIDDEN.sub("", name)
    cleaned = _WS.sub(" ", cleaned).strip()
    # Windows не любит имена, оканчивающиеся точкой или пробелом.
    cleaned = cleaned.rstrip(". ")
    return cleaned


def _first_words(caption: str, words: int) -> str:
    tokens = _WS.sub(" ", caption).strip().split(" ")
    tokens = [t for t in tokens if t]
    return " ".join(tokens[:words])


def note_basename(
    author: str,
    caption: str,
    video_id: str,
    *,
    max_len: int,
    words: int,
) -> str:
    """Человекочитаемое имя заметки без расширения: «{автор} - {первые слова}».

    `#^[]` из имён вычищаются (`#adhd` → `adhd`, `[x]` → `x`): внутри вики-ссылки
    Obsidian читает `#` как якорь заголовка, `^` как блок, а `[]` рвут скобки —
    иначе «Связанные посты» ссылались бы в никуда. Хэштеги живыми остаются
    в теле заметки, там они настоящие теги.

    Fallback на «{автор} - {video_id}», если из описания не выжать осмысленного
    имени (пусто / только эмодзи / всё вычистилось). Итог обрезается до max_len.
    """
    author_clean = _WIKILINK_BREAKERS.sub("", sanitize_filename(author)) or "unknown"
    head = _first_words(caption or "", words)
    head_clean = _WIKILINK_BREAKERS.sub("", sanitize_filename(head))
    # Осмысленность проверяем без эмодзи: имя из одних эмодзи не годится.
    if not re.search(r"[^\W_]", head_clean, re.UNICODE):
        head_clean = ""

    tail = head_clean or str(video_id)
    base = f"{author_clean} - {tail}"
    if len(base) > max_len:
        base = base[:max_len].rstrip(". ")
    return base


def media_basename(video_id: str, ext: str, index: int | None = None) -> str:
    """Имя медиафайла по video ID. Для карусели — суффикс _NN. ext без точки или с ней."""
    ext = ext.lstrip(".")
    if index is None:
        return f"{video_id}.{ext}"
    return f"{video_id}_{index:02d}.{ext}"
