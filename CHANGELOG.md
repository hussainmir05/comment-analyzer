# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]: 2.0.0, the rewrite

### Release 2.0.0-beta.1

- **Licence:** the code is now under the GNU GPL, version 3 or later (`LICENSE`). The installer includes the licence text.
- **Version:** `2.0.0b1`. It's a beta because the Facebook, Instagram and TikTok collectors and Meta publishing haven't run against the real sites yet.
- **Release workflow:** tags with a hyphen, such as `v2.0.0-beta.1`, are published as pre-releases.

### Completing the plan

#### Added
- **Command line:** `python -m comment_analyzer analyse <link> [--limit N] [--newest] [--out FOLDER]`, as the previous version had.
  - It collects a YouTube video, analyses it, prints the summary, and writes the same Word, Excel and CSV report.
  - It uses the app's data folder and saved key, or `YOUTUBE_API_KEY`.
  - Tested for real on a copy of the data folder: 60 comments read, analysed and exported.
- **Dislike estimate (YouTube, off by default):** a Settings option asks the Return YouTube Dislike project for its estimate. Only the video's ID is sent, never the API key.
  - The figure shows as "Dislikes (estimate)" on the Analyse page, the dashboard and in exports.
- **Tracking for Facebook, Instagram and TikTok:** these posts can be tracked too.
  - They're checked at most every 6 hours, up to 300 comments, in a visible window, within each account's daily limit.
  - A post whose check can't start waits 30 minutes without holding up the other posts.
- **One post at a time per account:** tracking and the Analyse page never read with the same account at once.
- **Evaluation with Claude:** `python -m comment_analyzer.analysis.evaluate score <labels.csv> --claude` adds Claude's scores to the report. It shows the estimated cost first and sends nothing until you type `y`.
- **Facebook note:** before the first Facebook account, the app says that Facebook allows one personal account per person.

#### Changed
- **Roman Urdu and Hindi:** common function words written in Latin letters ("hai", "bhai", "bhi", "yeh"...) no longer name topics or issues.
  - On a real video, the top topic changed from "hai, bhai, chakkar" to "chakkar, chip".

#### Fixed (found by the new tests)
- **Test isolation:** the tests could reach the real Credential Manager and the real Documents folder through the app's startup code. Now neither can be reached.
  - One test report had been written to Documents\Comment Analyzer Reports; it was removed.
  - Nothing was written to Credential Manager.

### Phase 8, part 2: publishing to a Facebook Page and Instagram (tested offline)

#### Added
- **Publishing on the Post maker:** **Publish to Facebook Page...** and **Publish to Instagram...** use Meta's Graph API (v23.0).
  - Every post needs your click and then a confirmation that shows where it goes and the text.
  - The same caption never goes out twice: the app checks what it has already published, ignoring spacing.
  - Instagram fetches images from a public address, which the app doesn't have. So the card goes to your Page as an unpublished photo first, Instagram gets that photo's address, and the helper photo is deleted afterwards.
- **Settings, under Meta:** paste a Page access token from your own Meta developer app. The app asks Facebook (read-only) which Page it's for and whether an Instagram account is linked. The token goes into Windows Credential Manager.
- **Errors:** Meta's error codes become plain messages: expired token, missing permission, rate limits, image problems. The token never reaches the log.
- **Database:** migration `0004_published.sql` records each post: platform, Page or Instagram account, text, image and remote ID.

#### Not yet
- No real post has been made. It needs your Meta app and token, and it's tested against a mock server only.

### Phase 6: Facebook, Instagram and TikTok collectors (tested offline)

#### Added
- **Browser collection:** paste a Facebook, Instagram or TikTok post link (share links too) and click **Analyse**. The post opens in a visible window of a logged-in account's own browser profile.
  - `capture.js` hands the app the page's own API answers. A parser for each site turns them into the same records YouTube produces.
  - Every 2–5 seconds, at random, one step clicks at most three buttons such as "View more comments" or "View 3 replies", then scrolls. The buttons are listed in the selectors file.
  - The step never touches forms, text boxes, or Like, Reply, Share or Follow buttons. A test checks this in a real page.
  - It stops at the comment limit, when nothing new arrives for four steps, at the step cap, or on Stop.
  - At a login wall, checkpoint or captcha it stops, keeps what was read, marks the account "needs you", and hands the window over to you.
