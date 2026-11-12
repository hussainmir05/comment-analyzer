"""Everything the dashboard, charts and exports need about one post, loaded in one go.

The dashboard (Plotly) and the exports (matplotlib, Word, Excel) draw from these same numbers,
so they never disagree.
"""

from collections import Counter
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from comment_analyzer.analysis import summarize
from comment_analyzer.analysis.summarize import PostAnalysis
from comment_analyzer.db import Database, repository
from comment_analyzer.models import AnalysisResult, CollectionRun, Comment, Post, Snapshot, Topic

CATEGORIES = ("positive", "neutral", "negative", "spam", "not analysed")
COLORS = {  # colour-blind safe: blue = good, red = bad, grey = neutral (the previous palette)
    "positive": "#2a78d6",
    "neutral": "#8a8984",
    "negative": "#e34948",
    "spam": "#d9a441",
    "not analysed": "#c8c7c2",
}
SORT_WORDS = {"relevance": "top comments first", "time": "newest first"}


@dataclass(slots=True)
class Timeline:
    unit: str  # "hour", "day", "week" or "month"
    starts: list[datetime]  # local time, one per bin, with no gaps
    counts: dict[str, list[int]]  # per category, one count per bin


@dataclass(slots=True)
class PostReport:
    post: Post
    snapshot: Snapshot | None
    run: CollectionRun | None
    comments: list[Comment]
    results: dict[int, AnalysisResult]
    snapshots: list[Snapshot] = field(default_factory=list)  # oldest first, for growth charts
    topics: list[Topic] = field(default_factory=list)  # biggest first
    analysis: PostAnalysis | None = field(init=False)
    _topic_labels: dict[int | None, str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.analysis = summarize.build(self.comments, self.results, self.topics)
        self._topic_labels = {t.id: t.label for t in self.topics}

    @property
    def title(self) -> str:
        return self.post.title or f"{self.post.platform.label} {self.post.post_id}"

    def category(self, comment: Comment) -> str:
        result = self.results.get(comment.id) if comment.id is not None else None
        if result is None or result.sentiment is None:
            return "not analysed"
        return "spam" if result.spam_flag else result.sentiment

    def topic(self, comment: Comment) -> str:
        result = self.results.get(comment.id) if comment.id is not None else None
        if result is None or result.topic_id is None:
            return ""
        return self._topic_labels.get(result.topic_id, "")

    def coverage(self) -> str:
        """Collected out of reported, the order used, and when."""
        run = self.run
        if run is None:
            return "Not collected yet."
        when = (run.finished_at or run.started_at).astimezone().strftime("%Y-%m-%d %H:%M")
        order = SORT_WORDS.get(run.sort_used or "", "unknown order")
        text = f"Collected {run.comments_collected:,} comments"
        if run.comments_reported:
            share = min(100, round(100 * run.comments_collected / run.comments_reported))
            text += (
                f" of the {run.comments_reported:,} {self.post.platform.label} reports ({share}%)"
            )
        return f"{text}, {order}, on {when}."

    def footer(self) -> str:
        counted = self.analysis.counted if self.analysis else len(self.comments)
        collected = max((c.collected_at for c in self.comments if c.collected_at), default=None)
        return summarize.footer(counted, collected)

    def most_liked(self, count: int = 10) -> list[Comment]:
        texts = [c for c in self.comments if c.text and self.category(c) != "spam"]
        return sorted(texts, key=lambda c: -(c.like_count or 0))[:count]

    def timeline(self) -> Timeline | None:
        return build_timeline(self.comments, self.category)


def load_post(db: Database, post_row_id: int) -> PostReport | None:
    with closing(db.connect()) as conn:
        post = repository.get_post_by_id(conn, post_row_id)
        if post is None:
            return None
        return PostReport(
            post=post,
            snapshot=repository.latest_snapshot(conn, post_row_id),
            run=repository.latest_run(conn, post_row_id),
            comments=repository.list_comments(conn, post_row_id),
            results=repository.analysis_for_post(conn, post_row_id),
            snapshots=repository.list_snapshots(conn, post_row_id),
            topics=repository.list_topics(conn, post_row_id),
        )


def build_timeline(
    comments: list[Comment], category_of: Callable[[Comment], str]
) -> Timeline | None:
    """Comments per hour, day, week or month (whichever suits the time span), per category."""
    dated = [
        (c.created_at.astimezone().replace(tzinfo=None), category_of(c))
        for c in comments
        if c.created_at
    ]
    if len(dated) < 2:
        return None
    first, last = min(t for t, _ in dated), max(t for t, _ in dated)
    span = last - first
    if span <= timedelta(days=2):
        unit = "hour"
    elif span <= timedelta(days=31):
        unit = "day"
    elif span <= timedelta(days=180):
        unit = "week"
    else:
        unit = "month"
    starts = [_floor(first, unit)]
    while starts[-1] < _floor(last, unit):
        starts.append(_next(starts[-1], unit))
    index = {start: i for i, start in enumerate(starts)}
    counts = {category: [0] * len(starts) for category in CATEGORIES}
    tally = Counter((_floor(t, unit), category) for t, category in dated)
    for (start, category), n in tally.items():
        counts[category][index[start]] += n
    return Timeline(unit, starts, counts)


def _floor(t: datetime, unit: str) -> datetime:
    if unit == "hour":
        return t.replace(minute=0, second=0, microsecond=0)
    day = t.replace(hour=0, minute=0, second=0, microsecond=0)
    if unit == "day":
        return day
    if unit == "week":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def _next(t: datetime, unit: str) -> datetime:
    if unit == "hour":
        return t + timedelta(hours=1)
    if unit == "day":
        return t + timedelta(days=1)
    if unit == "week":
        return t + timedelta(days=7)
    return t.replace(year=t.year + 1, month=1) if t.month == 12 else t.replace(month=t.month + 1)


def growth(report: PostReport) -> list[tuple[float, int]]:
    """(hours since posting, comments so far): for comparing posts of different ages."""
    times = sorted(c.created_at for c in report.comments if c.created_at)
    if not times:
        return []
    start = report.post.published_at or times[0]
    return [((t - start).total_seconds() / 3600, i + 1) for i, t in enumerate(times)]
