"""The plain-language summary of a post's analysis.

It follows the honest-analysis rules in CLAUDE.md:
- shares are shown both unweighted and weighted by likes;
- spam and low-confidence results are counted openly;
- every summary ends with "Comments are not a poll".
"""

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from comment_analyzer.analysis.issues import Issue, criticized_issues
from comment_analyzer.analysis.language import UNKNOWN, language_name
from comment_analyzer.models import AnalysisResult, Comment, Topic

SENTIMENTS = ("positive", "neutral", "negative")
MOOD_GAP = 15  # percentage points between positive and negative needed for "mostly ..."
HEAVY_COMMENT = 20  # percent of the like weight that makes one comment worth pointing out


@dataclass(slots=True)
class TopicShare:
    label: str
    keywords: list[str]
    comments: int
    share: float  # percent of the comments that were grouped
    sentiments: dict[str, float]  # percent of the topic's comments
    example: str | None  # its most-liked comment
    summary: str | None = None  # Claude mode writes one


@dataclass(slots=True)
class PostAnalysis:
    analysed: int  # comments with a sentiment, spam included
    counted: int  # without spam: the base of the shares
    spam: int
    low_confidence: int
    shares: dict[str, float]  # percent of the counted comments
    weighted: dict[str, float]  # percent of the like weight (1 + likes per comment)
    top_weight: float  # percent of the like weight held by the most-liked comment
    languages: list[tuple[str, int]]
    issues: list[Issue]
    collected_at: datetime | None
    topics: list[TopicShare] = field(default_factory=list)  # biggest first


def build(
    comments: list[Comment], results: dict[int, AnalysisResult], topics: Sequence[Topic] = ()
) -> PostAnalysis | None:
    """None when none of the comments has been analysed yet."""
    pairs = [
        (c, results[c.id])
        for c in comments
        if c.id is not None and c.id in results and results[c.id].sentiment
    ]
    if not pairs:
        return None
    counted = [(c, r) for c, r in pairs if not r.spam_flag]
    counts = Counter(r.sentiment for _, r in counted)
    weights: Counter[str | None] = Counter()
    for comment, result in counted:
        weights[result.sentiment] += 1 + (comment.like_count or 0)
    total_weight = sum(weights.values())
    heaviest = max((1 + (c.like_count or 0) for c, _ in counted), default=0)
    negative = [
        (c.text, c.like_count or 0) for c, r in counted if r.sentiment == "negative" and c.text
    ]
    return PostAnalysis(
        analysed=len(pairs),
        counted=len(counted),
        spam=len(pairs) - len(counted),
        low_confidence=sum(r.low_confidence for _, r in counted),
        shares=_percent(counts, len(counted)),
        weighted=_percent(weights, total_weight),
        top_weight=100 * heaviest / total_weight if total_weight else 0.0,
        languages=Counter(c.lang or UNKNOWN for c, _ in pairs).most_common(),
        issues=criticized_issues(negative),
        collected_at=max((c.collected_at for c, _ in pairs if c.collected_at), default=None),
        topics=_topic_shares(counted, topics),
    )


def _topic_shares(
    counted: list[tuple[Comment, AnalysisResult]], topics: Sequence[Topic]
) -> list[TopicShare]:
    members: defaultdict[int, list[tuple[Comment, AnalysisResult]]] = defaultdict(list)
    for comment, result in counted:
        if result.topic_id is not None:
            members[result.topic_id].append((comment, result))
    shares = []
    for topic in topics:
        group = members.get(topic.id or -1)
        if not group:
            continue
        counts = Counter(r.sentiment for _, r in group)
        liked = max((c for c, _ in group if c.text), key=lambda c: c.like_count or 0, default=None)
        shares.append(
            TopicShare(
                label=topic.label,
                keywords=topic.keywords,
                comments=len(group),
                share=100 * topic.share,
                sentiments=_percent(counts, len(group)),
                example=liked.text if liked else None,
                summary=topic.summary,
            )
        )
    return shares


def _percent(counter: Counter[str | None], total: int) -> dict[str, float]:
    return {s: 100 * counter[s] / total if total else 0.0 for s in SENTIMENTS}


def mood(shares: dict[str, float]) -> str:
    gap = shares["positive"] - shares["negative"]
    if gap >= MOOD_GAP:
        return "mostly positive"
    if gap <= -MOOD_GAP:
        return "mostly negative"
    return "mixed"


def summary_lines(analysis: PostAnalysis) -> list[str]:
    a = analysis
    lines = []
    if a.counted:
        lines.append(f"Mood: {mood(a.shares)}. {_shares(a.shares)} ({a.counted:,} comments).")
        weighted = f"Weighted by likes: {_shares(a.weighted)}."
        if a.top_weight >= HEAVY_COMMENT:
            weighted += f" The most-liked comment alone carries {a.top_weight:.0f}% of that weight."
        lines.append(weighted)
    if a.topics:
        talk = "; ".join(f"{t.label} ({t.share:.0f}%)" for t in a.topics[:4])
        lines.append(f"Comments talk most about: {talk}.")
    if a.issues:
        mentions = ", ".join(f'"{i.term}" ({i.mentions})' for i in a.issues[:5])
        lines.append(f"Negative comments most often mention: {mentions}.")
    languages = ", ".join(
        f"{language_name(code)} {100 * n / a.analysed:.0f}%" for code, n in a.languages[:4]
    )
    lines.append(f"Languages: {languages}.")
    if a.spam:
        lines.append(f"{_count(a.spam, 'comment')} look like spam and are left out of the shares.")
    if a.low_confidence:
        lines.append(
            f"{_count(a.low_confidence, 'comment')} are in languages the model wasn't trained "
            'on or were hard to read, so their mood is less certain (marked "unsure").'
        )
    lines.append(footer(a.counted, a.collected_at))
    return lines


def second_opinion(
    comments: list[Comment],
    local: dict[int, AnalysisResult],
    claude: dict[int, AnalysisResult],
    topics: Sequence[Topic] = (),
) -> list[str]:
    """Claude mode's reading, and how often it agrees with the local model."""
    analysis = build(comments, claude, topics)
    if analysis is None or not analysis.counted:
        return []
    first = next(iter(claude.values()))
    when = max(r.analyzed_at for r in claude.values()).astimezone()
    both = [
        i
        for i, r in claude.items()
        if not r.spam_flag and i in local and local[i].sentiment is not None
    ]
    line = (
        f"Second opinion from Claude ({first.model_version}, {when:%Y-%m-%d %H:%M}): "
        f"{_shares(analysis.shares)} ({analysis.counted:,} comments)."
    )
    if both:
        agree = 100 * sum(local[i].sentiment == claude[i].sentiment for i in both) / len(both)
        line += f" It agrees with the local model on {agree:.0f}% of them."
    lines = [line]
    for topic in analysis.topics[:5]:
        about = f": {topic.summary}" if topic.summary else ""
        lines.append(f"  - {topic.label} ({topic.share:.0f}%){about}")
    return lines


def footer(count: int, collected_at: datetime | None) -> str:
    when = collected_at.astimezone().strftime("%Y-%m-%d %H:%M") if collected_at else "earlier"
    return f"Based on {count:,} public comments collected {when}. Comments are not a poll."


def _shares(shares: dict[str, float]) -> str:
    return ", ".join(f"{shares[s]:.0f}% {s}" for s in SENTIMENTS)


def _count(n: int, noun: str) -> str:
    return f"{n:,} {noun}" if n == 1 else f"{n:,} {noun}s"
