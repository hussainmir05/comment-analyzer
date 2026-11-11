"""
Comment Analyzer
================
A one-file desktop app: paste a YouTube, Facebook, Instagram or TikTok link and get
a Word report on the post's reactions and the mood of its comments.

    python comment_analyzer.py                       opens the app window
    python comment_analyzer.py "<link>" [--firefox]  same analysis without the window

The report (plus an Excel file with every comment) is saved in
Documents\\Comment Analyzer Reports.

What it reads
    YouTube    views, likes, dislikes (estimate), comments                no login needed
    Facebook   Like / Love / Care / Haha / Wow / Sad / Angry, shares,     Firefox login
               and the comments Facebook shows on the post
    Instagram  likes, views, comments                                     Firefox login for comments
    TikTok     views, likes, shares, saves, comments                      Firefox login

Facebook, Instagram and TikTok only show this to signed-in visitors, so the app borrows
the login from Firefox: sign in to those sites in Firefox once, then tick
"Use my Firefox login". Chrome and Edge lock their logins away from other apps.

Install (only to run from source):
    pip install yt-dlp pandas openpyxl python-docx requests matplotlib vaderSentiment wordcloud

Build the Windows installer ("Comment Analyzer Setup.exe", saved next to this file):
    pip install pyinstaller          and install Inno Setup 6 (https://jrsoftware.org/isdl.php)
    python comment_analyzer.py --build
Rebuild every few months with an updated yt-dlp (pip install -U yt-dlp) - the sites change
often and an old copy stops working.
"""

import argparse
import html as htmllib
import json
import os
import queue
import re
import sys
import tempfile
import threading
import time
import traceback
import warnings
from collections import Counter, deque
from datetime import datetime

# a windowed .exe has no console - give libraries somewhere harmless to print
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import matplotlib
matplotlib.use("Agg")  # draw straight to image files, no pop-up windows
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import requests
import tkinter as tk
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from matplotlib import font_manager
from matplotlib.ticker import MaxNLocator
from tkinter import messagebox, ttk
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from wordcloud import STOPWORDS, WordCloud
from yt_dlp import YoutubeDL

APP_NAME = "Comment Analyzer"
COLUMNS = ["username", "time", "comment", "likes", "is_reply"]
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0"


class Problem(Exception):
    """Something the user can fix - shown as a plain message, not a crash."""


# --------------------------------------------------------------------------
# colours (blue = positive, red = negative, gray = neutral - colour-blind safe)
# --------------------------------------------------------------------------
INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e5e4e0", "#fcfcfb"
BLUE, RED, GRAY = "#2a78d6", "#e34948", "#8a8984"
POLARITY_COLORS = {"Positive": BLUE, "Neutral": GRAY, "Negative": RED}
EMOTION_COLORS = {"Agree": BLUE, "Happy": BLUE, "Neutral": GRAY,
                  "Sad": RED, "Angry": RED, "Disagree": RED}
BLUES = ["#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
REDS = ["#e34948", "#c93c3b", "#a8302f", "#862525", "#661b1b"]
NEUTRALS = ["#0b0b0b", "#52514e", "#8a8984", "#2a78d6", "#1c5cab"]

# Facebook reactions: positive ones blue, laughing/surprised gray, sad/angry red
FB_REACTIONS = ["Like", "Love", "Care", "Haha", "Wow", "Sad", "Angry"]
FB_REACTION_IDS = {"1635855486666999": "Like", "1678524932434102": "Love",
                   "613557422527858": "Care", "115940658764963": "Haha",
                   "478547315650144": "Wow", "908563459236466": "Sad",
                   "444813342392137": "Angry"}
REACTION_COLORS = {"Like": BLUE, "Love": BLUE, "Care": BLUE, "Haha": GRAY, "Wow": GRAY,
                   "Sad": RED, "Angry": RED}

# --------------------------------------------------------------------------
# emotion word lists - edit these to tune the results
# --------------------------------------------------------------------------
EMOTIONS = {
    "Agree": {
        "words": "agree agreed exactly true correct absolutely totally indeed facts yes yep",
        "phrases": ["well said", "so true", "100%", "spot on", "+1", "same here"],
        "emoji": "👍 💯 ✅ 🙌 👏",
    },
    "Disagree": {
        "words": "disagree wrong false incorrect misleading nonsense fake lie lies liar "
                 "lying propaganda clickbait",
        "phrases": ["not true", "no way", "makes no sense", "that's not"],
        "emoji": "👎 🤥",
    },
    "Happy": {
        "words": "love loved great amazing awesome best beautiful thanks thank helpful nice "
                 "good excellent wonderful enjoyed enjoy fun funny lol haha happy glad "
                 "brilliant perfect fantastic cool cute masterpiece inspiring",
        "phrases": ["thank you", "well done", "keep it up", "good job"],
        "emoji": "❤ ♥ 😍 😂 🤣 😊 🥰 🔥 🎉 😄 😁 😆 🤩",
    },
    "Sad": {
        "words": "sad miss cry crying cried tears heartbreaking heartbroken sorry rip "
                 "unfortunately depressing depressed lonely painful",
        "phrases": ["miss you", "rest in peace"],
        "emoji": "😢 😭 💔 😞 🥺 😔 ☹",
    },
    "Angry": {
        "words": "angry hate hated worst terrible awful stupid disgusting ridiculous annoying "
                 "annoyed trash garbage pathetic shame shameful idiot idiots wtf furious "
                 "horrible useless",
        "phrases": ["shame on", "how dare"],
        "emoji": "😡 😠 🤬 💢 🤮 🖕",
    },
}
# when a comment matches two emotions equally, the one listed first here wins
PRIORITY = ["Angry", "Disagree", "Sad", "Agree", "Happy"]
NEGATORS = {"not", "no", "never", "don't", "dont", "doesn't", "didn't", "isn't", "wasn't",
            "aren't", "can't", "cannot", "won't", "hardly", "nothing"}

# skin tones (👍🏽 -> 👍) and the invisible emoji-style marker (❤️ -> ❤)
STRIP = re.compile("[\U0001F3FB-\U0001F3FF️]")
WORD_TO_EMOTION = {w: e for e, d in EMOTIONS.items() for w in d["words"].split()}
PHRASE_TO_EMOTION = {p: e for e, d in EMOTIONS.items() for p in d["phrases"]}
EMOJI_TO_EMOTION = {STRIP.sub("", x): e for e, d in EMOTIONS.items() for x in d["emoji"].split()}

# words too common to tell you anything
COMMON = {"video", "videos", "youtube", "channel", "just", "like", "get", "got", "really",
          "one", "will", "people", "know", "think", "would", "even", "much", "also",
          "still", "make", "want", "see", "now", "time", "go", "going", "say", "said",
          "thing", "things", "lot", "way", "can", "could", "should", "every", "ever",
          "us", "im", "i'm", "it's", "don't", "dont", "that's", "you're", "watch",
          "watching", "watched", "please", "guys", "man", "bro", "used", "again", "anything",
          "post", "page", "facebook", "instagram", "tiktok", "reel", "reels"}
CLOUD_STOP = set(STOPWORDS) | COMMON
ISSUE_STOP = CLOUD_STOP | NEGATORS | set(WORD_TO_EMOTION) | {
    "bad", "good", "better", "worse", "really", "very", "much", "many", "make", "made"}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def clean(text):
    # Excel and Word reject invisible control characters
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text or "")).strip()


def words_of(text):
    """Lower-case words (works for English, Korean and other alphabets)."""
    text = str(text).lower().replace("’", "'")
    words = re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)?", text)
    return [w for w in words if len(w) >= 3 or (not w.isascii() and len(w) >= 2)]


