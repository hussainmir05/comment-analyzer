"""Likely spam, by simple rules anyone can check by eye.

- Copies: the same longer message from two or more commenters, or posted three times or more.
- Promotion: a link together with words like "subscribe", "my channel" or "whatsapp".

Spam stays in the table but is left out of the sentiment shares.
"""

import re
from collections import Counter, defaultdict

from comment_analyzer.analysis.clean import fingerprint
from comment_analyzer.models import Comment

MIN_COPY_LENGTH = 30  # short messages ("love this song") match by chance, not by copying
_LINK = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|net|org|ly|io|me|xyz|gg)\b", re.IGNORECASE)
_PROMOTION = re.compile(
    r"\b(?:subscribe|sub4sub|check (?:out )?my|visit my|follow me|my channel|whats ?app|"
    r"telegram|dm me|giveaway|free (?:money|gift)|crypto|bitcoin|forex|invest(?:ment|ing)?)\b",
    re.IGNORECASE,
)


def spam_flags(comments: list[Comment]) -> dict[str, bool]:
    """Spam or not, by platform_comment_id. Copies are judged across all the given comments."""
    authors: defaultdict[str, set[str | None]] = defaultdict(set)
    copies: Counter[str] = Counter()
    keys = {}
    for comment in comments:
        key = fingerprint(comment.text or "")
        keys[comment.platform_comment_id] = key
        if len(key) >= MIN_COPY_LENGTH:
            authors[key].add(comment.author_hash)
            copies[key] += 1
    flags = {}
    for comment in comments:
        key = keys[comment.platform_comment_id]
        copied = len(key) >= MIN_COPY_LENGTH and (len(authors[key]) >= 2 or copies[key] >= 3)
        text = comment.text or ""
        promotion = bool(_LINK.search(text) and _PROMOTION.search(text))
        flags[comment.platform_comment_id] = copied or promotion
    return flags