- **Parsers:** ported from the previous version's Facebook, Instagram and TikTok readers. They search the JSON for objects with a comment's fields, so small layout changes don't break them.
  - Facebook: reactions by type, shares, views, comments and replies.
  - Instagram: likes, views and the caption.
  - TikTok: views, likes, shares and saves.
- **Accounts:** the least-used logged-in account is picked for each post, within the posts-per-day limit in Settings. Each run is counted against its account.
- **Selectors files:** `platforms/selectors/<site>.json` hold the capture patterns, button texts, wall addresses and texts, and a step cap. A copy in the data folder's `selectors\` replaces the built-in one.
- **Fixtures:** `scripts\sanitize_fixture.py` turns a debug capture into a test fixture, replacing names, user IDs, texts and links.
- **Build:** the smoke test loads the selectors, `more.js` and `capture.js`.

#### Not yet
- These collectors haven't run against the real sites yet. The fixtures are made up in the sites' shapes, so expect to adjust selectors and parsers after the first real runs. Debug capture plus the sanitiser makes that quick.
- Tracking still covers YouTube only.

### Phase 5: accounts and in-app login

#### Added
- **Accounts page:** add Facebook, Instagram and TikTok accounts, each with its own browser profile in `profiles\` in the data folder. Several can be logged in at once, two on the same site included.
  - Before the first account for a site, a plain-language note covers that site's terms.
  - **Open browser**, **Check login**, and **Log out and delete**, which removes the cookies and the profile folder. Files Chromium still holds are deleted at the next start.
- **Login window:** the site's own login page. The password goes to the site only; the app never reads the page and stores no password. Pop-ups such as "Continue with Google" open in the same window, and camera, microphone, location and notification requests are turned down.
- **Login status:** read from each site's login cookie (`c_user` or `sessionid`) as the profile's cookies change.
- **`capture.js` and the bridge:**
  - The script is injected only into collection pages, never into login pages.
  - The bridge accepts one kind of message, only while the page is on the account's own site and the address is one the selectors file names. Messages are capped at 9 MB and 600 a minute.
- **Debug capture** (Settings → Privacy, off by default): saves captured responses, never cookies or headers, to the data folder's `debug\`.
- **Logins survive restarts:**
  - At exit, the app closes browser windows and then the profiles, so Chromium writes each profile's cookies to disk.
  - Chromium doesn't report the cookies it loads from disk, so **Check login** reads the profile's cookie file directly: names, sites and expiry dates only, never values.
  - Tested across two processes.

#### Fixed (found by the new tests)
- A damaged list of folders to delete could have pointed outside `profiles\`. Only exact profile folder names are accepted now.

### Phase 3, step 2: topics and Claude mode

#### Added
- **Topics:** comments that say much the same thing, in any language, are grouped. Each group is named by its most typical words, for example "dera ismail khan, farhan ali raza, father".
  - The model is paraphrase-multilingual-MiniLM-L12-v2 (sentence-transformers, Apache-2.0), run with ONNX Runtime. It's a one-time download of 135 MB, after asking, pinned and checked with SHA-256.
  - Clustering is done in numpy rather than with HDBSCAN, which would add scikit-learn and SciPy (about 150 MB) to the app.
  - Spam is left out. Posts under 30 comments get no topics; "What negative comments mention most" still covers them.
- **Where topics show up:**
  - the summary ("Comments talk most about ...");
  - a dashboard chart of each topic split by sentiment, whose hover shows the topic's most-liked comment;
  - a Topic column and topic filter in the comments table;
  - a Topics sheet in `Comments.xlsx`, a topic column in the CSV, and a topics table in `Report.docx`.
- **Claude mode (optional, paid):** **Ask Claude...** on the Analyse page gets a second opinion from Claude through Anthropic's API.
  - **Output:** sentiment per comment, plus up to 8 topics with one-sentence summaries. The Analyse page shows Claude's shares, how often it agrees with the local model, and its topics.
  - **Cost:** the app first counts the tokens, which is free, and then shows the estimated cost and the most it could cost. Nothing is sent until you click Yes.
  - **What's sent:** only comment texts, with @mentions replaced by "@user" and links by "http".
  - **Settings, under Claude:** the Anthropic API key, checked at no cost and kept in Windows Credential Manager, and the model. Opus 5.5 is the default; Sonnet 5.5 and Haiku 5.5 cost less.
  - **Requests:** sent one at a time. The Batches API would cost half as much, but it can take up to 24 hours.
  - **Testing:** tested with a fake client only. No paid call was made during development.
- **Database:** migration `0003_topic_mode.sql` gives each topic its analysis mode (local or Claude).
- **New dependency:** `anthropic` (the official SDK, Apache-2.0) with pydantic. The build's smoke test loads it.

#### Changed
- The sentiment and topic models share one ONNX loader (`analysis/onnx_text.py`).
- "Thanks" counts as a mood word: issues skip it and topic names keep it.

### Phase 8, part 1: post maker (cards and captions; no API publishing yet)

#### Added
- **Post maker page:**
  - **Card:** 1080×1350, saved as JPEG for Instagram and as PNG. It shows the mood, a sentiment bar, the like-weighted shares, the top issues, and the coverage and "Comments are not a poll" footer. Commenters' names and photos never appear.
  - **Captions:** four wordings, editable, so two accounts never post the same text.
  - **Save post kit:** writes `card.jpg`, `card.png` and `caption.txt` to `Documents\Comment Analyzer Reports\`.
- **Not yet:** publishing straight to a Facebook Page or Instagram account needs a Meta developer app.

### Phase 9: build, installer, CI, release

#### Added
- **Installer:** `scripts\build_exe.ps1 -Installer` builds `dist\Comment-Analyzer-<version>-Setup.exe` with Inno Setup.
  - Installs per user, with no admin rights.
  - Keeps the previous version's AppId, so it upgrades the old app in place.
  - Uninstalling removes autostart, and offers to delete the data folder and the saved keys.
- **Smaller build:** Chromium's developer-tools files and non-English browser locales are left out (about 120 MB).
- **Notices:** `THIRD-PARTY-NOTICES.md` lists the bundled software and the downloaded model with their licences, and is installed with the app.
- **GitHub Actions:**
  - `ci.yml` runs ruff, mypy and pytest on Windows.
  - `release.yml` builds, smoke-tests and packages on a `v*` tag, then attaches the installer to a GitHub Release.

### Phase 7: tracking over time, tray, spike alerts

#### Added
- **Tracking:** tick **Track this post** on the Analyse page. While the app runs, tracked YouTube posts are checked on an interval (Settings, default 60 minutes).
  - Each check takes a snapshot and collects only comments newer than the newest stored one, newest first.
  - New comments are analysed.
- **Spike alerts:** Windows notifications for 10+ comments in an hour at three times the usual rate, or a negative share 20+ points above the post's overall share.
- **Tracking page:** tracked posts, their last check, and the change since tracking began.
- **Dashboard:** a growth chart from the snapshots.
- **Tray icon:** Open, Check now, Quit. Closing the window keeps tracking running in the tray (a setting). **Start with Windows** opens in the tray.

### Phase 4: dashboard, comments table, compare, exports

#### Added
- **Dashboard page:**
  - KPI tiles and the coverage line;
  - sentiment, unweighted and weighted by likes;
  - comments over time by sentiment (per hour, day, week or month), languages, and what negative comments mention most.

  Plotly charts run in the built-in browser with plotly.js loaded from disk, so nothing is fetched online.
- **Compare tab:** 2 to 4 posts, showing sentiment shares and comment growth by hours since posting.
- **Export report:**
  - writes `Report.docx`, `Comments.xlsx`, `Comments.csv` and chart PNGs to `Documents\Comment Analyzer Reports\`;
  - never includes commenters' names or IDs;
  - every report ends with "Comments are not a poll".
- **Comments table:** a sentiment filter (positive, neutral, negative, spam) next to the text filter.
- **Packaging check:** the build's smoke test also writes a Word file, a workbook and a chart, and checks that plotly.js is present.

### Phase 3, step 1: analysis engine

#### Added
- **Language:** fastText's compact language-ID model, offline. Short texts need a confident guess, otherwise they're "unknown".
- **Sentiment:** XLM-T (Cardiff NLP, Apache-2.0), run with ONNX Runtime on the CPU.
  - The model (282 MB) downloads once, after asking, pinned to a revision and checked with SHA-256.
  - Results in untrained languages, or below 50% confidence, are marked "unsure".
- **Spam rules:** copies of the same message from several commenters, and links with self-promotion. Spam is left out of the shares.
- **Issues:** "What negative comments mention most", ported from the previous version. Phrases that are pieces of one name are joined.
- **Summary:** mood, unweighted and like-weighted shares, issues, languages, spam and unsure counts, and the "Comments are not a poll" line.
- **Automatic and on demand:** analysis runs after every collection, and an **Analyse comments** button handles earlier posts.
- **Evaluation:** `python -m comment_analyzer.analysis.evaluate sample|score` checks the model against your own labels. It reports per-language accuracy and macro-F1, a confusion matrix, calibration and a VADER baseline.
- **Database:** migration `0002_analysis.sql` adds the `analysis` and `topics` tables.

### Phase 2: YouTube collector

#### Added
- **YouTube collection** through the official Data API v3:
  - every comment and every reply, the reply part via `comments.list` once a thread has more replies than `commentThreads` includes;
  - "Top comments first" or "Newest first";
  - the per-post limit from Settings;
  - Stop, the limit and an exhausted quota all keep what was read.
- **Collection runs** recorded with coverage (collected and reported), sort order and times. Collectors only emit records; `CollectionSession` stores them.
- **Analyse page:** a results summary, a sortable and filterable comments table, saved posts, and today's quota use (counted on the Pacific-time day).
- **Settings:** saving the API key tests it with YouTube, and a **Test key** button repeats the check.
- **Plain-language YouTube errors:**
  - comments turned off;
  - video not found;
  - quota used up;
  - invalid or restricted key;
  - API not enabled;
  - "Permission denied" for the project.

  Busy answers are retried with backoff.
- **Privacy:** commenter IDs are stored as HMAC-SHA256 hashes with a per-install key in Credential Manager.
- **Retention:** YouTube data older than 30 days and comment text past the Settings limit are deleted at startup and every 6 hours. Runs interrupted by a crash are closed at startup.
- **Packaging check:** the build's smoke test now also loads the HTTP client and time-zone data.

#### Changed
- The Phase 1 demo job is gone; **Analyse** collects for real.
- New dependencies: `httpx2` and `tzdata`.
- ruff formats for Python 3.13 syntax, so `except (A, B):` keeps its parentheses.

### Phase 1: app skeleton

#### Added
- **Main window.** A PySide6 window with Analyse, Dashboard, Accounts, Tracking, Post maker and Settings pages. Pages for later phases are placeholders.
- **Link detection.** Covers YouTube, Facebook, Instagram and TikTok posts, videos, Shorts, reels and share links. X links get an explanation, and shortened links get advice.
- **Settings.**
  - Stored in `settings.ini` in the data folder.
  - The YouTube API key goes in Windows Credential Manager.
  - A one-time offer moves the plain-text key from the previous version.
- **Database.** SQLite with versioned migrations, a backup before each upgrade, the WAL journal and foreign keys.
- **Logging.** A rotating log file that masks API keys, tokens and cookies, plus crash capture (uncaught exceptions and native crashes).
- **Background jobs.** They show progress and have a Stop button. A second launch hands its link to the running copy.
- **Build.** `scripts\build_exe.ps1` makes a one-folder Windows build and smoke-tests it (window plus built-in browser).

#### Changed
- The one-file Tkinter app moved to `legacy/` as a read-only reference.
- Installers are no longer stored in the repository.

## [1.0]: the previous version

- The one-file Tkinter app. See [legacy/README.md](legacy/README.md).
