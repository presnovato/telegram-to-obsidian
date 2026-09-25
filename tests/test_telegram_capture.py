"""Оркестрация process_forward с фейковыми download/process_link (§8, §10)."""
from datetime import datetime, timezone

from tiktok_obsidian import telegram_capture as tc
from tiktok_obsidian.core.mode import CaptureMode
from tiktok_obsidian.core.telegram import ForwardOrigin, TgEntity
from tiktok_obsidian.service import Status
from tiktok_obsidian.telegram_capture import ForwardBundle

DATE = int(datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc).timestamp())
POST_ID = "tg_1001001234567_842"

U_TIKTOK = "https://www.tiktok.com/@user/video/111"
U_X = "https://x.com/user/status/222"
U_DUP = "https://www.tiktok.com/@user/video/333"
U_MOVED = "https://x.com/user/status/444"
U_FAIL = "https://www.tiktok.com/@user/video/555"


def make_origin() -> ForwardOrigin:
    return ForwardOrigin(
        kind="channel", date=DATE, message_id=842,
        chat_id=-1001001234567, chat_title="Канал про дизайн", chat_username="design_ch",
    )


def make_dirs(tmp_path):
    vault = tmp_path / "vault"
    media = vault / "Media"
    notes = vault / "NotesTg"
    media.mkdir(parents=True)
    notes.mkdir(parents=True)
    return vault, media, notes


def make_download(payload: bytes = b"data", fail_ids=(), calls=None):
    async def _download(file_id, dest):
        if calls is not None:
            calls.append(file_id)
        if file_id in fail_ids:
            raise OSError("network down")
        dest.write_bytes(payload)

    return _download


def make_process_link(mapping, calls=None):
    async def _process(url):
        if calls is not None:
            calls.append(url)
        item = mapping[url]
        if isinstance(item, Exception):
            raise item
        return item

    return _process


def make_bundle(**kw) -> ForwardBundle:
    base = dict(origin=make_origin(), text="Текст поста", entities=[])
    base.update(kw)
    return ForwardBundle(**base)


def run_kwargs(tmp_path, **kw):
    vault, media, notes = make_dirs(tmp_path)
    base = dict(media_dir=media, notes_dir=notes, vault_root=vault)
    base.update(kw)
    return vault, media, notes, base


async def test_success_single_photo(tmp_path):
    vault, media, notes, kw = run_kwargs(tmp_path)
    bundle = make_bundle(
        text="Текст поста",
        entities=[TgEntity(type="italic", offset=6, length=5)],
        files=[tc.TgMediaItem("f1", ".jpg", 100, True, "фото")],
    )
    result = await tc.process_forward(
        bundle, comment="моё", download=make_download(b"img"),
        process_link=make_process_link({}), **kw,
    )
    assert result.status is Status.DONE
    assert result.media_count == 1
    assert (media / f"{POST_ID}.jpg").read_bytes() == b"img"
    note = result.note_path.read_text(encoding="utf-8")
    assert f'post_id: "{POST_ID}"' in note
    assert "Канал про дизайн (@design_ch)" in note
    assert "https://t.me/design_ch/842" in note
    assert "upload_date: 2026-09-20" in note
    assert f"![[{POST_ID}.jpg]]" in note
    assert "Текст *поста*" in note  # entities → Markdown
    assert "## Впечатления" in note and "моё" in note
    assert "## Связанные посты" not in note


async def test_duplicate_by_media_skips_everything(tmp_path):
    vault, media, notes, kw = run_kwargs(tmp_path)
    (media / f"{POST_ID}.jpg").write_bytes(b"old")
    calls, link_calls = [], []
    result = await tc.process_forward(
        make_bundle(files=[tc.TgMediaItem("f1", ".jpg", 100, True, "фото")]),
        download=make_download(calls=calls),
        process_link=make_process_link({}, calls=link_calls),
        **kw,
    )
    assert result.status is Status.DUPLICATE
    assert calls == [] and link_calls == []
    assert "уже сохранён" in tc.build_summary(result, "author")


async def test_text_only_forward_deduped_by_note(tmp_path):
    vault, media, notes, kw = run_kwargs(tmp_path)
    first = await tc.process_forward(
        make_bundle(), download=make_download(),
        process_link=make_process_link({}), **kw,
    )
    assert first.status is Status.DONE
    assert first.note_path is not None and first.note_path.exists()
    second = await tc.process_forward(
        make_bundle(), download=make_download(),
        process_link=make_process_link({}), **kw,
    )
    assert second.status is Status.DUPLICATE
    assert second.note_path == first.note_path


