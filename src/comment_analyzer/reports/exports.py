"""Exports for one post: Report.docx, Comments.xlsx, Comments.csv and the chart images.

Saved in Documents\\Comment Analyzer Reports\\<date time> <platform> - <title>. Exports never
include commenters' names, IDs or photos (CLAUDE.md, Privacy). Quoted comment text is the
public comment itself.
"""

import csv
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import platformdirs
from docx import Document
from docx.document import Document as DocxDocument
from docx.shared import Inches, Pt, RGBColor
from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from comment_analyzer import APP_NAME
from comment_analyzer.analysis import summarize
from comment_analyzer.analysis.clean import clean
from comment_analyzer.analysis.language import language_name
from comment_analyzer.analysis.sentiment import TRAINED_LANGUAGES
from comment_analyzer.platforms.youtube import DISLIKES_KEY
from comment_analyzer.reports import charts
from comment_analyzer.reports.data import PostReport

COMMENT_COLUMNS = (
    "posted",
    "likes",
    "replies",
    "type",
    "sentiment",
    "confidence",
    "unsure",
    "spam",
    "language",
    "topic",
    "comment",
)
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def reports_folder() -> Path:
    """Documents\\Comment Analyzer Reports (follows Documents if it moved, e.g. to OneDrive)."""
    return Path(platformdirs.user_documents_dir()) / f"{APP_NAME} Reports"


def export_post(report: PostReport, base: Path | None = None) -> Path:
    """Write every export for the post into a new folder; returns the folder."""
    title = _UNSAFE.sub("", report.title)[:40].strip(" .") or "post"
    name = f"{datetime.now():%Y-%m-%d %H%M} {report.post.platform.label} - {title}"
    folder = (base or reports_folder()) / name
    folder.mkdir(parents=True, exist_ok=True)
    images = {
        "sentiment": charts.sentiment_chart(report, folder / "sentiment.png"),
        "timeline": charts.timeline_chart(report, folder / "timeline.png"),
        "issues": charts.issues_chart(report, folder / "issues.png"),
    }
    write_csv(report, folder / "Comments.csv")
    write_excel(report, folder / "Comments.xlsx")
    write_word(report, images, folder / "Report.docx")
    return folder


def export_check(folder: Path) -> None:
    """Write a tiny Word document, workbook and chart (the build's smoke test uses this to catch
    missing templates or fonts)."""
    doc = Document()
    doc.add_paragraph("check")
    _table(doc, ["a"], [("b",)])
    doc.save(str(folder / "check.docx"))
    Workbook().save(folder / "check.xlsx")
    figure, axes = charts._figure(2, 2)
    axes.bar([1], [1])
    charts._save(figure, folder / "check.png")


def comment_rows(report: PostReport) -> list[list[object]]:
    rows: list[list[object]] = []
    for c in report.comments:
        result = report.results.get(c.id) if c.id is not None else None
        rows.append(
            [
                c.created_at.astimezone().strftime("%Y-%m-%d %H:%M") if c.created_at else "",
                c.like_count or 0,
                c.reply_count if c.reply_count is not None else "",
                "reply" if c.parent_id else "comment",
                result.sentiment if result and result.sentiment else "",
                round(result.confidence, 3) if result and result.confidence is not None else "",
                "yes" if result and result.low_confidence else "",
                "yes" if result and result.spam_flag else "",
                c.lang or "",
                report.topic(c),
                clean(c.text) if c.text else "(deleted after the retention period)",
            ]
        )
    return rows


