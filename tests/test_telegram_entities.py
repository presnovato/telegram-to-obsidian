"""Entities → Markdown: все типы, вложенность, UTF-16 с эмодзи (§6, §10)."""
from tiktok_obsidian.core.telegram import TgEntity, entities_to_markdown


def E(type_, offset, length, url="", language=""):  # noqa: N803
    return TgEntity(type=type_, offset=offset, length=length, url=url, language=language)


def test_empty_text_and_no_entities():
    assert entities_to_markdown("", []) == ""
    assert entities_to_markdown("просто текст #тег", []) == "просто текст #тег"


def test_bold_italic_strike_code():
    assert entities_to_markdown("hello world", [E("bold", 6, 5)]) == "hello **world**"
    assert entities_to_markdown("hello world", [E("italic", 0, 5)]) == "*hello* world"
    assert entities_to_markdown("abc", [E("strikethrough", 0, 3)]) == "~~abc~~"
    assert entities_to_markdown("run x now", [E("code", 4, 1)]) == "run `x` now"


def test_pre_with_language():
    out = entities_to_markdown("print(1)", [E("pre", 0, 8, language="python")])
    assert out == "```python\nprint(1)\n```"


def test_pre_without_language():
    out = entities_to_markdown("print(1)", [E("pre", 0, 8)])
    assert out == "```\nprint(1)\n```"


def test_text_link():
    out = entities_to_markdown(
        "смотреть тут", [E("text_link", 9, 4, url="https://tiktok.com/@u/video/1")]
    )
    assert out == "смотреть [тут](https://tiktok.com/@u/video/1)"


def test_bold_inside_link_opens_link_first():
    text = "click here now"
    entities = [E("text_link", 0, 10, url="https://x.example"), E("bold", 6, 4)]
    assert entities_to_markdown(text, entities) == "[click **here**](https://x.example) now"


def test_same_span_link_and_bold():
    text = "сюда"
    entities = [E("bold", 0, 4), E("text_link", 0, 4, url="https://x.example")]
    assert entities_to_markdown(text, entities) == "[**сюда**](https://x.example)"


def test_blockquote_multiline():
    out = entities_to_markdown("line1\nline2", [E("blockquote", 0, 11)])
    assert out == "> line1\n> line2"


def test_expandable_blockquote():
    out = entities_to_markdown("a\nb", [E("expandable_blockquote", 0, 3)])
    assert out == "> a\n> b"


def test_plain_passthrough_types_unchanged():
    text = "@user #тег https://x.com/a hi"
    entities = [E("mention", 0, 5), E("hashtag", 6, 4), E("url", 11, 14)]
    assert entities_to_markdown(text, entities) == text


def test_underline_spoiler_custom_emoji_are_plain():
    text = "abc def ghi"
    entities = [E("underline", 0, 3), E("spoiler", 4, 3), E("custom_emoji", 8, 3)]
    assert entities_to_markdown(text, entities) == text


def test_emoji_before_entity_utf16_offset():
    # 😀 — 2 единицы UTF-16, но 1 символ str: наивная нарезка взяла бы "ello".
    text = "😀 hello"
    assert entities_to_markdown(text, [E("bold", 3, 5)]) == "😀 **hello**"


def test_emoji_inside_entity():
    assert entities_to_markdown("a😀b", [E("bold", 1, 2)]) == "a**😀**b"
    assert entities_to_markdown("a😀b", [E("italic", 0, 4)]) == "*a😀b*"


def test_supplementary_plane_char_inside_bold():
    # Математический символ вне BMP — тоже 2 единицы UTF-16.
    text = "x𝕳y"
    assert entities_to_markdown(text, [E("bold", 1, 2)]) == "x**𝕳**y"


def test_entity_clipped_to_text_bounds():
    assert entities_to_markdown("hi", [E("bold", 0, 100)]) == "**hi**"
    assert entities_to_markdown("hi", [E("bold", 50, 5)]) == "hi"


def test_caption_only_album_item():
    assert entities_to_markdown("подпись", [E("italic", 0, 8)]) == "*подпись*"


def test_bold_trailing_newline_stays_outside():
    assert entities_to_markdown("Заголовок\n", [E("bold", 0, 10)]) == "**Заголовок**\n"


def test_bold_trailing_space_stays_outside():
    assert entities_to_markdown("Важно: срок", [E("bold", 0, 7)]) == "**Важно:** срок"


def test_italic_leading_space_stays_outside():
    assert entities_to_markdown(" x", [E("italic", 0, 2)]) == " *x*"


def test_bold_spanning_two_lines_splits():
    assert entities_to_markdown("line1\nline2", [E("bold", 0, 11)]) == "**line1**\n**line2**"


def test_whitespace_only_entity_dropped():
    assert entities_to_markdown("   ", [E("bold", 0, 3)]) == "   "


def test_emoji_at_trimmed_edge_utf16():
    assert entities_to_markdown("😀 hi ", [E("bold", 0, 6)]) == "**😀 hi** "


def test_bold_with_trailing_space_nested_in_link():
    text = "go here "
    entities = [E("text_link", 0, 8, url="https://x.example"), E("bold", 3, 8)]
    assert entities_to_markdown(text, entities) == "[go **here**](https://x.example) "


def test_pre_keeps_edge_newlines():
    assert entities_to_markdown("code\n", [E("pre", 0, 5)]) == "```\ncode\n\n```"