def no_emoji(text):
    """Charts can't draw colour emoji, so remove them from chart labels."""
    return re.sub("[\U00010000-\U0010FFFF☀-➿⬀-⯿️‍]", "",
                  str(text)).strip()


def short(text, n):
    text = no_emoji(text).replace("\n", " ")
    return text if len(text) <= n else text[:n - 1] + "…"


def number(v):
    return "not public" if v is None or pd.isna(v) else f"{int(v):,}"


def stamp(ts):
    """Unix time -> 'YYYY-MM-DD HH:MM' in the computer's time zone."""
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M") if ts else ""
    except (TypeError, ValueError, OSError):
        return ""


def ymd(yyyymmdd):
    s = str(yyyymmdd or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:]}" if re.fullmatch(r"\d{8}", s) else ""


def parse_count(v):
    """Counts arrive as 1234, '1,234', '1.2K' or '3M'."""
    if isinstance(v, int):
        return v
    m = re.fullmatch(r"\s*([\d.,]+)\s*([KkMm]?)\s*", str(v or ""))
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    return int(n * {"": 1, "k": 1_000, "m": 1_000_000}[m.group(2).lower()])


def new_post(platform, url):
    return {"platform": platform, "url": url, "title": "", "author": "", "published": "",
            "stats": {}, "reactions": {}, "notes": [], "times_exact": True}


def app_folder():
    folder = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
    os.makedirs(folder, exist_ok=True)
    return folder


def reports_folder():
    """Documents\\Comment Analyzer Reports (follows Documents if it was moved to OneDrive)."""
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    if sys.platform == "win32":
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:  # 5 = Documents
            docs = buf.value
    folder = os.path.join(docs, f"{APP_NAME} Reports")
    os.makedirs(folder, exist_ok=True)
    return folder


def load_settings():
    try:
        with open(os.path.join(app_folder(), "settings.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    with open(os.path.join(app_folder(), "settings.json"), "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


# --------------------------------------------------------------------------
# which site is the link from?
# --------------------------------------------------------------------------
def detect_platform(url):
    host = re.sub(r"^https?://", "", url.strip().lower()).split("/")[0]
    if re.search(r"(^|\.)(youtube\.com|youtu\.be)$", host):
        return "YouTube"
    if re.search(r"(^|\.)(facebook\.com|fb\.com|fb\.watch)$", host):
        return "Facebook"
    if re.search(r"(^|\.)instagram\.com$", host):
        return "Instagram"
    if re.search(r"(^|\.)tiktok\.com$", host):
        return "TikTok"
    return None


# --------------------------------------------------------------------------
# downloading helpers: Firefox login, yt-dlp, web requests
# --------------------------------------------------------------------------
class QuietLog:
    def debug(self, msg): pass
    info = warning = error = debug


class YDLProgress(QuietLog):
    """Turns yt-dlp's chatter into an occasional 'n of about N comments read' line."""

    def __init__(self, log, stop=None):
        self.log, self.stop, self.last, self.total = log, stop, 0.0, None
        self.interrupted = False

    def debug(self, msg):
        m = re.search(r"comment.*?\((\d+)/~?(\d+)\)", msg)
        if m:
            self.total = int(m.group(2))   # the site's own comment count
            # yt-dlp treats Ctrl+C while reading comments as "keep what you have so far"
            if self.stop is not None and self.stop.is_set() and not self.interrupted:
                self.interrupted = True
                raise KeyboardInterrupt
        if m and time.time() - self.last > 3:
            self.last = time.time()
            self.log(f"  {int(m.group(1)):,} of about {int(m.group(2)):,} comments read")
    info = debug


def web_session():
    s = requests.Session()
    s.headers.update({"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"})
    return s


def firefox_session():
    """A web session signed in the way Firefox is."""
    from yt_dlp.cookies import extract_cookies_from_browser
    try:
        jar = extract_cookies_from_browser("firefox", logger=QuietLog())
    except Exception as e:
        raise Problem("Couldn't read your login from Firefox.\n\nMake sure Firefox is installed "
                      "and that you have signed in to the website in Firefox at least once."
                      f"\n\n(Details: {e})")
    s = web_session()
    for c in jar:
        s.cookies.set_cookie(c)
    return s


def cookie(session, name):
    # the same cookie name can exist on several sub-domains, so don't use .get()
    return next((c.value for c in session.cookies if c.name == name), "")


def ydl_info(url, log, firefox=False, comments=False, extractor_args=None, progress=None):
    opts = {"skip_download": True, "quiet": True, "no_warnings": True, "noprogress": True,
            "ignore_no_formats_error": True,  # photo posts have no video, keep the details
            "logger": progress or YDLProgress(log), "getcomments": comments}
    if extractor_args:
        opts["extractor_args"] = extractor_args
    if firefox:
        opts["cookiesfrombrowser"] = ("firefox",)
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def ytdlp_rows(raw):
    """yt-dlp's comment list -> our rows."""
    return [{"username": clean(c.get("author")), "time": stamp(c.get("timestamp")),
             "comment": clean(c.get("text")), "likes": c.get("like_count") or 0,
             "is_reply": "no" if (c.get("parent") or "root") == "root" else "yes"}
            for c in raw or []]


def enough(rows, opts):
    """Stop reading: the comment limit is reached or the user pressed Stop."""
    stop = opts.get("stop")
    return (bool(opts["limit"]) and len(rows) >= opts["limit"]) or bool(stop and stop.is_set())


# ==========================================================================
# YOUTUBE
# ==========================================================================
YT_API = "https://www.googleapis.com/youtube/v3"


def youtube_id(url):
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})", url)
    if not m:
        raise Problem("That doesn't look like a link to a YouTube video.")
    return m.group(1)


def estimated_dislikes(vid):
    """
    YouTube hid public dislike counts in 2021 - only the channel owner can see them.
    This asks the Return YouTube Dislike project for its ESTIMATE instead.
    """
    try:
        r = requests.get("https://returnyoutubedislikeapi.com/votes",
                         params={"videoId": vid}, timeout=10)
        return int(r.json()["dislikes"])
    except Exception:
        return None


def api_get(session, key, endpoint, params):
    """One YouTube API call, retried on network hiccups, with clear errors for real problems."""
    for attempt in range(6):
        try:
            r = session.get(f"{YT_API}/{endpoint}", params={**params, "key": key}, timeout=30)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 200:
            return r.json()
        try:
            reason = r.json()["error"]["errors"][0]["reason"]
        except Exception:
            reason = ""
        if reason == "commentsDisabled":
            return {"items": []}
        if reason in ("quotaExceeded", "dailyLimitExceeded"):
            raise Problem("Today's free YouTube API quota is used up. Try again tomorrow, or "
                          "clear the API key box to use the slower way.")
        if reason in ("keyInvalid", "badRequest", "forbidden", "accessNotConfigured"):
            raise Problem(f"YouTube rejected the API key ({reason}). Check the key, or clear "
                          "the API key box to use the slower way.")
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 ** attempt)
            continue
        raise Problem(f"YouTube API error {r.status_code}: {r.text[:300]}")
    raise Problem("YouTube kept failing - check your internet connection and try again.")


def api_row(snippet, reply=False):
    published = datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00"))
    return {"username": clean(snippet.get("authorDisplayName")),
            "time": published.astimezone().strftime("%Y-%m-%d %H:%M"),
            "comment": clean(snippet.get("textDisplay")),
            "likes": snippet.get("likeCount", 0), "is_reply": "yes" if reply else "no"}


