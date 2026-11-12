"""What negative comments mention most (ported from the previous version's "criticized issues").

Counts how many comments mention each word or two-word phrase (not how many times). Phrases
never cross punctuation, and a word is skipped when a phrase containing it covers most of its
mentions ("audio" when "audio quality" says it). Unlike the previous version, a term needs at
least two comments: one comment is an opinion, not an issue.
"""

import itertools
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from comment_analyzer.analysis.clean import words_of

MIN_MENTIONS = 2
_PARTS = re.compile(r"[.,!?;:()\[\]\"\n]+")

# Function words and words about the platform itself, then plain mood words and thanks: issues
# are topics, and the mood is already in the sentiment. Topic names keep the mood words.
_FUNCTION_WORD_TEXT = """
    a about above after again against all also am an and any are aren't as at be because been
    before being below between both but by can can't cannot could couldn't did didn't do does
    doesn't doing don't dont down during each even ever every few for from further get got had
    hadn't has hasn't have haven't having he he'd he'll he's her here here's hers herself him
    himself his how how's however i i'd i'll i'm i've im if in into is isn't it it's its itself
    just let's like lot make made many me more most much must mustn't my myself no nor not now of
    off on once one only or other ought our ours ourselves out over own really same say said see
    shan't she she'd she'll she's should shouldn't so some still such than that that's thats the
    their theirs them themselves then there there's these they they'd they'll they're they've
    thing things think this those though through time to too under until up upon us use used
    very want was wasn't way we we'd we'll we're we've well were weren't what what's when when's
    where where's which while who who's whom why why's will with without won't would wouldn't
    yes yet you you'd you'll you're you've your yours yourself yourselves already always never
    nothing something anything everything someone anyone everyone people know going go gonna
    wanna got gotta even also ever bro guys man dude lol omg wow yeah yep okay ok
    try trying tried need needs needed look looks looking give gives giving take takes taking
    come comes came put keep let seem seems feel feels felt actually literally maybe probably
    definitely totally sure
    video videos channel youtube youtuber watch watching watched comment comments post page
    facebook instagram tiktok reel reels shorts short content creator please
"""
# Urdu and Hindi written in Latin letters ("yeh video bhi acha hai"), common in comments from
# South Asia: function words and forms of address. Two-letter words are already skipped.
_ROMAN_URDU_TEXT = """
    hai hain hay hy bhi aur nahi nhi nahin nai kya kia koi mein main yeh woh wo wala wali wale
    kar karo kare karna karte kiya raha rahe rahi tha thi the hota hoti hoga hum tum aap apna apni
    apne bhai bhae yaar yar sab bas sirf abhi kuch kyun kyon kiun jab tab phir fir kaise kese
    bohot bahut bht bhot ham hamara hamari tera teri mera meri uska uski unka unki inka iski
    usko isko liye lye sath saath par pe karega karegi karenge gaya gayi gaye diya liya
"""
_MOOD_WORD_TEXT = """
    thanks thank good bad great best worst better worse love loved hate hated nice awesome
    amazing terrible awful horrible beautiful perfect excellent wonderful fantastic cool stupid
    ridiculous disgusting pathetic sad happy angry funny agree disagree true false right wrong
    acha achha accha bura buri zabardast behtareen kamaal shukriya mashallah mashaallah
"""
FUNCTION_WORDS = frozenset(_FUNCTION_WORD_TEXT.split() + _ROMAN_URDU_TEXT.split())
STOPWORDS = FUNCTION_WORDS | frozenset(_MOOD_WORD_TEXT.split())


@dataclass(frozen=True, slots=True)
class Issue:
    term: str
    mentions: int  # comments mentioning it
    example: str  # the most-liked comment mentioning it


@dataclass(slots=True)
class _Pick:
    words: list[str]
    mentions: int
    example: str
    comments: set[int]  # which comments mention it


def terms_of(text: str, stopwords: frozenset[str]) -> set[str]:
    """The words and two-word phrases in a text, without stopwords. Phrases never cross
    punctuation."""
    terms: set[str] = set()
    for part in _PARTS.split(text):
        words = words_of(part)
        terms |= {w for w in words if w not in stopwords}
        terms |= {
            f"{a} {b}"
            for a, b in itertools.pairwise(words)
            if a != b and a not in stopwords and b not in stopwords
        }
    return terms


def criticized_issues(comments: list[tuple[str, int]], top: int = 10) -> list[Issue]:
    """comments: (text, likes) of the negative comments."""
    mentions: Counter[str] = Counter()
    where: defaultdict[str, set[int]] = defaultdict(set)
    example: dict[str, str] = {}
    for index, (text, _likes) in enumerate(sorted(comments, key=lambda c: -c[1])):
        for term in terms_of(text, STOPWORDS):
            mentions[term] += 1
            where[term].add(index)
            example.setdefault(term, text)  # the first one seen is the most liked
    phrases = [(t, n) for t, n in mentions.items() if " " in t and n >= MIN_MENTIONS]
    picks: list[_Pick] = []
    for term, count in sorted(mentions.items(), key=lambda item: (-item[1], item[0])):
        if count < MIN_MENTIONS:
            break
        covered = " " not in term and any(
            term in phrase.split() and n >= 0.8 * count for phrase, n in phrases
        )
        if covered or _joined(picks, term.split(), where[term]):
            continue
        picks.append(_Pick(term.split(), count, example[term], where[term]))
        if len(picks) == top:
            break
    return [Issue(" ".join(p.words), p.mentions, p.example) for p in picks]


def _joined(picks: list[_Pick], words: list[str], comments: set[int]) -> bool:
    """Join phrases that are pieces of one longer name ("dera ismail" + "ismail khan") when the
    same comments mention both."""
    if len(words) < 2:
        return False
    for pick in picks:
        if len(pick.words) < 2 or pick.comments != comments:
            continue
        if pick.words[-1] == words[0]:
            pick.words += words[1:]
            return True
        if words[-1] == pick.words[0]:
            pick.words = words + pick.words[1:]
            return True
    return False
