"""Deck density modes: one deck is either presented, read, or something in between.

    present   presenter-led: one message per slide, the title carries the point, one big exhibit and at most ~3 short
              bullets (~40 body words), larger type; the detail goes to the speaker notes.
    balanced  the default: lighter than a send-ahead (~60-110 body words, at most 4-5 bullets, body 12-14 pt).
    read      the dense send-ahead deliverable: full sentences, footnotes, dense tables and matrices.

detect() reads the mode from a request (and an explicit "density" option); condense() is the renderer's safety net:
it trims a slide the content model wrote too densely for its mode and moves what it cuts into the speaker notes, so
nothing is lost and nothing is invented. Type scales per mode live here too (applied by render.py)."""
from __future__ import annotations

import copy
import json
import re

MODES = ("present", "balanced", "read")
DEFAULT = "balanced"

# ------------------------------------------------------------------ detection
_EXPLICIT = re.compile(r"""(?ix)["']?density["']?\s*[:=]\s*["']?(present|presenting|presentation|balanced|read|reading)\b
                           |--density[ =](present|balanced|read)\b
                           |\b(present|balanced|read)\s+(?:density|mode)\b""")
_STRONG_PRESENT = re.compile(r"(?i)\bfor present(?:ing|ation)\b|\bto present\b|\bpresenter[- ]led\b|\bkeynote\b|"
                             r"\btalk[- ]track\b|\bon stage\b|\bspeaker notes\b|\bpresent(?:ed)? live\b|\bpitch(?: deck)?\b|"
                             r"\bnot (?:too |so |as )?(?:dense|busy|wordy)\b|\b(?:less|too) (?:dense|busy|wordy)\b|"
                             r"\bfewer words\b|\bpresentable\b")
_WEAK_PRESENT = re.compile(r"(?i)\bpresentation\b|\bvisual(?:ly)?\b|\bpresent\b|\bclean\b|\bminimal(?:ist)?\b|"
                           r"\bsimple\b|\bpresenting\b")
_STRONG_READ = re.compile(r"(?i)\bsend[- ]ahead\b|\bpre[- ]?read\b|\bread[- ]ahead\b|\bleave[- ]behind\b|"
                          r"\bdeliverable\b|\breading deck\b|\bto be read\b|\bfor reading\b|\bread on (?:its|their) own\b|"
                          r"\bstand[- ]?alone\b|\bcontent[- ]heavy\b|\btext[- ]heavy\b|\bdocument[- ]style\b")
_WEAK_READ = re.compile(r"(?i)\bdetailed\b|\b(?<!too )(?<!less )(?<!not )dense\b|\bin[- ]depth\b|\bcomprehensive\b|"
                        r"\bexhaustive\b|\bread\b(?! the\b| this\b| it\b| through\b| from\b| in\b| my\b| our\b| attached\b)")


def normalize(value) -> str | None:
    v = str(value or "").strip().lower()
    return {"present": "present", "presenting": "present", "presentation": "present", "balanced": "balanced",
            "default": "balanced", "read": "read", "reading": "read", "dense": "read"}.get(v)


def detect(text: str, option=None) -> str:
    """The deck's density mode: an explicit option wins, then an explicit 'density: x' in the request, then the
    request's wording (strong phrases outweigh weak ones), else balanced."""
    if normalize(option):
        return normalize(option)
    t = text or ""
    m = _EXPLICIT.search(t)
    if m:
        got = normalize(next(g for g in m.groups() if g))
        if got:
            return got
    present = 2 * len(_STRONG_PRESENT.findall(t)) + len(_WEAK_PRESENT.findall(t))
    read = 2 * len(_STRONG_READ.findall(t)) + len(_WEAK_READ.findall(t))
    if present > read:
        return "present"
    if read > present:
        return "read"
    return DEFAULT


