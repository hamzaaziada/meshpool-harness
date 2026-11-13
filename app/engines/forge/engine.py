"""Forge deck engine: request (+ input files) -> consulting-grade .pptx in the output folder.

The model writes the storyline and the slide content as JSON; the forge renderer applies the design system
(engines/forge/render.py) and validate_pptx checks the rendered file. Used by `meshpool run --engine forge` and,
by default, by the slides engine.
"""
from __future__ import annotations

import os
import re
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


def solve(task: str, *, workdir: str, task_id: str, client=None, files: list[str] | None = None,
          model: str | None = None, **_) -> dict:
    from engines.forge.build import build
    from engines.forge.brief import complete
    # an open brief (audience, density, length, look) gets the recommended answers (engines/forge/brief.py)
    task = complete(task, files)
    if model:
        os.environ.setdefault("FORGE_MODEL", model)
    m = re.search(r"\b(\d{1,2})[- ]slide", task)
    slides = int(m.group(1)) if m else 10
    res = build(_brief(task, workdir, files), workdir, slides, client=client, task_id=task_id, theme=theme_from(task))
    pptx = res["pptx"]
    failing = [k for k, v in res["rules"].items() if v == "fail"]
    left = sum(len(v) for v in (res.get("unresolved") or {}).values())
    answer = (f"Built {Path(pptx).name}: {slides} slides in the forge design system, checked after rendering. "
              + (f"{left} items still flagged in the speaker notes ([QA] lines); rules failing: {', '.join(failing)}."
                 if left else "All content checks passed."))
    return {"ok": Path(pptx).exists(), "answer": answer, "artifacts": [pptx], "evidence": [],
            "turns": res.get("calls"), "reason": None}