def youtube_with_api(vid, key, opts, log):
    s, limit = requests.Session(), opts["limit"]
    data = api_get(s, key, "videos", {"part": "snippet,statistics", "id": vid})
    if not data.get("items"):
        raise Problem("Video not found - it may be private or deleted.")
    sn, st = data["items"][0]["snippet"], data["items"][0].get("statistics", {})
    as_int = lambda x: int(x) if x is not None else None
    info = {"title": sn.get("title", ""), "author": sn.get("channelTitle", ""),
            "published": sn.get("publishedAt", "")[:10], "views": as_int(st.get("viewCount")),
            "likes": as_int(st.get("likeCount")), "comments": as_int(st.get("commentCount"))}

    rows, token = [], None
    while True:
        params = {"part": "snippet,replies", "videoId": vid, "maxResults": 100,
                  "textFormat": "plainText", "order": "relevance" if limit else "time"}
        if token:
            params["pageToken"] = token
        page = api_get(s, key, "commentThreads", params)
        for item in page.get("items", []):
            rows.append(api_row(item["snippet"]["topLevelComment"]["snippet"]))
            rows += [api_row(c["snippet"], reply=True)
                     for c in item.get("replies", {}).get("comments", [])]
        log(f"  {len(rows):,} comments read")
        token = page.get("nextPageToken")
        if not token or enough(rows, opts):
            break
    return info, rows[:limit] if limit else rows


def youtube_with_ytdlp(vid, opts, log):
    log("Reading comments (a free YouTube API key makes this much faster on big videos)...")
    limit, progress = opts["limit"], YDLProgress(log, opts.get("stop"))
    try:
        raw = ydl_info(f"https://www.youtube.com/watch?v={vid}", log, comments=True,
                       progress=progress, extractor_args={"youtube": {
                           "max_comments": [str(limit) if limit else "all", "all", "all", "all"],
                           "comment_sort": ["top" if limit else "new"]}})
    except Problem:
        raise
    except Exception as e:
        raise Problem(f"Couldn't read this YouTube video. It may be private, age-restricted "
                      f"or deleted.\n\n(Details: {e})")
    # with comments on, yt-dlp replaces comment_count with how many it read
    on_youtube = max(progress.total or 0, raw.get("comment_count") or 0) or None
    info = {"title": raw.get("title", ""), "author": raw.get("channel") or raw.get("uploader", ""),
            "published": ymd(raw.get("upload_date")), "views": raw.get("view_count"),
            "likes": raw.get("like_count"), "comments": on_youtube}
    return info, ytdlp_rows(raw.get("comments"))


def fetch_youtube(url, opts, log):
    vid = youtube_id(url)
    post = new_post("YouTube", f"https://www.youtube.com/watch?v={vid}")
    if opts["api_key"]:
        info, rows = youtube_with_api(vid, opts["api_key"], opts, log)
    else:
        info, rows = youtube_with_ytdlp(vid, opts, log)
        # yt-dlp only sees "3 months ago", so older comment times are approximate
        post["times_exact"] = False
    log("Getting the dislike estimate...")
    post.update(title=info["title"], author=info["author"], published=info["published"])
    post["stats"] = {"Views": info["views"], "Likes": info["likes"],
                     "Dislikes (estimate)": estimated_dislikes(vid), "Comments": info["comments"]}
    post["notes"].append("YouTube stopped showing dislikes in 2021, so the dislike figure is an "
                         "estimate from the Return YouTube Dislike project.")
    if not post["times_exact"]:
        post["notes"].append("Older comment times are approximate because YouTube only shows "
                             "\"3 months ago\", \"1 year ago\" and so on.")
    return post, rows


# ==========================================================================
# TIKTOK
# ==========================================================================
def fetch_tiktok(url, opts, log):
    session = firefox_session() if opts["firefox"] else web_session()
    if not re.search(r"/(?:video|photo)/\d+", url):   # short share links: vm.tiktok.com/...
        try:
            url = session.get(url, timeout=30).url
        except requests.RequestException:
            pass
    m = re.search(r"/(?:video|photo)/(\d+)", url)
    if not m:
        raise Problem("That doesn't look like a link to a TikTok video.")
    aweme = m.group(1)
    post = new_post("TikTok", url)

    try:
        info = ydl_info(url, log, firefox=opts["firefox"])
        post.update(title=info.get("description") or info.get("title", ""),
                    author=info.get("uploader") or info.get("channel", ""),
                    published=ymd(info.get("upload_date")))
        post["stats"] = {"Views": info.get("view_count"), "Likes": info.get("like_count"),
                         "Comments": info.get("comment_count"), "Shares": info.get("repost_count"),
                         "Saves": info.get("save_count")}
    except Exception:
        post["notes"].append("TikTok didn't share the video's view and like counts.")

    log("Reading comments...")
    rows, cursor = [], 0
    while True:
        params = {"aid": "1988", "aweme_id": aweme, "count": 50, "cursor": cursor}
        if cookie(session, "msToken"):
            params["msToken"] = cookie(session, "msToken")
        try:
            data = session.get("https://www.tiktok.com/api/comment/list/", params=params,
                               headers={"Referer": url}, timeout=30).json()
        except (requests.RequestException, ValueError):
            break
        items = data.get("comments") or []
        for c in items:
            user = c.get("user") or {}
            rows.append({"username": clean(user.get("nickname") or user.get("unique_id")),
                         "time": stamp(c.get("create_time")), "comment": clean(c.get("text")),
                         "likes": c.get("digg_count") or 0, "is_reply": "no"})
        if items:
            log(f"  {len(rows):,} comments read")
        if not items or not data.get("has_more") or enough(rows, opts):
            break
        cursor = data.get("cursor") or cursor + len(items)
        time.sleep(0.4)

    if not post["stats"] and not rows:
        raise Problem("TikTok blocked the request." + (
            "\n\nTick \"Use my Firefox login\", open TikTok in Firefox once (signing in is best), "
            "then try again." if not opts["firefox"] else
            "\n\nOpen TikTok in Firefox, play any video, then try again. Signing in to TikTok in "
            "Firefox helps."))
    if not rows and (post["stats"].get("Comments") or 0) > 0:
        post["notes"].append("TikTok didn't hand over the comments. Signing in to TikTok in "
                             "Firefox usually fixes this.")
    return post, rows


# ==========================================================================
# INSTAGRAM
# ==========================================================================
IG_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
IG_HEADERS = {"X-IG-App-ID": "936619743392459", "X-Requested-With": "XMLHttpRequest"}


def instagram_pk(shortcode):
    """The post code in the link (e.g. Chunk8-jurw) -> Instagram's numeric media id."""
    if len(shortcode) > 28:   # private-post links carry extra characters at the end
        shortcode = shortcode[:-28]
    pk = 0
    for ch in shortcode:
        pk = pk * 64 + IG_ALPHABET.index(ch)
    return pk


