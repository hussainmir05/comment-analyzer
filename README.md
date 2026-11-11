# Comment Analyzer

A Windows desktop app that turns a social media link into a report. Paste a link to a **YouTube, Facebook, Instagram or TikTok** post, and the app reads the post's reactions and comments, scores how people feel, and saves a **Word report** with charts and an **Excel file** containing every comment.

The whole app is one Python file: [`comment_analyzer.py`](comment_analyzer.py).

## Features

- **Four platforms from one box:** the app works out which site a link is from.
- **Reaction stats:** views, likes, shares and saves. It also reads Facebook's Like / Love / Care / Haha / Wow / Sad / Angry breakdown and gives an estimated YouTube dislike count.
- **Sentiment analysis:** each comment is scored Positive, Neutral or Negative with [VADER](https://github.com/cjhutto/vaderSentiment).
- **Emotion detection:** comments are sorted into Agree, Disagree, Happy, Sad, Angry or Neutral using word, phrase and emoji lists. These handle negation, so "not good" is not counted as Happy.
- **Most criticized issues:** the words and two-word phrases that come up most often in negative comments, each shown with the most-liked example comment.
- **Charts:** reaction breakdown, comments over time, sentiment and emotion split, top complaints, and word clouds for all, positive and negative comments. The colours are safe for colour-blind readers.
- **Plain-language summary** at the top of the report, for example: *"The overall mood of the comments is mostly positive: 62% positive, 25% neutral and 13% negative."*
- **Korean text support:** Hangul usernames and comments display correctly in charts and word clouds.
- **Stop at any time:** press Stop and the report is built from the comments read so far.
- **Command-line mode** for running without the window or from a script.

## What each platform provides

| Platform  | Data collected                                              | Login needed?                        |
|-----------|-------------------------------------------------------------|--------------------------------------|
| YouTube   | Views, likes, dislikes (estimate), comments and replies     | No                                   |
| Facebook  | All 7 reaction types, shares, views, top comments           | Yes (Firefox)                        |
| Instagram | Likes, views, comments                                      | Only for comments (Firefox)          |
| TikTok    | Views, likes, shares, saves, comments                       | Yes (Firefox)                        |

### Why Firefox?

Facebook, Instagram and TikTok only show reactions and comments to visitors who are signed in. Rather than asking for your password, the app reuses the login you already have in **Firefox**: sign in to the site in Firefox once, then tick **"Use my Firefox login"**. Chrome and Edge encrypt their logins so that other apps can't read them, which is why only Firefox is supported.

## Installation

### Option 1: Windows installer (no Python needed)

Download **`Comment Analyzer Setup.exe`** from the [Releases](../../releases/latest) page and run it. No admin rights are needed. The installer adds the app to the Start menu and can put a shortcut on the desktop.

### Option 2: Run from source

You need Python 3.9 or newer.

```bash
git clone https://github.com/<your-username>/<repo-name>.git
cd <repo-name>
pip install yt-dlp pandas openpyxl python-docx requests matplotlib vaderSentiment wordcloud
python comment_analyzer.py
```

## Usage

### Desktop app

1. Copy a post's link (**Share → Copy link**).
2. Click **Paste**, then **Analyse**.
3. When the report is ready, it opens in Word.

Options:

- **Comments to read:** 500, 2,000, 10,000 or All. "All" can take a long time on large YouTube videos.
- **Use my Firefox login:** required for Facebook and TikTok, and for Instagram comments.
- **YouTube API key** (optional): makes reading comments on large YouTube videos much faster. See [Getting a YouTube API key](#getting-a-youtube-api-key).

### Command line

```bash
python comment_analyzer.py "https://www.youtube.com/watch?v=VIDEO_ID"
python comment_analyzer.py "https://www.facebook.com/..." --firefox
python comment_analyzer.py "<link>" --limit 500 --out "C:\Reports"
```

| Flag          | Meaning                                                              |
|---------------|----------------------------------------------------------------------|
| `--firefox`   | Use the Firefox login                                                |
| `--limit N`   | Maximum number of comments to read (default 2000, `0` reads them all) |
| `--api-key K` | YouTube Data API key (can also be set as the `YOUTUBE_API_KEY` environment variable) |
| `--out DIR`   | Folder to save the report in (default: Documents)                    |

## Output

Each run creates its own folder in `Documents\Comment Analyzer Reports\`, named like `2026-11-11 1430 YouTube - Video title`. The folder contains two files.

**`Report.docx`** has these sections:

1. Summary of the key findings
2. Reactions: stats table, reaction breakdown, comments over time
3. Comment sentiment and emotion
4. The 10 most-liked comments
5. Most criticized issues
6. Word clouds
7. How these numbers were made (method and data caveats)

**`Comments.xlsx`** has three sheets:

- **Summary:** the post's details and stats
- **Comments:** every comment, with its username, time, likes, whether it is a reply, sentiment score, sentiment and emotion
- **Criticized issues:** the top complaint terms with example comments

## How it works

```
link ─► detect platform ─► fetch post + comments ─► score sentiment & emotion ─► charts ─► Word + Excel
```

- **YouTube** comments are read through the YouTube Data API v3 if you give an API key, and through [yt-dlp](https://github.com/yt-dlp/yt-dlp) otherwise. Dislikes come from the [Return YouTube Dislike](https://returnyoutubedislike.com/) API.
- **TikTok** and **Instagram** comments come from the sites' own web APIs, using the Firefox login. Post details come from yt-dlp.
- **Facebook** data is taken from the JSON embedded in the post page.
- **Sentiment:** a VADER compound score of 0.05 or more counts as Positive, and -0.05 or less counts as Negative.
- **Emotion:** each comment is matched against the word, phrase and emoji lists in `EMOTIONS`, which you can edit at the top of the script to tune the results. If a comment matches none of them, a very strong VADER score is used instead.

### Getting a YouTube API key

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and create a project.
2. Enable **YouTube Data API v3**.
3. Under **Credentials**, create an **API key** and paste it into the app.

The free quota is 10,000 units per day, which covers roughly a million comments.

## Limitations

- **English only:** sentiment and emotion scoring only understands English, so comments in other languages mostly end up Neutral.
- **Facebook** only puts its first "most relevant" comments on the post page, so the app analyses those rather than every comment.
- **YouTube dislikes** are an estimate. YouTube stopped showing public dislike counts in 2021.
- **YouTube comment times are approximate** without an API key, because yt-dlp only sees times like "3 months ago".
- **The sites change often.** If a platform stops working, update yt-dlp (`pip install -U yt-dlp`) and rebuild.

## Building the installer

```bash
pip install pyinstaller
# also install Inno Setup 6: https://jrsoftware.org/isdl.php
python comment_analyzer.py --build
```

This builds the app with PyInstaller, draws the app icon, and packages everything into **`Comment Analyzer Setup.exe`** next to the script.

## Privacy

- Everything runs on your own computer. Comments are not sent to any server other than the platform they come from (and the Return YouTube Dislike API for dislikes).
- Settings, including your YouTube API key, are stored in plain text at `%APPDATA%\Comment Analyzer\settings.json`.
- The Firefox login is only read from your local browser profile and is never saved or shared.

## Disclaimer

This tool is for personal research and analysis. Only analyse posts you are allowed to view, and respect each platform's terms of service and the privacy of the people who commented.

## Built with

[yt-dlp](https://github.com/yt-dlp/yt-dlp) · [pandas](https://pandas.pydata.org/) · [VADER Sentiment](https://github.com/cjhutto/vaderSentiment) · [matplotlib](https://matplotlib.org/) · [wordcloud](https://github.com/amueller/word_cloud) · [python-docx](https://python-docx.readthedocs.io/) · [openpyxl](https://openpyxl.readthedocs.io/) · Tkinter
