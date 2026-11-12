"""E4: parallel attempts + cross-test voting (no access to hidden tests).

k independent attempts run in parallel, each in its own copy of the repo and its own container. Each attempt usually
writes a reproduction test. Every candidate patch is then run against the OTHER attempts' new tests; the patch that
passes the most of its rivals' tests wins (ties: passes regression gate, then smaller diff). Rationale: independent
attempts that understood the issue write tests that encode the expected behaviour; a symptom-only fix fails them.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from .gates import TEST_FILE

EXCL = [":!.run", ":!.junie"]


def _sh(cmd, cwd=None, timeout=600):
    r = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout,
                       errors="replace")
    return r.returncode, r.stdout + r.stderr


def _container_info(container: str):
    info = json.loads(subprocess.run(["docker", "inspect", container], capture_output=True, text=True).stdout)[0]
    return info["Config"]["Image"]


def _start(image, host, cdir, name):
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    rc, out = _sh(["docker", "run", "-d", "--name", name, "-v", f"{host}:{cdir}", "-w", cdir, "--entrypoint", "sleep",
                   image, "infinity"])
    if rc:
        raise RuntimeError(out[-300:])


def _diff(host):
    _sh(["git", "add", "-A", "--", ".", *EXCL], cwd=host)
    _, d = _sh(["git", "diff", "--cached", "--", ".", *EXCL], cwd=host)
    _sh(["git", "reset", "-q"], cwd=host)
    return d


def _new_tests(host):
    _sh(["git", "add", "-A", "--", ".", *EXCL], cwd=host)
    _, names = _sh(["git", "diff", "--cached", "--name-only", "--diff-filter=A", "--", ".", *EXCL], cwd=host)
    _sh(["git", "reset", "-q"], cwd=host)
    return [n for n in names.splitlines() if TEST_FILE.search(n) and n.endswith(".py")]


def _source_patch(host):
    """Diff without new test files: the actual fix."""
    tests = set(_new_tests(host))
    _sh(["git", "add", "-A", "--", ".", *EXCL], cwd=host)
    _, d = _sh(["git", "diff", "--cached", "--", ".", *EXCL, *[f":!{t}" for t in tests]], cwd=host)
    _sh(["git", "reset", "-q"], cwd=host)
    return d


def solve_k(task: str, *, workdir: str, task_id: str, k: int = 3, max_turns: int = 40, solve_fn=None) -> dict:
    from .engine import solve
    solve_fn = solve_fn or solve
    m = re.search(r"docker exec -w (\S+) (\S+) bash -lc", task)
    if not m:
        return solve_fn(task, workdir=workdir, task_id=task_id, max_turns=max_turns)
    cdir, base_c = m.group(1), m.group(2)
    image = _container_info(base_c)
    base = Path(workdir)
    runs = []
    for i in range(k):
        host = Path(f"{workdir}__a{i}")
        if host.exists():
            shutil.rmtree(host)
        shutil.copytree(base, host, symlinks=True)
        name = f"{base_c}_a{i}"[:60]
        _start(image, str(host), cdir, name)
        runs.append({"i": i, "host": str(host), "c": name, "task": task.replace(base_c, name)})
    temps = [0.0, 0.4, 0.8]

    def go(r):
        os.environ.setdefault("MH_TEMP", "0")
        try:
            r["out"] = solve_fn(r["task"], workdir=r["host"], task_id=f"{task_id}-a{r['i']}", max_turns=max_turns,
                                temperature=temps[r["i"] % len(temps)])
        except Exception as e:
            r["out"] = {"ok": False, "reason": f"{type(e).__name__}: {e}", "usage": {}}

    th = [threading.Thread(target=go, args=(r,)) for r in runs]
    [t.start() for t in th]
    [t.join() for t in th]

    for r in runs:
        r["patch"] = _source_patch(r["host"])
        r["tests"] = {t: (Path(r["host"]) / t).read_text(errors="replace") for t in _new_tests(r["host"])}
    cands = [r for r in runs if r["patch"].strip()]
    # cross-test voting
    for r in cands:
        r["votes"], r["vote_total"] = 0, 0
        for o in runs:
            if o is r or not o["tests"]:
                continue
            for name, body in o["tests"].items():
                dst = Path(r["host"]) / ".run" / "xtest" / f"o{o['i']}_{Path(name).name}"
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(body)
                rel = os.path.relpath(dst, r["host"])
                rc, out = _sh(["docker", "exec", "-w", cdir, r["c"], "bash", "-lc",
                               f"python -m pytest -q -p no:cacheprovider {rel} 2>&1 | tail -3"], timeout=600)
                r["vote_total"] += 1
                r["votes"] += int(" passed" in out and " failed" not in out and " error" not in out)
    for r in runs:
        subprocess.run(["docker", "rm", "-f", r["c"]], capture_output=True)
    usage = {"prompt_tokens": sum((r["out"].get("usage") or {}).get("prompt_tokens", 0) for r in runs),
             "completion_tokens": sum((r["out"].get("usage") or {}).get("completion_tokens", 0) for r in runs)}
    if not cands:
        return {"ok": False, "answer": "", "reason": "no attempt produced a patch", "usage": usage, "turns": 0}
    best = max(cands, key=lambda r: (r["votes"] / max(1, r["vote_total"]), bool(r["out"].get("ok")),
                                     -len(r["patch"])))
    # apply the winning source patch (plus its own new tests) to the original workdir
    full = _diff(best["host"])
    p = Path(workdir) / ".run" / "ensemble_winner.patch"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(full)
    rc, out = _sh(["git", "apply", "--whitespace=nowarn", str(p)], cwd=workdir)
    summary = [{"attempt": r["i"], "ok": r["out"].get("ok"), "votes": f"{r.get('votes', 0)}/{r.get('vote_total', 0)}",
                "patch_lines": len(r["patch"].splitlines())} for r in runs]
    for r in runs:
        shutil.rmtree(r["host"], ignore_errors=True)
    return {"ok": rc == 0, "answer": best["out"].get("answer", ""), "reason": f"ensemble k={k}: {summary}",
            "usage": usage, "turns": sum(r["out"].get("turns", 0) or 0 for r in runs), "attempts": summary}
