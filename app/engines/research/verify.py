"""Deterministic checks on a research report against its corpus (sources.json).

Rules (each failure is a problem the engine sends back to the model):
  R1 every citation [S3.p12] / [S3] refers to a stored passage / source;
  R2 every number in a sentence, bullet or table row is backed: it appears in a passage cited in that unit
     (scale-aware, within rounding), or it is the result of a [calc: expression] whose inputs are cited numbers;
  R3 every quotation of five or more words appears verbatim in a cited passage;
  R4 enough independent sources are cited (duplicates count once), at least one primary or reputable source
     (tier 1-2) when the corpus has one, and at most a quarter of citations from tier 4;
  R5 no number rests only on tier-4 sources;
  R6 the report ends with a Sources section that lists every cited source;
  R7 every number in the Answer section is verified: a primary or reputable (tier 1-2) passage states it, or two
     independent sources do (an existing report alone is a hypothesis, not a fact).
"""
from __future__ import annotations

import ast
import operator
import re

from .library import Corpus, Number, parse_numbers, same_number

CITE_RE = re.compile(r"\[((?:S\d+(?:\.p\d+)?)(?:\s*[;,]\s*S\d+(?:\.p\d+)?)*)\]")
CALC_RE = re.compile(r"\[calc:\s*([^\]]+)\]", re.I)
CONSTANTS = {0.0, 1.0, 2.0, 4.0, 12.0, 52.0, 100.0, 365.0, 1000.0, 1e6, 1e9}
QUOTE_RE = re.compile(r"[\"“]([^\"“”]{20,})[\"”]")


def citations_in(text: str) -> list[str]:
    out = []
    for m in CITE_RE.finditer(text):
        out += [c.strip() for c in re.split(r"[;,]", m.group(1)) if c.strip()]
    return out


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}


def safe_eval(expr: str) -> float:
    """Arithmetic only: numbers, + - * / ^ ** and parentheses (commas in numbers allowed)."""
    e = re.sub(r"(?<=\d),(?=\d{3}\b)", "", expr).replace("^", "**").replace("×", "*").replace("÷", "/")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        raise ValueError(f"not arithmetic: {expr}")
    return ev(ast.parse(e, mode="eval"))


