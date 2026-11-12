"""Captions to go with a card. Several wordings, so two accounts never post the same text.

Every caption keeps the coverage and "Comments are not a poll" (CLAUDE.md, Honest analysis).
"""

from comment_analyzer.analysis.summarize import mood
from comment_analyzer.reports.data import PostReport

VARIANTS = 4


def caption(report: PostReport, variant: int = 0) -> str:
    analysis = report.analysis
    title = report.title
    platform = report.post.platform.label
    if analysis is None or not analysis.counted:
        body = f'We collected comments on "{title}" ({platform}); the analysis is still to come.'
    else:
        positive = f"{analysis.shares['positive']:.0f}%"
        negative = f"{analysis.shares['negative']:.0f}%"
        feeling = mood(analysis.shares)
        issues = ", ".join(f'"{i.term}"' for i in analysis.issues[:3])
        complaint = f" The criticism mostly mentions {issues}." if issues else ""
        bodies = [
            f'What do people think of "{title}"? The comments are {feeling}: {positive} positive '
            f"and {negative} negative.{complaint}",
            f'{positive} of the comments on "{title}" are positive and {negative} are negative, '
            f"a {feeling} reaction on {platform}.{complaint}",
            f'We read the comments on "{title}" so you don\'t have to. Overall: {feeling} '
            f"({positive} positive, {negative} negative).{complaint}",
            f'The {platform} crowd on "{title}": {feeling}. Positive {positive}, negative '
            f"{negative}.{complaint}",
        ]
        body = bodies[variant % VARIANTS]
    return f"{body}\n\n{report.footer()}"