def fetch_instagram(url, opts, log):
    m = re.search(r"instagram\.com/(?:[^/?#]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)", url)
    if not m:
        raise Problem("That doesn't look like a link to an Instagram post or reel.")
    code = m.group(1)
    link = f"https://www.instagram.com/p/{code}/"
    post = new_post("Instagram", link)
    session = firefox_session() if opts["firefox"] else None
    signed_in = bool(session and cookie(session, "sessionid"))
    headers = {**IG_HEADERS, "Referer": link}
    if signed_in:
        headers["X-CSRFToken"] = cookie(session, "csrftoken")

    # post details: Instagram's own API when signed in, otherwise yt-dlp
    try:
        if not signed_in:
            raise LookupError
        item = session.get(f"https://www.instagram.com/api/v1/media/{instagram_pk(code)}/info/",
                           headers=headers, timeout=30).json()["items"][0]
        post.update(title=(item.get("caption") or {}).get("text", ""),
                    author=(item.get("user") or {}).get("username", ""),
                    published=stamp(item.get("taken_at"))[:10])
        post["stats"] = {"Views": item.get("play_count") or item.get("view_count"),
                         "Likes": item.get("like_count"), "Comments": item.get("comment_count")}
    except Exception:
        try:
            info = ydl_info(link, log, firefox=opts["firefox"])
            post.update(title=info.get("description") or "", published=ymd(info.get("upload_date")),
                        author=info.get("channel") or info.get("uploader_id") or info.get("uploader", ""))
            post["stats"] = {"Views": info.get("view_count"), "Likes": info.get("like_count"),
                             "Comments": info.get("comment_count")}
        except Exception as e:
            if not signed_in:
                raise Problem("Instagram wants you to be signed in to see this post.\n\n"
                              "Sign in to Instagram in Firefox, tick \"Use my Firefox login\", "
                              f"then try again.\n\n(Details: {e})")
            raise Problem(f"Couldn't open this Instagram post. It may be private or deleted."
                          f"\n\n(Details: {e})")

    if not signed_in:
        post["notes"].append("Instagram only shows comments to signed-in visitors. Sign in to "
                             "Instagram in Firefox and tick \"Use my Firefox login\" to include them.")
        return post, []

    log("Reading comments...")
    rows, params = [], {"can_support_threading": "true", "permalink_enabled": "false"}
    while True:
        try:
            data = session.get(f"https://www.instagram.com/api/v1/media/{instagram_pk(code)}/comments/",
                               params=params, headers=headers, timeout=30).json()
        except (requests.RequestException, ValueError):
            break
        for c in data.get("comments") or []:
            rows.append({"username": clean((c.get("user") or {}).get("username")),
                         "time": stamp(c.get("created_at")), "comment": clean(c.get("text")),
                         "likes": c.get("comment_like_count") or 0, "is_reply": "no"})
        log(f"  {len(rows):,} comments read")
        nxt = data.get("next_min_id")
        if not nxt or not data.get("comments") or enough(rows, opts):
            break
        params["min_id"] = nxt
        time.sleep(0.6)

    if not rows and (post["stats"].get("Comments") or 0) > 0:
        try:   # fall back on yt-dlp's reader, which gets the first page
            rows = ytdlp_rows(ydl_info(link, log, firefox=True, comments=True).get("comments"))
        except Exception:
            post["notes"].append("Instagram didn't hand over the comments for this post.")
    return post, rows


# ==========================================================================
# FACEBOOK
# ==========================================================================
def json_blobs(page):
    """Facebook hides its data in <script type="application/json"> blocks."""
    for m in re.finditer(r'<script type="application/json"[^>]*>(.*?)</script>', page, re.S):
        try:
            yield json.loads(m.group(1))
        except ValueError:
            pass


def all_dicts(node):
    """Every dict inside a JSON tree, in page order."""
    todo = deque([node])
    while todo:
        n = todo.popleft()
        if isinstance(n, dict):
            yield n
            todo.extend(n.values())
        elif isinstance(n, list):
            todo.extend(n)


def biggest(dicts, *paths):
    """Largest whole number found at any of these key paths (the post beats its comments)."""
    found = []
    for d in dicts:
        for path in paths:
            v = d
            for k in path:
                v = v.get(k) if isinstance(v, dict) else None
            if isinstance(v, int) and not isinstance(v, bool):
                found.append(v)
    return max(found) if found else None


def fb_reactions(dicts):
    best = {}
    for d in dicts:
        top = d.get("top_reactions")
        if not isinstance(top, dict):
            continue
        counts = {}
        for edge in top.get("edges") or []:
            node = edge.get("node") or {}
            name = (FB_REACTION_IDS.get(str(node.get("id")))
                    or str(node.get("reaction_type") or node.get("localized_name") or "").title())
            n = parse_count(edge.get("reaction_count"))
            if name and n is not None:
                counts[name] = counts.get(name, 0) + n
        if sum(counts.values()) > sum(best.values()):
            best = counts
    order = FB_REACTIONS + sorted(k for k in best if k not in FB_REACTIONS)
    return {k: best[k] for k in order if k in best}


def fb_comment_likes(comment):
    for d in all_dicts(comment.get("feedback") or {}):
        for key in ("reactors", "reaction_count"):
            v = d.get(key)
            if isinstance(v, dict):
                n = parse_count(v.get("count") if v.get("count") is not None else v.get("count_reduced"))
                if n is not None:
                    return n
    return 0


def fb_comments(dicts):
    rows = {}
    for d in dicts:
        if d.get("__typename") != "Comment" or not isinstance(d.get("body"), dict):
            continue
        text = d["body"].get("text")
        if not text:
            continue
        row = {"username": clean((d.get("author") or {}).get("name")),
               "time": stamp(d.get("created_time")), "comment": clean(text),
               "likes": fb_comment_likes(d),
               "is_reply": "yes" if d.get("depth") or d.get("comment_parent") else "no"}
        # the same comment can appear twice on the page - keep whatever each copy knows
        old = rows.setdefault(d.get("id") or text, row)
        for k, v in row.items():
            if v and not old[k] or (k == "likes" and v > old[k]):
                old[k] = v
    return list(rows.values())


