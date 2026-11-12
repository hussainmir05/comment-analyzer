"""The command line, as the previous version had: collect a YouTube video, analyse it, export.

    python -m comment_analyzer analyse <link> [--limit N] [--newest] [--out FOLDER]

It uses the same data folder and API key as the app (or --api-key, or the YOUTUBE_API_KEY
environment variable), and writes the same Word, Excel and CSV report. Facebook, Instagram and
TikTok need the app's own logged-in browser window, so they're app-only.

Logging goes to the console only: the app may be running, and two processes writing one log
file overwrite each other's lines.
"""

import argparse
import logging
import os
import sys
from contextlib import closing
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from comment_analyzer import __version__, config, privacy
from comment_analyzer.analysis.pipeline import LocalAnalyzer, analyse_post
from comment_analyzer.analysis.summarize import summary_lines
from comment_analyzer.collection import CollectionSession, RunSummary
from comment_analyzer.config import AppPaths
from comment_analyzer.db import Database, retention
from comment_analyzer.models import Platform, RunStatus
from comment_analyzer.platforms import youtube
from comment_analyzer.platforms.detect import detect
from comment_analyzer.reports.data import load_post
from comment_analyzer.reports.exports import export_post
from comment_analyzer.workers import CancelToken, TaskRunner


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m comment_analyzer analyse",
        description="Collect a YouTube video's comments, analyse them and write a report.",
    )
    p.add_argument("link", help="a YouTube video, Short or youtu.be link")
    p.add_argument("--limit", type=int, help="comments to read (default: the app's setting)")
    p.add_argument("--newest", action="store_true", help="newest first (default: top comments)")
    p.add_argument(
        "--out", type=Path, help="report folder (default: Documents\\Comment Analyzer Reports)"
    )
    p.add_argument(
        "--api-key", default=os.environ.get("YOUTUBE_API_KEY", ""), help=argparse.SUPPRESS
    )
    p.add_argument("--version", action="version", version=__version__)
    return p


def main(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    link = detect(args.link)
    if not link.ok or link.platform is not Platform.YOUTUBE:
        reason = link.message or "That isn't a link to a post."
        if link.platform in (Platform.FACEBOOK, Platform.INSTAGRAM, Platform.TIKTOK):
            reason = f"{link.platform.label} is read in the app's own browser: open the app for it."
        print(reason, file=sys.stderr)
        return 2

    paths = AppPaths.default()
    paths.create()
    config.use_system_keyring()
    key = args.api_key or config.get_secret(config.YOUTUBE_API_KEY)
    if not key:
        print(
            "No YouTube API key: save one in the app's Settings, or set YOUTUBE_API_KEY.",
            file=sys.stderr,
        )
        return 2
    db = Database(paths.db)
    db.migrate()
    with closing(db.connect()) as conn:
        retention.close_interrupted_runs(conn)
    settings = config.load_settings(config.settings_store(paths))

    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    tasks = TaskRunner()
    collector = youtube.YouTubeCollector(
        key, tasks, privacy.author_hasher(), dislike_estimate=settings.youtube_dislike_estimate
    )
    session = CollectionSession(
        db,
        collector,
        link,
        limit=args.limit or settings.comments_per_post,
        sort="time" if args.newest else "relevance",
    )
    results: list[RunSummary] = []
    session.progress.connect(lambda done, total, text: print(f"  {text}", flush=True))

    def done(summary: RunSummary) -> None:
        results.append(summary)
        app.quit()

    session.done.connect(done)
    print(f"Reading {link.message}...", flush=True)
    session.start()
    app.exec()
    tasks.wait(5000)
    summary = results[0]
    if summary.status is RunStatus.FAILED or summary.post is None or summary.post.id is None:
        print(summary.message or "The collection failed.", file=sys.stderr)
        return 1
    print(summary.message or f"Read {summary.collected:,} comments ({summary.new:,} new).")

    analyzer = LocalAnalyzer(paths.models)
    if analyzer.ready:
        print("Analysing...", flush=True)
        analyse_post(
            db,
            summary.post.id,
            analyzer.classifier,
            lambda done, total, text: None,
            CancelToken(),
            make_embedder=analyzer.embedder,
        )
    else:
        print(
            "Not analysed: the analysis models aren't downloaded. Open the app once and click "
            '"Analyse comments" to download them.'
        )
    report = load_post(db, summary.post.id)
    assert report is not None
    if report.analysis is not None:
        for line in summary_lines(report.analysis):
            print(f"  {line}")
    folder = export_post(report, args.out)
    print(f"Report: {folder}")
    return 0
