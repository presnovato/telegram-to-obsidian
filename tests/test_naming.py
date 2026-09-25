import re

from tiktok_obsidian.core.naming import media_basename, note_basename, sanitize_filename


def test_sanitize_removes_forbidden():
    assert sanitize_filename('a<b>c:d"e/f\\g|h?i*j') == "abcdefghij"


def test_sanitize_preserves_cyrillic_and_emoji():
    assert sanitize_filename("Привет 🚀 мир") == "Привет 🚀 мир"


def test_sanitize_trailing_dot_space():
    assert sanitize_filename("имя.  ") == "имя"


def test_note_basename_normal():
    name = note_basename("arbuziki", "Как снизить расход токенов в проекте", "123", max_len=80, words=8)
    assert name == "arbuziki - Как снизить расход токенов в проекте"


def test_note_basename_empty_caption_fallback():
    assert note_basename("arbuziki", "", "7412345", max_len=80, words=8) == "arbuziki - 7412345"


def test_note_basename_emoji_only_fallback():
    # Только эмодзи без букв/цифр → fallback на video_id.
    assert note_basename("user", "🔥🔥", "999", max_len=80, words=8) == "user - 999"


def test_note_basename_strips_wikilink_breakers():
    name = note_basename("user", "правила #adhd [x] ^b", "1", max_len=80, words=8)
    assert name == "user - правила adhd x b"
    assert not re.search(r"[#^\[\]]", name)


def test_note_basename_length_trimmed():
    long = "слово " * 40
    name = note_basename("author", long, "1", max_len=30, words=20)
    assert len(name) <= 30


def test_note_basename_hashtag_becomes_plain_word():
    name = note_basename("user", "монтаж рилсов #edit", "1", max_len=80, words=8)
    assert name == "user - монтаж рилсов edit"


def test_note_basename_hash_only_caption_is_nonempty():
    name = note_basename("user", "#adhd", "1", max_len=80, words=8)
    assert name == "user - adhd"
    assert not re.search(r"[#^\[\]]", name)


def test_media_basename_video():
    assert media_basename("7412345", "mp4") == "7412345.mp4"
    assert media_basename("7412345", ".mp4") == "7412345.mp4"


def test_media_basename_carousel():
    assert media_basename("7412345", "jpg", index=1) == "7412345_01.jpg"
    assert media_basename("7412345", "jpg", index=12) == "7412345_12.jpg"
