"""The deck brief: the few answers that change a deck most, asked before it is built.

One place for the question set and its recommendations, shared by the make_deck MCP tool, the built-in agent's
office_task (engine slides) and the forge engine itself (`meshpool run --engine slides`):

    questions(request, files, fields) -> the open questions, in ask_user's shape (header, question, options with one
                                         recommended), for audience and purpose, density, length and look
    recommend(request, files, fields) -> {"audience", "density", "slides", "theme"}: the recommended answers
    apply(request, answers)           -> the request with the answers written in plain words the engine parses
                                         ("density: present; 8 slides; ocean theme; audience: the board")
    complete(request, files)          -> apply() with the recommendations for whatever is still open (headless runs)
    needed_text(...)                  -> the "brief needed" tool result: the questions and how to call again

A question is settled when the request says it (or a field gives it); recommendations come from the request and the
input files (a board audience recommends "present"; a pre-read recommends "read").
"""
from __future__ import annotations

import re
from pathlib import Path

try:
    from engines.forge.template import PALETTES as _PAL
    THEMES = tuple(_PAL)
except Exception:  # noqa: BLE001  the template needs nothing heavy, but never let the brief fail on it
    THEMES = ("navy", "ocean", "forest", "burgundy", "graphite", "mono")

DENSITIES = ("present", "balanced", "read")
FIELDS = ("audience", "purpose", "density", "slides", "theme", "opening", "closing", "sources")

AUDIENCES = [
    ("Leadership or board", r"\bboard\b|\bexec(?:utive)?s?\b|\bc-suite\b|\bceo\b|\bcfo\b|\bleadership\b|"
                            r"\bmanagement\b|\bsteering\b|\bcommittee\b|\bdirectors\b", "A decision or an update for "
                            "senior people: the answer first, few slides, numbers that hold up."),
    ("Investors", r"\binvestors?\b|\bvcs?\b|\bpitch\b|\bfundrais\w*|\bseries [a-d]\b|\bseed round\b",
     "A pitch: problem, solution, market, traction, team, the ask."),
    ("Clients or customers", r"\bclients?\b|\bcustomers?\b|\bprospects?\b|\bsales\b|\bproposal\b|\bbuyers?\b",
     "Persuade an outside audience: their problem, your offer, proof, next steps."),
    ("My team", r"\bteam\b|\ball[- ]hands\b|\bstaff\b|\bemployees\b|\bcolleagues\b|\bweekly\b|\bstand[- ]?up\b|"
                r"\bkick[- ]?off\b", "An internal update: status, what changed, who does what next."),
    ("Students or a training", r"\bstudents?\b|\bclass\b|\blecture\b|\btraining\b|\bworkshop\b|\bcourse\b|"
                               r"\bteach\w*|\bonboarding\b", "Teach step by step, with examples."),
]
_AUDIENCE_SAID = re.compile(r"(?i)\baudience\s*[:=]|\bfor (?:the|my|our|a|an) [\w-]+ (?:meeting|review|audience)\b|"
                            r"\bpresent(?:ing|ed)? (?:it )?to\b|\bpurpose\s*[:=]|\bconference\b|\bstakeholders?\b|"
                            r"\bpartners?\b|\bregulators?\b|\bboss\b|\bmanager\b")
_SLIDES = re.compile(r"(?i)\b(\d{1,2})\s*[- ]?\s*(?:slides?|pages?)\b|\bslides?\s*[:=]\s*(\d{1,2})\b")
_THEME_SAID = re.compile(r"(?i)\b(?:theme|palette|colou?rs?|look|brand(?:ing)?|template|style)\s*[:=]|"
                         r"\b(?:our|my|the company'?s?|corporate) (?:brand|template|colou?rs?)\b|"
                         r"\b(?:" + "|".join(THEMES) + r")\b\s*(?:theme|palette|colou?rs?|look)|"
                         r"\b(?:theme|palette)\s+(?:" + "|".join(THEMES) + r")\b|\baccent\s*#?[0-9a-f]{6}\b")
_DENSITY_SAID = re.compile(r"(?i)[\"']?density[\"']?\s*[:=]|\b(?:present|balanced|read)\s+(?:density|mode)\b|"
                           r"\bpre[- ]?read\b|\bsend[- ]ahead\b|\bread[- ]ahead\b|\bleave[- ]behind\b|"
                           r"\bto present\b|\bpresent(?:ed)? live\b|\bpresenter[- ]led\b|\bkeynote\b|\bon stage\b|"
                           r"\bstand[- ]?alone\b|\btext[- ]heavy\b|\bfew(?:er)? words\b|\b(?:less|not too) (?:dense|wordy)\b")


def _text(request: str, files=None) -> str:
    names = " ".join(Path(str(f)).stem.replace("_", " ").replace("-", " ") for f in files or [])
    return f"{request or ''} {names}"


def _given(fields: dict | None, key: str):
    v = (fields or {}).get(key)
    return v if v not in (None, "", [], 0) else None


