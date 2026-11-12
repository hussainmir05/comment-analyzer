"""Work out which platform a pasted link is from and which post it points at.

Nothing here touches the network. Share links (fb.watch/..., vm.tiktok.com/...) only lead to
the post inside a logged-in browser, so they come back as NEEDS_BROWSER and the in-app browser
resolves them. Facebook IDs taken from links are provisional: one post can be reached through
several IDs, and the Facebook collector replaces them with the ID from the page data.
"""

import re
from dataclasses import dataclass, replace
from enum import StrEnum
from urllib.parse import parse_qs, urlsplit

from comment_analyzer.models import Platform


class LinkStatus(StrEnum):
    OK = "ok"  # platform and post ID known
    NEEDS_BROWSER = "needs_browser"  # share link: the in-app browser finds the post
    NOT_A_POST = "not_a_post"  # a supported site, but a profile, channel, feed...
    X = "x"  # X (Twitter): explained in the UI, never collected
    UNSUPPORTED = "unsupported"  # any other site
    INVALID = "invalid"  # not a web link
    EMPTY = "empty"


@dataclass(frozen=True, slots=True)
class LinkInfo:
    status: LinkStatus
    platform: Platform | None = None
    post_id: str | None = None
    canonical_url: str | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status is LinkStatus.OK


ASK_FOR_A_POST = "Paste a link to a YouTube, Facebook, Instagram or TikTok post."
X_MESSAGE = (
    "X (Twitter) isn't supported. Its API charges for every post read and its rules ban "
    "automating x.com, so the app can't collect X comments."
)
SHORT_LINK_MESSAGE = (
    "This is a shortened link. Open it in your browser, then paste the address it ends up on."
)

_X_HOSTS = ("x.com", "twitter.com")
_SHORTENERS = (
    "bit.ly", "t.co", "tinyurl.com", "goo.gl", "ow.ly", "buff.ly", "lnkd.in",
    "rebrand.ly", "cutt.ly", "is.gd", "shorturl.at", "s.id",
)  # fmt: skip
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_BARE_DOMAIN = re.compile(r"^(?:[\w-]+\.)+[a-z]{2,}(?:[/?#:]|$)", re.IGNORECASE)
_TRAILING = ".,;:!?)]}>'\""
_NUMBER = re.compile(r"[0-9]+")


def detect(text: str) -> LinkInfo:
    """Classify a pasted link. Never raises: problems come back as a status with a message."""
    if not text or not text.strip():
        return LinkInfo(LinkStatus.EMPTY)
    raw = _find_url(text)
    if raw is None:
        return LinkInfo(
            LinkStatus.INVALID, message="That doesn't look like a link. " + ASK_FOR_A_POST
        )
    if not _SCHEME.match(raw):
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").rstrip(".")
    except ValueError:
        return LinkInfo(LinkStatus.INVALID, message="That link is malformed. " + ASK_FOR_A_POST)
    if parts.scheme.lower() not in ("http", "https") or not host:
        return LinkInfo(LinkStatus.INVALID, message="That isn't a web link. " + ASK_FOR_A_POST)

    query = parse_qs(parts.query)
    if _on(host, "youtube.com", "youtu.be", "youtube-nocookie.com"):
        return _youtube(host, parts.path, query)
    info: LinkInfo | None = None
    if _on(host, "facebook.com", "fb.com", "fb.watch"):
        info = _facebook(host, parts.path, query)
    elif _on(host, "instagram.com", "instagr.am"):
        info = _instagram(parts.path)
    elif _on(host, "tiktok.com"):
        info = _tiktok(host, raw, parts.path)
    if info is not None:
        if info.status is LinkStatus.NEEDS_BROWSER and info.canonical_url is None:
            opened = "https://" + raw.split("://", 1)[1].split("#")[0]  # the browser opens this
            info = replace(info, canonical_url=opened)
        return info
    if _on(host, *_X_HOSTS):
        return LinkInfo(LinkStatus.X, message=X_MESSAGE)
    if _on(host, *_SHORTENERS):
        return LinkInfo(LinkStatus.UNSUPPORTED, message=SHORT_LINK_MESSAGE)
    return LinkInfo(LinkStatus.UNSUPPORTED, message=ASK_FOR_A_POST)


