"""Office engine jobs (slides, sheets, data), shared by the built-in agent's office_task (agent/core.py) and the
Meshpool Office MCP server (agent/office_mcp_server.py): where the files go, how the engine process runs, and the
report the agent reads.

A job runs `python -m meshpool_harness run <request> --engine <engine> --out <folder>` in its own process and
prints its result as JSON on stdout. Files land in "<project>/Meshpool outputs/<stamp>-<engine>".
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
JOBS: dict[str, dict] = {}
REBUILD_LIMIT = int(os.environ.get("OFFICE_REBUILD_LIMIT", "3"))   # builds of one engine in a session before we say stop
REBUILD_WINDOW_S = 45 * 60


def resolve_files(root: Path, files: list | None) -> tuple[list[str], str]:
    """Input files (relative to root, or absolute) -> (absolute paths, "" or the first file not found)."""
    paths = []
    for f in files or []:
        p = Path(str(f)).expanduser()
        p = p if p.is_absolute() else (Path(root) / p)
        if not p.exists():
            return [], str(f)
        paths.append(str(p.resolve()))
    return paths, ""


def new_job(engine: str, request: str, paths: list[str], root: Path) -> str:
    """Register a job (its output folder, command and build number) and return its id; run() runs it."""
    jid = uuid.uuid4().hex[:8]
    out = Path(root) / "Meshpool outputs" / f"{time.strftime('%Y%m%d-%H%M%S')}-{engine}"
    out.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "meshpool_harness", "run", request, "--engine", engine, "--out", str(out)]
    if paths:
        cmd += ["--files", *paths]
    now = time.time()
    recent = [x for x in JOBS.values() if x["engine"] == engine and now - x["started"] < REBUILD_WINDOW_S]
    JOBS[jid] = {"engine": engine, "cmd": cmd, "out": out, "started": now, "build_no": len(recent) + 1}
    return jid


def run(jid: str, timeout: float | None = None, should_stop=None) -> None:
    """Run a job's engine process to the end. Its output is read while it runs (a full pipe would block the engine
    until the timeout). should_stop() true: the process is killed and the job is marked stopped."""
    j = JOBS[jid]
    timeout = float(timeout if timeout is not None else os.environ.get("OFFICE_JOB_TIMEOUT_S", "1800"))
    env = dict(os.environ, PYTHONPATH=str(APP), PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    env.pop("MH_EVENT_LOG", None)
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}     # CREATE_NO_WINDOW: no console flashes up
    try:
        proc = subprocess.Popen(j["cmd"], cwd=str(j["out"]), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", **kw)
        end = time.time() + timeout
        while True:
            try:
                out, err = proc.communicate(timeout=0.5)   # keeps reading both pipes; nothing is lost on a timeout
                break
            except subprocess.TimeoutExpired:
                stop = bool(should_stop and should_stop())
                if stop or time.time() > end:
                    proc.kill()
                    proc.communicate()
                    j["result"], j["stopped"] = {}, stop
                    j["stderr"] = ("Stopped." if stop else
                                   f"The engine took longer than {round(timeout / 60)} minutes and was stopped.")
                    return
        try:
            j["result"] = json.loads(out[out.find("{"):]) if "{" in out else {}
        except ValueError:
            j["result"] = {}
        j["stderr"] = (err or "")[-1500:]
    except Exception as e:  # noqa: BLE001  report, never crash the caller
        j["result"], j["stderr"] = {}, f"{type(e).__name__}: {e}"
    finally:
        j["done"] = time.time()


def report(jid: str, rel_to: Path | None = None) -> tuple[str, bool]:
    """(the text the agent reads, failed). Files are listed relative to rel_to when given, else absolute."""
    j = JOBS[jid]
    if not j.get("done"):
        mins = int((time.time() - j["started"]) // 60)
        return (f"Still working on the {j['engine']} job ({mins} min so far). Job id: {jid}. "
                f"Call office_job_status with this id to get the result."), False
    out: Path = j["out"]
    made = [p for p in sorted(out.rglob("*"))
            if p.is_file() and not any(x.startswith(".") for x in p.relative_to(out).parts)]
    res = j.get("result") or {}
    if not res:
        return f"The {j['engine']} engine failed. {j.get('stderr', '')}", True
    notes = "\n".join((s.get("answer") or "") for s in res.get("steps") or []).strip()
    qa = ""
    try:                                    # forge QA on every produced deck/workbook (never fails the job)
        if str(APP) not in sys.path:
            sys.path.insert(0, str(APP))
        from engines.forge.tools import qa_summary
        qa = qa_summary([str(m) for m in made if m.suffix in (".pptx", ".xlsx")])
    except Exception:  # noqa: BLE001
        pass
    # "passed" only when nothing is flagged: an engine's ok means "a file was made", and a headline saying "passed"
    # above a list of failing rules sent the agent into rebuild after rebuild (seen live: 8 deck builds, 20 minutes).
    flagged = "rules failing:" in notes and not re.search(r"rules failing:\s*(none)?\s*$", notes, re.M) \
        or bool(re.search(r"forge rules failing: (?!none)", qa))
    if not res.get("ok"):
        checks = "did not all pass (review the file)"
    elif flagged:
        checks = "flagged some items (listed below); the file is built and usable"
    else:
        checks = "passed"

    def show(p: Path) -> str:
        try:
            return str(p.relative_to(rel_to)) if rel_to else str(p)
        except ValueError:
            return str(p)
    lines = [f"The {j['engine']} engine finished; its quality checks {checks}.", "Files:"] + [f"- {show(m)}" for m in made]
    if notes:
        lines.append("Engine notes: " + notes[:2500])
    if qa:
        lines += ["Forge QA:", qa]
    n = j.get("build_no", 1)
    if n >= REBUILD_LIMIT:
        lines.append(f"This is build {n} from the {j['engine']} engine in this session. If you are rebuilding to clear "
                     "check results, stop here: rebuilding again rarely clears the rest. Deliver the best build so "
                     "far and tell the user which checks remain. Build again only for a new request from the user.")
    return "\n".join(lines), not made
