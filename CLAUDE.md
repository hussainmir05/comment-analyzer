# Comment Analyzer

Windows desktop app (Python 3.14 + PySide6). The user pastes a link to a YouTube, Facebook, Instagram or TikTok post; the app collects the post's stats and comments, analyzes sentiment, topics and growth over time, and turns the findings into post-ready images and captions.

Owner: Hussain, a computer-vision researcher who is comfortable with Python and ML. Explain non-obvious decisions in a sentence or two. Build phase by phase (see Phases) and stop for testing at the end of each phase.

## Commands (PowerShell, inside the project .venv)
- Setup: `uv sync` (creates .venv from `uv.lock`)
- Run: `python -m comment_analyzer`
- Tests: `pytest`
- Lint and format: `ruff check .` and `ruff format .`
- Type check: `mypy`
- Windows build: `scripts\build_exe.ps1` (builds, then smoke-tests the exe)

## Stack
- GUI: PySide6 Widgets. QtWebEngine for logins and browser collection; QWebChannel for the JavaScript-to-Python bridge.
- HTTP: httpx2 (the client the anthropic SDK uses), with our own retries and backoff for 429 and 5xx responses.
- Storage: SQLite (stdlib sqlite3) in the app-data folder from platformdirs; versioned SQL migrations in `src/comment_analyzer/db/migrations/`.
- Secrets: keyring (Windows Credential Manager). Never in files, logs, settings or the repo. The one exception is browser profiles, which must hold session cookies. They stay in app data, never go into exports or debug captures, and every account has "Log out and delete".
- Analysis: ONNX Runtime + tokenizers on CPU (models exported to ONNX; torch and transformers are dev-only), fast-langdetect, and numpy. Topics come from multilingual embeddings (paraphrase-multilingual-MiniLM) + community clustering in numpy + c-TF-IDF names, with the legacy issue phrases for small posts. Optional: anthropic SDK.
- Charts: Plotly in a QWebEngineView with plotly.js bundled locally; matplotlib for exported images.
- Packaging: PyInstaller one-folder build plus an Inno Setup installer. Dependencies are locked with uv (`uv.lock`).

## Layout
```
src/comment_analyzer/
  __main__.py            entry point
  app.py                 QApplication, theme, main window wiring
  cli.py                 command line: analyse <link> without a window (YouTube)
  collection.py          CollectionSession: stores what a collector emits
  config.py              QSettings + keyring helpers
  errors.py              UserError: messages safe to show the user
  instance.py            one running copy; a second launch hands over its link
  logs.py                rotating log file, secret masking, crash capture
  models.py              dataclasses: Account, Post, Snapshot, Comment, CollectionRun, AnalysisResult
  privacy.py             HMAC-SHA256 of commenter IDs (per-install key in keyring)
  timeutil.py            UTC timestamps for the database
  workers.py             QThreadPool workers with progress, Stop and plain-language errors
  db/                    connection, migrations (each phase adds its own), repository functions, retention
  platforms/
    base.py              Collector interface
    detect.py            URL -> (platform, post_id, canonical_url)
    youtube.py           official YouTube Data API v3
    browser_collector.py the Facebook/Instagram/TikTok collector: visible window, step driver
    facebook.py, instagram.py, tiktok.py    parsers for what each site's pages receive
    web_data.py          shared JSON helpers and PostInfo
    selectors/*.json     capture patterns, "load more" buttons, walls, per platform; more.js
  browser/
    sites.py             domains, login pages and login cookies per site
    profiles.py          one QWebEngineProfile per account
    login.py             in-app login window
    capture.js           fetch/XHR hook injected at document creation
    bridge.py            QWebChannel bridge
  analysis/              clean, language, model_store, onnx_text, sentiment, embeddings, topics, spam,
                         issues, summarize, pipeline, evaluate
  tracking/              scheduler, spikes, startup (Windows Run key)
  reports/               data (one loader for dashboard and exports), charts, exports, cards, captions
  publish/meta.py        Facebook Page and Instagram publishing (Graph API)
  ui/                    main window, pages, comments table, dashboard HTML (Plotly), tray, theme
tests/                   unit tests; fixtures/ = sanitized sample responses; eval/ = sentiment evaluation
legacy/                  the previous one-file app (all four platforms), read-only reference
scripts/                 build and maintenance scripts (build_exe.ps1, comment_analyzer.spec,
                         sanitize_fixture.py)
docs/PLAN.md             phase plan, test steps for each phase, decisions
.github/workflows/       ci.yml, release.yml
```

App data, never in the repo: `platformdirs.user_data_dir("CommentAnalyzer", appauthor=False)`, which is `%LOCALAPPDATA%\CommentAnalyzer`. It holds `data.db`, `settings.ini`, `backups/`, `profiles/<account-id>/`, `logs/`, `exports/`, `models/` and `debug/`. The `COMMENT_ANALYZER_HOME` environment variable overrides it for tests and the smoke test.

