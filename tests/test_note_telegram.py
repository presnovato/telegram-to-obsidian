"""build_note для Telegram: related/warnings; дефолт TikTok/X — байтово тот же."""
from tiktok_obsidian.core.models import PostMeta, Source
from tiktok_obsidian.core.note import build_note

CREATED = "2026-09-23T14:05:11"


def _tg_meta(**kw) -> PostMeta:
    base = dict(
        video_id="tg_1001234567_842",
        author="Канал про дизайн (@design_ch)",
        caption="Текст поста в **Markdown**.",
        url="https://t.me/design_ch/842",
        upload_date="2026-09-20",
        media_files=["tg_1001234567_842_01.jpg", "tg_1001234567_842_02.mp4"],
        source=Source.TELEGRAM,
    )
    base.update(kw)
    return PostMeta(**base)


def test_telegram_note_full():
    note = build_note(
        _tg_meta(),
        comment="Комментарий владельца.",
        related=["[[1. Входящие/TikTok/author - words|author - words]]"],
        warnings=["Не сохранено: голосовое сообщение"],
        created=CREATED,
    )
    assert "source: Telegram" in note
    assert "  - telegram" in note
    assert "![[tg_1001234567_842_01.jpg]]" in note
    assert "> [!warning] Не сохранено: голосовое сообщение" in note
    assert note.index("## Описание") < note.index("## Связанные посты")
    assert "- [[1. Входящие/TikTok/author - words|author - words]]" in note
    assert note.index("## Связанные посты") < note.index("## Впечатления")
    assert "Комментарий владельца." in note
    assert note.endswith("\n") and not note.endswith("\n\n")


def test_warnings_joined_in_single_callout():
    note = build_note(_tg_meta(), warnings=["a", "b"], created=CREATED)
    assert "> [!warning] a; b" in note
    assert note.count("> [!warning]") == 1


def test_no_related_section_when_empty():
    note = build_note(_tg_meta(), created=CREATED)
    assert "## Связанные посты" not in note


def test_plain_files_rendered_as_links():
    note = build_note(
        _tg_meta(media_files=["tg_1.pdf"]),
        plain_files=["tg_1.zip"],
        created=CREATED,
    )
    assert "![[tg_1.pdf]]" in note  # PDF встраивается
    assert "[[tg_1.zip]]" in note  # прочий документ — ссылкой
    assert "![[tg_1.zip]]" not in note


def test_defaults_keep_tiktok_output_byte_identical():
    meta = PostMeta(
        video_id="7412345",
        author="arbuziki",
        caption="Как снизить расход токенов #ai #claude",
        url="https://www.tiktok.com/@arbuziki/video/7412345",
        upload_date="2026-07-01",
        duration=42,
        media_files=["7412345.mp4"],
    )
    expected = (
        "---\n"
        "created: 2026-07-14T12:00:00\n"
        "source: TikTok\n"
        'post_id: "7412345"\n'
        'author: "arbuziki"\n'
        'url: "https://www.tiktok.com/@arbuziki/video/7412345"\n'
        "upload_date: 2026-07-01\n"
        "tags:\n"
        "  - tiktok\n"
        "  - inbox\n"
        "---\n"
        "\n"
        "![[7412345.mp4]]\n"
        "\n"
        "## Описание\n"
        "\n"
        "Как снизить расход токенов #ai #claude\n"
    )
    assert build_note(meta, created="2026-07-14T12:00:00") == expected