# ------------------------------------------------------------------ type scale (pt), on top of render.T/DENSE/ARGUMENT
SCALES = {
    "read": {"T": {}, "DENSE": {}, "ARGUMENT": {}, "bump": 0},
    "balanced": {"T": {"body": 13, "lead": 13, "cell": 13, "head": 15},
                 "DENSE": {"cell": 11, "lead": 11, "label": 11},
                 "ARGUMENT": {"intro": 15, "bullet": 13, "callout": 13}, "bump": 1},
    "present": {"T": {"body": 16, "lead": 18, "cell": 16, "head": 18, "label": 14, "num": 28},
                "DENSE": {"cell": 14, "lead": 16, "head": 16, "num": 24, "label": 14},
                "ARGUMENT": {"intro": 18, "bullet": 16, "callout": 16, "exhibit": 18, "unit": 13}, "bump": 4},
}

# ------------------------------------------------------------------ limits the renderer enforces (content beyond goes to notes)
LIMITS = {
    "read": None,
    "balanced": {"bullets": 4, "cell_bullets": 1, "block_bullets": 2, "blocks": 3, "callout": 3, "rows": 5,
                 "table_rows": 12, "steps": 5, "milestones": 1, "parts": 4, "footnotes": 3, "words": 20,
                 "part_bullets": 4, "quadrant_items": 3, "stats": 6},
    "present": {"bullets": 3, "cell_bullets": 0, "block_bullets": 1, "blocks": 3, "callout": 2, "rows": 4,
                "table_rows": 6, "steps": 4, "milestones": 0, "parts": 3, "footnotes": 1, "words": 12,
                "part_bullets": 3, "quadrant_items": 2, "stats": 4},
}
EXHIBIT_PARTS = {"chart", "table", "stats", "process", "timeline", "matrix2x2", "harvey"}


def _plain(t) -> str:
    return re.sub(r"\*\*|\^\w+", "", str(t or "")).strip()


def _balance(t: str) -> str:
    t = t.rstrip(" ,;:–—-")
    if t.count("**") % 2:
        t += "**"
    return t


def headline(text: str, max_words: int) -> str:
    """The lead clause of a bullet: the text before its first ';', dash, colon or comma once that clause has at least
    four words; a long sentence with no clean break is cut at a word boundary. Never adds words."""
    t = str(text or "").strip()
    if len(_plain(t).split()) <= max_words:
        return t
    for sep in (r";\s", r"\s[–—]\s", r"\s-\s", r":\s", r",\s", r"\s(?:because|as|while|which|after|since|with)\s"):
        for m in re.finditer(sep, t):
            head = t[:m.start()]
            k = len(_plain(head).split())
            if 4 <= k <= max_words:
                return _balance(head)
            if k > max_words:
                break
    words = t.split()
    if len(words) <= max_words + 5:                     # one sentence with no clean break: keep it whole
        return t
    return _balance(" ".join(words[:max_words])) + "…"


class _Notes:
    def __init__(self):
        self.lines: list[str] = []

    def add(self, text):
        t = _plain(text) if isinstance(text, str) else _plain(_txt(text))
        if t and t not in self.lines:
            self.lines.append(t)


def _txt(v) -> str:
    if isinstance(v, dict):
        return str(v.get("text") or v.get("lead") or v.get("title") or v.get("label") or v.get("action") or "")
    return "" if v is None else str(v)


def _short(item, lim, notes: _Notes):
    """A bullet ({"text"} or str) cut to its headline; the full text goes to the notes when cut."""
    if isinstance(item, dict):
        t = item.get("text")
        if not isinstance(t, str):
            return item
        h = headline(t, lim["words"])
        if h != t:
            notes.add(t)
        return dict(item, text=h)
    h = headline(str(item), lim["words"])
    if h != str(item):
        notes.add(str(item))
    return h


def _cap(items, k, notes: _Notes):
    items = list(items or [])
    for it in items[k:]:
        notes.add(it)
    return items[:k]


def _bullets(items, k, lim, notes):
    return [_short(b, lim, notes) for b in _cap(items, k, notes)]