def write_csv(report: PostReport, path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # Excel reads utf-8-sig
        writer = csv.writer(f)
        writer.writerow(COMMENT_COLUMNS)
        writer.writerows(comment_rows(report))


def summary_rows(report: PostReport) -> list[tuple[str, object]]:
    post, snapshot, analysis = report.post, report.snapshot, report.analysis
    rows: list[tuple[str, object]] = [
        ("Platform", post.platform.label),
        ("Title", post.title or ""),
        ("Posted by", post.author or ""),
        (
            "Posted on",
            post.published_at.astimezone().strftime("%Y-%m-%d") if post.published_at else "",
        ),
        ("Link", post.url),
        ("Coverage", report.coverage()),
    ]
    if snapshot is not None:
        rows += [
            ("Views", snapshot.views if snapshot.views is not None else "not public"),
            ("Likes", snapshot.likes if snapshot.likes is not None else "not public"),
            ("Comments reported", snapshot.comments_reported or ""),
        ]
        if isinstance(snapshot.extra.get(DISLIKES_KEY), int):
            rows.append(
                ("Dislikes (estimate, Return YouTube Dislike)", snapshot.extra[DISLIKES_KEY])
            )
    if analysis is not None:
        for name, shares in (("", analysis.shares), (" (weighted by likes)", analysis.weighted)):
            for sentiment in summarize.SENTIMENTS:
                rows.append((f"{sentiment.capitalize()}{name}", f"{shares[sentiment]:.1f}%"))
        rows += [("Spam left out", analysis.spam), ("Marked unsure", analysis.low_confidence)]
    rows.append(("Note", report.footer()))
    return rows


def write_excel(report: PostReport, path: Path) -> None:
    book = Workbook()
    summary = book.active
    assert summary is not None
    summary.title = "Summary"
    summary.append(["", ""])
    for key, value in summary_rows(report):
        summary.append([key, value])
    summary.delete_rows(1)
    comments = book.create_sheet("Comments")
    comments.append(list(COMMENT_COLUMNS))
    for row in comment_rows(report):
        comments.append(row)
    comments.freeze_panes = "A2"
    issues = book.create_sheet("Issues")
    issues.append(["issue", "negative comments mentioning it", "example (most liked)"])
    for issue in report.analysis.issues if report.analysis else []:
        issues.append([issue.term, issue.mentions, clean(issue.example)])
    topics = book.create_sheet("Topics")
    topics.append(["topic", "comments", "share", *summarize.SENTIMENTS, "keywords", "example"])
    for t in report.analysis.topics if report.analysis else []:
        topics.append(
            [t.label, t.comments, f"{t.share:.1f}%"]
            + [f"{t.sentiments[s]:.0f}%" for s in summarize.SENTIMENTS]
            + [", ".join(t.keywords), clean(t.example) if t.example else ""]
        )
    for sheet, widths in (
        (summary, (24, 80)),
        (comments, (17, 8, 8, 9, 10, 10, 7, 6, 9, 24, 90)),
        (issues, (24, 14, 90)),
        (topics, (30, 10, 8, 9, 9, 9, 40, 90)),
    ):
        for i, width in enumerate(widths, start=1):
            sheet.column_dimensions[get_column_letter(i)].width = width
    book.save(path)


def write_word(report: PostReport, images: dict[str, Path | None], path: Path) -> None:
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)
    doc.add_heading(report.title[:120], 0)
    meta = doc.add_paragraph()
    post = report.post
    for label, value in (
        ("Platform", post.platform.label),
        ("Posted by", post.author),
        (
            "Posted on",
            post.published_at.astimezone().strftime("%Y-%m-%d") if post.published_at else None,
        ),
        ("Link", post.url),
        ("Report made", datetime.now().strftime("%Y-%m-%d %H:%M")),
    ):
        if value:
            meta.add_run(f"{label}: ").bold = True
            meta.add_run(f"{value}\n")

    doc.add_heading("Summary", 1)
    doc.add_paragraph(report.coverage(), style="List Bullet")
    analysis = report.analysis
    if analysis is None:
        doc.add_paragraph("These comments haven't been analysed yet.", style="List Bullet")
    else:
        for line in summarize.summary_lines(analysis)[:-1]:
            doc.add_paragraph(line, style="List Bullet")

    doc.add_heading("Reactions and comments over time", 1)
    if report.snapshot is not None:
        numbers = [
            (k, v)
            for k, v in summary_rows(report)
            if k in ("Views", "Likes", "Comments reported") or k.startswith("Dislikes")
        ]
        _table(doc, ["", "Count"], [(k, f"{v:,}" if isinstance(v, int) else v) for k, v in numbers])
    for key in ("sentiment", "timeline"):
        image = images.get(key)
        if image is not None:
            doc.add_picture(str(image), width=Inches(6.3))

    if analysis is not None and analysis.topics:
        doc.add_heading("What comments talk about", 1)
        _table(
            doc,
            ["Topic", "Share", "Positive / neutral / negative", "Example (most liked)"],
            [
                (
                    t.label,
                    f"{t.share:.0f}%",
                    " / ".join(f"{t.sentiments[s]:.0f}%" for s in summarize.SENTIMENTS),
                    clean(t.example)[:300] if t.example else "",
                )
                for t in analysis.topics
            ],
        )

    if analysis is not None and analysis.issues:
        doc.add_heading("What negative comments mention most", 1)
        _table(
            doc,
            ["Issue", "Comments", "Example (most liked)"],
            [(i.term, i.mentions, clean(i.example)[:300]) for i in analysis.issues],
        )
        if images.get("issues"):
            doc.add_picture(str(images["issues"]), width=Inches(6.3))

    liked = report.most_liked()
    if liked:
        doc.add_heading("Most-liked comments", 1)
        rows = []
        for c in liked:
            result = report.results.get(c.id) if c.id is not None else None
            rows.append(
                (f"{c.like_count or 0:,}", result.sentiment if result else "", clean(c.text)[:400])
            )
        _table(doc, ["Likes", "Sentiment", "Comment"], rows)

    doc.add_heading("How these numbers were made", 1)
    trained = ", ".join(sorted(language_name(code) for code in TRAINED_LANGUAGES))
    for note in (
        f"Comments come from the platform itself. {report.coverage()}",
        "Sentiment comes from XLM-T, a model by Cardiff NLP trained on tweets in "
        f"{trained}. Comments in other languages, and ones the model was less than 50% sure "
        'about, are marked "unsure".',
        "Weighted by likes: each comment counts once plus once per like, so popular comments "
        "weigh more.",
        "Spam (the same long message from several commenters, or links with self-promotion) is "
        "left out of the shares.",
        "Topics group comments that say much the same thing, in any language, using "
        "paraphrase-multilingual-MiniLM (sentence-transformers). Each is named by the words "
        "most typical of it. Spam is left out, and comments that fit no group aren't shown.",
        "Issues are words and two-word phrases mentioned by at least two negative comments.",
        "Counts such as views and comments are the platform's own. Its comment count includes "
        "comments it doesn't hand out, so coverage can stay under 100%.",
        report.footer(),
    ):
        paragraph = doc.add_paragraph(note, style="List Bullet")
        for run in paragraph.runs:
            run.font.color.rgb = RGBColor(0x52, 0x51, 0x4E)
    doc.save(str(path))


def _table(doc: DocxDocument, header: list[str], rows: Sequence[Sequence[object]]) -> None:
    table = doc.add_table(rows=1, cols=len(header))
    table.style = "Light Grid Accent 1"
    for cell, text in zip(table.rows[0].cells, header, strict=True):
        cell.text = text
    for row in rows:
        for cell, value in zip(table.add_row().cells, row, strict=True):
            cell.text = str(value)
