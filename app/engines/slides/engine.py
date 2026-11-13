"""meshpool-slides (fallback version): the model writes a deck spec; the deterministic renderer owns layout.
Priority 3: to be rebuilt to consulting standard (see PRIORITIES.md)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from meshpool_harness.core.client import LiveClient  # noqa: E402
from meshpool_harness.core.loop import Caps, run  # noqa: E402
from meshpool_harness.core.tools import Workspace  # noqa: E402

EXAMPLE = (ROOT / "engines/slides/examples/sample_deck.json").read_text()

SYSTEM = f"""You write consulting-grade presentation decks as a JSON spec. A renderer owns all layout; you own the
storyline and the words.

Work only in the current folder, which is your output folder: write every file there and run every command from there. The build and check commands already work from it. Do not cd into or read Meshpool's own source folders.

Process:
1. Read inputs. Decide the storyline first: the one-sentence answer, then 3-5 supporting arguments, each a section.
2. Write deck.json (schema below). Every content slide title is an action title: a full sentence stating the
   takeaway (max 20 words). One idea per slide. Max 5 bullets, max 15 words each. Put the source in "source".
   Use chart/table/kpi slides whenever you have numbers; use real numbers from the inputs only.
3. Run: python -m engines.slides.build deck.json deck.pptx   Fix every error and STYLE warning, rerun until PASS.
4. Finish citing that command; list deck.pptx in artifacts.

Slide types and fields: title(title, subtitle, meta) | section(title, number, subtitle) | bullets(title, bullets[str or
{{text, sub[]}}], callout) | two_column(title, left{{heading, bullets}}, right{{heading, bullets}}) | chart(title,
chart{{type: bar|column|stacked_column|line|pie|doughnut, categories[], series[{{name, values[numbers]}}],
number_format}}, takeaways[]) | table(title, columns[], rows[[...]], highlight_row, takeaway) | kpi(title,
kpis[{{value, label, delta}}] 2-4, takeaway) | timeline(title, items[{{date, label, detail}}] 2-7) | quote(quote,
attribution, title) | image_text(title, image path, bullets). All slides may have notes and source.
Deck: {{"title", "requested_slide_count" (int if the user asked for a number, else null), "slides": [...]}}.

Writing (checked automatically, answers above grade 9 are rejected): write for a busy reader at an 8th-9th grade
reading level. Lead with the answer. Short sentences (under 20 words), common words, active voice, one idea per
sentence, concrete numbers instead of adjectives. No jargon or filler ("leverage", "holistic", "robust",
"furthermore", "in order to"). Say exactly what happened, how much, and what to do next.
This applies to titles, bullets, takeaways and notes.

Example of the expected quality and density:
{EXAMPLE}"""


def finish_check(answer, artifacts, ws):
    from engines.slides import qa
    decks = [a for a in artifacts if a.endswith(".pptx")]
    if not decks:
        return "ENGINE CHECK: list the produced .pptx in artifacts."
    for a in decks:
        p = a if os.path.isabs(a) else str(ws.root / a)
        if not os.path.exists(p):
            return f"ENGINE CHECK: artifact not found: {a}"
        issues = qa.check(p)
        from pptx import Presentation
        from meshpool_harness.core.readability import check as plain
        text = "\n".join(sh.text_frame.text for s in Presentation(p).slides for sh in s.shapes if sh.has_text_frame)
        issues += [{"slide": 0, "check": i["check"], "detail": i["detail"]} for i in plain(text)]
        if issues:
            return "ENGINE CHECK: deck QA failed:\n" + "\n".join(f"- slide {i['slide']}: {i['check']} {i['detail']}" for i in issues[:12])
    return None


def solve(task: str, *, workdir: str, task_id: str, max_turns: int = 20, model: str = "deepseek-v4.1-flash",
          client=None, phase: str = "phase_2_build") -> dict:
    ws = Workspace(workdir, os.path.join(workdir, ".run"))
    client = client or LiveClient(phase)
    ctx = {"task_id": task_id, "model": model, "profile": "deepseek", "role": "worker"}
    r = run(task, client=client, model=model, profile=None, ws=ws, ctx=ctx, system_text=SYSTEM,
            caps=Caps(max_turns=max_turns), temperature=0.3, finish_check=finish_check)
    return {"ok": r.ok, "answer": r.answer, "artifacts": r.artifacts, "evidence": r.evidence, "turns": r.turns,
            "reason": r.reason, "usage": r.usage}
