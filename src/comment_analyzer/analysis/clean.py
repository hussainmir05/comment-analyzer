"""Text helpers shared by the analysis steps (ported from the previous version)."""

import html
import re
import unicodedata

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_MENTION = re.compile(r"(?<!\w)@[\w.\-]+")
_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")
_HANGUL = re.compile(r"[가-힣]")
RIGHT_QUOTE = chr(0x2019)  # the curly apostrophe that phones type
# Korean particles attach to the end of a word: "영상이", "영상을" and "영상" are one word.
_JOSA = (
    "에서는", "에게서", "으로는", "에서", "에게", "으로", "까지", "부터", "한테", "처럼",
    "보다", "이랑", "은", "는", "이", "가", "을", "를", "의", "에", "로", "와", "과", "도",
    "만", "랑",
)  # fmt: skip


def clean(text: str | None) -> str:
    """Readable text: HTML entities decoded, invisible control characters removed.

    Excel and Word reject control characters, so exports need this too.
    """
    return _CONTROL.sub("", html.unescape(text or "")).strip()


def words_of(text: str) -> list[str]:
    """Lower-case words: 3+ letters, or 2+ in non-Latin scripts. Links and @mentions are dropped,
    Korean particles stripped."""
    text = _URL.sub(" ", _MENTION.sub(" ", text.lower().replace(RIGHT_QUOTE, "'")))
    words = []
    for word in _WORD.findall(text):
        if _HANGUL.match(word):
            word = strip_josa(word)
        if len(word) >= 3 or (not word.isascii() and len(word) >= 2):
            words.append(word)
    return words


def strip_josa(word: str) -> str:
    for josa in _JOSA:
        if word.endswith(josa) and len(word) - len(josa) >= 2:
            return word[: -len(josa)]
    return word


def fingerprint(text: str) -> str:
    """Letters and digits only, lower case: the same message typed with other spacing or
    punctuation gets the same fingerprint."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", text).lower() if ch.isalnum())
