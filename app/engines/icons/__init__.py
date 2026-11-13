"""Icon library: every bundled outline icon set, indexed for keyword and synonym search.

Sets
  lucide  1,539 icons, ISC (engines/slides/icons, LICENSE-lucide.txt); tags from lucide-static 0.460.0
  tabler  4,855 outline icons, MIT (engines/icons/tabler, LICENSE-tabler.txt); @tabler/icons 3.51.0 without the
          Brand category (company logos are trademarks, not icons we should put on slides)

Use one set per deck so the strokes and corner styles match: pass `set=` to search(), or let best_set() choose the
set that covers most of a deck's meanings.

    search("money")              -> [{"name": "circle-dollar-sign", "set": "lucide", "score": ..., "path": ...}, ...]
    search("money", set="tabler")
    path("coin", "tabler")       -> Path to the SVG (or None)
    path("tabler:coin")          -> same, with the set in the name
"""
from __future__ import annotations

import ast
import json
import re
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINES = HERE.parent

SETS = {
    "lucide": {"dir": ENGINES / "slides" / "icons", "tags": HERE / "tags-lucide.json", "license": "ISC",
               "license_file": ENGINES / "slides" / "icons" / "LICENSE-lucide.txt", "version": "0.460.0",
               "source": "https://lucide.dev (npm lucide-static)", "style": "outline, 2px stroke, rounded"},
    "tabler": {"dir": HERE / "tabler", "tags": HERE / "tags-tabler.json", "license": "MIT",
               "license_file": HERE / "tabler" / "LICENSE-tabler.txt", "version": "3.51.0",
               "source": "https://tabler.io/icons (npm @tabler/icons)", "style": "outline, 2px stroke, rounded"},
}
DEFAULT_SET = "lucide"          # what the slides and forge engines draw with

