from tiktok_obsidian.core import fs


def test_atomic_write_creates_file_and_content(tmp_path):
    target = tmp_path / "sub" / "note.md"
    fs.atomic_write_text(target, "привет 🚀\n")
    assert target.read_text(encoding="utf-8") == "привет 🚀\n"
    # Временный файл не остаётся.
    assert not (tmp_path / "sub" / "note.md.tmp").exists()


def test_find_existing_media_video_and_carousel(tmp_path):
    (tmp_path / "7412345.mp4").write_bytes(b"x")
    (tmp_path / "999_01.jpg").write_bytes(b"x")
    (tmp_path / "999_02.jpg").write_bytes(b"x")
    assert [p.name for p in fs.find_existing_media(tmp_path, "7412345")] == ["7412345.mp4"]
    assert len(fs.find_existing_media(tmp_path, "999")) == 2
    assert fs.find_existing_media(tmp_path, "absent") == []


def test_find_existing_media_missing_dir(tmp_path):
    assert fs.find_existing_media(tmp_path / "nope", "1") == []


def test_find_note_for_by_content(tmp_path):
    (tmp_path / "a.md").write_text("![[7412345.mp4]]", encoding="utf-8")
    (tmp_path / "b.md").write_text("другое", encoding="utf-8")
    found = fs.find_note_for(tmp_path, "7412345")
    assert found is not None and found.name == "a.md"
    assert fs.find_note_for(tmp_path, "0000") is None


def test_find_note_for_exact_frontmatter_anchor(tmp_path):
    (tmp_path / "n.md").write_text(
        '---\npost_id: "tg_1_123"\n---\nтекст\n', encoding="utf-8"
    )
    assert fs.find_note_for(tmp_path, "tg_1_12") is None
    found = fs.find_note_for(tmp_path, "tg_1_123")
    assert found is not None and found.name == "n.md"


def test_find_note_for_legacy_embed_anchor(tmp_path):
    (tmp_path / "n.md").write_text("![[7600000000000000001.mp4]]", encoding="utf-8")
    assert fs.find_note_for(tmp_path, "7600000000000000001") is not None
    assert fs.find_note_for(tmp_path, "760000000000000000") is None


def test_find_note_for_body_mention_is_not_a_match(tmp_path):
    (tmp_path / "n.md").write_text("видел tg_1_123 вчера\n", encoding="utf-8")
    assert fs.find_note_for(tmp_path, "tg_1_123") is None


def test_unique_note_path_no_collision(tmp_path):
    p = fs.unique_note_path(tmp_path, "author - name", "123")
    assert p.name == "author - name.md"


def test_unique_note_path_collision_appends_id(tmp_path):
    (tmp_path / "author - name.md").write_text("x", encoding="utf-8")
    p = fs.unique_note_path(tmp_path, "author - name", "123")
    assert p.name == "author - name 123.md"


def test_unique_note_path_double_collision_counter(tmp_path):
    (tmp_path / "author - name.md").write_text("x", encoding="utf-8")
    (tmp_path / "author - name 123.md").write_text("x", encoding="utf-8")
    p = fs.unique_note_path(tmp_path, "author - name", "123")
    assert p.name == "author - name 123 2.md"
