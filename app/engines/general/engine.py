"""meshpool-general: fallback for anything else, core tools only. No mandatory check (some tasks have none)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from meshpool_harness.core.client import LiveClient  # noqa: E402
from meshpool_harness.core.loop import Caps, run  # noqa: E402
from meshpool_harness.core.tools import Workspace  # noqa: E402

SYSTEM = """You are a capable assistant with file and shell tools. Do the task directly and concisely.
If the task produces a file, write it in the working directory and list it in artifacts. If you can verify the result
with a command, run it and cite it. Call finish with the complete answer.

Writing (checked automatically, answers above grade 9 are rejected): write for a busy reader at an 8th-9th grade
reading level. Lead with the answer. Short sentences (under 20 words), common words, active voice, one idea per
sentence, concrete numbers instead of adjectives. No jargon or filler ("leverage", "holistic", "robust",
"furthermore", "in order to"). Say exactly what happened, how much, and what to do next."""


def solve(task: str, *, workdir: str, task_id: str, max_turns: int = 15, model: str = "deepseek-v4.1-flash",
          client=None, phase: str = "phase_2_build") -> dict:
    ws = Workspace(workdir, os.path.join(workdir, ".run"))
    client = client or LiveClient(phase)
    ctx = {"task_id": task_id, "model": model, "profile": "deepseek", "role": "worker"}
    r = run(task, client=client, model=model, profile=None, ws=ws, ctx=ctx, system_text=SYSTEM,
            caps=Caps(max_turns=max_turns), temperature=0.5, require_check=False)
    return {"ok": r.ok, "answer": r.answer, "artifacts": r.artifacts, "evidence": r.evidence, "turns": r.turns,
            "reason": r.reason, "usage": r.usage}
