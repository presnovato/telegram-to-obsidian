from tiktok_obsidian.core.models import PostMeta
from tiktok_obsidian.core.note import build_note

CREATED = "2026-07-14T12:00:00"


def _video_meta(**kw) -> PostMeta:
    base = dict(
        video_id="7412345",
        author="arbuziki",
        caption="Как снизить расход токенов #ai #claude",
        url="https://www.tiktok.com/@arbuziki/video/7412345",
        upload_date="2026-07-01",
        duration=42,
        media_files=["7412345.mp4"],
    )
    base.update(kw)
    return PostMeta(**base)


def test_video_note_with_comment():
    note = build_note(_video_meta(), comment="пересмотреть перед стримом", created=CREATED)
    assert "created: 2026-07-14T12:00:00" in note
    assert 'author: "arbuziki"' in note
    assert 'post_id: "7412345"' in note  # якорь дедупа, живёт даже без встроенного медиа
    assert "upload_date: 2026-07-01" in note
    assert "![[7412345.mp4]]" in note
    assert "## Описание" in note
    assert "#ai #claude" in note  # хэштеги остаются в тексте
    assert "## Впечатления" in note
    assert "пересмотреть перед стримом" in note


def test_video_note_without_comment_has_no_impressions():
    note = build_note(_video_meta(), comment="", created=CREATED)
    assert "## Впечатления" not in note
    assert "## Описание" in note


def test_carousel_note_embeds_all_images():
    meta = _video_meta(
        media_files=["7412345_01.jpg", "7412345_02.jpg", "7412345_03.jpg"],
        is_carousel=True,
        duration=None,
    )
    note = build_note(meta, created=CREATED)
    assert "![[7412345_01.jpg]]" in note
    assert "![[7412345_02.jpg]]" in note
    assert "![[7412345_03.jpg]]" in note


def test_watermark_banner():
    note = build_note(_video_meta(watermarked=True), created=CREATED)
    assert "watermark" in note.lower()


def test_upload_date_omitted_when_empty():
    note = build_note(_video_meta(upload_date=""), created=CREATED)
    assert "upload_date:" not in note


def test_yaml_escaping_of_author_with_quotes():
    note = build_note(_video_meta(author='имя "в кавычках": да'), created=CREATED)
    assert r'author: "имя \"в кавычках\": да"' in note


def test_note_ends_with_single_newline():
    note = build_note(_video_meta(), created=CREATED)
    assert note.endswith("\n")
    assert not note.endswith("\n\n")


def test_transcript_section_is_last_after_impressions():
    transcript = "---\n**Слайд 1:** Распознанный текст"
    note = build_note(
        _video_meta(),
        comment="моё впечатление",
        transcript_section=transcript,
        created=CREATED,
    )
    assert note.index("## Впечатления") < note.index("**Слайд 1:**")
    assert note.endswith("**Слайд 1:** Распознанный текст\n")


def test_empty_transcript_section_does_not_change_note():
    expected = build_note(_video_meta(), created=CREATED)
    actual = build_note(_video_meta(), transcript_section="  ", created=CREATED)
    assert actual == expected
