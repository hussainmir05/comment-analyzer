"""Chart images for exports, drawn with matplotlib's object API (no pyplot, so it's safe in a
worker thread). Colours and layout follow the previous version's charts."""

from pathlib import Path

import matplotlib
import matplotlib.dates as mdates
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from comment_analyzer.reports.data import CATEGORIES, COLORS, PostReport

INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e5e4e0", "#ffffff"
# Malgun Gothic draws Hangul; matplotlib falls back along the list glyph by glyph
matplotlib.rcParams["font.family"] = ["Segoe UI", "Malgun Gothic", "DejaVu Sans"]
matplotlib.rcParams.update(
    {
        "axes.edgecolor": MUTED,
        "axes.labelcolor": MUTED,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "text.color": INK,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
    }
)


def _figure(width: float, height: float) -> tuple[Figure, Axes]:
    figure = Figure(figsize=(width, height), dpi=150, facecolor=SURFACE)
    axes = figure.add_subplot()
    axes.set_facecolor(SURFACE)
    axes.spines[["top", "right"]].set_visible(False)
    axes.set_axisbelow(True)
    return figure, axes


def _save(figure: Figure, path: Path) -> Path:
    figure.savefig(path, facecolor=SURFACE, bbox_inches="tight")
    return path


def sentiment_chart(report: PostReport, path: Path) -> Path | None:
    analysis = report.analysis
    if analysis is None or not analysis.counted:
        return None
    figure, axes = _figure(10, 2.6)
    rows = [("Weighted by likes", analysis.weighted), ("Comments", analysis.shares)]
    for y, (_, shares) in enumerate(rows):
        left = 0.0
        for category in ("positive", "neutral", "negative"):
            value = shares[category]
            axes.barh(y, value, left=left, height=0.6, color=COLORS[category], label=category)
            if value >= 6:
                axes.text(
                    left + value / 2,
                    y,
                    f"{value:.0f}%",
                    ha="center",
                    va="center",
                    color="white",
                    fontsize=10,
                )
            left += value
    axes.set_yticks(range(len(rows)), [name for name, _ in rows])
    axes.set_xlim(0, 100)
    axes.xaxis.set_major_formatter(lambda value, _: f"{value:.0f}%")
    axes.grid(axis="x", color=GRID, linewidth=0.8)
    handles, labels = axes.get_legend_handles_labels()
    axes.legend(
        handles[:3],
        labels[:3],
        loc="upper center",
        bbox_to_anchor=(0.5, -0.25),
        ncols=3,
        frameon=False,
    )
    axes.set_title(f"Comment sentiment  ({analysis.counted:,} comments, spam left out)")
    return _save(figure, path)


def timeline_chart(report: PostReport, path: Path) -> Path | None:
    timeline = report.timeline()
    if timeline is None:
        return None
    figure, axes = _figure(12, 4)
    widths = {"hour": 1 / 24, "day": 0.8, "week": 5.6, "month": 24}
    starts = mdates.date2num(timeline.starts)
    bottom = [0] * len(timeline.starts)
    for category in CATEGORIES:
        counts = timeline.counts[category]
        if not any(counts):
            continue
        axes.bar(
            starts,
            counts,
            bottom=bottom,
            width=widths[timeline.unit] * 0.9,
            color=COLORS[category],
            label=category,
            align="edge",
        )
        bottom = [b + c for b, c in zip(bottom, counts, strict=True)]
    axes.xaxis_date()
    published = report.post.published_at
    if published is not None:
        local = published.astimezone().replace(tzinfo=None)
        if timeline.starts[0] <= local:
            x = float(mdates.date2num(local))
            axes.axvline(x, color=MUTED, linestyle="--", linewidth=1)
            axes.text(x, max(bottom), " posted", color=MUTED, fontsize=9, va="top")
    axes.yaxis.set_major_locator(MaxNLocator(integer=True))
    axes.grid(axis="y", color=GRID, linewidth=0.8)
    axes.set_ylabel("Comments")
    axes.legend(frameon=False, ncols=5, loc="upper right")
    axes.set_title(f"When people commented  (comments per {timeline.unit})")
    figure.autofmt_xdate()
    return _save(figure, path)


def issues_chart(report: PostReport, path: Path) -> Path | None:
    issues = report.analysis.issues if report.analysis else []
    if not issues:
        return None
    figure, axes = _figure(10, max(2.5, 0.45 * len(issues) + 1))
    shown = list(reversed(issues))
    axes.barh(
        [i.term for i in shown], [i.mentions for i in shown], height=0.6, color=COLORS["negative"]
    )
    for y, issue in enumerate(shown):
        axes.text(issue.mentions, y, f" {issue.mentions}", va="center", fontsize=10)
    axes.xaxis.set_major_locator(MaxNLocator(integer=True))
    axes.grid(axis="x", color=GRID, linewidth=0.8)
    axes.set_xlabel("Negative comments mentioning it")
    axes.set_title("What negative comments mention most")
    return _save(figure, path)
