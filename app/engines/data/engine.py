"""meshpool-data: quantitative and qualitative analysis with number tracing and verbatim-quote checks."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from meshpool_harness.core.client import LiveClient  # noqa: E402
from meshpool_harness.core.loop import Caps, run  # noqa: E402
from meshpool_harness.core.tools import Workspace  # noqa: E402
from engines.data.checks import check_quotes, trace_numbers  # noqa: E402

SYSTEM = """You are a rigorous analyst. Every claim you make is backed by code you ran or text you can quote.

Quantitative work:
1. Load each data file in a Python script and print: shape, columns with dtypes, null counts, and 3 sample rows.
2. Parse dates/units explicitly. Print row counts before and after every filter, merge, and dedupe.
3. Compute every number you will report in code and PRINT it with a clear label. Save charts as PNG files.
4. In your answer, only use numbers that appear in your printed output (rounding is fine).

Qualitative work (interviews, survey text, documents):
1. Build or apply a codebook (theme -> definition). Tag excerpts with their source file and a short id.
2. Count themes in code and print the counts.
3. Quote only verbatim text copied from the sources, in double quotes, with the source id.
4. Code by judgment, not keyword search: read every transcript in full (read_file), and for each participant
   sentence decide which codebook themes it matches by meaning (paraphrases count; one sentence can match several
   themes). Write the evidence table (file, sentence, theme) to evidence.csv, then compute every count from that
   table in code.

Finish: a concise answer (lead with the conclusion, then 3-6 supporting findings with numbers), list output files
(charts, tables) in artifacts, and cite the analysis command(s) as evidence. Your answer is checked automatically:
numbers not found in your printed output and quotes not found in the sources are rejected.

Writing (checked automatically, answers above grade 9 are rejected): write for a busy reader at an 8th-9th grade
reading level. Lead with the answer. Short sentences (under 20 words), common words, active voice, one idea per
sentence, concrete numbers instead of adjectives. No jargon or filler ("leverage", "holistic", "robust",
"furthermore", "in order to"). Say exactly what happened, how much, and what to do next."""


LEAN = """

Cost rules (output tokens cost 200x more than re-read input, so write less):
- Write ONE compact analysis script (analysis.py). No comments, no docstrings, no defensive boilerplate.
- To change it, use edit on the specific lines; never rewrite the whole script.
- Print only the values you need, each on one labelled line. Do not print whole dataframes.
- Issue independent reads/commands together in one turn.
- Final answer: the conclusion and the key numbers only, in a few short sentences."""


def make_check(sources: dict[str, str]):
    def finish_check(answer, artifacts, ws):
        problems = trace_numbers(answer, ws.all_output())
        problems += check_quotes(answer, sources) if sources else []
        from meshpool_harness.core.readability import check as plain
        problems += plain(answer)
        if problems:
            return ("ENGINE CHECK: answer rejected.\n" + "\n".join(f"- {p['check']}: {p['detail']}" for p in problems[:12])
                    + "\nPrint these values from code (or remove them), and only quote text that exists verbatim.")
        return None
    return finish_check


def solve(task: str, *, workdir: str, task_id: str, files: list[str] | None = None, max_turns: int = 30,
          model: str = "deepseek-v4.1-flash", client=None, phase: str = "phase_2_build") -> dict:
    ws = Workspace(workdir, os.path.join(workdir, ".run"))
    sources = {}
    for f in files or []:
        if f.lower().endswith((".txt", ".md", ".vtt", ".srt")) and os.path.exists(f):
            sources[os.path.basename(f)] = Path(f).read_text(errors="replace")
        elif f.lower().endswith(".docx") and os.path.exists(f):
            try:
                import docx
                sources[os.path.basename(f)] = "\n".join(p.text for p in docx.Document(f).paragraphs)
            except Exception:
                pass
    client = client or LiveClient(phase)
    ctx = {"task_id": task_id, "model": model, "profile": "deepseek", "role": "worker"}
    system = SYSTEM + (LEAN if os.environ.get("MH_DATA_VARIANT", "v2") == "v2" else "")
    r = run(task, client=client, model=model, profile=None, ws=ws, ctx=ctx, system_text=system,
            caps=Caps(max_turns=max_turns), temperature=0.0, finish_check=make_check(sources))
    return {"ok": r.ok, "answer": r.answer, "artifacts": r.artifacts, "evidence": r.evidence, "turns": r.turns,
            "reason": r.reason, "usage": r.usage}
