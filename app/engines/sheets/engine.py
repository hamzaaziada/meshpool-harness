"""meshpool-sheets: the model writes a build script using engines.sheets.builder; deterministic QA gates finish."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from meshpool_harness.core.client import LiveClient  # noqa: E402
from meshpool_harness.core.loop import Caps, run  # noqa: E402
from meshpool_harness.core.tools import Workspace  # noqa: E402

SYSTEM = f"""You build Excel workbooks that a finance professional would accept without edits.

Work only in the current folder, which is your output folder: write every file there and run every command from there. The build and check commands already work from it. Do not cd into or read Meshpool's own source folders.

How you work:
1. Read any attached input files first (inspect with a short pandas script: sheets, columns, row counts).
2. Write ONE Python build script (build_workbook.py) using the helper library below, then run it with python.
3. Run the checker: python -m engines.sheets.check <file.xlsx>. Fix every issue it reports and rerun until it prints PASS.
4. Finish with a short answer (what the workbook contains, key outputs with their cell references) and cite the
   checker command as evidence. List the .xlsx in artifacts.

Workbook rules:
- Structure (banker style): wb = B.banker_workbook(title, purpose, author, date) gives Cover -> Inputs -> Calc ->
  Outputs -> Checks with colored tabs and a linked tab map. Put middle tabs in that order; add tabs only when needed
  (e.g. one per scenario), passing middle=(...). Use B.section(ws, row, "Revenue build") bands to group rows.
- Add at least one tie-out check with B.add_check(wb, "Summary ties to Calc", "=Outputs!B4-Calc!D5"); every check
  must show OK.
- Labels in plain words with units ("Revenue ($K)", "Growth per year (%)"). No jargon.
- Every computed number is a live Excel formula. Never type a computed value. Never hardcode a number in Calc or
  Outputs; put it in Inputs and reference it (named ranges for key assumptions).
- Units in labels ("Revenue ($K)"), consistent number formats, years/periods across columns, totals with SUM.
- No circular references. Guard divisions with IF(denominator=0, 0, ...).

Helper library (import from engines.sheets.builder as B; run scripts from {ROOT} or add it to sys.path):
  wb = B.new_workbook(("Inputs","Calc","Outputs"))   B.title(ws, text)   B.header(ws, row, labels, col=1)
  B.input_cell(ws, "B3", 0.1, "pct", name="Growth")  B.formula(ws, "C5", "=B5*(1+Growth)", "usd", total=False)
  B.section(ws, row, text, ncols)   B.add_check(wb, label, "=diff formula")
  B.widths(ws)   B.chart(ws, "bar"|"line"|"pie", (min_col,min_row,max_col,max_row), cats_ref, "E3", "Title", "usd")
  formats: usd, usd2, pct, int, x, num, or any Excel format string. wb.save(path)."""


def finish_check(answer, artifacts, ws):
    from engines.sheets.qa import check
    xl = [a for a in artifacts if a.endswith((".xlsx", ".xlsm"))]
    if not xl:
        return "ENGINE CHECK: list the produced .xlsx file in artifacts."
    for a in xl:
        p = a if os.path.isabs(a) else str(ws.root / a)
        if not os.path.exists(p):
            return f"ENGINE CHECK: artifact not found: {a}"
        r = check(p)
        from engines.sheets.qa import value
        if "Checks" in r["stats"]["sheets"]:
            try:
                status = value(p, "Checks!F3")
            except Exception:
                status = None
            if status not in (None, "OK"):
                r["issues"].append({"check": "checks_tab", "cell": "Checks!F3", "detail": f"shows {status}"})
        if "Cover" not in r["stats"]["sheets"]:
            r["issues"].append({"check": "structure", "cell": "", "detail": "no Cover tab; use B.banker_workbook"})
        if r["issues"]:
            return "ENGINE CHECK: workbook QA failed:\n" + "\n".join(
                f"- {i['check']} {i['cell']}: {i['detail']}" for i in r["issues"][:15])
    return None


def solve(task: str, *, workdir: str, task_id: str, max_turns: int = 30, model: str = "deepseek-v4.1-flash",
          client=None, phase: str = "phase_2_build") -> dict:
    ws = Workspace(workdir, os.path.join(workdir, ".run"))
    client = client or LiveClient(phase)
    ctx = {"task_id": task_id, "model": model, "profile": "deepseek", "role": "worker"}
    r = run(task, client=client, model=model, profile=None, ws=ws, ctx=ctx, system_text=SYSTEM,
            caps=Caps(max_turns=max_turns), temperature=0.0, finish_check=finish_check)
    return {"ok": r.ok, "answer": r.answer, "artifacts": r.artifacts, "evidence": r.evidence, "turns": r.turns,
            "reason": r.reason, "usage": r.usage}
