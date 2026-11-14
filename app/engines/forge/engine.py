"""Forge deck engine: request (+ input files) -> consulting-grade .pptx in the output folder.

The model writes the storyline and the slide content as JSON; the forge renderer applies the design system
(engines/forge/render.py) and validate_pptx checks the rendered file. Used by `meshpool run --engine forge` and,
by default, by the slides engine.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def _brief(task: str, workdir: str, files: list[str] | None) -> str:
    parts = [task]
    names = list(files or [])
    for f in Path(workdir).iterdir() if not names else []:
        if f.is_file() and f.suffix.lower() in (".md", ".txt", ".pdf", ".docx", ".pptx", ".xlsx", ".csv"):
            names.append(str(f))
    for f in names:
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


def density_from(task: str, options: dict | None = None, density: str | None = None) -> str:
    """'for presenting', 'keynote', 'visual' -> present; 'send-ahead', 'pre-read', 'detailed', 'dense' -> read;
    otherwise balanced. An explicit density (argument, options["density"], or 'density: x' in the request) wins."""
    from engines.forge.density import detect
    return detect(task, density or (options or {}).get("density"))


def solve(task: str, *, workdir: str, task_id: str, client=None, files: list[str] | None = None,
          model: str | None = None, density: str | None = None, options: dict | None = None, **_) -> dict:
    from engines.forge.build import build
    from engines.forge.brief import complete
    # an open brief (audience, density, length, look) gets the recommended answers (engines/forge/brief.py)
    task = complete(task, files)
    if model:
        os.environ.setdefault("FORGE_MODEL", model)
    m = re.search(r"\b(\d{1,2})[- ]slide", task)
    slides = int(m.group(1)) if m else 10
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
    look, prof = brand_from(task, files)
    res = build(_brief(task, workdir, files), workdir, slides + len(fixed), client=client, task_id=task_id,
                density=mode, fixed_pages=fixed, extra_facts=extra, **look)
    pptx = res["pptx"]
    failing = [k for k, v in res["rules"].items() if v == "fail"]
    left = sum(len(v) for v in (res.get("unresolved") or {}).values())
    answer = (f"Built {Path(pptx).name}: {slides + len(fixed)} slides in the forge design system ({mode} density), "
              + (f"{len(fixed)} of them computed from {Path(wbk).name} (football field, comps, DCF, statements), "
                 if fixed else "")
              + (f"in the {prof.get('name') or 'company'} look ("
                 + ("its slide master, " if look.get("template") else "") + "colours, fonts"
                 + (", logo" if (look.get("brand") or {}).get("logo") else "") + "), " if prof else "")
              + "checked after rendering. "
              + (f"{left} items still flagged in the speaker notes ([QA] lines); rules failing: {', '.join(failing)}."
                 if left else "All content checks passed."))
    return {"ok": Path(pptx).exists(), "answer": answer, "artifacts": [pptx], "evidence": [],
            "turns": res.get("calls"), "reason": None}
