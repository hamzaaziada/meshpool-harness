"""Finish-time checks shared by the benchmark loop (core/loop.py) and the app agent (agent/core.py)."""
from __future__ import annotations

from .guards import TEST_PATH


def scope_report(ws, done: str = "finish") -> str | None:
    """MH_SCOPE_CHECK=1: at the first finish, if the change spreads past a minimal fix (more than one non-test source
    file, or more than 30 changed non-test lines), show the per-file diff size once and ask to drop what the task
    does not need. Rule fixed in advance, not tuned on any task. None = nothing to show."""
    try:
        out, _ = ws._run("git diff --numstat HEAD -- . ':!.run' ':!.junie' 2>/dev/null", 30, "bash", record=False)
    except Exception:                                 # noqa: BLE001 - a report must never break a run
        return None
    rows = []
    for line in str(out).splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit() or TEST_PATH.search(parts[2]):
            continue
        rows.append((parts[2], int(parts[0]) + int(parts[1])))
    if len(rows) <= 1 and sum(n for _, n in rows) <= 30:
        return None
    lines = "\n".join(f"  {f}: {n} lines" for f, n in sorted(rows, key=lambda r: -r[1])[:10])
    return (f"Scope check (shown once; {done} is accepted next time). Your change touches these non-test files:\n"
            + lines + "\nTests check the behaviour the task asks for, and every extra change is a chance to "
            "break one. Keep the smallest fix that does what the task asks: revert edits it does not need (git "
            "checkout -- <file> for a whole file), re-run your check, then {done}. If every change is needed, {done} "
            "again.".format(done=done))
