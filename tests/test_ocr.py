import pytest

from tiktok_obsidian.core.ocr import build_transcript_section, clean_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Текст с краями  ", "Текст с краями"),
        ("Первый\n\n\nВторой", "Первый\n\nВторой"),
        ("Обычный\nперенос", "Обычный перенос"),
        ("дефис-\nный перенос", "дефисный перенос"),
        ("Первая\r\nстрока\r\n\r\nВторая", "Первая строка\n\nВторая"),
    ],
)
def test_clean_text(raw: str, expected: str):
    assert clean_text(raw) == expected


def test_build_transcript_section_regular():
    assert build_transcript_section(["Первый", "Второй"]) == (
        "---\n**Слайд 1:** Первый\n\n**Слайд 2:** Второй"
    )


def test_build_transcript_section_skips_empty_and_keeps_original_numbers():
    assert build_transcript_section(["Первый", "  ", "Третий\nслайд"]) == (
        "---\n**Слайд 1:** Первый\n\n**Слайд 3:** Третий слайд"
    )


def test_build_transcript_section_all_empty():
    assert build_transcript_section(["", " \n "]) == ""


def test_build_transcript_section_single_slide():
    assert build_transcript_section(["Один"]) == "---\n**Слайд 1:** Один"
