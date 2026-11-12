"""Plain-language guard: Flesch-Kincaid grade level, long sentences, and jargon. Deterministic, no model call.

Target (user requirement): grade 8-9 at most, short unambiguous sentences, simple words.
"""
from __future__ import annotations

import re

MAX_GRADE = 9.0
MAX_SENTENCE_WORDS = 25
# common consulting/AI filler that hurts clarity -> simpler replacement
JARGON = {
    "leverage": "use", "utilize": "use", "utilise": "use", "synergy": "shared benefit", "synergies": "shared benefits",
    "holistic": "complete", "paradigm": "model", "robust": "strong", "seamless": "smooth", "seamlessly": "smoothly",
    "facilitate": "help", "operationalize": "put in place", "actionable": "practical", "delve": "look",
    "furthermore": "also", "moreover": "also", "notwithstanding": "despite", "aforementioned": "this",
    "endeavor": "try", "commence": "start", "ascertain": "find out", "in order to": "to", "a plethora of": "many",
    "cutting-edge": "new", "best-in-class": "leading", "game-changer": "big change", "tapestry": "mix",
    "landscape": "market", "underscore": "show", "underscores": "shows", "pivotal": "key", "myriad": "many",
}


def _syllables(word: str) -> int:
    w = word.lower().strip(".,;:!?\"'()[]")
    if not w:
        return 0
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    return max(1, len(re.findall(r"[aeiouy]{1,2}", w)))


def _clean(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)          # code blocks
    text = re.sub(r"`[^`]*`", " x ", text)                        # inline code
    text = re.sub(r"https?://\S+|\S+\.(py|csv|xlsx|pptx|png|json)\b", " x ", text)
    text = re.sub(r"[$€£]?\d[\d,.]*%?[KMBkmb]?", " 5 ", text)        # numbers count as one short word
    text = re.sub(r"^\s*[-*•]\s*", "", text, flags=re.M)           # bullets end sentences
    return text


def _prose(s: str) -> bool:
    """Skip code, JSON, tables, and key=value lines: readability rules apply to prose only."""
    if re.search(r"[{}\[\]=<>|]|\w+\(|^\s*\w+:\s*\S+$", s):
        return False
    letters = sum(c.isalpha() for c in s)
    return letters >= 0.6 * max(1, len(s.replace(" ", "")))


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", _clean(text))
    return [p.strip() for p in parts if len(p.split()) >= 3 and _prose(p)]


def grade(text: str) -> float:
    ss = sentences(text)
    words = [w for s in ss for w in s.split()]
    if len(words) < 30:
        return 0.0       # too short to score reliably
    syl = sum(_syllables(w) for w in words)
    return 0.39 * len(words) / len(ss) + 11.8 * syl / len(words) - 15.59


def check(text: str, max_grade: float = MAX_GRADE) -> list[dict]:
    issues = []
    g = grade(text)
    if g > max_grade:
        issues.append({"check": "reading_level", "detail": f"grade {g:.1f} (max {max_grade}); use shorter sentences "
                                                            f"and simpler words"})
    for s in sentences(text):
        if len(s.split()) > MAX_SENTENCE_WORDS:
            issues.append({"check": "long_sentence", "detail": f"{len(s.split())} words: {s[:90]}..."})
    low = text.lower()
    for j, rep in JARGON.items():
        if re.search(rf"\b{re.escape(j)}\b", low):
            issues.append({"check": "jargon", "detail": f"'{j}' -> '{rep}'"})
    return issues
