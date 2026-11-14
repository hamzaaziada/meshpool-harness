"""Analysis guards: numbers must trace to executed code output; quotes must exist verbatim in sources."""
from __future__ import annotations

import re
import unicodedata

NUM = re.compile(r"(?<![\w.])[-−]?\$?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|(?<![\w.])[-−]?\$?\d+(?:\.\d+)?%?")
QUOTE = re.compile(r"[\"“]([^\"”]{12,})[\"”]")
TRIVIAL = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 100}


def _num(tok: str):
    t = tok.replace("−", "-").replace("$", "").replace(",", "")
    pct = t.endswith("%")
    t = t.rstrip("%")
    try:
        return float(t), pct
    except ValueError:
        return None, pct


def numbers_in(text: str) -> list[tuple[str, float, bool]]:
    out = []
    for m in NUM.finditer(text):
        v, pct = _num(m.group())
        if v is not None:
            out.append((m.group(), v, pct))
    return out


def _year(tok: str) -> bool:
    """A plain year such as 2024; '$2,045', '2045%' or '2045.0' are amounts, not years."""
    return re.fullmatch(r"(19|20)\d\d|2100", tok) is not None


def _decimals(tok: str) -> int:
    t = tok.rstrip("%")
    return len(t.split(".")[1]) if "." in t else 0


def trace_numbers(answer: str, code_output: str, rel_tol: float = 0.006) -> list[dict]:
    """Every non-trivial number in `answer` must match some number printed by executed code
    (allowing rounding, and percent <-> fraction). Returns untraced numbers."""
    logged = [v for _, v, _ in numbers_in(code_output)]
    missing = []
    for tok, v, pct in numbers_in(answer):
        if v in TRIVIAL or _year(tok):
            continue
        d = _decimals(tok)
        # (candidate, decimals it is written to): percent <-> fraction moves the decimal point two places
        cands = [(v, d), (v / 100, d + 2)] if pct else [(v, d), (v * 100, d - 2)]
        ok = False
        for c, nd in cands:
            for l in logged:
                if l == c or (c != 0 and abs(l - c) / abs(c) <= rel_tol):
                    ok = True
                    break
                if abs(round(l, nd) - c) <= 1e-9 * max(1.0, abs(c)):   # the answer shows the logged value rounded
                    ok = True
                    break
            if ok:
                break
        if not ok:
            missing.append({"check": "untraceable_number", "detail": tok})
    return missing


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


def check_quotes(answer: str, sources: dict[str, str]) -> list[dict]:
    """Every quoted passage (12+ chars) must appear verbatim (whitespace/quote-style normalised) in a source."""
    normed = {k: _norm(v) for k, v in sources.items()}
    bad = []
    for m in QUOTE.finditer(answer):
        q = _norm(m.group(1)).strip(" .,'")
        q = re.sub(r"\s*\.\.\.\s*|\s*…\s*", "\u0000", q)  # ellipsis: each fragment must match, in order
        parts = [p for p in q.split("\u0000") if p]
        hit = None
        for k, src in normed.items():
            pos, ok = 0, True
            for p in parts:
                i = src.find(p, pos)
                if i < 0:
                    ok = False
                    break
                pos = i + len(p)
            if ok:
                hit = k
                break
        if not hit:
            bad.append({"check": "fabricated_quote", "detail": m.group(1)[:120]})
    return bad
