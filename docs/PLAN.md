# Plan

The working plan for the rewrite (version 2.0). [CLAUDE.md](../CLAUDE.md) has the rules. This file has the phases, how each one is tested, and the decisions behind them.

## Status (2026-11-12)

| Phase | State |
|---|---|
| 1. App skeleton | Done |
| 2. YouTube collector | Done, and tested by you with a real video |
| 3. Analysis engine | Step 1 done: language, sentiment (XLM-T), spam, issues, summary, evaluation script. Step 2 done: embedding topics, optional Claude mode (tested with a fake client; no paid call made) |
| 4. Dashboard, table, compare, exports | Done |
| 5. Accounts, in-app login | Built and tested offline: profiles, login window, cookie-based status, `capture.js` and the bridge in a real page. Still needs you to log in to real accounts |
| 6. Facebook, Instagram, TikTok collectors | Built and tested offline: parsers ported from the old app (tested on made-up fixtures in the sites' shapes), the step driver, the "load more" script in a real page, daily limits, and the fixture sanitiser. Still needs real pages: selectors and parsers may need adjusting |
| 7. Tracking, tray, spike alerts | Done. YouTube hourly by default; Facebook, Instagram and TikTok at most every 6 hours, capped at 300 comments |
| 8. Post maker and publishing | Cards, captions and the manual post kit are done. Publishing to a Facebook Page and Instagram through the Graph API is built and tested against a mock server. A real post needs your Meta developer app and Page token |
| 9. Build, README, CI, release | Installer, notices and workflows are done. Still yours: pick a licence, log in to GitHub on this computer (`gh auth login`), push, publish the v1.0 and v2.0.0 releases, and decide on code signing (a paid certificate). Further Qt trimming is optional |

## Decisions (2026-11-12)

| Topic | Decision | Why |
|---|---|---|
| Python | 3.14 from python.org | The newest version every dependency supports, and still getting Windows installer updates. The 3.12 line's last Windows installer was 3.12.10 (April 2025). |
| GUI | PySide6 6.12 with QtWebEngine (about 245 MB of wheels, approved) | Needed for logins, browser collection and charts |
| Model runtime | ONNX Runtime + tokenizers in the app. torch, transformers and optimum only for exporting models and running the evaluation. | Keeps torch (hundreds of MB) out of the installer |
| Topics | Multilingual embeddings (paraphrase-multilingual-MiniLM-L12-v2, ONNX int8, 135 MB download) → community clustering in numpy → c-TF-IDF keywords. Posts under 30 comments get no topics; the old app's "criticized issues" phrases cover them. | BERTopic pulls in torch and numba. HDBSCAN needs scikit-learn and SciPy (about 150 MB more in the app) for no clear gain on short comments. |
| Language detection | fast-langdetect (0.8 MB) | lingua's Windows wheel is 162 MB. Phase 3's evaluation checks accuracy on short comments. |
| HTTP | httpx2 | httpx has had no release since December 2024, and the anthropic SDK uses httpx2 |
| Dependencies | uv with `uv.lock` | Exact, reproducible installs |
| Collectors | QObjects that emit signals | QtWebEngine lives on the GUI thread, so a blocking iterator can't work |
| YouTube history | A rolling 30 days | The API policy requires deleting or refreshing API data after 30 days, and a past snapshot can't be refreshed |
| User agent | QtWebEngine's default. If a site rejects it, stop and tell the user. | No spoofing |
| Settings | An INI file in the data folder | Easy to inspect and reset |
| Versions | The rewrite is 2.0, since the old app was 1.0 | The new installer can then upgrade the old one cleanly |
| Releases | 2.0 = Phases 1–6 + 9. 2.1 adds tracking (Phase 7), 2.2 the post maker (Phase 8). | Replaces the old app on all four platforms sooner |
| Installers | GitHub Releases only | The old 49 MB installer stays in git history but leaves the tree |
| Claude mode | Claude Opus 5.5 by default; Sonnet 5.5 and Haiku 5.5 in Settings. Requests are sent one at a time, not through the Batches API. | Best quality by default, and cheaper models are one click away. Batches cost half as much but can take up to 24 hours, and the app would have to follow a batch across restarts. |
| Legacy extras | Kept: the Word and Excel exports (without names), the command line (`python -m comment_analyzer analyse <link>`), and the dislike estimate as an opt-in, labelled estimate. Dropped: the English-only emotion lists and the word clouds. | Topics and keywords say more than word clouds do |
| Accounts | Several per platform. Warn that Facebook allows one personal account per person. | |
| Instagram publishing | Through the Graph API: the card is uploaded to the linked Page as an unpublished photo, Instagram fetches it from there, and the helper photo is deleted | Instagram's API takes images only from a public URL, and this needs no paid host |
| Comment languages | Decided from data: Phase 3 measures the language mix of the comments you collect | Picks the sentiment model and the evaluation set |

## Phases

Each phase ends with `ruff check .`, `ruff format .`, `mypy`, `pytest`, `scripts\build_exe.ps1`, README and CHANGELOG updates, and a proposed commit message.

### Phase 1: App skeleton (done)

**Delivers**
- Main window with Analyse, Dashboard, Accounts, Tracking, Post maker and Settings pages. The later pages are placeholders.
- Link detection with about 80 test cases.
- Settings in `settings.ini`, and the API key in Credential Manager.
- One-time offer to move the old app's plain-text key.
- SQLite database with migrations, backups, the WAL journal and foreign keys.
- Masked, rotating logs and crash capture.
- Background workers with progress and Stop.
- A single running copy.
- `scripts\build_exe.ps1` with a smoke test.

**You test**

Run `pytest`, `ruff check .` and `mypy`, then `python -m comment_analyzer` and go through these steps:

1. **Detection.** Paste each of these links. The line under the box should say:

   | Link | Expected |
   |---|---|
   | `https://youtu.be/dQw4w9WgXcQ?si=abc` | YouTube video dQw4w9WgXcQ |
   | `https://www.youtube.com/shorts/aBcDeFgHiJk` | YouTube Short aBcDeFgHiJk |
   | `https://www.facebook.com/SomePage/posts/pfbid02abcDEF123xyz` | Facebook post pfbid02abcDEF123xyz |
   | `https://fb.watch/abcDEF123/` | Facebook share link… (Phase 6) |
   | `https://www.instagram.com/reel/C1a2B3c4D5e/?igsh=abc` | Instagram reel C1a2B3c4D5e |
   | `https://vm.tiktok.com/ZMabc123/` | TikTok share link… (Phase 6) |
   | `https://www.tiktok.com/@someone/video/7301234567890123456` | TikTok video 7301234567890123456 |
   | `https://x.com/someone/status/1790000000000000000` | X (Twitter) isn't supported… |
   | `https://bit.ly/3abcDEF` | This is a shortened link… |
   | `https://www.youtube.com/@SomeChannel` | This YouTube link isn't a single video… |

   **Analyse** should be enabled only for links that point at a post.
2. **Analyse and Stop.** Phase 1 tested these with a demo job; Phase 2 replaced it with real collection, so its test steps cover them now.
3. **API key.**
   1. In **Settings**, save your YouTube key.
   2. Open Credential Manager (Windows Credentials → Generic Credentials) and check `CommentAnalyzer:youtube_api_key` is there.
   3. Check the key is not in `%LOCALAPPDATA%\CommentAnalyzer\settings.ini` and not in `logs\app.log`.
4. **Old key.** If the old app saved a key, a one-time question offers to move it. Afterwards, `%APPDATA%\Comment Analyzer\settings.json` should have `"youtube_api_key": ""`.
5. **Single copy.** With the app open, run `python -m comment_analyzer https://youtu.be/dQw4w9WgXcQ` in a second terminal. The open window should come forward with the link in the box.
6. **Built app.** Run `scripts\build_exe.ps1`, then open `dist\Comment Analyzer\Comment Analyzer.exe`. Its Dashboard page should show the built-in browser text.

### Phase 2: YouTube collector (done)

**Delivers**
- **Collectors and storage.** Collectors are QObjects that only emit records (`platforms/base.py`). `CollectionSession` stores them on the GUI thread, so the browser collectors in Phase 6 plug into the same path.
- **YouTube API calls:**
  - `videos.list` and `commentThreads.list`;
  - `comments.list` for threads with more replies than the up to 5 that `commentThreads` includes;
  - the sort order ("Top comments first" or "Newest first") saved with each run.
- **Limits and Stop.** The per-post limit comes from Settings. Stop, the limit and an exhausted quota all keep what was read.
- **Quota.** Every request is counted on the Pacific-time day, and the Analyse page shows today's total.
- **Errors.** Each one becomes a plain message saying what to fix, including the key and project problems. Busy answers are retried with backoff and Retry-After. The key goes in the `X-Goog-Api-Key` header, never in a URL.
- **Commenter IDs** are stored as HMAC-SHA256 hashes, with a per-install key in Credential Manager.
- **Retention.** YouTube data older than 30 days and comment text past the Settings limit are deleted at startup and every 6 hours. Runs left open by a crash are closed at startup.
- **Analyse page:** results with coverage, sort order and collection time, and a sortable, filterable comments table.
- **Settings page:** saving a key tests it with YouTube (1 unit), and a **Test key** button repeats the check.

**Before testing: a key YouTube accepts**

Your current key gets `403 Permission denied on the service` (`ACCESS_DENIED`) from Google, whether it's sent in a header or in the URL. Make a new one:
1. Sign in to [Google Cloud Console](https://console.cloud.google.com/) with a personal Google account. School and work accounts can be blocked from YouTube services.
2. Create a project.
3. Go to **APIs & Services → Library → YouTube Data API v3** and click **Enable**.
4. Go to **APIs & Services → Credentials → Create credentials → API key**. If it offers to tie the key to a service account, don't. You can restrict the key to YouTube Data API v3, but leave application restrictions at None.
5. In the app, open **Settings**, paste the key and click **Save key**. It should say "YouTube accepted the key".

**You test**
- `pytest`: everything runs offline against the made-up responses in `tests/fixtures/youtube/`.
- With your key, collect:
  - a small video (all comments, coverage close to 100%);
  - a video with over 10,000 comments, clicking **Stop** after a few seconds (it says "Kept the N comments");
  - a video with comments turned off ("Comments are turned off for this video.");
  - a made-up video ID (`https://youtu.be/AAAAAAAAAAA`, "couldn't find this video");
  - a Shorts link.
- Analyse the same video again. The table shouldn't grow duplicates.
- Sort the table by Likes and by Posted, and type in the filter box.
- Compare the quota line on the Analyse page with **Quotas** for YouTube Data API v3 in Google Cloud Console. The console updates with a delay.

### Phase 3: Analysis engine

**Delivers**
- Analysis modules:
  - cleaning;
  - language detection;
  - sentiment, flagged as low-confidence for languages the model wasn't trained on;
  - spam detection;
  - topics, with the small-post fallback;
  - timeline and summary.
- Model download with pinned versions and checksums.
- Claude mode: shows the estimated cost from a token count and asks you first; sends no usernames.
- Evaluation script: F1 per language, confusion matrix and calibration, comparing VADER, the local model and Claude.

**You test**
- Label about 300 of your collected comments in a CSV the app generates (about an hour).
- Run the evaluation, then pick the model and thresholds from the numbers.

### Phase 4: Dashboard, comments table, compare, exports

**Delivers**
- **Dashboard:**
  - a coverage banner;
  - sentiment both unweighted and weighted by likes;
  - topics, timeline and language mix.

  Charts use Plotly, loading plotly.js from a local file because `setHtml` caps pages at 2 MB.
- **Comments table:** fast at 100,000 rows.
- **Compare:** two to four posts side by side.
- **Exports:** Word, Excel and CSV, all without names.

**You test**
- The table's Negative filter matches the dashboard's Negative figure.
- No usernames appear in the exported files.
- Korean text renders correctly.

### Phase 5: Accounts and in-app login

**Delivers**
- One browser profile per account.
- Login window; status is read from cookies, and passwords are never stored.
- Several accounts at once, each with "Log out and delete".
- `capture.js`.
- A bridge that accepts one kind of message, checks the page's site and caps sizes.
- Optional debug capture.

**You test**
- Two accounts on the same platform logged in at once.
- Still logged in after a restart.
- Deleting an account removes its folder.
- Debug capture writes JSON files to `debug/`.

### Phase 6: Facebook, then Instagram, then TikTok (stop after each)

**Delivers**
- `selectors/<platform>.json`. A copy in the data folder can override it.
- Parsers ported from the old app.
- A driver that loads more comments with random 2–5 second pauses and hands the browser back to you at walls.
- Per-account daily limits.
- A fixture sanitizer for test data.

**You test**
- Collect from your own posts (under 50 comments, then about 500) and compare the count with the site.
- Log out partway through: the app should stop and hand the browser back to you.
- Set the daily limit to 1: a second post should be refused.

### Phase 7: Tracking, tray, spike alerts

**Delivers**
- **Tracking:** YouTube uses the API (one quota unit covers 50 videos). Browser platforms are checked rarely and with caps.
- **Tray:** close-to-tray, and optional start with Windows.
- **Charts and alerts:** growth charts and spike notifications.

**You test**
- Track 2–3 posts on a 15-minute test interval.
- Trigger a test spike.
- Restart the app and check tracking resumes.

### Phase 8: Post maker and publishing

**Delivers**
- Cards as 1080×1350 JPEGs with the "Based on N public comments…" footer.
- Captions written differently for each account.
- Facebook Page publishing via the Graph API.
- Export folders for manual posting elsewhere.
- A check that blocks the same text on two accounts.

**You test**
- Publish to a test Page.
- Try the same caption on two accounts; it should be blocked.

### Phase 9: Build, README, CI, release

**Delivers**
- Trim the build (570 MB in Phase 1). Drop unused Qt modules and the WebEngine devtools resources.
- An Inno Setup installer that upgrades the old app in place.
- License notices for Qt and Chromium, and a license for the repo.
- GitHub Actions CI and a release workflow.

**You test**
- Install in Windows Sandbox and run a smoke checklist.
- Upgrade over the old app.
- Push a test tag and check the release.
