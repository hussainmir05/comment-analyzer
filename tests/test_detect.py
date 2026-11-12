import pytest

from comment_analyzer.models import Platform
from comment_analyzer.platforms.detect import (
    SHORT_LINK_MESSAGE,
    X_MESSAGE,
    LinkStatus,
    detect,
    instagram_media_id,
    instagram_shortcode,
)

OK, BROWSER, NOT_POST = LinkStatus.OK, LinkStatus.NEEDS_BROWSER, LinkStatus.NOT_A_POST
YT, FB, IG, TT = Platform.YOUTUBE, Platform.FACEBOOK, Platform.INSTAGRAM, Platform.TIKTOK
PRIVATE_IG = "https://www.instagram.com/p/Chunk8-jurw" + "A" * 28 + "/"

CASES = [
    # YouTube
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", OK, YT, "dQw4w9WgXcQ"),
    ("https://youtube.com/watch?v=dQw4w9WgXcQ&t=43s", OK, YT, "dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&feature=share", OK, YT, "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?app=desktop&v=dQw4w9WgXcQ", OK, YT, "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?si=AbCdEf123", OK, YT, "dQw4w9WgXcQ"),
    ("youtu.be/dQw4w9WgXcQ", OK, YT, "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/aBcDeFgHiJk?feature=share", OK, YT, "aBcDeFgHiJk"),
    ("https://www.youtube.com/live/aBcDeFgHiJk", OK, YT, "aBcDeFgHiJk"),
    ("https://www.youtube.com/embed/dQw4w9WgXcQ", OK, YT, "dQw4w9WgXcQ"),
    ("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ", OK, YT, "dQw4w9WgXcQ"),
    ("https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=RDAMVM", OK, YT, "dQw4w9WgXcQ"),
    ("HTTPS://WWW.YOUTUBE.COM/watch?v=dQw4w9WgXcQ", OK, YT, "dQw4w9WgXcQ"),
    ("Check this out: https://youtu.be/dQw4w9WgXcQ!", OK, YT, "dQw4w9WgXcQ"),
    ("<https://youtu.be/dQw4w9WgXcQ>", OK, YT, "dQw4w9WgXcQ"),
    ("https://www.youtube.com/@SomeChannel", NOT_POST, YT, None),
    ("https://www.youtube.com/playlist?list=PL123", NOT_POST, YT, None),
    ("https://www.youtube.com/watch?v=tooShort", NOT_POST, YT, None),
    ("https://www.youtube.com/results?search_query=cats", NOT_POST, YT, None),
    # Facebook
    ("https://www.facebook.com/SomePage/posts/pfbid02abcDEF123xyz", OK, FB, "pfbid02abcDEF123xyz"),
    ("https://www.facebook.com/SomePage/posts/1234567890123456/", OK, FB, "1234567890123456"),
    (
        "https://m.facebook.com/SomePage/posts/1234567890123456?mibextid=x",
        OK,
        FB,
        "1234567890123456",
    ),
    ("https://fb.com/SomePage/posts/1234567890123456", OK, FB, "1234567890123456"),
    ("https://www.facebook.com/watch/?v=987654321012345", OK, FB, "987654321012345"),
    ("https://www.facebook.com/watch?v=987654321012345", OK, FB, "987654321012345"),
    ("https://www.facebook.com/SomePage/videos/987654321012345/", OK, FB, "987654321012345"),
    (
        "https://www.facebook.com/SomePage/videos/a-title/987654321012345/",
        OK,
        FB,
        "987654321012345",
    ),
    ("https://www.facebook.com/reel/1122334455667788", OK, FB, "1122334455667788"),
    ("https://www.facebook.com/photo/?fbid=10160000000000001&set=a.1", OK, FB, "10160000000000001"),
    ("https://www.facebook.com/photo.php?fbid=10160000000000001", OK, FB, "10160000000000001"),
    ("https://www.facebook.com/permalink.php?story_fbid=pfbid0xyz&id=1000640", OK, FB, "pfbid0xyz"),
    ("https://m.facebook.com/story.php?story_fbid=1234567890&id=1000640", OK, FB, "1234567890"),
    ("https://www.facebook.com/groups/123456789/posts/987654321/", OK, FB, "987654321"),
    ("https://www.facebook.com/groups/somegroup/permalink/987654321/", OK, FB, "987654321"),
    ("https://www.facebook.com/SomePage/photos/a.123456/7890123456/", OK, FB, "7890123456"),
    ("https://fb.watch/abcDEF123/", BROWSER, FB, None),
    ("https://www.facebook.com/share/p/1AbCdEfGh/", BROWSER, FB, None),
    ("https://www.facebook.com/share/v/1AbCdEfGh/", BROWSER, FB, None),
    ("https://www.facebook.com/share/r/1AbCdEfGh/", BROWSER, FB, None),
    ("https://www.facebook.com/share/1AbCdEfGh/", BROWSER, FB, None),
    ("https://www.facebook.com/SomePage", NOT_POST, FB, None),
    ("https://www.facebook.com/profile.php?id=100064000000000", NOT_POST, FB, None),
    ("https://www.facebook.com/", NOT_POST, FB, None),
    (
        "https://l.facebook.com/l.php?u=https%3A%2F%2Fyoutu.be%2FdQw4w9WgXcQ&h=AT0",
        OK,
        YT,
        "dQw4w9WgXcQ",
    ),
    # Instagram
    ("https://www.instagram.com/p/Chunk8-jurw/", OK, IG, "Chunk8-jurw"),
    ("https://www.instagram.com/reel/C1a2B3c4D5e/?igsh=abc", OK, IG, "C1a2B3c4D5e"),
    ("https://www.instagram.com/reels/C1a2B3c4D5e/", OK, IG, "C1a2B3c4D5e"),
    ("https://www.instagram.com/tv/B1a2B3c4D5e/", OK, IG, "B1a2B3c4D5e"),
    ("https://www.instagram.com/someuser/p/Chunk8-jurw/", OK, IG, "Chunk8-jurw"),
    ("https://www.instagram.com/some.user_1/reel/C1a2B3c4D5e/", OK, IG, "C1a2B3c4D5e"),
    ("instagram.com/p/Chunk8-jurw", OK, IG, "Chunk8-jurw"),
    ("https://instagr.am/p/Chunk8-jurw/", OK, IG, "Chunk8-jurw"),
    (PRIVATE_IG, OK, IG, "Chunk8-jurw"),
    ("https://www.instagram.com/share/BAbCdEfGh1/", BROWSER, IG, None),
    ("https://www.instagram.com/share/reel/BAbCdEfGh1/", BROWSER, IG, None),
    ("https://www.instagram.com/someuser/", NOT_POST, IG, None),
    ("https://www.instagram.com/stories/someuser/3123456789012345678/", NOT_POST, IG, None),
    # TikTok
    ("https://www.tiktok.com/@someuser/video/7301234567890123456", OK, TT, "7301234567890123456"),
    (
        "https://www.tiktok.com/@some.user/video/7301234567890123456?is_from_webapp=1",
        OK,
        TT,
        "7301234567890123456",
    ),
    ("https://www.tiktok.com/@someuser/photo/7301234567890123456", OK, TT, "7301234567890123456"),
    ("https://m.tiktok.com/v/7301234567890123456.html", OK, TT, "7301234567890123456"),
    ("https://www.tiktok.com/embed/v2/7301234567890123456", OK, TT, "7301234567890123456"),
    ("https://vm.tiktok.com/ZMabc123/", BROWSER, TT, None),
    ("https://vt.tiktok.com/ZSabc123/", BROWSER, TT, None),
    ("https://www.tiktok.com/t/ZTabc123/", BROWSER, TT, None),
    ("https://www.tiktok.com/@someuser", NOT_POST, TT, None),
    ("https://www.tiktok.com/@someuser/live", NOT_POST, TT, None),
    # X, other sites, not links
    ("https://x.com/someone/status/1790000000000000000", LinkStatus.X, None, None),
    ("https://twitter.com/someone/status/1790000000000000000", LinkStatus.X, None, None),
    ("https://mobile.twitter.com/someone/status/123", LinkStatus.X, None, None),
    ("https://www.reddit.com/r/python/comments/abc/", LinkStatus.UNSUPPORTED, None, None),
    ("https://bit.ly/3abcDEF", LinkStatus.UNSUPPORTED, None, None),
    ("https://t.co/AbCdEf", LinkStatus.UNSUPPORTED, None, None),
    ("https://evil.com/?u=youtube.com/watch?v=dQw4w9WgXcQ", LinkStatus.UNSUPPORTED, None, None),
    ("https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ", LinkStatus.UNSUPPORTED, None, None),
    ("https://notyoutube.com/watch?v=dQw4w9WgXcQ", LinkStatus.UNSUPPORTED, None, None),
    ("ftp://youtube.com/watch?v=dQw4w9WgXcQ", LinkStatus.INVALID, None, None),
    ("hello world", LinkStatus.INVALID, None, None),
    ("", LinkStatus.EMPTY, None, None),
    ("   ", LinkStatus.EMPTY, None, None),
]


