"""Spike alerts, by two rules simple enough to check by hand.

- Comment rate: the last hour has at least 10 comments and three times the hourly average of
  the 24 hours before it.
- Mood: of the last hour's analysed comments (at least 10), the negative share is 20 points or
  more above the post's overall negative share.
"""

from datetime import datetime, timedelta

from comment_analyzer.models import AnalysisResult, Comment

MIN_COMMENTS = 10
RATE_FACTOR = 3
NEGATIVE_JUMP = 20  # percentage points


def comment_spike(times: list[datetime], now: datetime) -> str | None:
    hour_ago = now - timedelta(hours=1)
    last_hour = sum(hour_ago < t <= now for t in times)
    before = sum(now - timedelta(hours=25) < t <= hour_ago for t in times)
    usual = max(before / 24, 1.0)
    if last_hour >= MIN_COMMENTS and last_hour >= RATE_FACTOR * usual:
        return (
            f"{last_hour} comments in the last hour, about {last_hour / usual:.0f} times the "
            "usual rate."
        )
    return None


def negative_spike(
    comments: list[Comment], results: dict[int, AnalysisResult], now: datetime
) -> str | None:
    def is_negative(comment: Comment) -> bool | None:
        result = results.get(comment.id) if comment.id is not None else None
        if result is None or result.sentiment is None or result.spam_flag:
            return None
        return result.sentiment == "negative"

    judged: list[tuple[Comment, bool]] = []
    for comment in comments:
        negative = is_negative(comment)
        if negative is not None:
            judged.append((comment, negative))
    recent = [n for c, n in judged if c.created_at and c.created_at > now - timedelta(hours=1)]
    if len(recent) < MIN_COMMENTS or not judged:
        return None
    recent_share = 100 * sum(recent) / len(recent)
    overall_share = 100 * sum(n for _, n in judged) / len(judged)
    if recent_share >= overall_share + NEGATIVE_JUMP:
        return (
            f"{recent_share:.0f}% of the last hour's comments are negative, against "
            f"{overall_share:.0f}% overall."
        )
    return None