def _find_url(text: str) -> str | None:
    """The first word that looks like a link, so "Look at this: https://..." works too."""
    for word in text.split():
        candidate = word.strip("<>\"'").rstrip(_TRAILING)
        if _SCHEME.match(candidate) or _BARE_DOMAIN.match(candidate):
            return candidate
    return None


def _on(host: str, *domains: str) -> bool:
    """True if host is one of the domains or a subdomain of one (m.youtube.com, ...)."""
    return any(host == d or host.endswith("." + d) for d in domains)


# --- YouTube ----------------------------------------------------------------------------------
_YT_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_YT_SHORT_LINK = re.compile(r"/([A-Za-z0-9_-]{11})/?")
_YT_PATH = re.compile(r"/(shorts|live|embed|v|e)/([A-Za-z0-9_-]{11})/?")


def _youtube(host: str, path: str, query: dict[str, list[str]]) -> LinkInfo:
    video_id, kind = None, "video"
    if _on(host, "youtu.be"):
        if m := _YT_SHORT_LINK.fullmatch(path):
            video_id = m.group(1)
    elif path.rstrip("/") == "/watch":
        v = query.get("v", [""])[0]
        video_id = v if _YT_ID.fullmatch(v) else None
    elif m := _YT_PATH.fullmatch(path):
        video_id, kind = m.group(2), ("Short" if m.group(1) == "shorts" else "video")
    if video_id is None:
        return LinkInfo(
            LinkStatus.NOT_A_POST,
            Platform.YOUTUBE,
            message="This YouTube link isn't a single video (it may be a channel, playlist or "
            "search). Paste a link to one video.",
        )
    return LinkInfo(
        LinkStatus.OK,
        Platform.YOUTUBE,
        video_id,
        f"https://www.youtube.com/watch?v={video_id}",
        f"YouTube {kind} {video_id}",
    )


# --- Facebook ---------------------------------------------------------------------------------
_FB_ID = r"(pfbid[0-9A-Za-z]+|[0-9]+)"
_FB_PATHS = [
    (re.compile(rf"/groups/[^/]+/(?:posts|permalink)/{_FB_ID}"), "post"),
    (re.compile(rf"/[^/]+/posts/{_FB_ID}"), "post"),
    (re.compile(r"/[^/]+/videos/(?:[^/]+/)?([0-9]+)"), "video"),
    (re.compile(r"/reel/([0-9]+)"), "reel"),
    (re.compile(r"/[^/]+/photos/(?:[^/]+/)?([0-9]+)"), "photo"),
]


def _facebook(host: str, path: str, query: dict[str, list[str]]) -> LinkInfo:
    def param(name: str) -> str:
        return query.get(name, [""])[0]

    p = path.rstrip("/")
    if p == "/l.php" and param("u"):  # l.facebook.com wraps links to other sites
        return detect(param("u"))
    if _on(host, "fb.watch") or p.startswith("/share/"):
        return LinkInfo(
            LinkStatus.NEEDS_BROWSER,
            Platform.FACEBOOK,
            message="Facebook share link. The app opens it in its own browser to find the post.",
        )
    if p == "/watch" and _NUMBER.fullmatch(param("v")):
        return _fb_ok(param("v"), f"https://www.facebook.com/watch/?v={param('v')}", "video")
    if p in ("/photo", "/photo.php") and _NUMBER.fullmatch(param("fbid")):
        return _fb_ok(
            param("fbid"), f"https://www.facebook.com/photo/?fbid={param('fbid')}", "photo"
        )
    if p in ("/permalink.php", "/story.php") and re.fullmatch(_FB_ID, param("story_fbid")):
        story, owner = param("story_fbid"), param("id")
        url = f"https://www.facebook.com/permalink.php?story_fbid={story}"
        if _NUMBER.fullmatch(owner):
            url += f"&id={owner}"
        return _fb_ok(story, url, "post")
    for pattern, kind in _FB_PATHS:
        if m := pattern.fullmatch(p):
            return _fb_ok(m.group(1), f"https://www.facebook.com{p}/", kind)
    return LinkInfo(
        LinkStatus.NOT_A_POST,
        Platform.FACEBOOK,
        message="This Facebook link isn't a single post. Open the post itself (click its date or "
        "time) and copy that link.",
    )