def _density_of(text: str) -> str | None:
    try:                                          # the engine's own detector, when present
        from engines.forge.density import detect
        d = detect(text)
        return d if _DENSITY_SAID.search(text) or d != "balanced" else None
    except Exception:  # noqa: BLE001
        if not _DENSITY_SAID.search(text):
            return None
        t = text.lower()
        m = re.search(r"density\s*[:=]\s*[\"']?(present|balanced|read)", t)
        if m:
            return m.group(1)
        return "read" if re.search(r"pre[- ]?read|send[- ]ahead|read[- ]ahead|leave[- ]behind|stand[- ]?alone|"
                                   r"text[- ]heavy", t) else "present"


def _company_look(request: str) -> bool:
    """A company look (Settings > Company, engines/forge/brand.py) applies to this request: the look is answered."""
    try:
        from engines.forge.brand import for_request
        return for_request(request) is not None
    except Exception:                                   # noqa: BLE001 - no store, no look
        return False


def _like_these(request: str) -> bool:
    from engines.forge.engine import LIKE_THESE
    return bool(LIKE_THESE.search(request or ""))


def settled(request: str, files=None, fields: dict | None = None) -> dict:
    """Which of the four deck questions the request (or a field) already answers: {key: bool}."""
    t = request or ""
    aud = bool(_given(fields, "audience") or _given(fields, "purpose") or _AUDIENCE_SAID.search(t)
               or any(re.search(rx, t, re.I) for _, rx, _ in AUDIENCES))
    dens = bool(_given(fields, "density") or _DENSITY_SAID.search(t))
    n = bool(_given(fields, "slides") or _SLIDES.search(t))
    look = bool(_given(fields, "theme") or _THEME_SAID.search(t)
                or any(str(f).lower().endswith((".potx", ".pptx")) and "template" in str(f).lower()
                       for f in files or [])
                or _company_look(t)
                or (any(str(f).lower().endswith((".pptx", ".potx")) for f in files or []) and _like_these(t)))
    return {"audience": aud, "density": dens, "slides": n, "theme": look}


def recommend(request: str, files=None, fields: dict | None = None) -> dict:
    t = _text(request, files)
    hit = _given(fields, "audience") or next((name for name, rx, _ in AUDIENCES if re.search(rx, t, re.I)), None)
    aud = hit or "Leadership or board"
    dens = str(_given(fields, "density") or "").lower()
    if dens not in DENSITIES:
        dens = _density_of(t) or ""
    if dens not in DENSITIES:
        a = str(hit or "").lower()             # only an audience the request names moves the density
        dens = ("present" if re.search(r"board|leader|exec|investor|client|customer|student|training", a)
                else "balanced")
        if re.search(r"(?i)\breport\b|\bdetailed\b|\bin[- ]depth\b|\bcomprehensive\b|\bdeliverable\b", t):
            dens = "read"
    m = _SLIDES.search(request or "")
    slides = (_norm_answer("slides", _given(fields, "slides") or "") or (m and (m.group(1) or m.group(2)))
              or {"present": 8, "balanced": 10, "read": 12}[dens])
    theme = str(_given(fields, "theme") or "").lower()
    if theme not in THEMES:
        hit = re.search(r"(?i)\b(" + "|".join(THEMES) + r")\b", request or "")
        theme = hit.group(1).lower() if hit else "navy"
    return {"audience": aud, "density": dens, "slides": max(1, min(int(slides), 60)), "theme": theme}


def _opt(label, desc, rec):
    return {"label": label, "description": desc, "recommended": bool(rec)}


def questions(request: str, files=None, fields: dict | None = None) -> list[dict]:
    """The open questions (at most four), each with 2-5 options and one recommended, in ask_user's shape."""
    have, rec = settled(request, files, fields), recommend(request, files, fields)
    qs = []
    if not have["audience"]:
        qs.append({"header": "Audience", "question": "Who is the deck for, and what should it do for them?",
                   "multi": False, "options": [_opt(n, d, n == rec["audience"]) for n, _, d in AUDIENCES]})
    if not have["density"]:
        qs.append({"header": "Density", "question": "Will you present it live, or will people read it on their own?",
                   "multi": False, "options": [
                       _opt("present", "Visual: one message per slide, few words, big type; detail in the speaker "
                                       "notes. For presenting live.", rec["density"] == "present"),
                       _opt("balanced", "Some text on each slide; works presented or read.", rec["density"] == "balanced"),
                       _opt("read", "A dense pre-read: full sentences, tables and footnotes.", rec["density"] == "read")]})
    if not have["slides"]:
        counts = sorted({5, 8, 10, 15, rec["slides"]})
        if len(counts) > 5:
            counts = sorted({5, 10, 15, rec["slides"]})
        qs.append({"header": "Length", "question": "How many slides?", "multi": False,
                   "options": [_opt(f"{n} slides", "", n == rec["slides"]) for n in counts]})
    if not have["theme"]:
        order = [rec["theme"]] + [x for x in ("navy", "ocean", "forest", "graphite", "burgundy") if x != rec["theme"]]
        desc = {"navy": "Classic consulting navy", "ocean": "Bright blue and teal", "forest": "Calm greens",
                "graphite": "Dark grey with an orange accent", "burgundy": "Deep red, formal",
                "mono": "Black and white with one blue accent"}
        qs.append({"header": "Look", "question": "Which colour theme?", "multi": False,
                   "options": [_opt(x, desc.get(x, ""), x == rec["theme"]) for x in order[:5]]})
    return qs