# Business words people ask for, and the words icon authors tag with. Both directions are searched, at a lower
# weight than the query word itself.
SYNONYMS = {
    "money": ["dollar", "currency", "cash", "coin", "banknote", "payment", "finance", "wallet"],
    "finance": ["money", "dollar", "bank", "currency", "chart"],
    "cost": ["money", "dollar", "price", "receipt"], "price": ["tag", "dollar", "money", "receipt"],
    "revenue": ["money", "dollar", "income", "trending", "chart"], "profit": ["trending", "money", "growth"],
    "investment": ["money", "chart", "growth", "piggy", "bank"], "capital": ["bank", "landmark", "money"],
    "valuation": ["gauge", "scale", "calculator", "chart"], "deal": ["handshake", "agreement", "contract"],
    "acquisition": ["handshake", "merge", "building"], "merger": ["merge", "handshake", "git-merge"],
    "growth": ["trending", "up", "chart", "arrow", "increase"], "decline": ["trending", "down", "decrease"],
    "risk": ["alert", "warning", "triangle", "shield"], "safety": ["shield", "check", "secure"],
    "security": ["shield", "lock", "key"], "compliance": ["shield", "check", "scale", "law"],
    "legal": ["scale", "gavel", "law", "contract"], "law": ["scale", "gavel", "legal"],
    "contract": ["file", "signature", "document", "pen"], "document": ["file", "page", "paper"],
    "report": ["file", "chart", "document", "clipboard"], "data": ["database", "chart", "table", "server"],
    "analytics": ["chart", "graph", "bar", "pie"], "chart": ["graph", "bar", "pie", "analytics"],
    "people": ["users", "user", "group", "team"], "team": ["users", "group", "people"],
    "customer": ["user", "users", "person", "client"], "employee": ["user", "badge", "id"],
    "workforce": ["users", "people", "team"], "hiring": ["user", "plus", "briefcase"],
    "time": ["clock", "timer", "hourglass", "calendar"], "deadline": ["calendar", "clock", "alarm"],
    "schedule": ["calendar", "clock"], "meeting": ["calendar", "users", "video"],
    "email": ["mail", "envelope", "inbox"], "message": ["chat", "mail", "bubble"], "phone": ["call", "mobile"],
    "idea": ["lightbulb", "bulb", "sparkles"], "innovation": ["lightbulb", "rocket", "sparkles"],
    "strategy": ["target", "chess", "compass", "map"], "goal": ["target", "flag", "trophy"],
    "success": ["trophy", "award", "check", "star"], "launch": ["rocket", "send"],
    "technology": ["cpu", "chip", "server", "code"], "ai": ["cpu", "brain", "sparkles", "bot"],
    "cloud": ["server", "upload"], "software": ["code", "app", "window"], "code": ["terminal", "brackets"],
    "global": ["globe", "world", "earth", "map"], "region": ["globe", "map", "pin"], "location": ["pin", "map"],
    "industry": ["factory", "building", "warehouse"], "manufacturing": ["factory", "gear", "tool"],
    "logistics": ["truck", "package", "box", "warehouse"], "shipping": ["truck", "package", "ship"],
    "supply": ["truck", "package", "box"], "retail": ["store", "shopping", "cart"], "sales": ["cart", "tag",
    "trending", "handshake"], "marketing": ["megaphone", "speakerphone", "target"],
    "health": ["heart", "pulse", "medical", "stethoscope"], "energy": ["bolt", "zap", "battery", "plug"],
    "environment": ["leaf", "tree", "plant", "recycle"], "sustainability": ["leaf", "recycle", "plant"],
    "education": ["school", "graduation", "book"], "research": ["search", "flask", "microscope", "book"],
    "search": ["magnifier", "find", "zoom"], "settings": ["gear", "cog", "adjustments", "sliders"],
    "process": ["workflow", "flow", "steps", "arrows"], "integration": ["puzzle", "plug", "link"],
    "partnership": ["handshake", "link", "users"], "scale": ["maximize", "expand", "layers"],
    "quality": ["badge", "check", "star", "award"], "insight": ["eye", "lightbulb", "chart"],
    "warning": ["alert", "triangle", "exclamation"], "question": ["help", "info"],
}
MODIFIERS = {"off", "x", "slash", "minus", "plus", "question", "exclamation", "dashed", "check", "cancel", "pause",
             "up", "down", "left", "right", "share", "star", "heart", "search", "bolt", "cog", "code", "dollar",
             "pin", "spark", "filled", "edit", "move", "sad", "dot", "due", "exclamation", "2", "3", "4", "5"}


