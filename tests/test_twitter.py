import pytest

from tiktok_obsidian.core.twitter import (
    MediaKind,
    TweetParseError,
    parse_fx,
    parse_vx,
    tweet_ref_from_url,
)


def test_ref_from_plain_and_mirror_links():
    # Уже подменённые вручную домены должны продолжать работать.
    for host in ("x.com", "twitter.com", "fixupx.com", "vxtwitter.com", "fxtwitter.com"):
        ref = tweet_ref_from_url(f"https://{host}/NASA/status/123")
        assert ref is not None, host
        assert ref.tweet_id == "123"
        assert ref.screen_name == "NASA"


def test_ref_canonical_url_and_anonymous_form():
    assert tweet_ref_from_url("https://x.com/i/status/9").screen_name == "i"
    assert (
        tweet_ref_from_url("https://fixupx.com/NASA/status/5").canonical_url
        == "https://x.com/NASA/status/5"
    )
    assert tweet_ref_from_url("https://mobile.twitter.com/u/statuses/9").tweet_id == "9"


def test_ref_rejects_non_status_links():
    assert tweet_ref_from_url("https://x.com/NASA") is None
    assert tweet_ref_from_url("https://example.com/x/status/1") is None
    assert tweet_ref_from_url("") is None


def test_parse_fx_preserves_mixed_media_order_and_date():
    payload = {
        "code": 200,
        "tweet": {
            "id": "100",
            "url": "https://x.com/NASA/status/100",
            "text": "Привет из космоса",
            "created_timestamp": 1770000000,
            "author": {"name": "NASA", "screen_name": "NASA"},
            "media": {
                "all": [
                    {"type": "photo", "url": "https://pbs.twimg.com/media/a.jpg"},
                    {"type": "video", "url": "https://video.twimg.com/b.mp4"},
                ]
            },
        },
    }
    data = parse_fx(payload)
    assert [m.kind for m in data.media] == [MediaKind.PHOTO, MediaKind.VIDEO]
    assert data.author == "NASA"
    assert data.upload_date == "2026-02-02"


def test_parse_fx_tombstone_and_null_tweet():
    with pytest.raises(TweetParseError):
        parse_fx({"code": 404, "tweet": {"type": "tombstone", "message": "unavailable"}})
    with pytest.raises(TweetParseError):
        parse_fx({"code": 404, "message": "NOT_FOUND", "tweet": None})


def test_parse_fx_falls_back_to_quoted_post_media():
    payload = {
        "code": 200,
        "tweet": {
            "id": "1",
            "author": {"screen_name": "u"},
            "quote": {"media": {"all": [{"type": "video", "url": "https://video.twimg.com/q.mp4"}]}},
        },
    }
    data = parse_fx(payload)
    assert [m.url for m in data.media] == ["https://video.twimg.com/q.mp4"]
    # Автор остаётся от цитирующего поста — это то, что прислал заказчик.
    assert data.author == "u"


def test_parse_fx_own_media_wins_over_quote():
    payload = {
        "code": 200,
        "tweet": {
            "id": "1",
            "author": {},
            "media": {"all": [{"type": "photo", "url": "own.jpg"}]},
            "quote": {"media": {"all": [{"type": "photo", "url": "quoted.jpg"}]}},
        },
    }
    assert [m.url for m in parse_fx(payload).media] == ["own.jpg"]


def test_parse_fx_text_only_tweet_has_no_media():
    assert parse_fx({"code": 200, "tweet": {"id": "1", "author": {}}}).media == []


def test_parse_vx_maps_image_to_photo():
    payload = {
        "tweetID": "200",
        "tweetURL": "https://twitter.com/u/status/200",
        "user_screen_name": "u",
        "text": "t",
        "media_extended": [
            {"type": "image", "url": "https://pbs.twimg.com/a.jpg", "size": {"width": 1}},
            {"type": "gif", "url": "https://video.twimg.com/g.mp4"},
        ],
    }
    assert [m.kind for m in parse_vx(payload).media] == [MediaKind.PHOTO, MediaKind.GIF]


def test_parse_vx_drops_link_card_preview():
    # Пост-ссылка без своих медиа: vx отдаёт превью чужого сайта, fx — ничего.
    payload = {
        "tweetID": "1",
        "user_screen_name": "u",
        "media_extended": [{"type": "image", "url": "https://pbs.twimg.com/card_img/9/a?format=jpg"}],
    }
    assert parse_vx(payload).media == []


def test_parse_vx_html_stub_is_parse_error():
    with pytest.raises(TweetParseError):
        parse_vx({"error": "Failed to scan your link!"})