def units(md: str) -> list[tuple[int, str]]:
    """(line number, text) checking units: sentences of prose paragraphs, each bullet, each table row. Headings,
    code, the Sources section and HTML comments are skipped."""
    md = md.split("\n## Sources")[0]
    out, in_code = [], False
    para: list[tuple[int, str]] = []

    def flush():
        if not para:
            return
        ln = para[0][0]
        text = " ".join(t for _, t in para)
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“(])", text):
            if s.strip():
                out.append((ln, s.strip()))
        para.clear()
    for i, raw in enumerate(md.split("\n"), start=1):
        line = raw.rstrip()
        if line.strip().startswith("```"):
            in_code = not in_code
            flush()
            continue
        if in_code or line.strip().startswith("<!--"):
            continue
        if not line.strip() or re.match(r"^\s*#{1,6}\s", line):
            flush()
            continue
        if re.match(r"^\s*\|", line):
            flush()
            if not re.match(r"^\s*\|[\s:|-]+\|\s*$", line):
                out.append((i, line.strip()))
            continue
        if re.match(r"^\s*([-*+]|\d+[.)])\s+", line):
            flush()
            out.append((i, re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", line)))
            continue
        para.append((i, line.strip()))
    flush()
    return out


def _claim_numbers(unit: str) -> list[Number]:
    """Numbers stated in a unit, without citation ids, calc expressions, dates, list markers and small counts."""
    t = CALC_RE.sub(" ", CITE_RE.sub(" ", unit))
    t = re.sub(r"\b(19|20)\d{2}[-/]\d{1,2}([-/]\d{1,2})?\b", " ", t)                 # dates
    t = re.sub(r"\b(FY|CY|Q[1-4]|H[12])\s?'?\d{2,4}\b", " ", t, flags=re.I)          # periods
    t = re.sub(r"\b\d{1,2}(st|nd|rd|th)\b", " ", t)
    out = []
    for n in parse_numbers(t):
        if not n.unit and n.scale == 1 and n.decimals == 0 and abs(n.value) < 10:
            continue                                                                  # "3 peers", "2 sources"
        out.append(n)
    return out


def check(report_md: str, corpus: Corpus, min_sources: int = 4) -> list[dict]:
    probs: list[dict] = []

    def bad(rule, line, detail):
        probs.append({"check": rule, "where": f"line {line}" if line else "report", "detail": detail})
    body = report_md.split("\n## Sources")[0]
    all_cites = citations_in(body)
    for c in sorted(set(all_cites)):
        if "." in c and c not in corpus.passages:
            bad("R1 unknown citation", None, f"[{c}] is not a stored passage (see sources.json / c.find())")
        elif "." not in c and c not in corpus.sources:
            bad("R1 unknown citation", None, f"[{c}] is not a stored source")
    tier_of = lambda c: corpus.sources[c.split(".")[0]].tier if c.split(".")[0] in corpus.sources else 9  # noqa
    for ln, u in units(report_md):
        cites = [c for c in citations_in(u) if c.split(".")[0] in corpus.sources]
        texts = []
        for c in cites:
            if "." in c:
                if c in corpus.passages:
                    texts.append((c, corpus.passages[c].text))
            else:                                               # source-level citation: all its passages
                texts += [(p.id, p.text) for p in corpus.passages.values() if p.source_id == c]
        src_nums = [n for pid, t in texts for n in parse_numbers(t, pid, keep_years=True)]
        calcs = []
        for m in CALC_RE.finditer(u):
            expr = m.group(1)
            try:
                calcs.append(safe_eval(expr))
            except (ValueError, SyntaxError, ZeroDivisionError) as e:
                bad("R2 bad calc", ln, f"[calc: {expr}] cannot be evaluated ({e}); use numbers and + - * / ^ only")
                continue
            for x in parse_numbers(expr, keep_years=True):
                if x.unit == "" and x.scale == 1 and x.value in CONSTANTS:
                    continue                                    # unit conversions: x100 for %, /1000, x12 months
                if not any(same_number(x, s) or _same_bare(x, s) for s in src_nums):
                    bad("R2 calc input not cited", ln, f"{x.raw} in [calc: {expr}] is not in the passages cited "
                        f"in this sentence ({', '.join(cites) or 'none'})")
        claims = _claim_numbers(u)
        if claims and not cites:
            bad("R2 uncited number", ln, f"'{_short(u)}' states {', '.join(n.raw for n in claims[:4])} without a "
                "citation; cite the passage [S#.p#] it comes from")
            continue
        for n in claims:
            ok = [s for s in src_nums if same_number(n, s) or _same_bare(n, s)]
            if not ok and any(_calc_match(n, v) for v in calcs):
                continue
            if not ok:
                bad("R2 number not in cited passages", ln, f"{n.raw} in '{_short(u)}' does not appear in "
                    f"{', '.join(cites)}; quote the figure as the source states it, cite the right passage, or "
                    "show the arithmetic as [calc: ...]")
            elif all(tier_of(s.passage_id or "") >= 4 for s in ok):
                bad("R5 weak source", ln, f"{n.raw} rests only on tier-4 sources ({', '.join(cites)}); confirm it "
                    "in a tier 1-3 source")
        for m in QUOTE_RE.finditer(u):
            q = _norm_q(m.group(1))
            if len(q.split()) >= 5 and not any(q in _norm_q(t) for _, t in texts):
                bad("R3 quote not verbatim", ln, f"\"{m.group(1)[:80]}\" is not verbatim in the cited passages")
    for ln, u in units(_answer_section(report_md)):            # R7: headline numbers are verified
        cites = [c for c in citations_in(u) if c.split(".")[0] in corpus.sources]
        for n in _claim_numbers(u):
            backers = set()
            for c in cites:
                pids = [c] if "." in c else [p.id for p in corpus.passages.values() if p.source_id == c]
                for pid in pids:
                    p = corpus.passages.get(pid)
                    if p and any(same_number(n, s) or _same_bare(n, s) for s in parse_numbers(p.text, pid, True)):
                        src = corpus.sources[p.source_id]
                        backers.add((src.duplicate_of or src.id, src.tier))
            if backers and min(t for _, t in backers) > 2 and len({b for b, _ in backers}) < 2:
                bad("R7 headline not verified", ln, f"{n.raw} in the Answer rests on one tier-3 source; confirm "
                    "it in a filing, release or reputable source, or in a second independent source "
                    "(c.corroborate)")
    indep = {corpus.sources[c.split(".")[0]].duplicate_of or c.split(".")[0] for c in all_cites
             if c.split(".")[0] in corpus.sources}
    usable = {s.duplicate_of or s.id for s in corpus.sources.values() if s.tier <= 3}
    need = min(min_sources, len(usable))
    if len(indep) < need:
        bad("R4 too few sources", None, f"{len(indep)} independent sources cited; cite at least {need} "
            "(syndicated copies count once)")
    if any(s.tier <= 2 for s in corpus.sources.values()) and not any(tier_of(c) <= 2 for c in all_cites):
        bad("R4 no primary source", None, "the corpus has primary or reputable sources (tier 1-2) but none is cited")
    if all_cites and sum(tier_of(c) >= 4 for c in all_cites) / len(all_cites) > 0.25:
        bad("R4 weak sourcing", None, "more than a quarter of citations are tier-4 (forums, user content)")
    if all_cites:
        m = re.search(r"\n## Sources\n(.*)\Z", report_md, re.S)
        if not m:
            bad("R6 no sources list", None, "end with the Sources section: run R.finalize(report_path, corpus)")
        else:
            missing = sorted({c.split(".")[0] for c in all_cites} - set(re.findall(r"\[(S\d+)\]", m.group(1))),
                             key=lambda x: int(x[1:]))
            if missing:
                bad("R6 sources list incomplete", None, f"Sources section misses {', '.join(missing)}; rerun "
                    "R.finalize")
    return probs


def _answer_section(md: str) -> str:
    """The '## Answer' (or '## Executive summary' / '## Summary') section, or the text before the first ## heading."""
    m = re.search(r"\n##\s*(answer|executive summary|summary|key findings)\b[^\n]*\n(.*?)(?=\n## |\Z)", md, re.I | re.S)
    if m:
        return m.group(2)
    return md.split("\n## ")[0]


def _same_bare(a: Number, b: Number) -> bool:
    """A report number written without scale matches a source number with one when the digits agree
    ('revenue of 4.2 [bn]' style tables whose header carries the unit)."""
    if a.scale != 1 or b.scale == 1:
        return False
    return abs(a.value - b.value / b.scale) <= max(abs(b.value / b.scale) * 0.006, 0.5 * 10 ** (-a.decimals))


def _calc_match(n: Number, v: float) -> bool:
    for cand in (v, v * 100):                      # a ratio shown as a percentage
        tol = max(abs(cand) * 0.006, 0.5 * 10 ** (-n.decimals) * n.scale)
        if abs(n.value - cand) <= tol:
            return True
    return False


def _norm_q(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


def _short(s: str, n: int = 90) -> str:
    s = CITE_RE.sub("", s).strip()
    return s if len(s) <= n else s[:n] + "..."


def report_text(probs: list[dict], limit: int = 15) -> str:
    return "\n".join(f"- {p['check']} ({p['where']}): {p['detail']}" for p in probs[:limit]) + (
        f"\n- ... and {len(probs) - limit} more" if len(probs) > limit else "")