def _norm_answer(key: str, value) -> str:
    v = str(value[0] if isinstance(value, list) and value else value or "").strip()
    if key == "slides":
        m = re.search(r"\d{1,2}", v)
        return m.group(0) if m else ""
    if key in ("density", "theme"):
        return v.split()[0].lower() if v else ""
    return v


def apply(request: str, answers: dict | None) -> str:
    """Write the answers into the request in words the engine parses. Only what is given and not already said."""
    a = {k: _norm_answer(k, v) for k, v in (answers or {}).items() if k in FIELDS and v not in (None, "", [])}
    have = settled(request)
    bits = []
    if a.get("audience") and not re.search(r"(?i)\baudience\s*[:=]", request or ""):
        bits.append(f"audience: {a['audience']}")
    if a.get("purpose"):
        bits.append(f"purpose: {a['purpose']}")
    if a.get("density") in DENSITIES and not re.search(r"(?i)density\s*[:=]", request or ""):
        bits.append(f"density: {a['density']}")
    if a.get("slides") and not have["slides"]:
        bits.append(f"{a['slides']} slides")
    if a.get("theme") in THEMES and not re.search(r"(?i)\b" + a["theme"] + r"\s+theme\b", request or ""):
        bits.append(f"{a['theme']} theme")
    if a.get("opening"):
        bits.append(f"opening: {a['opening']}")
    if a.get("closing"):
        bits.append(f"closing: {a['closing']}")
    if a.get("sources"):
        bits.append(f"sources: {a['sources']}")
    if not bits:
        return request
    return (request or "").rstrip() + "\n\nBrief: " + "; ".join(bits) + "."


def complete(request: str, files=None, fields: dict | None = None) -> str:
    """The request with the given fields and, for every question still open, the recommended answer (headless runs,
    brief_ok=true, or "use defaults")."""
    have, rec = settled(request, files, fields), recommend(request, files, fields)
    ans = {k: v for k, v in (fields or {}).items() if k in FIELDS}
    for k in ("density", "slides", "theme"):
        if not have[k]:
            ans.setdefault(k, rec[k])
    if not have["audience"]:
        ans.setdefault("audience", rec["audience"])
    return apply(request, ans)


def answers_from(qs: list[dict], picked: list[dict]) -> dict:
    """ask_user answers ([{"selected": [...], "other": ""}], one per question) -> brief fields."""
    key = {"Audience": "audience", "Density": "density", "Length": "slides", "Look": "theme"}
    out = {}
    for q, a in zip(qs, picked):
        k = key.get(q.get("header"))
        if not k:
            continue
        v = (a.get("selected") or [""])[0] or a.get("other") or ""
        if a.get("other") and a.get("selected"):
            v = f"{v} ({a['other']})" if k == "audience" else v
        if v:
            out[k] = v
    return out


def wants_defaults(request: str) -> bool:
    return bool(re.search(r"(?i)\buse (?:the )?defaults\b|\bbrief[_ ]ok\b|\byour (?:call|choice)\b|"
                          r"\bdon'?t ask\b|\bno questions\b|\bjust (?:build|make) it\b", request or ""))


def needed_text(qs: list[dict], tool: str = "make_deck") -> str:
    """The tool result when the brief is not settled: the questions and how to call again."""
    lines = ["BRIEF NEEDED: the deck was not built yet. These answers change the deck most and the request does not "
             "give them. Ask the user (Meshpool agents: one ask_user call with these questions; other agents: show "
             "the options and the recommendation), then call " + tool + " again with the answers.", ""]
    for n, q in enumerate(qs, 1):
        lines.append(f"{n}. {q['header']}: {q['question']}")
        for o in q["options"]:
            lines.append(f"   - {o['label']}" + (" (recommended)" if o["recommended"] else "")
                         + (f": {o['description']}" if o["description"] else ""))
    lines += ["", "How to call again: pass the answers as fields (audience, purpose, density = present|balanced|read, "
              "slides = a number, theme = " + "|".join(THEMES) + ", opening, closing, sources), or append them to the "
              "request, e.g. \"density: present; audience: the board; 8 slides; ocean theme\". To build now with the "
              "recommended answers, pass brief_ok=true.", "",
              "questions_json: " + __import__("json").dumps(qs, ensure_ascii=False)]
    return "\n".join(lines)
