"""Сборка Markdown-заметки из метаданных поста."""
from __future__ import annotations

from datetime import datetime

from .models import PostMeta


def _yaml_str(value: str) -> str:
    """Безопасный YAML-скаляр: двойные кавычки + экранирование. Пустое → пустые кавычки."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_note(
    meta: PostMeta,
    comment: str = "",
    *,
    transcript_section: str = "",
    created: str | None = None,
    related: list[str] = (),
    warnings: list[str] = (),
    plain_files: list[str] = (),
) -> str:
    """Возвращает содержимое .md: frontmatter + встроенное медиа + Описание + (Впечатления).

    `created` — ISO-строка; по умолчанию текущий момент. `comment` пустой → раздел
    «Впечатления» не добавляется (конвенция: ручное заполнение).
    `related` — уже отрендеренные пункты списка для раздела «Связанные посты»
    (появляется только при непустом списке). `warnings` — пункты одного
    `> [!warning]` коллаута под встраиваниями. `plain_files` — вложения, которые
    даются ссылкой `[[...]]`, а не встраиванием `![[...]]` (документы, кроме PDF).
    """
    created = created or datetime.now().isoformat(timespec="seconds")

    lines: list[str] = ["---"]
    lines.append(f"created: {created}")
    lines.append(f"source: {meta.source.value}")
    # ID поста во frontmatter — единственный след для дедупа у постов без медиа:
    # встраивать ![[...]] нечего, а имя заметки строится из описания, не из ID.
    lines.append(f"post_id: {_yaml_str(meta.video_id)}")
    lines.append(f"author: {_yaml_str(meta.author)}")
    lines.append(f"url: {_yaml_str(meta.url)}")
    if meta.upload_date:
        lines.append(f"upload_date: {meta.upload_date}")
    lines.append("tags:")
    lines.append(f"  - {meta.source.tag}")
    lines.append("  - inbox")
    lines.append("---")
    lines.append("")

    # Встраивание медиа: одно видео или галерея изображений карусели.
    for name in meta.media_files:
        lines.append(f"![[{name}]]")
    for name in plain_files:
        lines.append(f"[[{name}]]")
    if meta.media_files or plain_files:
        lines.append("")

    if meta.watermarked:
        lines.append("> [!warning] Видео скачано с watermark — версия без него была недоступна.")
        lines.append("")

    if warnings:
        lines.append("> [!warning] " + "; ".join(w.strip() for w in warnings if w.strip()))
        lines.append("")

    lines.append("## Описание")
    lines.append("")
    lines.append(meta.caption.strip() if meta.caption else "")

    if related:
        lines.append("")
        lines.append("## Связанные посты")
        lines.append("")
        for item in related:
            lines.append(f"- {item.strip()}")

    if comment.strip():
        lines.append("")
        lines.append("## Впечатления")
        lines.append("")
        lines.append(comment.strip())

    if transcript_section.strip():
        lines.append("")
        lines.append(transcript_section.strip())

    return "\n".join(lines).rstrip() + "\n"
