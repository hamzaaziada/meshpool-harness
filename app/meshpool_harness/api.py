"""Safe entry point for apps that embed the harness.

`run_task` never raises for a failed task. Gateway errors, model errors, tool crashes and bad input all come back as
{"ok": False, "error": "..."}, so a bad network moment or a broken task cannot take the host app down. It does not
call sys.exit, and it writes only inside `workdir` (its .run/ folder) unless the app sets MH_TIMING_LOG or
MH_USAGE_LOG.

    from meshpool_harness import run_task
    result = run_task("Fix the failing test in tests/test_api.py", workdir="/path/to/repo", preset="v4")
    if not result["ok"]:
        show(result["error"])

Notes for app authors:
- Settings are environment variables and are process-wide. `preset=` fills in unset MH_* variables for the whole
  process (explicit variables win). Run tasks that need different presets in separate processes.
- The browser engine starts a Chromium daemon (engines/browser/ctl.py) that outlives the call. Stop it with
  `python -m engines.browser.ctl stop` when the app shuts down.
- KeyboardInterrupt and SystemExit are not swallowed, so the app can still shut down normally.
"""
from __future__ import annotations

import importlib
import os
import time
import traceback

ENGINES = ("code", "browser", "data", "sheets", "slides")


def run_task(task: str, *, workdir: str, engine: str = "code", task_id: str | None = None,
             preset: str | None = None, client=None, **kw) -> dict:
    """Run one task with the chosen engine and always return a dict: ok, answer, reason, turns, usage, error."""
    t0 = time.time()
    base = {"ok": False, "answer": "", "reason": "error", "turns": 0, "usage": {}, "error": None, "wall_s": 0.0}
    try:
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task text is empty")
        if engine not in ENGINES:
            raise ValueError(f"unknown engine {engine!r}; choose one of {', '.join(ENGINES)}")
        if not workdir or not os.path.isdir(workdir):
            raise ValueError(f"workdir does not exist: {workdir!r}")
        if preset:
            from .presets import PRESETS, apply
            if preset not in PRESETS:
                raise ValueError(f"unknown preset {preset!r}; choose one of {', '.join(PRESETS)}")
            apply(preset)
        mod = importlib.import_module(f"engines.{engine}.engine")
        out = mod.solve(task, workdir=workdir, task_id=task_id or f"app-{int(t0)}", client=client, **kw)
        res = dict(base, **(out if isinstance(out, dict) else {"answer": str(out)}))
        res["ok"] = bool(res.get("ok"))
        res["error"] = None
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:                          # noqa: BLE001 - the whole point: never crash the host app
        res = dict(base, error=f"{type(e).__name__}: {e}",
                   detail="".join(traceback.format_exception(type(e), e, e.__traceback__))[-2000:])
    res["wall_s"] = round(time.time() - t0, 1)
    return res