def fetch_facebook(url, opts, log):
    if not opts["firefox"]:
        raise Problem("Facebook only shows reactions and comments to people who are signed in."
                      "\n\nSign in to Facebook in Firefox, tick \"Use my Firefox login\", "
                      "then try again.")
    session = firefox_session()
    if not cookie(session, "c_user"):
        raise Problem("You're not signed in to Facebook in Firefox.\n\nOpen Firefox, sign in "
                      "to Facebook, then try again.")

    log("Opening the Facebook post...")
    try:
        r = session.get(url, timeout=40, headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none"})
    except requests.RequestException as e:
        raise Problem(f"Couldn't reach Facebook - check your internet connection.\n\n({e})")
    page = r.text
    dicts = [d for blob in json_blobs(page) for d in all_dicts(blob)]

    post = new_post("Facebook", r.url)
    post["reactions"] = fb_reactions(dicts)
    rows = fb_comments(dicts)
    if not post["reactions"] and not rows:
        if "/login" in r.url or "checkpoint" in r.url:
            raise Problem("Facebook asked to sign in again. Open Facebook in Firefox, make sure "
                          "you're signed in, then try again.")
        raise Problem("Couldn't find the reactions on this Facebook page. Check that the link "
                      "opens the post itself (not a profile or the news feed) and that you "
                      "can see the post in Firefox.")

    og = re.search(r'<meta property="og:title" content="([^"]*)"', page)
    message = next((d["message"]["text"] for d in dicts
                    if isinstance(d.get("message"), dict) and d["message"].get("text")), "")
    post["title"] = message or (htmllib.unescape(og.group(1)) if og else "Facebook post")
    post["author"] = next((a[0]["name"] for d in dicts for a in [d.get("actors")]
                           if isinstance(a, list) and a and isinstance(a[0], dict)
                           and a[0].get("name")), "")
    created = next((d["creation_time"] for d in dicts
                    if isinstance(d.get("creation_time"), int) and "message" in d), None)
    post["published"] = stamp(created)[:10]
    post["stats"] = {
        "Reactions": biggest(dicts, ("reaction_count", "count")) or sum(post["reactions"].values()) or None,
        "Comments": biggest(dicts, ("comments", "total_count"), ("total_comment_count",),
                            ("comment_count", "total_count")),
        "Shares": biggest(dicts, ("share_count", "count")),
        "Views": biggest(dicts, ("video_view_count",), ("play_count",)),
    }
    if not post["stats"]["Views"]:
        del post["stats"]["Views"]
    if opts["limit"]:
        rows = rows[:opts["limit"]]
    post["notes"].append("Facebook only puts the first comments (its \"most relevant\" ones) on "
                         "the post page, so the app analyses those rather than every comment.")
    return post, rows


FETCHERS = {"YouTube": fetch_youtube, "TikTok": fetch_tiktok,
            "Instagram": fetch_instagram, "Facebook": fetch_facebook}


# --------------------------------------------------------------------------
# sentiment and emotion
# --------------------------------------------------------------------------
def polarity(score):
    if score >= 0.05:
        return "Positive"
    if score <= -0.05:
        return "Negative"
    return "Neutral"


def emotion(text, score):
    t = STRIP.sub("", str(text)).lower().replace("’", "'")
    tokens = re.findall(r"[a-z0-9'%+]+", t)
    hits = Counter()

    for i, tok in enumerate(tokens):
        e = WORD_TO_EMOTION.get(tok)
        # "not good" should not count as Happy
        if e and not set(tokens[max(0, i - 2):i]) & NEGATORS:
            hits[e] += 1
    joined = f" {' '.join(tokens)} "
    for phrase, e in PHRASE_TO_EMOTION.items():
        hits[e] += joined.count(f" {phrase} ")
    for ch, e in EMOJI_TO_EMOTION.items():
        hits[e] += t.count(ch)

    best = max(PRIORITY, key=lambda e: (hits[e], -PRIORITY.index(e)))
    if hits[best] > 0:
        return best
    # no emotion words at all: fall back on a very strong overall score
    if score >= 0.5:
        return "Happy"
    if score <= -0.5:
        return "Angry"
    return "Neutral"


def criticized_issues(neg, top=15):
    """Words and two-word phrases mentioned most often in negative comments."""
    mentions, example = Counter(), {}
    for _, r in neg.sort_values("likes", ascending=False).iterrows():
        terms = set()
        # two-word phrases never cross punctuation ("too loud, ruins it" is not "loud ruins")
        for part in re.split(r"[.,!?;:()\[\]\"\n]+", r["comment"]):
            toks = words_of(part)
            terms |= {t for t in toks if t not in ISSUE_STOP}
            terms |= {f"{a} {b}" for a, b in zip(toks, toks[1:])
                      if a != b and a not in ISSUE_STOP and b not in ISSUE_STOP}
        for term in terms:
            mentions[term] += 1                  # counts comments, not repeats
            example.setdefault(term, r)          # keeps the most-liked example

    pairs = [(t, n) for t, n in mentions.items() if " " in t and n >= 2]
    chosen = []
    for term, n in sorted(mentions.items(), key=lambda x: (-x[1], x[0])):
        if " " in term and n < 2:
            continue
        # skip "audio" when "audio quality" already covers it
        if " " not in term and any(term in p.split() and pn >= 0.8 * n for p, pn in pairs):
            continue
        chosen.append({"issue": term, "comments_mentioning": n,
                       "example_user": example[term]["username"],
                       "example_comment": example[term]["comment"]})
        if len(chosen) == top:
            break
    return pd.DataFrame(chosen, columns=["issue", "comments_mentioning",
                                         "example_user", "example_comment"])


def score_comments(df):
    analyzer = SentimentIntensityAnalyzer()
    df["score"] = [analyzer.polarity_scores(t)["compound"] for t in df["comment"]]
    df["sentiment"] = df["score"].apply(polarity)
    df["emotion"] = [emotion(t, s) for t, s in zip(df["comment"], df["score"])]
    return df


# --------------------------------------------------------------------------
# chart helpers
# --------------------------------------------------------------------------
def setup_fonts():
    """Add a Korean-capable font so 한글 usernames and comments don't show as boxes."""
    names = [f.name for f in font_manager.fontManager.ttflist]
    korean = [n for n in ("Malgun Gothic", "AppleGothic", "NanumGothic") if n in names]
    korean += sorted({n for n in names if "CJK" in n})[:1]
    plt.rcParams.update({
        "font.family": ["DejaVu Sans"] + korean[:1],
        "axes.edgecolor": MUTED, "axes.labelcolor": MUTED,
        "xtick.color": MUTED, "ytick.color": MUTED, "text.color": INK,
        "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
    })
    warnings.filterwarnings("ignore", message="Glyph .* missing")
    for f in font_manager.fontManager.ttflist:  # same font for the word clouds
        if korean and f.name == korean[0]:
            return f.fname
    return None


def style(ax, grid="y"):
    ax.set_facecolor(SURFACE)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis=grid, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    # counts are whole numbers - no "2.5 comments" ticks
    (ax.xaxis if grid == "x" else ax.yaxis).set_major_locator(MaxNLocator(integer=True))


def save(fig, path):
    fig.savefig(path, dpi=150, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    return path


def reactions_chart(reactions, path):
    total = sum(reactions.values())
    names = list(reactions)[::-1]
    fig, ax = plt.subplots(figsize=(10, max(3, 0.5 * len(names) + 1)), facecolor=SURFACE)
    style(ax, grid="x")
    ax.barh(names, [reactions[k] for k in names], height=0.6,
            color=[REACTION_COLORS.get(k, GRAY) for k in names])
    for y, k in enumerate(names):
        ax.text(reactions[k], y, f" {reactions[k]:,}  ({100 * reactions[k] / total:.0f}%)",
                va="center", fontsize=10)
    ax.set_xlim(0, max(reactions.values()) * 1.25)
    ax.set_title(f"Reactions  ({total:,} in total)")
    ax.set_xlabel("People")
    return save(fig, path)


def timeline_chart(df, published, path):
    times = pd.to_datetime(df["time"], errors="coerce").dropna()
    if len(times) < 2:
        return None
    fig, ax = plt.subplots(figsize=(12, 4), facecolor=SURFACE)
    style(ax)
    span = (times.max() - times.min()).days
    period, unit, width = (("D", "day", 0.8) if span <= 31 else
                           ("W", "week", 5.6) if span <= 180 else ("M", "month", 24))
    per = times.dt.to_period(period).value_counts().sort_index()
    per = per.reindex(pd.period_range(per.index.min(), per.index.max(), freq=period),
                      fill_value=0)
    ax.bar(per.index.to_timestamp(), per.values, width=width, color=BLUE, align="edge")
    ax.set_title(f"When people commented  (comments per {unit})")
    ax.set_ylabel("Comments")
    if published:
        when = pd.Timestamp(published)
        ax.axvline(when, color=MUTED, linestyle="--", linewidth=1)
        ax.text(when, ax.get_ylim()[1], " posted", color=MUTED, fontsize=9, va="top")
    loc = mdates.AutoDateLocator()
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
    return save(fig, path)


def sentiment_chart(df, path):
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.8), facecolor=SURFACE,
                                 gridspec_kw={"width_ratios": [1, 1.2], "wspace": 0.35})
    total = len(df)

    order = ["Positive", "Neutral", "Negative"]
    counts = df["sentiment"].value_counts().reindex(order, fill_value=0)
    style(a1)
    bars = a1.bar(order, counts.values, width=0.6, color=[POLARITY_COLORS[k] for k in order])
    for bar, n in zip(bars, counts.values):
        a1.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                f"{n:,}  ({100 * n / total:.0f}%)", ha="center", va="bottom", fontsize=10)
    a1.set_ylim(0, max(counts.max(), 1) * 1.15)
    a1.set_title(f"Comment sentiment  ({total:,} comments)")
    a1.set_ylabel("Comments")

    emo = df["emotion"].value_counts().reindex(list(EMOTION_COLORS), fill_value=0)
    emo = emo.sort_values()
    style(a2, grid="x")
    a2.barh(emo.index, emo.values, height=0.6, color=[EMOTION_COLORS[k] for k in emo.index])
    for y, n in enumerate(emo.values):
        a2.text(n, y, f" {n:,}  ({100 * n / total:.0f}%)", va="center", fontsize=10)
    a2.set_xlim(0, max(emo.max(), 1) * 1.25)
    a2.set_title("Emotion in comments")
    a2.set_xlabel("Comments")
    return save(fig, path)