def _matrix_as_grid(s: dict, lim: dict, notes: _Notes) -> dict:
    """A dense challenge matrix, for presenting: one short part per row (label, the today lead, the outlook lead);
    the evidence bullets go to the notes."""
    rows = [r for r in s.get("rows") or [] if isinstance(r, dict)]
    cols = (s.get("columns") or ["", "", ""]) + ["", "", ""]
    keep = rows[:4]
    for r in rows[4:]:
        notes.add(_plain(r.get("label")) + ": " + " / ".join(_plain(_txt(c)) for c in r.get("cells") or []))
    parts = []
    for r in keep:
        cells = [c if isinstance(c, dict) else {"lead": str(c)} for c in (r.get("cells") or [])] + [{}, {}]
        for c in cells[:2]:
            for b in c.get("bullets") or []:
                notes.add(b)
        bl = []
        b_lead = cells[1].get("lead")
        if b_lead:
            tag = re.sub(r"^[A-Z]\s*[-–—]\s*", "", _plain(cols[2]))
            bl.append({"text": (f"**{tag}:** " if tag else "") + headline(b_lead, lim["words"]),
                       "prov": cells[1].get("prov")})
        parts.append({"type": "text", "heading": _plain(r.get("label")),
                      "lead": headline(cells[0].get("lead") or "", lim["words"]), "prov": cells[0].get("prov"),
                      "bullets": bl})
    out = {k: v for k, v in s.items() if k not in ("rows", "columns")}
    out.update(archetype="grid", layout={1: "full", 2: "halves", 3: "thirds", 4: "quarters"}[max(1, len(parts))],
               look=s.get("look") or "bold", parts=parts)
    return out


def _part(p: dict, lim: dict, notes: _Notes) -> dict:
    p = dict(p)
    t = p.get("type")
    if t in ("text", "callout"):
        if isinstance(p.get("text"), str) and p.get("bullets"):
            notes.add(p["text"])
            p.pop("text")
        elif isinstance(p.get("text"), str):
            h = headline(p["text"], lim["words"] + 6)
            if h != p["text"]:
                notes.add(p["text"])
                p["text"] = h
        k = lim["callout"] if t == "callout" else lim["part_bullets"]
        p["bullets"] = _bullets(p.get("bullets"), k, lim, notes)
        if isinstance(p.get("lead"), str):
            p["lead"] = _short(p["lead"], dict(lim, words=lim["words"] + 4), notes)
    elif t == "process":
        steps = _cap(p.get("steps"), lim["steps"] + 1, notes)
        p["steps"] = [dict(st, text=_short(st["text"], lim, notes)) if isinstance(st, dict) and st.get("text") else st
                      for st in steps]
    elif t == "timeline":
        ms = _cap(p.get("milestones") or p.get("items"), 6, notes)
        p["milestones"] = [dict(m, text=_short(m["text"], lim, notes)) if isinstance(m, dict) and m.get("text") else m
                           for m in ms]
        p.pop("items", None)
    elif t == "matrix2x2":
        p["quadrants"] = [dict(q, items=_bullets(q.get("items"), lim["quadrant_items"], lim, notes))
                          if isinstance(q, dict) else q for q in p.get("quadrants") or []]
    elif t == "stats":
        p["items"] = _cap(p.get("items"), lim["stats"], notes)
    elif t == "table":
        tb = p.get("table") if isinstance(p.get("table"), dict) else None
        if tb:
            p["table"] = _table(tb, lim["table_rows"], notes)
    elif t == "harvey":
        p["rows"] = _cap(p.get("rows"), lim["table_rows"], notes)
    elif t == "quote" and isinstance(p.get("text"), str):
        p["text"] = _short(p["text"], dict(lim, words=lim["words"] * 2 + 4), notes)
    return p


def _table(t: dict, k: int, notes: _Notes) -> dict:
    rows = list(t.get("rows") or [])
    if len(rows) <= k:
        return t
    hl = [i for i in t.get("highlight_rows") or [] if isinstance(i, int) and i < len(rows)]
    it = [i for i in t.get("italic_rows") or [] if isinstance(i, int) and i < len(rows)]
    last = [len(rows) - 1] if t.get("total_row") else []
    keep = sorted(set(hl + last + it[:1]))
    for i in range(len(rows)):
        if len(keep) >= k:
            break
        if i not in keep:
            keep = sorted(set(keep) | {i})
    for i, r in enumerate(rows):
        if i not in keep:
            notes.add(" | ".join(_plain(c) for c in (r if isinstance(r, list) else [r])))
    idx = {old: new for new, old in enumerate(keep)}
    out = dict(t, rows=[rows[i] for i in keep], highlight_rows=[idx[i] for i in hl if i in idx],
               italic_rows=[idx[i] for i in it if i in idx])
    if isinstance(t.get("heat"), dict):                 # heat values follow the rows they shade
        out["heat"] = {c: [v[i] for i in keep if i < len(v)] if isinstance(v, list) else v for c, v in t["heat"].items()}
    return out


