"""Чистая постобработка OCR-текста и сборка Markdown-секции."""
from __future__ import annotations


def clean_text(raw: str) -> str:
    """Нормализует OCR-текст, сохраняя абзацы и склеивая переносы строк."""
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return ""

    paragraphs: list[str] = []
    current: list[str] = []

    def flush_paragraph() -> None:
        if not current:
            return
        text = current[0]
        for line in current[1:]:
            if text.endswith("-"):
                text = text[:-1] + line
            else:
                text += " " + line
        paragraphs.append(text)
        current.clear()

    for line in normalized.split("\n"):
        stripped = line.strip()
        if stripped:
            current.append(stripped)
        else:
            flush_paragraph()
    flush_paragraph()

    return "\n\n".join(paragraphs)


def build_transcript_section(slides: list[str]) -> str:
    """Строит секцию OCR с исходной 1-based нумерацией непустых слайдов."""
    entries = [
        f"**Слайд {number}:** {text}"
        for number, raw in enumerate(slides, start=1)
        if (text := clean_text(raw))
    ]
    if not entries:
        return ""
    return "---\n" + "\n\n".join(entries)
