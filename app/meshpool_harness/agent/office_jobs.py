"""Office engine jobs (slides, sheets, data), shared by the built-in agent's office_task (agent/core.py) and the
Meshpool Office MCP server (agent/office_mcp_server.py): where the files go, how the engine process runs, and the
report the agent reads.

A job runs `python -P -m meshpool_harness run <request> --engine <engine> --out <folder>` in its own process and
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


# What each engine builds. A request that names a Word-type deliverable and none of these goes to make_document.
_OWN = {
    "slides": r"deck|slides?|presentation|pptx|powerpoint|keynote|pitch",
    "sheets": r"spreadsheets?|workbooks?|excel|xlsx|sheets?|models?|budgets?|forecasts?|tables?|formulas?|p&l|"
              r"cash ?flows?|valuations?|dcf|financials?",
    "data": r"data|datasets?|charts?|analysis|analy[sz]e|csv|plots?|graphs?|statistics|dashboards?|trends?",
}
_WORDISH = re.compile(r"\b(word documents?|word docs?|docx|memos?|memorandum|letters?)\b", re.I)
_FILLER = {"placeholder", "test", "testing", "todo", "tbd", "xxx", "lorem", "ipsum", "example", "dummy", "na", "n/a",
           "none", "null", "request", "task", "here", "the", "a", "an"}


def check_request(engine: str, request: str) -> str | None:
    """Why an engine job should not start, or None. Checked before the job folder exists, so a refusal costs nothing
    and leaves nothing behind. In a 0.0.5 release run the Work agent sent sheets the request "placeholder" for a Word
    memo, and the engine spent 7 minutes and 50 turns inventing an unrelated valuation workbook."""
    text = (request or "").strip()
    words = re.findall(r"[a-z0-9&/$%.]+", text.lower())
    if not [w for w in (x.strip(".") for x in words) if w not in _FILLER and len(w) > 1]:
        return (f"the request {text[:60]!r} does not describe a deliverable. Describe the whole file you want "
                "(what it is for, what goes in it, the numbers or sources to use) in `request`.")
    own = _OWN.get(engine)
    if own and _WORDISH.search(text) and not re.search(r"\b(" + own + r")\b", text, re.I):
        return (f"the request asks for a Word-type document ({_WORDISH.search(text).group(0)}), which the {engine} "
                "engine does not make. Make memos, letters, reports and other Word documents with make_document "
                "(or python-docx through bash); the engines make decks (slides), spreadsheets and models (sheets) "
                "and data analysis (data).")
    return None


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


def _python_m() -> list[str]:
    """`python -m meshpool_harness` that keeps its working folder off sys.path: the engine runs in an output folder
    inside the user's project, where a json.py would shadow Python's own (-P on 3.11+, a bootstrap on 3.10; the same
    command the updater relaunches with, minus this process's arguments)."""
    from meshpool_harness import updater
    cmd = updater._relaunch_cmd()
    return cmd[:len(cmd) - (len(sys.argv) - 1)] if len(sys.argv) > 1 else cmd


def new_job(engine: str, request: str, paths: list[str], root: Path) -> str:
    """Register a job (its output folder, command and build number) and return its id; run() runs it."""
    jid = uuid.uuid4().hex[:8]
    out = Path(root) / "Meshpool outputs" / f"{time.strftime('%Y%m%d-%H%M%S')}-{engine}-{jid}"   # one folder per job
    out.mkdir(parents=True, exist_ok=True)
    cmd = _python_m() + ["run", request, "--engine", engine, "--out", str(out)]
    if paths:
        cmd += ["--files", *paths]
    now = time.time()
    recent = [x for x in JOBS.values() if x["engine"] == engine and now - x["started"] < REBUILD_WINDOW_S]
    JOBS[jid] = {"engine": engine, "cmd": cmd, "out": out, "started": now, "build_no": len(recent) + 1,
                 "inputs": {Path(x).name for x in paths or []}}
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
    res = j.get("result") or {}
    # the engine's own files: the copies of the user's inputs and the engine's build scripts are not results
    arts = [Path(a) if Path(a).is_absolute() else out / a for s in res.get("steps") or [] for a in s.get("artifacts") or []]
    made = sorted({a.resolve() for a in arts if a.is_file()}) or [
        p for p in sorted(out.rglob("*"))
        if p.is_file() and not any(x.startswith(".") for x in p.relative_to(out).parts)
        and not (p.parent == out and p.name in j.get("inputs", set())) and p.suffix not in (".py", ".run", ".log")]
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
