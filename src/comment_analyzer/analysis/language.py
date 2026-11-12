"""The language of each comment, from fastText's compact language-ID model (offline).

Very short comments and ones with no letters (emoji, numbers) are "und": too little to tell.
"""

import re

from fast_langdetect import detect

UNKNOWN = "und"
MIN_LETTERS = 3
MIN_SCORE = 0.4  # below this, fastText is mostly guessing
SHORT_TEXT = 15  # letters; short texts are easily mistaken ("so true" read as Bulgarian)
MIN_SCORE_SHORT = 0.8

_LETTER = re.compile(r"[^\W\d_]")

NAMES = {
    "af": "Afrikaans",
    "am": "Amharic",
    "ar": "Arabic",
    "az": "Azerbaijani",
    "bg": "Bulgarian",
    "bn": "Bengali",
    "ca": "Catalan",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "et": "Estonian",
    "fa": "Persian",
    "fi": "Finnish",
    "fr": "French",
    "gu": "Gujarati",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "kn": "Kannada",
    "ko": "Korean",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "ml": "Malayalam",
    "mr": "Marathi",
    "ms": "Malay",
    "my": "Burmese",
    "ne": "Nepali",
    "nl": "Dutch",
    "no": "Norwegian",
    "pa": "Punjabi",
    "pl": "Polish",
    "ps": "Pashto",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "si": "Sinhala",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sr": "Serbian",
    "sv": "Swedish",
    "sw": "Swahili",
    "ta": "Tamil",
    "te": "Telugu",
    "th": "Thai",
    "tl": "Tagalog",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "uz": "Uzbek",
    "vi": "Vietnamese",
    "zh": "Chinese",
    UNKNOWN: "unknown (emoji or very short)",
}


def detect_language(text: str) -> tuple[str, float]:
    """(language code, score). The code is "und" when there's too little text to tell."""
    letters = len(_LETTER.findall(text))
    if letters < MIN_LETTERS:
        return UNKNOWN, 0.0
    best = detect(" ".join(text.split()), model="lite")[0]
    language, score = str(best["lang"]), float(best["score"])
    needed = MIN_SCORE_SHORT if letters < SHORT_TEXT else MIN_SCORE
    return (language, score) if score >= needed else (UNKNOWN, score)


def language_name(code: str) -> str:
    return NAMES.get(code, code)
