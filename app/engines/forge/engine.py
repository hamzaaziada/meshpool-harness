"""Forge deck engine: request (+ input files) -> consulting-grade .pptx in the output folder.

The model writes the storyline and the slide content as JSON; the forge renderer applies the design system
(engines/forge/render.py) and validate_pptx checks the rendered file. Used by `meshpool run --engine forge` and
`--engine slides` (engines/slides/engine.py is a thin entry to this module).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def _inputs(workdir: str, files: list[str] | None) -> list[str]:
    """The files given with the request; with none given (the CLI), the input files in the work folder."""
    if files:
        return list(files)
    return [str(f) for f in sorted(Path(workdir).iterdir()) if f.is_file()
            and f.suffix.lower() in (".md", ".txt", ".pdf", ".docx", ".pptx", ".potx", ".xlsx", ".csv")]


def _brief(task: str, workdir: str, files: list[str] | None) -> str:
    import time
    # the model does not know the date: without this a cover reads "March 2026" in November
    parts = [task, f"(Today is {time.strftime('%d %B %Y')}. Date the cover {time.strftime('%B %Y')} unless the brief "
                   "gives another date.)"]
    for f in _inputs(workdir, files):
        p = Path(f)
        try:
            if p.suffix.lower() in (".md", ".txt", ".csv"):
                text = p.read_text(errors="replace")
            else:
                from meshpool_harness import docread
                text = docread.extract(p)
        except Exception as e:  # an unreadable input is reported, not fatal
            text = f"[could not read {p.name}: {type(e).__name__}]"
        parts.append(f"\n--- {p.name} ---\n{text[:20000]}")
    return "\n".join(parts)


def theme_from(task: str):
    """'ocean theme', 'palette: forest', 'sans fonts', 'accent #7C3AED' in the request -> a theme spec (or None)."""
    from engines.forge.template import FONT_PAIRS, PALETTES
    t = task.lower()
    spec = {}
    m = re.search(r"\b(" + "|".join(PALETTES) + r")\b\s*(?:theme|palette|colou?rs?|look)|(?:theme|palette|colou?rs?)\s*[:=]?\s*(" + "|".join(PALETTES) + r")\b", t)
    if m:
        spec["palette"] = m.group(1) or m.group(2)
    f = re.search(r"\b(" + "|".join(FONT_PAIRS) + r")\s*fonts?\b|fonts?\s*[:=]?\s*(" + "|".join(FONT_PAIRS) + r")\b", t)
    if f:
        spec["fonts"] = f.group(1) or f.group(2)
    a = re.search(r"accent\s*(?:colou?r)?\s*[:=]?\s*#?([0-9a-f]{6})\b", t)
    if a:
        spec["accent1"] = a.group(1)
    return spec or None


LIKE_THESE = re.compile(r"(?i)\b(?:same|this|that|their|these|the attached|our)\s+(?:style|look|format|template|"
                        r"branding|design)\b|\b(?:like|match(?:es|ing)?|based on|copy)\s+(?:this|that|these|the "
                        r"attached|the old|our old|the existing|our existing)\s+(?:decks?|presentations?|slides?|"
                        r"templates?)\b|\bas (?:a )?template\b")


def brand_from(task: str, files: list[str] | None = None) -> tuple[dict, dict | None]:
    """render arguments for the company look: decks attached to the request as the style to follow ('same format as
    the attached deck'), else the profile the request names or the active one (Settings > Company); colour words in
    the request ('ocean theme', 'accent #7C3AED') override its colours. ({...}, profile or None)."""
    asked = theme_from(task)
    prof = None
    try:
        from engines.forge import brand as B
        decks = [f for f in files or [] if str(f).lower().endswith((".pptx", ".potx"))]
        if decks and LIKE_THESE.search(task):                    # learned for this deck only, not saved
            import tempfile
            prof = B.clean(B.learn(decks, folder=tempfile.mkdtemp(prefix="forge_look_")))
        else:
            prof = B.for_request(task)
    except Exception as e:                                       # noqa: BLE001 - a broken profile never blocks a deck
        print(f"company look skipped: {type(e).__name__}: {e}", file=sys.stderr)
        prof = None
    if not prof:
        return {"theme": asked}, None
    look = B.render_kwargs(prof)
    if asked:
        look["theme"] = asked
        look.pop("template", None)
    return look, prof


def icon_set_from(task: str, options: dict | None = None) -> str | None:
    """The deck's icon set: options["icon_set"], or 'icon set: phosphor', 'material icons', 'carbon icon style' in
    the request (engines/icons SETS). None keeps the default (Lucide)."""
    from engines.icons import SETS
    asked = (options or {}).get("icon_set")
    if asked:
        return str(asked).strip().lower() if str(asked).strip().lower() in SETS else None
    names = "|".join(re.escape(s) for s in sorted(SETS, key=len, reverse=True))
    m = re.search(r"\bicon(?:s| set| style)?\s*[:=]\s*(" + names + r")\b|\b(" + names + r")\s+icons?\b", task.lower())
    return (m.group(1) or m.group(2)) if m else None


def density_from(task: str, options: dict | None = None, density: str | None = None) -> str:
    """'for presenting', 'keynote', 'visual' -> present; 'send-ahead', 'pre-read', 'detailed', 'dense' -> read;
    otherwise balanced. An explicit density (argument, options["density"], or 'density: x' in the request) wins."""
    from engines.forge.density import detect
    return detect(task, density or (options or {}).get("density"))


ADD_RE = re.compile(r"^\[add to deck: ([^\]]+)\]\s*", re.S)


def _plain_office(deck: str) -> bool:
    """True for a deck in PowerPoint's stock look: the Office theme and colours, stock fonts, no logo in the master."""
    import zipfile
    try:
        with zipfile.ZipFile(deck) as z:
            theme = z.read("ppt/theme/theme1.xml").decode("utf-8", "replace")
            pics = [n for n in z.namelist() if n.startswith(("ppt/slideMasters/_rels", "ppt/slideLayouts/_rels"))
                    and b"relationships/image" in z.read(n)]
    except (OSError, KeyError, zipfile.BadZipFile):
        return False
    fonts = set(re.findall(r'<a:latin typeface="([^"]*)"', theme)[:2])
    stock = {"Calibri", "Calibri Light", "Aptos", "Aptos Display", "Arial", "Times New Roman", ""}
    return bool(re.search(r'<a:clrScheme name="Office', theme)) and fonts <= stock and not pics


def add_to_deck(task: str, deck: str, *, workdir: str, task_id: str, client=None, files: list[str] | None = None,
                model: str | None = None, density: str | None = None) -> dict:
    """Slides added to a deck the user already has: forge builds them in that deck's look (its master and theme
    when it is 16:9, else its colours and fonts), then they are appended to a copy of the deck. The original is never
    changed; the copy is saved next to it as "<name> (updated).pptx"."""
    from engines.forge.brief import slide_count
    from engines.forge.build import build
    from engines.forge.merge import append
    if model:
        os.environ.setdefault("FORGE_MODEL", model)
    n = min(max(slide_count(task) or 1, 1), 12)
    # A real brand (its own colours, fonts or logo) is followed; PowerPoint's stock Office theme is not a brand, and
    # copying it gave Calibri and stock colours: those decks get the forge design system instead.
    look = {} if _plain_office(deck) else brand_from("same style as the attached deck", [deck])[0]
    res = build(_brief(task, workdir, files), workdir, n, client=client, task_id=task_id,
                density=density_from(task, None, density), insert=True, **look)
    src = Path(deck)
    # the job works on a copy in "Meshpool outputs/<job>/": save next to the user's own deck when it is there
    orig = src.parent.parent.parent / src.name
    home = orig.parent if src.parent.parent.name == "Meshpool outputs" and orig.is_file() else src.parent
    out = home / f"{src.stem} (updated){src.suffix}"
    i = 2
    while out.exists():
        out = home / f"{src.stem} (updated {i}){src.suffix}"
        i += 1
    added = append(str(src), res["pptx"], str(out))
    left = sum(len(v) for v in (res.get("unresolved") or {}).values())
    return {"ok": out.exists(), "artifacts": [str(out)], "evidence": [], "turns": res.get("calls"), "reason": None,
            "answer": (f"Added {added} slide{'s' if added != 1 else ''} to {src.name} and saved it as {out.name} "
                       f"(the original is unchanged). They follow the deck's look and were checked after rendering"
                       + (f"; {left} items are flagged in the speaker notes ([QA] lines)." if left else "."))}


def solve(task: str, *, workdir: str, task_id: str, client=None, files: list[str] | None = None,
          model: str | None = None, density: str | None = None, options: dict | None = None, **_) -> dict:
    m = ADD_RE.match(task or "")
    if m:                                                       # add_slides: into the user's own deck
        name = m.group(1).strip()
        deck = next((f for f in files or [] if Path(f).name == Path(name).name), None) or str(Path(workdir) / name)
        if not Path(deck).is_file():
            return {"ok": False, "answer": f"The deck {name} was not found.", "artifacts": [], "reason": "error"}
        return add_to_deck(task[m.end():], deck, workdir=workdir, task_id=task_id, client=client,
                           files=[f for f in files or [] if f != deck] + [deck], model=model, density=density)
    from engines.forge.build import build
    from engines.forge.brief import complete, slide_count
    attached = _inputs(workdir, files)                          # the CLI passes no files: the work folder's inputs
    # an open brief (audience, density, length, look) gets the recommended answers (engines/forge/brief.py)
    n = (options or {}).get("slides")                           # an explicit slides field wins over the wording
    task = complete(task, attached, {"slides": n} if n else None)
    if model:
        os.environ.setdefault("FORGE_MODEL", model)
    slides = slide_count(task) or 10
    mode = density_from(task, options, density)
    fixed, extra = [], []
    from engines.forge.finance_pages import finance_workbook, pages_from_workbook
    cands = list(files or []) + [str(p) for p in Path(workdir).glob("*.xls*") if not p.name.startswith("~$")]
    wbk = finance_workbook(cands)
    if wbk and not re.search(r"\b(no|without) (model|valuation) pages\b", task, re.I):
        try:                                                    # banker pages computed from the model's outputs
            fixed, extra = pages_from_workbook(wbk)
        except Exception as e:                                  # noqa: BLE001 - the deck still builds without them
            print(f"model pages skipped: {type(e).__name__}: {e}", file=sys.stderr)
    # The computed pages count inside the slides asked for: the model writes the rest (at least 3: cover, argument,
    # next steps). Asked for fewer than that leaves room for, the first pages (football field, DCF, comps) stay.
    if fixed and len(fixed) > slides - 3:
        keep = {id(p) for p in sorted(fixed, key=lambda p: p.get("rank", 99))[:max(2, slides - 3)]}
        fixed = [p for p in fixed if id(p) in keep]               # the most useful pages, still in deck order
    look, prof = brand_from(task, attached)
    iset = icon_set_from(task, options)
    if iset:                                                    # one icon set for the whole deck
        th = look.get("theme")
        look["theme"] = {**(th if isinstance(th, dict) else {"palette": th} if th else {}), "icon_set": iset}
    res = build(_brief(task, workdir, files), workdir, max(slides, len(fixed) + 3), client=client, task_id=task_id,
                density=mode, fixed_pages=fixed, extra_facts=extra, **look)
    pptx = res["pptx"]
    failing = [k for k, v in res["rules"].items() if v == "fail"]
    left = sum(len(v) for v in (res.get("unresolved") or {}).values())
    try:
        from pptx import Presentation
        made = len(Presentation(pptx).slides)               # what is in the file, not what was planned
    except Exception:  # noqa: BLE001
        made = max(slides, len(fixed) + 3)
    answer = (f"Built {Path(pptx).name}: {made} slides in the forge design system ({mode} density), "
              + (f"{len(fixed)} of them computed from {Path(wbk).name} (football field, comps, DCF, statements), "
                 if fixed else "")
              + (f"in the {prof.get('name') or 'company'} look ("
                 + ("its slide master, " if look.get("template") else "") + "colours, fonts"
                 + (", logo" if (look.get("brand") or {}).get("logo") else "") + "), " if prof else "")
              + "checked after rendering. "
              + (f"{left} items still flagged in the speaker notes ([QA] lines); rules failing: {', '.join(failing)}."
                 if left else "All content checks passed."))
    rv = res.get("review") or {}
    if rv.get("score") is not None:                            # the partner review's verdict, said plainly
        answer += (f" Partner review: {rv['score']}/10, "
                   + ("ready to send." if rv.get("verdict") == "ship" else
                      f"slides {', '.join(map(str, rv.get('fix') or []))} still need work"
                      + (f" (most common: {rv['top'][0]['example']})" if rv.get("top") else "") + "."))
    return {"ok": Path(pptx).exists(), "answer": answer, "artifacts": [pptx], "evidence": [],
            "turns": res.get("calls"), "reason": None}
