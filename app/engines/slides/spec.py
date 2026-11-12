"""Deck spec: the only thing a model writes. Validation returns precise, fixable errors.

Deck:  {"title": str, "slides": [Slide, ...], "requested_slide_count": int|null}
Slide: {"type": one of TYPES, "title": str, ..., "notes": str?, "source": str?}
"""
from __future__ import annotations

TYPES = {
    "title":      {"req": ["title"], "opt": ["subtitle", "meta"]},
    "section":    {"req": ["title"], "opt": ["number", "subtitle"]},
    "bullets":    {"req": ["title", "bullets"], "opt": ["callout"]},
    "two_column": {"req": ["title", "left", "right"], "opt": []},
    "chart":      {"req": ["title", "chart"], "opt": ["takeaways"]},
    "table":      {"req": ["title", "columns", "rows"], "opt": ["highlight_row", "takeaway"]},
    "kpi":        {"req": ["title", "kpis"], "opt": ["takeaway"]},
    "timeline":   {"req": ["title", "items"], "opt": []},
    "quote":      {"req": ["quote"], "opt": ["attribution", "title"]},
    "image_text": {"req": ["title", "image", "bullets"], "opt": []},
}
from .archetypes import ARCHETYPES as _A
TYPES.update({k: v[0] for k, v in _A.items()})
COMMON = {"type", "notes", "source", "section_tag"}
CHART_TYPES = {"bar", "column", "stacked_column", "line", "pie", "doughnut"}
MAX_BULLETS = 5
MAX_BULLET_WORDS = 15
MAX_TITLE_WORDS = 20


def _bullet_text(b):
    return b if isinstance(b, str) else (b.get("text", "") if isinstance(b, dict) else "")


def validate(deck: dict) -> tuple[list[str], list[str]]:
    """Returns (errors, warnings). Errors block rendering; warnings are style-rule breaks."""
    errs, warns = [], []
    if not isinstance(deck, dict) or not isinstance(deck.get("slides"), list) or not deck["slides"]:
        return ["deck must be an object with a non-empty 'slides' list"], []
    for i, s in enumerate(deck["slides"], 1):
        p = f"slide {i}"
        t = s.get("type") if isinstance(s, dict) else None
        if t not in TYPES:
            errs.append(f"{p}: type must be one of {sorted(TYPES)}, got {t!r}")
            continue
        allowed = set(TYPES[t]["req"]) | set(TYPES[t]["opt"]) | COMMON
        for k in TYPES[t]["req"]:
            if s.get(k) in (None, "", []):
                errs.append(f"{p} ({t}): missing required field '{k}'")
        for k in s:
            if k not in allowed:
                errs.append(f"{p} ({t}): unknown field '{k}'; allowed {sorted(allowed)}")
        title = s.get("title") or ""
        if t not in ("title", "section", "quote") and title and len(title.split()) < 4:
            warns.append(f"{p}: title '{title}' is a label; use an action title that states the takeaway")
        if len(title.split()) > MAX_TITLE_WORDS:
            warns.append(f"{p}: title has {len(title.split())} words (max {MAX_TITLE_WORDS})")
        for key in ("bullets",):
            if key in s and isinstance(s[key], list):
                for j, b in enumerate(s[key], 1):
                    if not _bullet_text(b):
                        errs.append(f"{p}: bullet {j} is empty or not a string/{{text,sub}}")
                    elif len(_bullet_text(b).split()) > MAX_BULLET_WORDS:
                        warns.append(f"{p}: bullet {j} has {len(_bullet_text(b).split())} words (max {MAX_BULLET_WORDS})")
        if t == "two_column":
            for side in ("left", "right"):
                col = s.get(side)
                if not isinstance(col, dict) or not isinstance(col.get("bullets"), list):
                    errs.append(f"{p}: '{side}' must be {{heading, bullets[]}}")
        if t == "chart":
            c = s.get("chart") or {}
            if c.get("type") not in CHART_TYPES:
                errs.append(f"{p}: chart.type must be one of {sorted(CHART_TYPES)}")
            cats, series = c.get("categories"), c.get("series")
            if not isinstance(cats, list) or not cats:
                errs.append(f"{p}: chart.categories must be a non-empty list")
            if not isinstance(series, list) or not series:
                errs.append(f"{p}: chart.series must be a non-empty list of {{name, values}}")
            else:
                for k, se in enumerate(series, 1):
                    vals = se.get("values") if isinstance(se, dict) else None
                    if not isinstance(vals, list) or (cats and len(vals) != len(cats)):
                        errs.append(f"{p}: series {k} needs {len(cats or [])} numeric values")
                    elif not all(isinstance(v, (int, float)) for v in vals):
                        errs.append(f"{p}: series {k} values must be numbers (no strings, no %)")
        if t == "table":
            cols, rows = s.get("columns") or [], s.get("rows") or []
            for k, r in enumerate(rows, 1):
                if not isinstance(r, list) or len(r) != len(cols):
                    errs.append(f"{p}: row {k} must have {len(cols)} cells")
            if len(rows) > 12:
                warns.append(f"{p}: table has {len(rows)} rows; it will be split across slides")
        if t == "kpi":
            k = s.get("kpis") or []
            if not 2 <= len(k) <= 4:
                errs.append(f"{p}: kpi slide needs 2-4 kpis, got {len(k)}")
            for j, x in enumerate(k, 1):
                if not isinstance(x, dict) or not x.get("value") or not x.get("label"):
                    errs.append(f"{p}: kpi {j} needs 'value' and 'label'")
        if t == "timeline":
            it = s.get("items") or []
            if not 2 <= len(it) <= 7:
                errs.append(f"{p}: timeline needs 2-7 items, got {len(it)}")
    want = deck.get("requested_slide_count")
    if isinstance(want, int) and want != len(deck["slides"]):
        errs.append(f"deck has {len(deck['slides'])} slides but {want} were requested")
    return errs, warns


def normalize(deck: dict) -> dict:
    """Deterministic splits so the renderer never has to overflow: >5 bullets and >12 table rows."""
    out = []
    for s in deck["slides"]:
        if s.get("type") == "bullets" and len(s["bullets"]) > MAX_BULLETS:
            b = s["bullets"]
            for k in range(0, len(b), MAX_BULLETS):
                part = dict(s, bullets=b[k:k + MAX_BULLETS])
                if k:
                    part["title"] = s["title"] + " (cont.)"
                    part.pop("callout", None)
                out.append(part)
        elif s.get("type") == "table" and len(s.get("rows", [])) > 12:
            r = s["rows"]
            for k in range(0, len(r), 12):
                part = dict(s, rows=r[k:k + 12])
                if k:
                    part["title"] = s["title"] + " (cont.)"
                out.append(part)
        else:
            out.append(s)
    return dict(deck, slides=out)