def issues_chart(issues, n_negative, path):
    fig, ax = plt.subplots(figsize=(10, max(3.5, 0.42 * len(issues) + 1)), facecolor=SURFACE)
    style(ax, grid="x")
    data = issues.iloc[::-1]
    ax.barh([short(t, 40) for t in data["issue"]], data["comments_mentioning"],
            height=0.6, color=RED)
    for y, n in enumerate(data["comments_mentioning"]):
        ax.text(n, y, f" {n}", va="center", fontsize=10)
    ax.set_title(f"Most criticized issues  (from {n_negative:,} negative comments)")
    ax.set_xlabel("Negative comments mentioning it")
    return save(fig, path)


def word_cloud(texts, colors, path, font):
    freq = Counter(w for t in texts for w in words_of(t) if w not in CLOUD_STOP)
    if len(freq) < 3:
        return None
    cloud = WordCloud(width=1600, height=800, background_color=SURFACE, font_path=font,
                      max_words=150, prefer_horizontal=0.95, random_state=1,
                      color_func=lambda *a, random_state=None, **k:
                          colors[random_state.randint(0, len(colors) - 1)])
    cloud.generate_from_frequencies(freq).to_file(path)
    return path


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------
def summary_points(post, df, issues):
    """The key findings in plain words, for the top of the report."""
    points, stats, n = [], post["stats"], len(df)
    on_site = stats.get("Comments")
    if n:
        points.append(f"{n:,} comments were analysed" + (
            f" out of about {on_site:,} on {post['platform']}" if on_site and on_site > n else "")
            + ".")
        share = df["sentiment"].value_counts(normalize=True) * 100
        pos, neu, neg = (share.get(k, 0) for k in ("Positive", "Neutral", "Negative"))
        mood = ("mostly positive" if pos - neg >= 15 else
                "mostly negative" if neg - pos >= 15 else "mixed")
        points.append(f"The overall mood of the comments is {mood}: {pos:.0f}% positive, "
                      f"{neu:.0f}% neutral and {neg:.0f}% negative.")
        emo = df.loc[df["emotion"] != "Neutral", "emotion"].value_counts()
        if len(emo):
            points.append(f"The most common emotion is {emo.index[0]} "
                          f"({emo.iloc[0]:,} comments).")
    else:
        points.append("No comments could be read, so this report covers the reactions only.")

    reactions = post["reactions"]
    if reactions:
        total = sum(reactions.values())
        top = max(reactions, key=reactions.get)
        points.append(f"Most people reacted with {top}: {reactions[top]:,} "
                      f"({100 * reactions[top] / total:.0f}% of all reactions).")
        others = [f"{k} {reactions[k]:,}" for k in reactions if k != top]
        if others:
            points.append("Other reactions: " + ", ".join(others) + ".")

    likes, dislikes = stats.get("Likes"), stats.get("Dislikes (estimate)")
    if likes and dislikes is not None:
        points.append(f"{100 * likes / (likes + dislikes):.0f}% of the people who rated it "
                      f"liked it ({likes:,} likes vs about {dislikes:,} dislikes).")
    if not issues.empty:
        points.append("Negative comments most often mention: "
                      + ", ".join(f"\"{t}\"" for t in issues["issue"].head(5)) + ".")
    if n and df["likes"].max() > 0:
        top_c = df.loc[df["likes"].idxmax()]
        points.append(f"The most-liked comment ({top_c['likes']:,} likes) is by "
                      f"{top_c['username']}: \"{short(top_c['comment'], 160)}\"")
    return points


def add_table(doc, header, rows, widths=None):
    table = doc.add_table(rows=1, cols=len(header))
    table.style = "Light Grid Accent 1"
    for cell, h in zip(table.rows[0].cells, header):
        cell.text = h
    for row in rows:
        for cell, v in zip(table.add_row().cells, row):
            cell.text = str(v)
    if widths:
        for row in table.rows:
            for cell, w in zip(row.cells, widths):
                cell.width = Inches(w)
    return table


def write_report(post, df, issues, charts, path):
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)

    doc.add_heading(short(post["title"], 120) or f"{post['platform']} post", 0)
    meta = doc.add_paragraph()
    for label, value in (("Platform", post["platform"]), ("Posted by", post["author"]),
                         ("Posted on", post["published"]), ("Link", post["url"]),
                         ("Report made", datetime.now().strftime("%Y-%m-%d %H:%M"))):
        if value:
            meta.add_run(f"{label}: ").bold = True
            meta.add_run(f"{value}\n")

    doc.add_heading("Summary", 1)
    for point in summary_points(post, df, issues):
        doc.add_paragraph(point, style="List Bullet")

    doc.add_heading("1. Reactions", 1)
    add_table(doc, ["", "Count"], [(k, number(v)) for k, v in post["stats"].items()], [2.5, 2])
    if post["reactions"]:
        doc.add_paragraph()
        total = sum(post["reactions"].values())
        add_table(doc, ["Reaction", "People", "Share"],
                  [(k, f"{v:,}", f"{100 * v / total:.0f}%") for k, v in post["reactions"].items()],
                  [2.5, 2, 1.5])
    if charts.get("reactions"):
        doc.add_picture(charts["reactions"], width=Inches(6.3))
    if charts.get("timeline"):
        doc.add_picture(charts["timeline"], width=Inches(6.3))

    doc.add_heading("2. Comment sentiment", 1)
    if df.empty:
        doc.add_paragraph("No comments could be read for this post.")
    else:
        sent, emo = df["sentiment"].value_counts(), df["emotion"].value_counts()
        for k in ("Positive", "Neutral", "Negative"):
            n = int(sent.get(k, 0))
            doc.add_paragraph(f"{k}: {n:,} ({100 * n / len(df):.1f}%)", style="List Bullet")
        doc.add_paragraph("Emotion: " + ",  ".join(f"{k} {int(emo.get(k, 0)):,}"
                                                    for k in EMOTION_COLORS))
        doc.add_picture(charts["sentiment"], width=Inches(6.3))

        doc.add_heading("3. Most-liked comments", 1)
        top = df.nlargest(10, "likes")
        add_table(doc, ["Likes", "User", "Sentiment", "Comment"],
                  [(f"{r['likes']:,}", r["username"], r["sentiment"], str(r["comment"])[:400])
                   for _, r in top.iterrows()], [0.7, 1.3, 0.9, 3.6])

        doc.add_heading("4. Most criticized issues", 1)
        if issues.empty:
            doc.add_paragraph("No repeated complaints were found in the negative comments.")
        else:
            add_table(doc, ["Issue", "Comments", "Example comment"],
                      [(r["issue"], r["comments_mentioning"],
                        f"{r['example_user']}: {str(r['example_comment'])[:300]}")
                       for _, r in issues.iterrows()], [1.5, 0.9, 4.1])
            doc.add_picture(charts["issues"], width=Inches(6.3))

        doc.add_heading("5. Word clouds", 1)
        doc.add_paragraph("The bigger the word, the more often people used it.")
        for name in ("All comments", "Positive comments", "Negative comments"):
            if charts.get(name):
                doc.add_heading(name, 2)
                doc.add_picture(charts[name], width=Inches(6.3))

    doc.add_heading("How these numbers were made", 1)
    for note in [
        "Sentiment uses VADER, a scorer built for English social-media text. Emotions come from "
        "word and emoji lists, with VADER as a fallback for comments that use none of those "
        "words. Both understand English only, so comments in other languages mostly land in "
        "Neutral.",
        "Criticized issues are the words and two-word phrases that appear in the most negative "
        "comments.",
    ] + post["notes"]:
        p = doc.add_paragraph(note, style="List Bullet")
        for run in p.runs:
            run.font.color.rgb = RGBColor(0x52, 0x51, 0x4E)
    doc.save(path)


