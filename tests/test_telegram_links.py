"""Сбор ссылок: plain + text_link, порядок, дедуп, кап, link-only (§6, §10)."""
from tiktok_obsidian.core.telegram import TgEntity, collect_post_urls, is_link_only
from tiktok_obsidian.core.urls import extract_post_url, extract_post_urls

TIKTOK = "https://www.tiktok.com/@user/video/111"
X = "https://x.com/user/status/222"


def E(type_, offset, length, url=""):  # noqa: N803
    return TgEntity(type=type_, offset=offset, length=length, url=url)


def test_extract_post_urls_order_and_dedup():
    text = f"смотри {X} а потом {TIKTOK} и снова {X}"
    urls = [p.url for p in extract_post_urls(text)]
    assert urls == [X, TIKTOK]


def test_extract_post_urls_empty():
    assert extract_post_urls("") == []
    assert extract_post_urls("просто текст") == []


def test_extract_post_url_unchanged_single():
    parsed = extract_post_url(f"первой {TIKTOK} второй {X}")
    assert parsed is not None and parsed.url == TIKTOK
    assert "второй" in parsed.comment and X in parsed.comment


def test_collect_plain_only():
    links = collect_post_urls(f"{TIKTOK} круто", [])
    assert links.urls == [TIKTOK] and links.overflow == 0


def test_collect_hidden_text_link():
    text = "смотреть тут"
    entities = [E("text_link", 9, 4, url=TIKTOK)]
    links = collect_post_urls(text, entities)
    assert links.urls == [TIKTOK]


def test_collect_order_plain_and_hidden():
    # Скрытая ссылка раньше по позиции, явная — позже.
    text = f"смотреть тут, а вот явная: {X}"
    entities = [E("text_link", 0, 12, url=TIKTOK)]
    links = collect_post_urls(text, entities)
    assert links.urls == [TIKTOK, X]


def test_collect_ignores_non_post_text_links():
    text = "читать далее"
    entities = [E("text_link", 0, 12, url="https://example.com/article")]
    assert collect_post_urls(text, entities).urls == []


def test_collect_dedup_plain_and_hidden():
    text = f"дубль {TIKTOK}"
    entities = [E("text_link", 0, 5, url=TIKTOK)]
    assert collect_post_urls(text, entities).urls == [TIKTOK]


def test_collect_cap_and_overflow():
    text = " ".join(
        f"https://www.tiktok.com/@u/video/{100 + i}" for i in range(7)
    )
    links = collect_post_urls(text, [], limit=5)
    assert len(links.urls) == 5
    assert links.overflow == 2
    assert links.urls[0].endswith("/video/100")


def test_collect_utf16_position_with_emoji():
    # Эмодзи перед скрытой ссылкой: позиция сущности в UTF-16, явная — в str.
    text = f"😀 смотреть тут и {X}"
    entities = [E("text_link", 3, 12, url=TIKTOK)]
    links = collect_post_urls(text, entities)
    assert links.urls == [TIKTOK, X]


def test_is_link_only_bare_url():
    assert is_link_only(TIKTOK, []) is True
    assert is_link_only(f"  {X}  ", []) is True


def test_is_link_only_with_text():
    assert is_link_only(f"{TIKTOK} смотри", []) is False
    assert is_link_only(f"#тег {TIKTOK}", []) is False


def test_is_link_only_hidden_link_with_words():
    text = "смотреть тут"
    entities = [E("text_link", 9, 4, url=TIKTOK)]
    assert is_link_only(text, entities) is False


def test_is_link_only_empty():
    assert is_link_only("", []) is True