## Data model (minimum)
- accounts(id, platform, label, profile_dir, status, created_at)
- posts(id, platform, post_id, url, author, title, published_at, first_seen_at, tracked)
- snapshots(id, post_id, taken_at, views, likes, comments_reported, shares, saves, reactions_json, extra_json)
- collection_runs(id, post_id, account_id, started_at, finished_at, status, sort_used, comment_limit, comments_collected, comments_reported, note)
- comments(id, post_id, platform_comment_id, parent_id, author_hash, text, lang, created_at, like_count, reply_count, collected_at, sort_used, run_id), unique on (post_id, platform_comment_id)
- analysis(comment_id, mode, sentiment, confidence, low_confidence, topic_id, spam_flag, model_version, analyzed_at)
- topics(id, post_id, label, keywords, share, summary)
- api_usage(id, platform, day, units)
- account_usage(account_id, day, posts, comments)
- published(id, post_id, platform, target, text, media_path, published_at, remote_id). target is the Page or Instagram account ID; publishing never uses the browser accounts

Times are stored as UTC ISO-8601 text. Each table arrives with the phase that first needs it: analysis and topics in Phase 3, published in Phase 8.

## Collector interface
Every collector implements `Collector` in `platforms/base.py`. QtWebEngine lives on the GUI thread, so collectors are QObjects that report through signals instead of returning iterators:
- `start(link, *, limit, sort, since=None)` and `stop()`. Stop keeps what was read.
- Signals arrive in this order:
  - `post_found(Post, Snapshot)` once;
  - then any number of `batch(list[Comment])`, `progress(done, total, message)` and `api_units(day, units)`;
  - then `finished(RunStatus, message)` exactly once.

  Browser collectors also emit `blocked(reason)`.

Collectors never touch the database. `CollectionSession` (`collection.py`) stores their records on the GUI thread. The YouTube collector makes its HTTP calls in a QThreadPool worker and emits the same signals. All collectors write the same records, so analysis, dashboard, tracking and publishing never depend on the platform.

## Platform rules (hard requirements)
- **YouTube:** official Data API v3 with an API key. Count quota (1 unit per list call, 10,000 per day, resets at midnight Pacific Time). Delete or refresh API data older than 30 days, so YouTube snapshot history covers a rolling 30 days. Never ask for or store YouTube login details. yt-dlp only as an opt-in fallback.
- **X:** not supported. Its API is paid per post read, and automating x.com is banned. Detect X links and explain this in the UI. Never scrape x.com.
- **Facebook, Instagram, TikTok:** no official API covers other people's posts for this app. Read only what the user's own logged-in in-app browser loads, via `capture.js` and the selectors JSON. Collection is visible, at human pace (random 2-5 s pauses), one post at a time per account, with per-post and per-day limits from Settings. On a login wall, checkpoint or captcha: stop and hand the browser to the user. No captcha solving, no anti-detection or fingerprint spoofing, and never post, like or comment through the websites. Keep QtWebEngine's default user agent; if a site rejects it, stop and tell the user. Each browser platform is opt-in, with a plain-language notice about that site's terms.
- **Facebook Pages, later:** if Meta approves the app for Page Public Content Access, add a Graph API collector for public Page posts and prefer it over the browser.
- **Publishing:** only through official APIs (Facebook Graph API for Pages, Instagram Content Publishing API) or by exporting files for manual posting (X, TikTok, YouTube). Every post needs the user's click. Never the same text on two accounts.

## Privacy
- Store an HMAC-SHA256 of commenter IDs (per-install key in keyring), not names. Show names only in memory, behind a setting that is off by default.
- Exports, cards and captions never include commenters' names or photos.
- Purge raw comment text after 30 days by default (setting); keep aggregates.
- Debug captures are off by default, live only in app data, and are gitignored.
- Comment text sent to the Claude API carries no usernames, and is sent only after the user confirms the estimated cost.

## Honest analysis
- Always show coverage (collected / reported comments), the sort order used, and when the data was collected.
- Show sentiment both unweighted and weighted by likes.
- Mark low-confidence results, such as languages the model was not trained on.
- Every card and report says: "Based on N public comments collected <date, time>. Comments are not a poll."

## Code conventions
- Type hints everywhere; dataclasses for models; small modules.
- Never block the UI thread: QThreadPool workers with progress and cancel signals.
- User-facing errors in plain language; details go to the log. Errors meant for the user are `UserError`; anything else is logged and shown as a generic message.
- Every phase keeps `scripts\build_exe.ps1` and its smoke test passing.
- Tests use pytest with no live network calls; use sanitized fixtures in `tests/fixtures/`.
- Don't edit `legacy/`; port its logic into the new structure.
- Ask before adding a dependency over about 50 MB or any paid service.

## Phases
0. Review the code and plan (no edits)
1. App skeleton: window, link detection, settings, database, logging, workers, build smoke test
2. YouTube collector
3. Analysis engine, optional Claude API mode, evaluation script
4. Dashboard, comments table, compare, exports
5. Accounts page with in-app login (several accounts at once)
6. Facebook, then Instagram, then TikTok collectors (stop after each)
7. Tracking over time, system tray, spike alerts
8. Post maker (cards, captions) and publishing
9. Windows build, README, GitHub Actions, release

After each phase: run ruff, mypy and pytest, update README and CHANGELOG, and propose a commit message. `docs/PLAN.md` has the test steps for each phase.

Release 2.0, the first release of the rewrite, is Phases 1–6 plus 9. Phase 7 follows as 2.1 and Phase 8 as 2.2. Installers go on GitHub Releases, never in the repo.
