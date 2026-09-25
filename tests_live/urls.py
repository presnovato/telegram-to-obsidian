from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class LiveURL:
    key: str
    url: str | None
    description: str
    source: str
    kind: str
    reason: str = ""


# Recheck availability at run time; platforms may remove, geo-restrict, or gate posts.
CASES = (
    LiveURL("tt_short_video", "https://vt.tiktok.com/ZS23BxsrH/", "short-link video candidate referenced in 2025 material", "TikTok", "video"),
    LiveURL("tt_large_video", "https://www.tiktok.com/t/ZP83XGfoM/", "user-provided 508-second long video regression; TikWM hdplay is about 23.7 MiB", "TikTok", "large_video"),
    LiveURL("tt_single_photo", "https://www.tiktok.com/t/ZP8cGfNBa/", "recently observed classified single-photo candidate", "TikTok", "photo"),
    LiveURL("tt_carousel_11", None, "TikTok photo carousel with at least 11 slides", "TikTok", "carousel", "could not confirm a currently public 11+ slide post"),
    LiveURL("tt_caption_markup", None, "TikTok caption containing < or &", "TikTok", "caption", "could not confirm a currently public caption with markup characters"),
    LiveURL("x_single_photo", "https://x.com/NASA/status/2040565815083225472", "X post with one described Artemis II photo", "X", "photo"),
    LiveURL("x_video", "https://x.com/Space_Station/status/2035699668022120539", "X video post linked by State of the Scene", "X", "video"),
    LiveURL("x_carousel_3", None, "X post with at least three photos", "X", "carousel", "could not confirm a currently public 3+ photo post"),
    LiveURL("x_gif", None, "X animated GIF post", "X", "gif", "could not confirm a currently public GIF post"),
    LiveURL("x_text_only", None, "X text-only post", "X", "text", "could not confirm a currently public text-only post"),
    LiveURL("x_quote", None, "X quote post", "X", "quote", "could not confirm a currently public quote post"),
)

BY_KEY = {case.key: case for case in CASES}


def pytest_params(*kinds: str):
    return [
        pytest.param(case, id=case.key, marks=pytest.mark.skip(reason=case.reason or "URL not selected"))
        if case.url is None
        else pytest.param(case, id=case.key)
        for case in CASES
        if not kinds or case.kind in kinds
    ]
