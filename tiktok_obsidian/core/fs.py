"""Файловые операции: атомарная запись, дедуп по ФС, разрешение коллизий имён.

Файловая система — единственный источник правды по дедупу (БД в MVP нет).
"""
from __future__ import annotations

import os
import re
from pathlib import Path


def atomic_write_text(path: Path, content: str) -> None:
    """Пишет текст в UTF-8 атомарно: сначала .tmp рядом, затем os.replace.

    Так Git/Obsidian никогда не увидят полуфайл.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def find_existing_media(media_dir: Path, video_id: str) -> list[Path]:
    """Все файлы, начинающиеся с video_id (само видео или кадры карусели _NN).

    Непустой список = пост уже сохранён (дедуп).
    """
    if not media_dir.exists():
        return []
    hits = list(media_dir.glob(f"{video_id}.*"))
    hits += list(media_dir.glob(f"{video_id}_*.*"))
    return sorted(hits)


def _yaml_escape(value: str) -> str:
    """Как `_yaml_str` из note.py: двойные кавычки + экранирование."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def find_note_for(notes_dir: Path, video_id: str) -> Path | None:
    """Ищет ранее созданную заметку по точному якорю, а не по подстроке.

    Подстрока даёт ложные дубликаты: `tg_1_12` содержится в `tg_1_123`, и
    следующий пост канала отвечал бы «♻️ уже сохранён» без новой заметки.
    Якоря: строка frontmatter `post_id: "<id>"` целиком, а для заметок эпохи до
    `f98c0a9` (без post_id) — встраивание `![[<id>.` / `![[<id>_NN.`.
    Простое упоминание ID в тексте совпадением не считается.

    Индекса нет, поэтому сканируем .md целевой папки. Для подкаталога Входящих дёшево.
    """
    if not notes_dir.exists():
        return None
    frontmatter = re.compile(
        r'^post_id: "' + re.escape(_yaml_escape(video_id)) + r'"$', re.MULTILINE
    )
    embed = re.compile(r"!\[\[" + re.escape(video_id) + r"(?:_\d{2})?\.")
    for md in sorted(notes_dir.glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        if frontmatter.search(text) or embed.search(text):
            return md
    return None


def unique_note_path(notes_dir: Path, basename: str, video_id: str) -> Path:
    """Путь заметки без коллизий: при занятости имени — суффикс с video_id, затем счётчик."""
    candidate = notes_dir / f"{basename}.md"
    if not candidate.exists():
        return candidate
    candidate = notes_dir / f"{basename} {video_id}.md"
    if not candidate.exists():
        return candidate
    i = 2
    while True:
        candidate = notes_dir / f"{basename} {video_id} {i}.md"
        if not candidate.exists():
            return candidate
        i += 1
