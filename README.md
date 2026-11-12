# Comment Analyzer

A Windows desktop app: paste a link to a **YouTube, Facebook, Instagram or TikTok** post and get an honest analysis of its comments, covering sentiment, topics and how the conversation grows over time.

> **Status: rewrite in progress.** This is version 2, rebuilt with PySide6. All phases are built.
>
> - **Tested with real data:** YouTube collection and the analysis.
> - **Built and tested offline only:** the Facebook, Instagram and TikTok collectors, which still need a test with real accounts, and Claude mode, which was tested with a stand-in client and no paid calls.
>
> [`docs/PLAN.md`](docs/PLAN.md) shows what's left. The previous one-file app is in [`legacy/`](legacy/README.md). Installers are published on the [Releases](../../releases) page, never in the repository.

## What works now

- **Analysis.** After collecting, the app reads each comment's language and mood, groups comments into topics, flags likely spam, and lists what negative comments mention most.
  - **Sentiment:** the model is XLM-T (Cardiff NLP), a one-time 282 MB download that runs offline afterwards. Its results in languages it wasn't trained on are marked "unsure".
  - **Topics:** comments that say much the same thing, in any language, are grouped using a multilingual sentence model (a one-time 135 MB download).
  - **Claude mode (optional, paid):** **Ask Claude...** gets a second opinion from Claude, using your own Anthropic API key. You see the estimated cost before anything is sent.
- **Post maker.** A 1080×1350 card and four caption wordings for a post's findings, without commenters' names.
  - Save the kit to post it yourself.
  - Or, with your own Meta developer app set up in Settings, publish to your Facebook Page or its Instagram account. Each post asks first, and the same caption never goes out twice.
- **Facebook, Instagram and TikTok.** Add your accounts on the **Accounts** page and log in, in the app's own browser. Each account has its own browser profile, so several can be logged in at once.
  - Paste a post link and click **Analyse**. The post opens in a visible window, and the app reads the comments the page loads, at human pace.
  - It stops at login walls and checks, and hands the window to you.
  - Daily limits per account are in Settings.
- **Tracking.** Tick **Track this post** on any YouTube post.
  - The app re-checks tracked posts on an interval you set, collects new comments, and charts growth.
  - It warns you, from the tray, when comments suddenly spike or turn negative.
- **Dashboard and exports.**
  - Charts for one post, or a comparison of several.
  - **Export report** writes a Word report, an Excel workbook, a CSV and chart images to `Documents\Comment Analyzer Reports`, without commenter names.
- **YouTube collection.** Paste a video link and click **Analyse**. The app reads the comments and every reply through the official YouTube Data API.
  - The results show coverage (comments collected out of those YouTube reports), the sort order and when they were collected.
  - A table lets you sort and filter the comments.
  - Stop keeps what was read.
  - The quota the app has used today is shown on the page.
- **Link detection.** The app recognises posts, videos, Shorts, reels and share links from all four platforms, and says why X (Twitter) links aren't supported.
- **Settings.** Your YouTube API key is kept in Windows Credential Manager, not in a file. The previous version's plain-text key can be moved there in one click. You can set collection limits and privacy options.
- **Local database.** It records the posts you've added.
- **Background jobs.** They show progress and have a Stop button. Opening the app a second time just brings the first window forward.

## Getting a YouTube API key

1. Sign in to [Google Cloud Console](https://console.cloud.google.com/), preferably with a personal Google account. School and work accounts can be blocked from YouTube services.
2. Create a project.
3. Go to **APIs & Services → Library → YouTube Data API v3** and click **Enable**.
4. Go to **APIs & Services → Credentials → Create credentials → API key**. Don't tie the key to a service account. You can restrict it to YouTube Data API v3, but leave application restrictions at None.
5. In the app, open **Settings**, paste the key and click **Save key**. The app checks it with YouTube straight away.

The free quota is 10,000 units a day. Each request (up to 100 comments) costs 1 unit.

## Run from source

You need Windows 10 or 11, [Python 3.14](https://www.python.org/downloads/windows/) from python.org, and [uv](https://docs.astral.sh/uv/).

```powershell
git clone https://github.com/hussainmir05/comment-analyzer.git
cd comment-analyzer
uv sync                      # creates .venv with the exact versions in uv.lock
.venv\Scripts\Activate.ps1
python -m comment_analyzer
```

Without uv: `py -3.14 -m venv .venv`, activate it, then `pip install -e . --group dev`.

## Develop

```powershell
pytest                 # tests (no network; a throwaway data folder)
ruff check .           # lint
ruff format .          # format
mypy                   # type check
scripts\build_exe.ps1  # one-folder build in dist\, then a smoke test of the built app
scripts\build_exe.ps1 -Installer  # also dist\Comment-Analyzer-<version>-Setup.exe (Inno Setup 6)
```

If PowerShell refuses to run scripts, allow local scripts once with `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## Command line

```powershell
python -m comment_analyzer analyse https://youtu.be/VIDEO_ID --limit 500 --out C:\Reports
```

It collects the video's comments, analyses them, prints the summary, and writes the Word, Excel and CSV report. It uses the app's data folder and saved API key, or the `YOUTUBE_API_KEY` environment variable. `--newest` reads newest first instead of top comments.

Facebook, Instagram and TikTok are read in the app's own logged-in browser window, so they work in the app only.

To check the sentiment model against your own labels: `python -m comment_analyzer.analysis.evaluate sample`, label the CSV, then `... evaluate score <file.csv>`. Add `--claude` to compare with Claude; it shows the cost and asks first.

## Where your data lives

- **Data folder:** everything is in `%LOCALAPPDATA%\CommentAnalyzer`:
  - `data.db`: the database;
  - `settings.ini`: your settings;
  - `logs\`: log files, with keys and tokens masked;
  - `backups\`: a copy of the database from before each upgrade;
  - `models\`: the downloaded analysis models;
  - `profiles\`: one browser profile per Facebook, Instagram or TikTok account, holding that account's login cookies. **Log out and delete** on the Accounts page removes it;
  - `selectors\` (optional): your own copy of a site's selectors file, which replaces the built-in one if a site changes;
  - `debug\`: only when debug capture is on in Settings.
- **API keys:** they're in Windows Credential Manager (Windows Credentials → Generic Credentials → `CommentAnalyzer:youtube_api_key` and `CommentAnalyzer:anthropic_api_key`).

## How each platform is handled

- **YouTube:** the official YouTube Data API v3, with your own free API key. The app never asks for your YouTube login.
- **Facebook, Instagram, TikTok:** no official API covers other people's posts. The app reads only what your own logged-in in-app browser loads, visibly and at human pace (a random 2–5 second pause between steps). It clicks only buttons such as "View more comments", and never likes, comments or posts.
  - It stops at login walls and captchas and hands the browser back to you.
  - These sites' terms restrict automated collection, so each one is opt-in, and the risk to the account you use is yours.
- **X:** not supported. Its API charges per post read and its rules ban automating x.com.

## Privacy

- **Commenters:** names are never stored. Commenter IDs are kept only as keyed hashes.
- **Comment text:** deleted after 30 days by default. Counts and results are kept.
- **Exports, images and captions:** never include commenters' names or photos.
- **Network:** nothing leaves your computer except requests to the platforms themselves. The optional Claude mode is the one exception: it sends comment text without usernames, and only after you confirm the estimated cost.