def _fb_ok(post_id: str, url: str, kind: str) -> LinkInfo:
    return LinkInfo(LinkStatus.OK, Platform.FACEBOOK, post_id, url, f"Facebook {kind} {post_id}")


# --- Instagram --------------------------------------------------------------------------------
IG_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_IG_POST = re.compile(r"/(?:[A-Za-z0-9._]+/)?(p|reels?|tv)/([A-Za-z0-9_-]+)/?")
_IG_PRIVATE_SUFFIX = 28  # links to private posts carry 28 extra characters after the shortcode


def _instagram(path: str) -> LinkInfo:
    if path.startswith("/share/"):
        return LinkInfo(
            LinkStatus.NEEDS_BROWSER,
            Platform.INSTAGRAM,
            message="Instagram share link. The app opens it in its own browser to find the post.",
        )
    if m := _IG_POST.fullmatch(path):
        code = instagram_shortcode(m.group(2))
        kind = "reel" if m.group(1).startswith("reel") else "post"
        return LinkInfo(
            LinkStatus.OK,
            Platform.INSTAGRAM,
            code,
            f"https://www.instagram.com/p/{code}/",
            f"Instagram {kind} {code}",
        )
    if path.startswith("/stories/"):
        message = "Instagram stories have no public comments. Paste a link to a post or reel."
    else:
        message = "This Instagram link isn't a single post or reel."
    return LinkInfo(LinkStatus.NOT_A_POST, Platform.INSTAGRAM, message=message)


def instagram_shortcode(code: str) -> str:
    """The post's shortcode, without the extra characters that private-post links carry."""
    return code[:-_IG_PRIVATE_SUFFIX] if len(code) > _IG_PRIVATE_SUFFIX else code


def instagram_media_id(shortcode: str) -> int:
    """Instagram's numeric media ID: the shortcode is that number written in base 64."""
    media_id = 0
    for ch in instagram_shortcode(shortcode):
        media_id = media_id * 64 + IG_ALPHABET.index(ch)
    return media_id


# --- TikTok -----------------------------------------------------------------------------------
_TT_SHARE = re.compile(r"/t/[A-Za-z0-9]+/?")
_TT_POST = re.compile(r"/@([^/]+)/(video|photo)/([0-9]+)/?")
_TT_OTHER = re.compile(r"/(?:v/([0-9]+)(?:\.html)?|embed(?:/v2)?/([0-9]+))/?")


def _tiktok(host: str, url: str, path: str) -> LinkInfo:
    if _on(host, "vm.tiktok.com", "vt.tiktok.com") or _TT_SHARE.fullmatch(path):
        return LinkInfo(
            LinkStatus.NEEDS_BROWSER,
            Platform.TIKTOK,
            message="TikTok share link. The app opens it in its own browser to find the video.",
        )
    if m := _TT_POST.fullmatch(path):
        user, kind, video_id = m.groups()
        return LinkInfo(
            LinkStatus.OK,
            Platform.TIKTOK,
            video_id,
            f"https://www.tiktok.com/@{user}/{kind}/{video_id}",
            f"TikTok {kind} {video_id}",
        )
    if m := _TT_OTHER.fullmatch(path):
        video_id = m.group(1) or m.group(2)
        clean = url.split("#")[0].split("?")[0]
        return LinkInfo(LinkStatus.OK, Platform.TIKTOK, video_id, clean, f"TikTok video {video_id}")
    return LinkInfo(
        LinkStatus.NOT_A_POST, Platform.TIKTOK, message="This TikTok link isn't a single video."
    )