@pytest.mark.parametrize(("text", "status", "platform", "post_id"), CASES)
def test_detect(text: str, status: LinkStatus, platform: Platform | None, post_id: str | None):
    info = detect(text)
    assert info.status is status
    assert info.platform == platform
    assert info.post_id == post_id
    assert info.ok == (status is OK)
    if status is not LinkStatus.EMPTY:
        assert info.message  # every non-empty result explains itself


@pytest.mark.parametrize(
    ("text", "canonical"),
    [
        ("https://youtu.be/dQw4w9WgXcQ?si=x", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        (
            "https://www.youtube.com/shorts/aBcDeFgHiJk",
            "https://www.youtube.com/watch?v=aBcDeFgHiJk",
        ),
        (
            "https://m.facebook.com/SomePage/posts/123?mibextid=x&__cft__=y",
            "https://www.facebook.com/SomePage/posts/123/",
        ),
        (
            "https://m.facebook.com/story.php?story_fbid=123&id=456&x=1",
            "https://www.facebook.com/permalink.php?story_fbid=123&id=456",
        ),
        ("https://www.facebook.com/watch?v=987&t=10", "https://www.facebook.com/watch/?v=987"),
        (
            "https://www.instagram.com/reel/C1a2B3c4D5e/?igsh=a",
            "https://www.instagram.com/p/C1a2B3c4D5e/",
        ),
        (PRIVATE_IG, "https://www.instagram.com/p/Chunk8-jurw/"),
        (
            "https://www.tiktok.com/@a.b/video/7301234567890123456?lang=en#c",
            "https://www.tiktok.com/@a.b/video/7301234567890123456",
        ),
        (
            "https://m.tiktok.com/v/7301234567890123456.html?u=1",
            "https://m.tiktok.com/v/7301234567890123456.html",
        ),
    ],
)
def test_canonical_url_drops_tracking(text: str, canonical: str):
    assert detect(text).canonical_url == canonical


def test_x_and_short_links_explain_themselves():
    assert detect("https://x.com/a/status/1").message == X_MESSAGE
    assert detect("https://bit.ly/abc").message == SHORT_LINK_MESSAGE


@pytest.mark.parametrize(
    ("shortcode", "media_id"),
    [("A", 0), ("B", 1), ("_", 63), ("BA", 64), ("BAA", 4096), ("Chunk8-jurw" + "A" * 28, None)],
)
def test_instagram_media_id(shortcode: str, media_id: int | None):
    expected = instagram_media_id("Chunk8-jurw") if media_id is None else media_id
    assert instagram_media_id(shortcode) == expected


def test_instagram_shortcode_keeps_normal_codes():
    assert instagram_shortcode("Chunk8-jurw") == "Chunk8-jurw"