def condense(s: dict, mode: str) -> dict:
    """The slide trimmed to the mode's limits; everything cut goes to the speaker notes under a 'Detail for the
    presenter' heading. 'read' (and covers, dividers) come back unchanged."""
    lim = LIMITS.get(mode)
    a = s.get("archetype")
    if not lim or a in ("cover", "section_divider", "appendix_divider", "end") or s.get("_cont"):
        return s
    s = copy.deepcopy(s)
    notes = _Notes()
    if a in ("challenge_matrix", "text_matrix") and mode == "present":
        s = _matrix_as_grid(s, lim, notes)
        a = "grid"
    if isinstance(s.get("bullets"), list):
        s["bullets"] = _bullets(s["bullets"], lim["bullets"], lim, notes)
    if a in ("challenge_matrix", "text_matrix"):
        rows = _cap(s.get("rows"), lim["rows"], notes)
        for r in rows:
            if isinstance(r, dict):
                r["cells"] = [dict(c, bullets=_bullets(c.get("bullets"), lim["cell_bullets"], lim, notes))
                              if isinstance(c, dict) else c for c in r.get("cells") or []]
        s["rows"] = rows
    arg = a == "exhibit_argument" and mode == "present"     # two short points and one so-what beside the exhibit
    if isinstance(s.get("blocks"), list):
        blocks = _cap(s["blocks"], 2 if arg else lim["blocks"], notes)
        nb = lim["block_bullets"] if a == "exhibit_argument" else lim["bullets"]
        out = []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            b = dict(b)
            if isinstance(b.get("text"), str) and mode == "present":
                b["text"] = _short(b["text"], dict(lim, words=lim["words"] + 6), notes)
            b["bullets"] = _bullets(b.get("bullets"), nb, lim, notes)
            out.append(b)
        s["blocks"] = out
    co = s.get("callout")
    if isinstance(co, dict) and isinstance(co.get("bullets"), list):
        s["callout"] = dict(co, bullets=_bullets(co["bullets"], 1 if arg else lim["callout"], lim, notes))
    if a in ("table_focus", "table_highlight") and isinstance(s.get("table"), dict):
        s["table"] = _table(s["table"], lim["table_rows"], notes)
        if mode == "present":                              # the mode's type scale sets the table size
            s["table"] = {k: v for k, v in s["table"].items() if k != "size"}
    if a in ("closing", "next_steps"):
        steps = _cap(s.get("steps"), lim["steps"], notes)
        out = []
        for st in steps:
            if not isinstance(st, dict):
                out.append(st)
                continue
            st = dict(st)
            if isinstance(st.get("detail"), str):
                st["detail"] = _short(st["detail"], dict(lim, words=lim["words"] + 6), notes)
            ms = st.get("milestones") or []
            for m in ms[lim["milestones"]:]:
                notes.add(f"{_plain(st.get('action'))}: {_txt(m)}")
            st["milestones"] = ms[:lim["milestones"]]
            out.append(st)
        s["steps"] = out
    if a == "grid" and isinstance(s.get("parts"), list):
        parts = [p for p in s["parts"] if isinstance(p, dict)]
        k = lim["parts"]
        if mode == "present" and all(p.get("type") in ("text", "callout", "quote", "stats") for p in parts):
            k = 4                                          # a grid of short cards may keep four
        for p in parts[k:]:
            notes.add(f"{_plain(p.get('heading') or p.get('type'))}: " + json.dumps(
                {x: y for x, y in p.items() if x not in ("type", "heading", "prov")}, ensure_ascii=False)[:400])
        if mode == "present" and len(parts[:k]) >= 3:     # three or four parts: two bullets each
            lim = dict(lim, part_bullets=2)
        s["parts"] = [_part(p, lim, notes) for p in parts[:k]]
    fn = s.get("footnotes")
    if isinstance(fn, list) and len(fn) > lim["footnotes"]:
        for f in fn[lim["footnotes"]:]:
            notes.add("Footnote: " + str(f))
        s["footnotes"] = fn[:lim["footnotes"]]
        s = _drop_markers(s, lim["footnotes"])
    if notes.lines:
        head = "Detail for the presenter (moved off the slide):"
        s["notes"] = (str(s.get("notes") or "") + "\n" + head + "\n" + "\n".join("- " + x for x in notes.lines)).strip()
    return s