def write_excel(post, df, issues, path):
    summary = pd.DataFrame(
        [("Platform", post["platform"]), ("Title", post["title"]), ("Posted by", post["author"]),
         ("Posted on", post["published"]), ("Link", post["url"])]
        + [(k, v) for k, v in post["stats"].items()]
        + [(f"Reaction: {k}", v) for k, v in post["reactions"].items()]
        + [("Comments analysed", len(df))],
        columns=["metric", "value"])
    cols = ["username", "time", "comment", "likes", "is_reply", "score", "sentiment", "emotion"]
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        summary.to_excel(xl, sheet_name="Summary", index=False)
        df[[c for c in cols if c in df.columns]].to_excel(xl, sheet_name="Comments", index=False)
        issues.to_excel(xl, sheet_name="Criticized issues", index=False)
        xl.sheets["Summary"].column_dimensions["A"].width = 24
        xl.sheets["Summary"].column_dimensions["B"].width = 60
        xl.sheets["Comments"].column_dimensions["C"].width = 80
        xl.sheets["Criticized issues"].column_dimensions["D"].width = 80


# --------------------------------------------------------------------------
# the whole job: link in, report out
# --------------------------------------------------------------------------
def analyse(url, opts, log):
    platform = detect_platform(url)
    if not platform:
        raise Problem("Paste a link to a YouTube, Facebook, Instagram or TikTok post.")
    log(f"{platform} link - fetching the post...")
    post, rows = FETCHERS[platform](url.strip(), opts, log)

    df = pd.DataFrame(rows, columns=COLUMNS)
    df = df[df["comment"].fillna("").str.strip() != ""].drop_duplicates()
    df["comment"] = df["comment"].fillna("").astype(str)
    df["likes"] = pd.to_numeric(df["likes"], errors="coerce").fillna(0).astype(int)
    log(f"Analysing {len(df):,} comments...")
    df = score_comments(df) if len(df) else df.assign(score=0.0, sentiment="", emotion="")

    negative = df[(df["sentiment"] == "Negative") | df["emotion"].isin(["Angry", "Disagree"])]
    positive = df[df["sentiment"] == "Positive"]
    issues = criticized_issues(negative)

    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", short(post["title"] or "post", 40)).strip(" .")
    folder = os.path.join(opts.get("out") or reports_folder(),
                          f"{datetime.now():%Y-%m-%d %H%M} {platform} - {title or 'post'}")
    os.makedirs(folder, exist_ok=True)

    log("Making charts and the Word report...")
    font = setup_fonts()
    with tempfile.TemporaryDirectory() as tmp:
        p = lambda name: os.path.join(tmp, name)
        charts = {}
        if post["reactions"]:
            charts["reactions"] = reactions_chart(post["reactions"], p("reactions.png"))
        if len(df):
            charts["timeline"] = timeline_chart(df, post["published"], p("timeline.png"))
            charts["sentiment"] = sentiment_chart(df, p("sentiment.png"))
            if not issues.empty:
                charts["issues"] = issues_chart(issues, len(negative), p("issues.png"))
            charts["All comments"] = word_cloud(df["comment"], NEUTRALS, p("cloud_all.png"), font)
            charts["Positive comments"] = word_cloud(positive["comment"], BLUES, p("cloud_pos.png"), font)
            charts["Negative comments"] = word_cloud(negative["comment"], REDS, p("cloud_neg.png"), font)
        report = os.path.join(folder, "Report.docx")
        write_report(post, df, issues, charts, report)
    write_excel(post, df, issues, os.path.join(folder, "Comments.xlsx"))

    for point in summary_points(post, df, issues)[:3]:
        log("  " + point)
    log(f"Done! Report saved in: {folder}")
    return report