def _stem(w: str) -> str:
    """A tiny English stemmer: enough that 'charts' finds 'chart' and 'companies' finds 'company'."""
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _words(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", str(text).lower())]


@lru_cache(maxsize=1)
def _meanings() -> dict:
    """ICON_MEANINGS from the forge renderer (meaning word -> Lucide icon), read without importing the module."""
    src = ENGINES / "forge" / "render.py"
    try:
        for node in ast.parse(src.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "ICON_MEANINGS" for t in node.targets):
                return {str(k): str(v) for k, v in ast.literal_eval(node.value).items()}
    except (OSError, SyntaxError, ValueError):
        pass
    return {}


@lru_cache(maxsize=None)
def index(set: str | None = None) -> tuple[dict, ...]:
    """Every icon of one set (or all sets): name, set, category, tags and the SVG path."""
    if set is None:
        return tuple(r for s in SETS for r in index(s))
    info = SETS[_check_set(set)]
    try:
        tags = json.loads(info["tags"].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        tags = {}
    rows = []
    for p in sorted(info["dir"].glob("*.svg")):
        t = tags.get(p.stem, [])
        cat, tl = (t.get("c", ""), t.get("t", [])) if isinstance(t, dict) else ("", t)
        rows.append({"name": p.stem, "set": set, "category": cat, "tags": list(tl), "path": str(p)})
    return tuple(rows)


def _check_set(set: str | None) -> str:
    s = (set or DEFAULT_SET).strip().lower()
    if s not in SETS:
        raise ValueError(f"Unknown icon set {set!r}. Sets: {', '.join(SETS)}")
    return s


@lru_cache(maxsize=None)
def _prepared(set: str) -> tuple:
    """Per icon: (row, name words, tag words, category words), computed once per set."""
    out = []
    for r in index(set):
        tag_words = {w for t in r["tags"] for w in _words(t)}
        out.append((r, _words(r["name"].replace("-", " ")), tag_words, frozenset(_words(r["category"]))))
    return tuple(out)


def _terms(query: str) -> tuple[list[str], dict[str, float]]:
    """The query's own words (weight 1) plus their synonyms (weight 0.5)."""
    q = _words(query)
    terms: dict[str, float] = {w: 1.0 for w in q}
    for w in q:
        for raw in (w, w + "s"):
            for syn in SYNONYMS.get(raw, []):
                for s in _words(syn.replace("-", " ")):
                    terms.setdefault(s, 0.5)
    return q, terms


def _score(prep, q: list[str], terms: dict, meaning: dict, phrase: str) -> float:
    r, nw, tw, cw = prep
    score, hit = 0.0, set()
    for t, w in terms.items():
        if nw and t == nw[0]:
            s = 4.0
        elif t in nw:
            s = 3.0
        elif t in tw:
            s = 2.0
        elif len(t) >= 4 and any(x.startswith(t) for x in nw):
            s = 1.0
        elif t in cw:
            s = 0.5
        else:
            continue
        score += s * w
        if w == 1.0:
            hit.add(t)
    if r["set"] == "lucide" and r["name"] in meaning:
        score += 6.0
    if not score:
        return 0.0
    if phrase and (phrase == " ".join(nw) or phrase in {" ".join(_words(t)) for t in r["tags"]}):
        score += 5.0
    score += 2.0 * len(hit) - 0.6 * sum(1 for x in nw[1:] if x in MODIFIERS) - 0.15 * len(nw)
    return round(score, 2)


def search(query: str, set: str | None = None, limit: int = 12) -> list[dict]:
    """Icons that fit `query` (a word or short phrase such as "money", "supply chain"), best first. `set` restricts
    the search to one set; without it every set is searched."""
    q, terms = _terms(query)
    if not q:
        return []
    names = {_stem(w) for w in q}
    meaning = {v for k, v in _meanings().items() if _stem(k) in names}
    phrase = " ".join(q)
    sets = [_check_set(set)] if set else list(SETS)
    rows = []
    for s in sets:
        for prep in _prepared(s):
            sc = _score(prep, q, terms, meaning, phrase)
            if sc > 0:
                r = prep[0]
                rows.append({"name": r["name"], "set": s, "score": sc, "category": r["category"],
                             "tags": r["tags"][:8], "path": r["path"]})
    rows.sort(key=lambda r: (-r["score"], len(r["name"]), r["name"]))
    return rows[:max(1, int(limit))]


def best_set(queries: list[str]) -> str:
    """The one set that serves all of a deck's meanings best (sum of each meaning's top score)."""
    def total(s):
        return sum((search(q, set=s, limit=1) or [{"score": 0}])[0]["score"] for q in queries)
    return max(SETS, key=lambda s: (total(s), s == DEFAULT_SET))


def path(name: str, set: str | None = None) -> Path | None:
    """The SVG file of an icon. `name` may carry its set ("tabler:coin"); without a set, Lucide is tried first."""
    if ":" in name and set is None:
        set, name = name.split(":", 1)
    for s in ([_check_set(set)] if set else list(SETS)):
        p = SETS[s]["dir"] / f"{name.strip().lower()}.svg"
        if p.is_file():
            return p
    return None


def sets() -> list[dict]:
    """The bundled sets with licence, source and icon count."""
    return [{"set": k, "count": len(index(k)), "license": v["license"], "license_file": str(v["license_file"]),
             "version": v["version"], "source": v["source"], "style": v["style"]} for k, v in SETS.items()]