def _drop_markers(v, keep: int):
    """Superscript markers ^n beyond the footnotes still shown are removed from the slide text."""
    if isinstance(v, str):
        return re.sub(r"\^(\d+)", lambda m: m.group(0) if int(m.group(1)) <= keep else "", v)
    if isinstance(v, list):
        return [_drop_markers(x, keep) for x in v]
    if isinstance(v, dict):
        return {k: (x if k in ("notes", "chart", "table", "prov") else _drop_markers(x, keep)) for k, x in v.items()}
    return v


# ------------------------------------------------------------------ prompts
STORY = {
    "present": "DENSITY: present (the deck will be PRESENTED live). Plan one message per slide: the title carries the "
               "point and the slide shows ONE big exhibit that proves it (a chart, 2-4 big numbers, a process, a "
               "timeline or a 2x2, via grid parts or a chart archetype) plus at most 3 short bullets. Prefer grid, "
               "bar_takeaway/column_trend/line_trend and closing; avoid challenge_matrix and long tables. The detail "
               "and the full evidence go in the speaker notes, which the presenter says out loud.",
    "balanced": "DENSITY: balanced. Each slide makes one point with one exhibit and a short, complete argument "
                "(about 60-110 words, at most 4-5 bullets). Prefer clear exhibits over dense matrices; put secondary "
                "detail in the speaker notes.",
    "read": "DENSITY: read (a send-ahead deliverable read without a presenter). Plan FEWER, DENSER slides: each slide "
            "must carry the full evidence in full sentences, with footnotes, dense tables and matrices where they "
            "organise it; prefer challenge_matrix, exhibit_argument and table_focus.",
}

FILL = {
    "present": "FILL (present mode): the slide is SPOKEN to, not read. The title says the point; one exhibit shows it; "
               "at most 3 bullets of <= 12 words (a headline, not a sentence); about 40 body words in all. Big numbers "
               "beat sentences. Put the full sentences, evidence and caveats in \"notes\" (speaker notes): nothing is "
               "lost, it is said rather than shown. challenge_matrix: lead only (no bullets) in each cell, at most 4 "
               "rows. No more than one footnote. Empty space around one strong exhibit is fine; never pad.",
    "balanced": "FILL (balanced): each slide carries enough to stand on its own without crowding: an exhibit plus a "
                "takeaway and 2-4 bullets of 10-18 words; about 60-110 body words in all; challenge_matrix cells = "
                "lead + 1-2 bullets; exhibit_argument = 2-3 blocks of intro + 1-2 bullets; tables of up to 12 rows. "
                "Secondary detail goes in \"notes\". If the evidence for a slide is thin, merge it into a neighbour.",
    "read": "Dense is good when it is organised. FILL: every slide must carry enough substance to fill its page the way a top strategy firm's slide does; "
            "the renderer never stretches thin content. Targets: challenge_matrix cells = lead + 2-3 bullets; "
            "exhibit_argument = 2-3 blocks of intro + 2-3 bullets; chart slides = takeaway + 3 bullets of 15-25 words; "
            "a table under 12 rows needs 2-4 bullets. If the evidence for a slide is thin, merge it into a neighbour "
            "instead of padding.",
}

WRITE = {
    "present": "Write it for a presenter: short, visual, one message; the detail goes in \"notes\".",
    "balanced": "Fill the archetype's fields with a complete but light argument; secondary detail goes in \"notes\".",
    "read": "Fill the archetype's fields completely and densely, the way a top strategy firm's slide carries evidence.",
}