async def test_partial_file_failure_warns_and_continues(tmp_path):
    vault, media, notes, kw = run_kwargs(tmp_path)
    bundle = make_bundle(files=[
        tc.TgMediaItem("f1", ".jpg", 100, True, "фото1"),
        tc.TgMediaItem("f2", ".jpg", 100, True, "фото2"),
    ])
    result = await tc.process_forward(
        bundle, download=make_download(b"x", fail_ids=("f2",)),
        process_link=make_process_link({}), **kw,
    )
    assert result.status is Status.DONE
    assert result.media_count == 1
    note = result.note_path.read_text(encoding="utf-8")
    assert "Не сохранено: не скачалось: фото2" in note


async def test_oversize_file_skipped_before_download(tmp_path):
    vault, media, notes, kw = run_kwargs(tmp_path)
    calls = []
    bundle = make_bundle(
        text="держи файл",
        files=[tc.TgMediaItem("big", ".zip", 21 * 1024 * 1024, False, "report.zip")],
    )
    result = await tc.process_forward(
        bundle, download=make_download(calls=calls),
        process_link=make_process_link({}), **kw,
    )
    assert result.status is Status.DONE
    assert calls == []
    note = result.note_path.read_text(encoding="utf-8")
    assert "файл report.zip больше 20 МБ" in note
    assert "![[" not in note


async def test_unsupported_content_goes_to_warnings(tmp_path):
    vault, media, notes, kw = run_kwargs(tmp_path)
    bundle = make_bundle(text="слушай", skipped=["голосовое сообщение"])
    result = await tc.process_forward(
        bundle, download=make_download(),
        process_link=make_process_link({}), **kw,
    )
    note = result.note_path.read_text(encoding="utf-8")
    assert "> [!warning] Не сохранено: голосовое сообщение" in note


async def test_related_links_all_cases(tmp_path):
    vault, media, notes, kw = run_kwargs(tmp_path)
    linked_dir = vault / "NotesTik"
    linked_dir.mkdir()
    done_note = linked_dir / "author - words.md"
    done_note.write_text("x", encoding="utf-8")
    dup_note = linked_dir / "dup - words.md"
    dup_note.write_text("x", encoding="utf-8")
    mapping = {
        U_TIKTOK: tc.LinkedPost(url=U_TIKTOK, status=tc.LinkStatus.DONE, note_path=done_note),
        U_X: tc.LinkedPost(url=U_X, status=tc.LinkStatus.DUPLICATE, note_path=dup_note),
        U_DUP: tc.LinkedPost(url=U_DUP, status=tc.LinkStatus.DUPLICATE, note_path=notes),
        U_MOVED: tc.LinkedPost(url=U_MOVED, status=tc.LinkStatus.FAILED, error="пост удалён\nвторая строка"),
    }

    async def _process(url):
        if url == U_FAIL:
            raise RuntimeError("boom")
        return mapping[url]

    bundle = make_bundle(text=f"{U_TIKTOK} {U_X} {U_DUP} {U_MOVED} {U_FAIL}")
    result = await tc.process_forward(
        bundle, download=make_download(), process_link=_process, **kw,
    )
    assert (result.linked_done, result.linked_duplicates, result.linked_failed) == (1, 2, 2)
    note = result.note_path.read_text(encoding="utf-8")
    assert "- [[NotesTik/author - words|author - words]]" in note
    assert "- [[NotesTik/dup - words|dup - words]]" in note
    assert f"- {U_DUP} — уже сохранён ранее" in note  # note_path — каталог
    assert f"- {U_MOVED} — не удалось сохранить: пост удалён" in note
    assert f"- {U_FAIL} — не удалось сохранить: boom" in note


async def test_chat_mode_touches_nothing_in_vault(tmp_path):
    """B1: в chat файлы не качаются, медиапапка пуста, связанные посты идут."""
    vault, media, notes, kw = run_kwargs(tmp_path)
    calls, link_calls = [], []
    bundle = make_bundle(
        text=U_TIKTOK,
        files=[tc.TgMediaItem("f1", ".jpg", 100, True, "фото")],
    )
    result = await tc.process_forward(
        bundle, mode=CaptureMode.CHAT, download=make_download(calls=calls),
        process_link=make_process_link(
            {U_TIKTOK: tc.LinkedPost(U_TIKTOK, tc.LinkStatus.DONE)}, link_calls
        ),
        **kw,
    )
    assert result.status is Status.DONE
    assert calls == []
    assert list(media.iterdir()) == []
    assert list(notes.glob("*.md")) == []
    assert link_calls == [U_TIKTOK]
    assert result.linked_done == 1


async def test_all_failed_forward_dedupes_by_note_on_retry(tmp_path):
    """F6: ни один файл не скачался — заметка с варнингом одна, повтор DUPLICATE."""
    vault, media, notes, kw = run_kwargs(tmp_path)
    bundle = make_bundle(files=[tc.TgMediaItem("f1", ".jpg", 100, True, "фото")])

    async def always_fail(file_id, dest):
        raise OSError("network down")

    first = await tc.process_forward(
        bundle, download=always_fail, process_link=make_process_link({}), **kw
    )
    assert first.status is Status.DONE
    assert len(list(notes.glob("*.md"))) == 1
    assert "Не сохранено" in first.warnings[0]

    second = await tc.process_forward(
        bundle, download=always_fail, process_link=make_process_link({}), **kw
    )
    assert second.status is Status.DUPLICATE
    assert len(list(notes.glob("*.md"))) == 1