# ==========================================================================
# THE APP WINDOW
# ==========================================================================
LIMITS = {"500": 500, "2,000": 2000, "10,000": 10000, "All": None}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("760x600")
        self.minsize(640, 520)
        if getattr(sys, "frozen", False):
            self.iconbitmap(default=sys.executable)   # the .exe's own icon
        self.settings = load_settings()
        self.messages, self.report = queue.Queue(), None

        ttk.Style(self).configure("TButton", padding=(10, 4))
        body = ttk.Frame(self, padding=18)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text=APP_NAME, font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(body, foreground=MUTED, font=("Segoe UI", 10),
                  text="Paste a link to a YouTube, Facebook, Instagram or TikTok post, "
                       "then click Analyse.").pack(anchor="w", pady=(2, 12))

        row = ttk.Frame(body)
        row.pack(fill="x")
        self.url = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.url, font=("Segoe UI", 11))
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        entry.bind("<Return>", lambda e: self.start())
        entry.focus_set()
        ttk.Button(row, text="Paste", command=self.paste).pack(side="left", padx=(8, 0))
        self.go = ttk.Button(row, text="Analyse", command=self.start)
        self.go.pack(side="left", padx=(8, 0))
        self.stop_button = ttk.Button(row, text="Stop", state="disabled", command=self.stop)
        self.stop_button.pack(side="left", padx=(8, 0))
        self.stop_event = None
        self.detected = ttk.Label(body, text=" ", foreground=MUTED, font=("Segoe UI", 9))
        self.detected.pack(anchor="w", pady=(4, 0))
        self.url.trace_add("write", lambda *a: self.show_platform())

        opts = ttk.LabelFrame(body, text=" Options ", padding=12)
        opts.pack(fill="x", pady=12)
        ttk.Label(opts, text="Comments to read:").grid(row=0, column=0, sticky="w")
        self.limit = tk.StringVar(value=self.settings.get("limit", "2,000"))
        ttk.Combobox(opts, textvariable=self.limit, values=list(LIMITS), state="readonly",
                     width=8).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(opts, foreground=MUTED, text="(\"All\" can take a long time on big YouTube "
                  "videos)").grid(row=0, column=2, sticky="w")
        self.firefox = tk.BooleanVar(value=self.settings.get("firefox", False))
        ttk.Checkbutton(opts, variable=self.firefox,
                        text="Use my Firefox login  (needed for Facebook, Instagram comments "
                             "and TikTok)").grid(row=1, column=0, columnspan=3, sticky="w",
                                                 pady=(10, 0))
        ttk.Label(opts, text="YouTube API key (optional, makes big YouTube videos much "
                  "faster):").grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 2))
        self.api_key = tk.StringVar(value=self.settings.get("youtube_api_key", ""))
        ttk.Entry(opts, textvariable=self.api_key, show="•", width=48).grid(
            row=3, column=0, columnspan=3, sticky="w")

        self.bar = ttk.Progressbar(body, mode="determinate")   # empty until a run starts
        self.bar.pack(fill="x")
        self.log_box = tk.Text(body, height=10, wrap="word", relief="flat", padx=8, pady=6,
                               font=("Consolas", 9), background="#f4f4f1", state="disabled")
        self.log_box.pack(fill="both", expand=True, pady=(8, 8))

        bottom = ttk.Frame(body)
        bottom.pack(fill="x")
        self.open_report = ttk.Button(bottom, text="Open report", state="disabled",
                                      command=lambda: os.startfile(self.report))
        self.open_report.pack(side="left")
        ttk.Button(bottom, text="Open reports folder",
                   command=lambda: os.startfile(reports_folder())).pack(side="left", padx=8)
        ttk.Button(bottom, text="Help", command=self.help).pack(side="right")

        self.write("Ready. Your reports will be saved in Documents\\Comment Analyzer Reports.")
        self.after(100, self.poll)

    def paste(self):
        try:
            self.url.set(self.clipboard_get().strip())
        except tk.TclError:
            pass

    def show_platform(self):
        platform = detect_platform(self.url.get())
        needs = {"Facebook": "  - needs \"Use my Firefox login\"",
                 "TikTok": "  - needs \"Use my Firefox login\"",
                 "Instagram": "  - comments need \"Use my Firefox login\""}.get(platform, "")
        self.detected.config(text=f"{platform} link{needs}" if platform else " ")

    def write(self, text):
        self.log_box.config(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def start(self):
        url = self.url.get().strip()
        if not detect_platform(url):
            messagebox.showwarning(APP_NAME, "Paste a link to a YouTube, Facebook, Instagram "
                                             "or TikTok post first.")
            return
        self.settings.update(limit=self.limit.get(), firefox=self.firefox.get(),
                             youtube_api_key=self.api_key.get().strip())
        save_settings(self.settings)
        self.stop_event = threading.Event()
        opts = {"limit": LIMITS[self.limit.get()], "firefox": self.firefox.get(),
                "api_key": self.api_key.get().strip(), "stop": self.stop_event}

        self.go.state(["disabled"])
        self.stop_button.state(["!disabled"])
        self.open_report.state(["disabled"])
        self.bar.config(mode="indeterminate")
        self.bar.start(12)
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")
        threading.Thread(target=self.work, args=(url, opts), daemon=True).start()

    def stop(self):
        self.stop_event.set()
        self.stop_button.state(["disabled"])
        self.write("Stopping - the report will use the comments read so far...")

    def work(self, url, opts):
        log = lambda m: self.messages.put(("log", m))
        try:
            self.messages.put(("done", analyse(url, opts, log)))
        except Problem as e:
            self.messages.put(("problem", str(e)))
        except KeyboardInterrupt:
            self.messages.put(("problem", "Stopped before any comments were read."))
        except Exception:
            details = traceback.format_exc()
            with open(os.path.join(app_folder(), "error.log"), "w", encoding="utf-8") as f:
                f.write(details)
            self.messages.put(("problem", "Something went wrong. Check your internet connection "
                                          "and try again.\n\nDetails were saved to "
                                          f"{os.path.join(app_folder(), 'error.log')}\n\n"
                                          + details.strip().splitlines()[-1]))

    def poll(self):
        while not self.messages.empty():
            kind, value = self.messages.get()
            if kind == "log":
                self.write(value)
                continue
            self.bar.stop()
            self.bar.config(mode="determinate", value=0)
            self.go.state(["!disabled"])
            self.stop_button.state(["disabled"])
            if kind == "done":
                self.report = value
                self.open_report.state(["!disabled"])
                os.startfile(value)
            else:
                self.write("Stopped: " + value.splitlines()[0])
                messagebox.showerror(APP_NAME, value)
        self.after(100, self.poll)

    def help(self):
        messagebox.showinfo(APP_NAME, (
            "1. Copy the link of a post (Share > Copy link).\n"
            "2. Click Paste, then Analyse.\n"
            "3. The Word report opens by itself when it's ready.\n\n"
            "YouTube works straight away.\n\n"
            "Facebook, Instagram comments and TikTok only show their data to signed-in "
            "people. Install Firefox, sign in to those sites in Firefox, then tick "
            "\"Use my Firefox login\". (Chrome and Edge don't let other apps use their "
            "logins.)\n\n"
            "Reports are saved in Documents\\Comment Analyzer Reports, each with a Word "
            "report and an Excel file of every comment."))


# --------------------------------------------------------------------------
# build the Windows installer:  python comment_analyzer.py --build
# --------------------------------------------------------------------------
APP_VERSION = "1.0"
INSTALLER = r"""
[Setup]
AppName={name}
AppVersion={version}
DefaultDirName={{localappdata}}\Programs\{name}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={out}
OutputBaseFilename={name} Setup
SetupIconFile={icon}
UninstallDisplayIcon={{app}}\{name}.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Put a shortcut on the desktop"

[Files]
Source: "{dist}\*"; DestDir: "{{app}}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{{autoprograms}}\{name}"; Filename: "{{app}}\{name}.exe"
Name: "{{autodesktop}}\{name}"; Filename: "{{app}}\{name}.exe"; Tasks: desktopicon

[Run]
Filename: "{{app}}\{name}.exe"; Description: "Open {name} now"; Flags: nowait postinstall skipifsilent
"""


def draw_icon(path):
    """A speech bubble with a blue / gray / red bar chart."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([8, 8, 248, 248], radius=52, fill=BLUE)
    d.rounded_rectangle([44, 52, 212, 168], radius=28, fill="white")
    d.polygon([(78, 160), (70, 206), (118, 164)], fill="white")
    for x, h, c in [(74, 70, BLUE), (116, 46, GRAY), (158, 28, RED)]:
        d.rounded_rectangle([x, 148 - h, x + 26, 148], radius=5, fill=c)
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def build():
    """Package this file as 'Comment Analyzer Setup.exe' next to it.
    Needs:  pip install pyinstaller   and Inno Setup 6 (https://jrsoftware.org/isdl.php)"""
    import shutil
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    work = os.path.join(tempfile.gettempdir(), "comment_analyzer_build")
    os.makedirs(work, exist_ok=True)
    icon = os.path.join(work, "icon.ico")
    draw_icon(icon)

    # a folder app, not --onefile: one-file apps unpack themselves on every launch,
    # which takes about a minute with antivirus scanning
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed",
                    "--name", APP_NAME, "--icon", icon, "--collect-data", "vaderSentiment",
                    "--collect-data", "wordcloud", "--collect-data", "docx",
                    "--distpath", os.path.join(work, "dist"), "--workpath", os.path.join(work, "build"),
                    "--specpath", work, os.path.abspath(__file__)], check=True)

    iss = os.path.join(work, "installer.iss")
    with open(iss, "w", encoding="utf-8") as f:
        f.write(INSTALLER.format(name=APP_NAME, version=APP_VERSION, out=here, icon=icon,
                                 dist=os.path.join(work, "dist", APP_NAME)))
    iscc = shutil.which("iscc") or next(
        (p for p in (os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
                     r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                     r"C:\Program Files\Inno Setup 6\ISCC.exe") if os.path.exists(p)), None)
    if not iscc:
        raise SystemExit("Inno Setup 6 isn't installed - get it from https://jrsoftware.org/isdl.php")
    subprocess.run([iscc, "/Q", iss], check=True)
    print(f"Installer ready: {os.path.join(here, APP_NAME + ' Setup.exe')}")


# --------------------------------------------------------------------------
# run without the window:  python comment_analyzer.py "<link>" --firefox --limit 500
# --------------------------------------------------------------------------
def main():
    if len(sys.argv) == 1:
        App().mainloop()
        return
    if sys.argv[1] == "--build":
        build()
        return
    parser = argparse.ArgumentParser(description="Analyse a post's reactions and comments.")
    parser.add_argument("url")
    parser.add_argument("--firefox", action="store_true", help="use the Firefox login")
    parser.add_argument("--limit", type=int, default=2000, help="comments to read (0 = all)")
    parser.add_argument("--api-key", default=os.environ.get("YOUTUBE_API_KEY", ""))
    parser.add_argument("--out", help="folder for the report (default: Documents)")
    args = parser.parse_args()
    try:
        analyse(args.url, {"limit": args.limit or None, "firefox": args.firefox,
                           "api_key": args.api_key, "out": args.out},
                lambda m: print(m, flush=True))
    except Problem as e:
        raise SystemExit(f"\n{e}")


if __name__ == "__main__":
    main()
