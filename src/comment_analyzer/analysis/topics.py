"""Topics: groups of comments that say much the same thing, named by their typical words.

1. Each comment becomes a vector (embeddings.py). Comments that mean the same, in any language,
   get nearby vectors.
2. Groups are "communities", the fast clustering of sentence-transformers. A comment with at least
   `min_size` comments within THRESHOLD of it starts a group. The biggest groups are picked first,
   and each comment joins only one. Comments that fit no group stay "other".
   HDBSCAN would need scikit-learn and SciPy, about 150 MB more in the app, for no clear gain on
   short comments.
3. Each group is named by c-TF-IDF, as in BERTopic: the words common in that group and rare in
   the others.

Posts with fewer than MIN_COMMENTS comments get no topics. "What negative comments mention most"
covers them.
"""

import math
from collections import Counter
from dataclasses import dataclass

import numpy as np

from comment_analyzer.analysis.issues import FUNCTION_WORDS, terms_of

MIN_COMMENTS = 30
THRESHOLD = 0.55  # cosine similarity; paraphrases score about 0.7 and more with this model
MAX_TOPICS = 8
KEYWORDS = 5
LABEL_WORDS = 3
NO_WORDS = "Reactions without words"  # emoji, or words too short to name


@dataclass(slots=True)
class FoundTopic:
    label: str
    keywords: list[str]
    members: list[int]  # indexes into the texts, closest to the group's centre first


def min_size(count: int) -> int:
    """Smallest group worth naming: 3 comments, rising to 25 for big posts."""
    return max(3, min(25, count // 40))


def find_topics(texts: list[str], vectors: np.ndarray) -> list[FoundTopic]:
    """texts and vectors (unit length, one row per text) in the same order. Biggest first."""
    if len(texts) < MIN_COMMENTS:
        return []
    groups = communities(vectors, THRESHOLD, min_size(len(texts)))[:MAX_TOPICS]
    keywords = name_groups(texts, groups)
    return [
        FoundTopic(", ".join(words[:LABEL_WORDS]) if words else NO_WORDS, words, members)
        for members, words in zip(groups, keywords, strict=True)
    ]


def communities(vectors: np.ndarray, threshold: float, min_size: int) -> list[list[int]]:
    """Biggest first."""
    similar = vectors @ vectors.T
    near = similar >= threshold
    candidates = []
    for centre in np.flatnonzero(near.sum(axis=1) >= min_size):
        members = np.flatnonzero(near[centre])
        candidates.append(members[np.argsort(-similar[centre, members], kind="stable")])
    candidates.sort(key=len, reverse=True)
    taken = np.zeros(len(vectors), dtype=bool)
    groups = []
    for members in candidates:
        free = members[~taken[members]]
        if len(free) >= min_size:
            taken[free] = True
            groups.append([int(i) for i in free])
    return sorted(groups, key=len, reverse=True)


def name_groups(texts: list[str], groups: list[list[int]]) -> list[list[str]]:
    """c-TF-IDF keywords for each group. The comments in no group count as one more class, so
    words everyone uses score low. Each comment counts once per word. A group where most
    comments have no words to name it by gets no keywords."""
    grouped = {i for members in groups for i in members}
    other = [i for i in range(len(texts)) if i not in grouped]
    terms = {i: terms_of(texts[i], FUNCTION_WORDS) for i in range(len(texts))}
    classes = [Counter(t for i in members for t in terms[i]) for members in [*groups, other]]
    everywhere = sum(classes, Counter[str]())
    average = sum(everywhere.values()) / len(classes)
    names: list[list[str]] = []
    for counter, members in zip(classes, groups, strict=False):  # "other" gets no name
        if 2 * sum(1 for i in members if terms[i]) < len(members):
            names.append([])
            continue
        size = sum(counter.values())
        needed = 2 if len(members) > 3 and counter.most_common(1)[0][1] >= 2 else 1
        scores = {
            term: (n / size) * math.log(1 + average / everywhere[term])
            for term, n in counter.items()
            if n >= needed
        }
        where = {term: {i for i in members if term in terms[i]} for term in scores}
        names.append(_pick(sorted(scores, key=lambda t: (-scores[t], t)), where))
    return names


def _pick(ranked: list[str], where: dict[str, set[int]]) -> list[str]:
    """The best KEYWORDS terms. A word already said by a picked phrase is skipped, a phrase
    replaces its words, and phrases that are pieces of one name in the same comments are joined
    ("dera ismail" + "ismail khan")."""
    picked: list[list[str]] = []
    for term in ranked:
        words = term.split()
        said = {w.removesuffix("s") for p in picked for w in p}  # "thank" after "thanks"
        if all(w.removesuffix("s") in said for w in words):
            continue
        if len(words) == 2:
            joined = next((p for p in picked if _same_name(p, words, where)), None)
            if joined is not None:
                if joined[-1] == words[0]:
                    joined.append(words[1])
                else:
                    joined.insert(0, words[0])
                continue
            picked = [p for p in picked if p[0] not in words or len(p) > 1]
        picked.append(words)
        if len(picked) == KEYWORDS:
            break
    return [" ".join(p) for p in picked]


def _same_name(picked: list[str], words: list[str], where: dict[str, set[int]]) -> bool:
    if len(picked) < 2 or not (picked[-1] == words[0] or words[-1] == picked[0]):
        return False
    edge = " ".join(picked[-2:] if picked[-1] == words[0] else picked[:2])
    return where.get(edge) == where[" ".join(words)]