async def test_summary_shows_partial_warnings(tmp_path):
    vault, media, notes, kw = run_kwargs(tmp_path)
    bundle = make_bundle(text="слушай", skipped=["голосовое сообщение"])
    result = await tc.process_forward(
        bundle, download=make_download(), process_link=make_process_link({}), **kw
    )
    summary = tc.build_summary(result, "author")
    assert "голосовое сообщение" in summary


async def test_chat_then_vault_same_post_is_done(tmp_path):
    """B1: форвард в chat не отравляет дедуп — повтор в vault пишет заметку."""
    vault, media, notes, kw = run_kwargs(tmp_path)
    bundle_kwargs = dict(
        files=[tc.TgMediaItem("f1", ".jpg", 100, True, "фото")],
    )
    first = await tc.process_forward(
        make_bundle(**bundle_kwargs), mode=CaptureMode.CHAT,
        download=make_download(b"img"),
        process_link=make_process_link({}), **kw,
    )
    assert first.status is Status.DONE
    second = await tc.process_forward(
        make_bundle(**bundle_kwargs), mode=CaptureMode.VAULT,
        download=make_download(b"img"),
        process_link=make_process_link({}), **kw,
    )
    assert second.status is Status.DONE
    assert second.note_path is not None and second.note_path.exists()
    assert (media / f"{POST_ID}.jpg").exists()


async def test_chat_mode_writes_nothing_but_processes_links(tmp_path):
    vault, media, notes, kw = run_kwargs(tmp_path)
    (media / f"{POST_ID}.jpg").write_bytes(b"old")  # дедуп в chat не применяется
    link_calls = []
    bundle = make_bundle(
        text=U_TIKTOK,
        files=[tc.TgMediaItem("f1", ".jpg", 100, True, "фото")],
    )
    result = await tc.process_forward(
        bundle, mode=CaptureMode.CHAT, download=make_download(),
        process_link=make_process_link({U_TIKTOK: tc.LinkedPost(U_TIKTOK, tc.LinkStatus.DONE)}, link_calls),
        **kw,
    )
    assert result.status is Status.DONE
    assert result.note_path is None
    assert link_calls == [U_TIKTOK]
    assert list(notes.glob("*.md")) == []
    assert "## Связанные посты" not in tc.build_summary(result, "a")
    assert "chat-режим" in tc.build_summary(result, "a")


async def test_link_cap_overflow_warns(tmp_path):
    vault, media, notes, kw = run_kwargs(tmp_path)
    calls = []
    bundle = make_bundle(text=f"{U_TIKTOK} {U_X}")
    result = await tc.process_forward(
        bundle, download=make_download(),
        process_link=make_process_link(
            {U_TIKTOK: tc.LinkedPost(U_TIKTOK, tc.LinkStatus.DONE, note_path=vault / "n.md")},
            calls,
        ),
        link_limit=1, **kw,
    )
    assert calls == [U_TIKTOK]
    assert any("сверх лимита" in w for w in result.warnings)


async def test_ocr_for_two_images(tmp_path, monkeypatch):
    vault, media, notes, kw = run_kwargs(tmp_path)
    monkeypatch.setattr(
        tc.ocr, "recognize_images", lambda paths: ["текст слайда"]
    )
    bundle = make_bundle(files=[
        tc.TgMediaItem("f1", ".jpg", 100, True, "фото1"),
        tc.TgMediaItem("f2", ".jpg", 100, True, "фото2"),
    ])
    result = await tc.process_forward(
        bundle, download=make_download(b"x"),
        process_link=make_process_link({}), **kw,
    )
    assert not result.ocr_failed
    note = result.note_path.read_text(encoding="utf-8")
    assert "текст слайда" in note
    assert f"![[{POST_ID}_01.jpg]]" in note
    assert f"![[{POST_ID}_02.jpg]]" in note


def test_related_item_failed_short_reason(tmp_path):
    link = tc.LinkedPost(url=U_FAIL, status=tc.LinkStatus.FAILED, error="a\nb")
    assert tc.related_item(link, tmp_path) == f"{U_FAIL} — не удалось сохранить: a"


def test_related_item_legacy_stem_without_wikilink(tmp_path):
    note = tmp_path / "messagetodaniel - 3 #adhd.md"
    note.write_text("x", encoding="utf-8")
    link = tc.LinkedPost(url=U_X, status=tc.LinkStatus.DUPLICATE, note_path=note)
    out = tc.related_item(link, tmp_path)
    assert "[[" not in out
    assert out.startswith(f"{U_X} — уже сохранён: ")
