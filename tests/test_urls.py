from tiktok_obsidian.core.urls import extract_tiktok_url, normalize_for_ytdlp, video_id_from_url


def test_full_video_url():
    p = extract_tiktok_url("https://www.tiktok.com/@user/video/7412345678901234567")
    assert p is not None
    assert p.url == "https://www.tiktok.com/@user/video/7412345678901234567"
    assert p.comment == ""


def test_photo_url():
    p = extract_tiktok_url("https://www.tiktok.com/@user/photo/7412345678901234567")
    assert p is not None and "/photo/" in p.url


def test_short_vt_and_vm():
    assert extract_tiktok_url("смотри https://vt.tiktok.com/ZSabc123/") is not None
    assert extract_tiktok_url("https://vm.tiktok.com/ZMabc123/") is not None


def test_url_with_comment_before_and_after():
    p = extract_tiktok_url("полезное про монтаж https://vt.tiktok.com/ZSabc123/ пересмотреть")
    assert p is not None
    assert p.url == "https://vt.tiktok.com/ZSabc123/"
    assert p.comment == "полезное про монтаж  пересмотреть".replace("  ", " ") or "монтаж" in p.comment


def test_trailing_punctuation_stripped():
    p = extract_tiktok_url("вот (https://www.tiktok.com/@u/video/123).")
    assert p is not None
    assert p.url.endswith("/123")


def test_russian_quotes_stripped():
    p = extract_tiktok_url("«https://vt.tiktok.com/ZS123/»")
    assert p is not None
    assert p.url == "https://vt.tiktok.com/ZS123/"


def test_trailing_bang_stripped():
    p = extract_tiktok_url("смотри https://vt.tiktok.com/ZS123/!")
    assert p is not None
    assert p.url == "https://vt.tiktok.com/ZS123/"


def test_comma_glue_cut_and_comment_kept():
    p = extract_tiktok_url("https://vt.tiktok.com/ZS123/,круто")
    assert p is not None
    assert p.url == "https://vt.tiktok.com/ZS123/"
    assert p.comment == "круто"


def test_stripped_tail_does_not_leak_into_comment():
    """R3: срезанная пунктуация — не комментарий; запятая-клей — комментарий."""
    u = "https://vt.tiktok.com/ZS123/"
    assert extract_tiktok_url(f"«{u}»").comment == ""
    assert extract_tiktok_url(f"{u}!").comment == ""
    assert extract_tiktok_url(f"Смотри {u}.").comment == "Смотри"
    assert extract_tiktok_url(f"смотри {u}!").comment == "смотри"
    assert extract_tiktok_url(f"({u})").comment == ""
    assert extract_tiktok_url(f"{u},круто").comment == "круто"


def test_comma_inside_url_kept():
    p = extract_tiktok_url("https://www.tiktok.com/@u/video/1,2")
    assert p is not None
    assert p.url == "https://www.tiktok.com/@u/video/1,2"


def test_forwarded_bot_format_with_extra_link():
    p = extract_tiktok_url(
        "Original 🔗 (https://www.tiktok.com/t/ZP8GRu5db) | Via 🤖 (https://t.me/fronend_bot)"
    )
    assert p is not None
    assert p.url == "https://www.tiktok.com/t/ZP8GRu5db"
    assert "t.me" not in p.url


def test_no_url():
    assert extract_tiktok_url("просто текст без ссылки") is None
    assert extract_tiktok_url("") is None
    assert extract_tiktok_url("https://youtube.com/watch?v=abc") is None


def test_normalize_photo_to_video():
    assert (
        normalize_for_ytdlp("https://www.tiktok.com/@nawraskader/photo/7648756493180914965?_r=1")
        == "https://www.tiktok.com/@nawraskader/video/7648756493180914965?_r=1"
    )


def test_normalize_leaves_video_and_short_untouched():
    v = "https://www.tiktok.com/@u/video/123"
    assert normalize_for_ytdlp(v) == v
    s = "https://vt.tiktok.com/ZSabc123/"
    assert normalize_for_ytdlp(s) == s


def test_video_id_from_url():
    assert video_id_from_url("https://www.tiktok.com/@u/video/7412345678901234567") == "7412345678901234567"
    assert video_id_from_url("https://www.tiktok.com/@u/photo/999") == "999"
    assert video_id_from_url("https://vt.tiktok.com/ZSabc/") is None


# --- Мультиисточник: TikTok + X (Twitter) ---
from tiktok_obsidian.core.models import Source  # noqa: E402
from tiktok_obsidian.core.urls import extract_post_url  # noqa: E402


def test_extract_post_url_detects_tiktok():
    p = extract_post_url("https://www.tiktok.com/@u/video/123")
    assert p is not None and p.source is Source.TIKTOK


def test_extract_post_url_detects_twitter_and_keeps_comment():
    p = extract_post_url("годный тред https://x.com/NASA/status/123 сохранить")
    assert p is not None
    assert p.source is Source.TWITTER
    assert p.url == "https://x.com/NASA/status/123"
    assert "годный тред" in p.comment and "сохранить" in p.comment


def test_extract_post_url_accepts_manually_substituted_mirrors():
    p = extract_post_url("https://fixupx.com/NASA/status/123")
    assert p is not None and p.source is Source.TWITTER


def test_extract_post_url_takes_the_first_link_when_both_present():
    p = extract_post_url("https://x.com/u/status/1 и https://www.tiktok.com/@u/video/2")
    assert p.source is Source.TWITTER
    p = extract_post_url("https://www.tiktok.com/@u/video/2 и https://x.com/u/status/1")
    assert p.source is Source.TIKTOK


def test_extract_post_url_ignores_other_links():
    assert extract_post_url("https://youtube.com/watch?v=abc") is None
    assert extract_post_url("https://x.com/NASA") is None
