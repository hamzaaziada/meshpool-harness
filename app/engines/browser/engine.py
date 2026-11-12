"""Browser engine (computer use for text-only models): drives a real Chromium through engines.browser.ctl.

The model never needs screenshots: each snapshot is a short numbered list of clickable/typeable elements plus the
visible text. Works with any OpenAI-compatible model.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from meshpool_harness.core.client import LiveClient
from meshpool_harness.core.loop import Caps, run
from meshpool_harness.core.tools import Workspace

ROOT = Path(__file__).resolve().parents[2]
CTL = f"{sys.executable} -m engines.browser.ctl"

SYSTEM = f"""You operate a real web browser through shell commands. One command per step.
  {CTL} goto <url>                 open a page; prints a numbered element list and the page text
  {CTL} snapshot                   show the current page again
  {CTL} click <n>                  click element [n]
  {CTL} type <n> <text>            replace the text in field [n]
  {CTL} select <n> <option text>   choose an option in dropdown [n]
  {CTL} press <key>                e.g. Enter, Tab, Escape
  {CTL} hover <n>                  open menus that appear on hover
  {CTL} click_text <text>          click the visible element showing exactly this text (for items not listed)
  {CTL} back | screenshot <file.png>
  {CTL} batch '<JSON list of steps>'  several actions in ONE command (see below)
Element numbers change after every page change: always use the numbers from the latest output.
BATCH (saves steps): when you already know the next few actions, send them together, e.g.
  {CTL} batch '[{{"do":"type","target":{{"field":"Username"}},"text":"ann"}},{{"do":"check","target":{{"label":"Remember me"}}}},{{"do":"click","target":{{"role":"button","name":"Log in"}}}}]'
  steps: type(target,text) click dblclick hover check uncheck(target) select(target,option) press(key[,target])
         wait_text(text) expect_text(text)
  targets: {{"n":3}} | {{"field":"label next to the box"}} | {{"label":..}} | {{"role":"button","name":..}} | {{"text":..}}
  The batch stops at the first failed step and tells you which; the page after the batch is printed.
Use single commands when the next step depends on what a page shows. Check the result, and stop as soon as the goal is reached. Never type passwords,
payment details or personal data you were not given. When done, call finish with what you did and the evidence
(the final page text that proves it)."""


def solve(task: str, *, workdir: str, task_id: str, client=None, max_turns: int = 40, model: str | None = None, **_):
    env_root = os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])
    os.environ["PYTHONPATH"] = env_root
    import contextlib
    os.environ.setdefault("MH_BROWSER_DIFF", "1")   # our harness: after an action, show only what changed
    from engines.browser import ctl
    with contextlib.redirect_stdout(sys.stderr):   # keep stdout clean for the CLI's JSON
        ctl.main(["start"])
    ws = Workspace(workdir, os.path.join(workdir, ".run"))
    model = model or os.environ.get("MH_MODEL", "deepseek-v4.1-flash")
    if os.environ.get("MH_BROWSER_PREFETCH", "1") == "1":     # saves the first "look at the page" model call
        try:
            snap = ctl.call("snapshot")
            if snap.get("url") and not str(snap.get("url")).startswith("about:"):
                task += ("\n\nCurrent page (already captured; numbers are valid until the page changes):\n"
                         + ctl.render(snap))
        except Exception:
            pass
    system = SYSTEM
    if os.environ.get("MH_BROWSER_FINISH_SAME_TURN") == "1":    # saves the last model call on simple tasks
        system += ("\nWhen you are sure an action completes the task, call finish in the same turn as that action "
                   "(issue both tool calls together). If the action fails, the finish is not accepted.")
    if os.environ.get("MH_BROWSER_DERIVE") == "1":              # fewer careless first actions on a prefetched page
        system += ("\nBefore you type or choose a value that you work out from the page (a count, a date, an order, a "
                   "sum, which item matches), write the working in one short line in your reply, then act.")
    r = run(task, client=client or LiveClient("browser"), model=model, profile=None, ws=ws,
            ctx={"task_id": task_id, "model": model, "role": "worker"}, system_text=system,
            caps=Caps(max_turns=max_turns), require_check=False)
    return {"ok": r.ok, "answer": r.answer, "evidence": r.evidence, "turns": r.turns, "reason": r.reason,
            "usage": r.usage, "artifacts": []}
