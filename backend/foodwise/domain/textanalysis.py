"""Feedback text analysis: sentiment and aspects for English, Hinglish and Hindi.

This is the lexicon baseline from the design: transparent, fast and dependency-free. The
contract (text in; sentiment, aspects, cleaned text out) is what a small multilingual model
or an LLM would implement later, so callers do not change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

POSITIVE = {
    # English
    "good": 1.0, "great": 1.4, "tasty": 1.3, "delicious": 1.5, "nice": 1.0, "fresh": 1.0, "hot": 0.6,
    "perfect": 1.4, "love": 1.4, "loved": 1.4, "excellent": 1.5, "yummy": 1.3, "awesome": 1.4, "best": 1.4,
    "enough": 0.5, "soft": 0.7, "crispy": 0.8, "flavourful": 1.2, "flavorful": 1.2, "amazing": 1.5,
    # Hinglish
    "accha": 1.0, "achha": 1.0, "acha": 1.0, "badhiya": 1.3, "badiya": 1.3, "mast": 1.3, "zabardast": 1.5,
    "swadisht": 1.4, "tasty-tha": 1.2, "bhot": 0.0, "garam": 0.6, "maza": 1.2, "mazaa": 1.2, "sahi": 0.9,
    # Hindi
    "अच्छा": 1.0, "अच्छी": 1.0, "बढ़िया": 1.3, "स्वादिष्ट": 1.4, "मस्त": 1.3, "गरम": 0.6, "ताज़ा": 1.0,
}
NEGATIVE = {
    "bad": -1.2, "cold": -0.9, "stale": -1.4, "bland": -1.0, "salty": -0.9, "oily": -0.9, "raw": -1.0,
    "undercooked": -1.2, "overcooked": -0.9, "burnt": -1.2, "hard": -0.6, "less": -0.6, "small": -0.5,
    "worst": -1.6, "terrible": -1.6, "awful": -1.5, "disgusting": -1.7, "smell": -1.1, "smells": -1.1,
    "spicy": -0.4, "watery": -0.9, "hair": -1.6, "insect": -1.8, "dirty": -1.5, "boring": -0.8, "again": -0.3,
    # Hinglish
    "bekar": -1.3, "bekaar": -1.3, "bakwas": -1.5, "bakwaas": -1.5, "thanda": -0.9, "thandi": -0.9,
    "kam": -0.6, "kachcha": -1.0, "kacha": -1.0, "teekha": -0.4, "tikha": -0.4, "jala": -1.2, "baasi": -1.4,
    "basi": -1.4, "ganda": -1.4, "kharab": -1.3, "phika": -1.0, "pheeka": -1.0,
    # Hindi
    "बेकार": -1.3, "ठंडा": -0.9, "ठंडी": -0.9, "कम": -0.6, "खराब": -1.3, "बासी": -1.4, "फीका": -1.0, "तीखा": -0.4,
}
NEGATORS = {"not", "no", "never", "nahi", "nahin", "nai", "mat", "नहीं", "ना"}
INTENSIFIERS = {"very": 1.4, "too": 1.3, "really": 1.3, "so": 1.2, "bahut": 1.4, "bohot": 1.4, "bhot": 1.4, "ekdum": 1.4, "बहुत": 1.4}

ASPECTS = {
    "taste": {"taste", "tasty", "bland", "salty", "salt", "namak", "spicy", "teekha", "tikha", "masala", "flavour", "flavor", "swad", "phika", "स्वाद", "नमक"},
    "quantity": {"quantity", "less", "kam", "portion", "small", "more", "zyada", "jyada", "enough", "मात्रा", "कम"},
    "temperature": {"cold", "thanda", "thandi", "hot", "garam", "warm", "ठंडा", "गरम"},
    "freshness": {"stale", "fresh", "baasi", "basi", "smell", "smells", "raw", "kachcha", "बासी", "ताज़ा"},
    "variety": {"again", "same", "repeat", "variety", "boring", "daily", "roz", "har"},
    "hygiene": {"hair", "insect", "dirty", "ganda", "clean", "keeda", "baal"},
}

PROFANITY = {"fuck", "shit", "bastard", "chutiya", "bc", "mc", "bsdk", "madarchod", "behenchod"}
PHONE_RE = re.compile(r"(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
TOKEN_RE = re.compile(r"[\wऀ-ॿ'-]+")


@dataclass
class TextResult:
    clean_text: str
    sentiment: float  # -1..1
    aspects: dict[str, float] = field(default_factory=dict)  # aspect -> -1..1
    language: str = "en"
    pii_removed: bool = False
    profanity: bool = False


def detect_language(text: str) -> str:
    if re.search(r"[ऀ-ॿ]", text):
        return "hi"
    hinglish_markers = {"tha", "thi", "hai", "nahi", "bahut", "accha", "kam", "thanda", "bekar", "mast", "zyada"}
    words = set(TOKEN_RE.findall(text.lower()))
    return "hinglish" if len(words & hinglish_markers) >= 1 else "en"


def scrub(text: str) -> tuple[str, bool, bool]:
    cleaned = PHONE_RE.sub("[phone removed]", text)
    cleaned = EMAIL_RE.sub("[email removed]", cleaned)
    pii = cleaned != text
    profane = False

    def mask(m: re.Match) -> str:
        nonlocal profane
        if m.group(0).lower() in PROFANITY:
            profane = True
            return "*" * len(m.group(0))
        return m.group(0)

    cleaned = TOKEN_RE.sub(mask, cleaned)
    return cleaned.strip()[:600], pii, profane


def analyze(text: str) -> TextResult:
    clean, pii, profane = scrub(text or "")
    tokens = [t.lower() for t in TOKEN_RE.findall(clean)]
    total, hits = 0.0, 0
    aspect_scores: dict[str, list[float]] = {}
    for i, tok in enumerate(tokens):
        value = POSITIVE.get(tok) or NEGATIVE.get(tok)
        if value is None or value == 0:
            continue
        window = tokens[max(0, i - 3) : i]
        after = tokens[i + 1 : i + 3]
        if any(w in NEGATORS for w in window) or any(w in {"nahi", "nahin", "nai", "नहीं"} for w in after):
            value = -value * 0.8
        for w in window:
            value *= INTENSIFIERS.get(w, 1.0)
        # "too spicy", "too salty" are complaints even though the base word is mild
        if any(w == "too" for w in window[-1:]) and value > -0.2:
            value = -0.8
        total += value
        hits += 1
        nearby = set(tokens[max(0, i - 4) : i + 5])
        for aspect, words in ASPECTS.items():
            if tok in words or nearby & words:
                aspect_scores.setdefault(aspect, []).append(value)
    sentiment = 0.0 if hits == 0 else max(-1.0, min(1.0, total / (hits + 1.0)))
    aspects = {a: round(max(-1.0, min(1.0, sum(v) / len(v))), 2) for a, v in aspect_scores.items()}
    for aspect, words in ASPECTS.items():
        if aspect not in aspects and set(tokens) & words:
            aspects[aspect] = 0.0
    return TextResult(clean, round(sentiment, 3), aspects, detect_language(text or ""), pii, profane)


def spam_score(text: str | None, recent_identical: int, burst_count: int) -> float:
    """0 (fine) .. 1 (almost certainly spam). Suspected spam is down-weighted, not dropped."""
    score = 0.0
    if text:
        letters = re.sub(r"\W", "", text)
        if letters and len(set(letters.lower())) <= 3 and len(letters) > 8:
            score += 0.5
        if re.search(r"https?://", text):
            score += 0.4
    score += min(0.5, 0.25 * recent_identical)
    score += min(0.4, 0.05 * max(0, burst_count - 10))
    return round(min(1.0, score), 2)
