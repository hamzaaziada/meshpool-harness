"""meshpool-code: repository and terminal work on the shared core loop."""
from __future__ import annotations

import os
import re
import shutil
import subprocess as _sp
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from meshpool_harness.core.client import LiveClient  # noqa: E402
from meshpool_harness.core.loop import Caps, run  # noqa: E402
from meshpool_harness.core.tools import Workspace  # noqa: E402

# Byte-stable system prompt (no task-specific text, no timestamps): shared prefix across all code tasks.
SYSTEM = """You are a senior software engineer fixing issues in an existing repository with a few precise tool calls.

Method (follow in order, keep each step short):
1. Locate: grep for the identifiers, error text, or behaviour named in the task. Read only the relevant line ranges.
2. Reproduce: run the most specific existing test, or write a tiny script/test that shows the bug. Note the failing line.
3. Fix the root cause, not the symptom. The entry point named in the task is often a thin wrapper: follow the call
   path to the shared function where the wrong behaviour originates, and fix it there so every caller (sibling APIs,
   other formats/backends) gets the correct behaviour. Grep other call sites of anything you change. Keep the diff
   minimal and match the surrounding style and the project's existing error types.
   If a value has the wrong type or format where it crashes, trace it back to where that value is PRODUCED
   (serializer, parser, constructor, config loader) and fix it there; patching the consumer that crashed leaves every
   other consumer broken.
4. Verify: rerun the reproduction and the closest existing tests. If something fails, quote the key error line and
   reason about it before changing anything else.
5. Finish: call finish with a two-sentence answer and the exact commands that passed after your last edit.

Rules:
- Do not modify or delete existing tests. You may add a new test file.
- Use edit for changes to existing files; copy `old` exactly from read_file output without line numbers.
- Never guess APIs: read the definition first.
- Keep tool output small: use grep with a path, read_file with start/end, and targeted test selections.
- Batch independent calls: when you need several reads or greps, issue them together in one turn.
- If a command needs a container or special environment, use exactly the command form given in the task."""


# v2 minimal profile (MH_HARNESS_PROFILE=minimal): a short prompt and a bare loop, the shape of the plain-DeepSeek baseline
# that out-solved v1. Every v1 extra keeps its own flag, so the ablation can add each one back and measure it.
SYSTEM_MIN = """You are a software engineer working in an existing repository or terminal environment. Use the tools.
Find the relevant code, reproduce the problem when you can, make the smallest correct change at the root cause, and
run the relevant tests or commands to check it. Batch independent reads and searches in one turn. Keep tool output
small (grep with a path, read_file with start/end, targeted tests). Do not modify or delete existing tests; you may
add new tests. When the task is done and checked, call finish with a short answer and the commands that passed."""

# feature flag -> (v1 default, minimal default)
FEATURES = {"MH_TESTCMD": ("1", "1"), "MH_CODE_HINTS": ("1", "0"), "MH_ISSUE_REPRO": ("1", "0"),
            "MH_NEAREST_TEST": ("1", "0"), "MH_REGRESSION_GATE": ("1", "0"), "MH_SCOPE_GATE": ("1", "0"),
            "MH_RELATED_SITES": ("1", "0"), "MH_ESCALATE": ("1", "0")}


def feature(name: str) -> bool:
    v1, mini = FEATURES[name]
    return os.environ.get(name, mini if os.environ.get("MH_HARNESS_PROFILE") == "minimal" else v1) == "1"


def checks_green(ws) -> bool:
    """v2: attempt 1 ran out of budget but its last check after the last edit passed: keep it, do not restart."""
    if not getattr(ws, "edits", 0):
        return False
    after = [c for c in ws.commands if c.get("kind") == "check" and c.get("after_edit") == ws.edits]
    return bool(after) and after[-1].get("code") == 0


# ---- exact snapshot / restore of the user's working tree (escalation resets between attempts)
_EXCLUDE = (":!.run", ":!.junie")      # harness folders: never snapshotted, never reset


def _git(workdir: str, *args: str, env: dict | None = None, stdin: bytes | None = None):
    return _sp.run(["git", *args], cwd=workdir, capture_output=True, env=env, input=stdin)


def _worktree_tree(workdir: str) -> str | None:
    """The working tree as a git tree id: tracked and untracked files that are not ignored, binary-safe, with modes.
    Built in a throwaway index (seeded from the real one for its stat cache): no ref, file or index entry changes."""
    fd, tmp = tempfile.mkstemp(prefix="mh-index-")
    os.close(fd)
    try:
        real = _git(workdir, "rev-parse", "--git-path", "index").stdout.decode().strip()
        real_p = Path(workdir, real) if real else None
        if real_p and real_p.is_file():
            shutil.copyfile(real_p, tmp)
        else:
            os.remove(tmp)
        env = dict(os.environ, GIT_INDEX_FILE=tmp)
        if _git(workdir, "add", "-A", "--", ".", *_EXCLUDE, env=env).returncode:
            return None
        r = _git(workdir, "write-tree", env=env)
        return r.stdout.decode().strip() if r.returncode == 0 else None
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def snapshot(workdir: str) -> tuple[str, str] | None:
    """(working-tree tree id, index tree id), or None when the state cannot be captured (e.g. a merge in progress)."""
    idx = _git(workdir, "write-tree")
    tree = _worktree_tree(workdir)
    if idx.returncode or not tree:
        return None
    return tree, idx.stdout.decode().strip()


def restore(workdir: str, snap: tuple[str, str]) -> bool:
    """Put the working tree and index back to `snap` exactly. Only paths that differ from it are touched: files
    changed or deleted since are rewritten from the snapshot, files created since are removed; ignored files and
    the harness folders are left alone. True when the tree afterwards matches the snapshot."""
    tree, index_tree = snap
    cur = _worktree_tree(workdir)
    if cur is None:
        return False
    if cur != tree:
        r = _git(workdir, "diff", "--name-status", "--no-renames", "--relative", "-z", tree, cur)
        if r.returncode:
            return False
        f = r.stdout.decode("utf-8", "surrogateescape").split("\0")
        changes = list(zip(f[0::2], f[1::2]))
        back = [p for st, p in changes if not st.startswith("A")]
        if back:
            fd, tmp = tempfile.mkstemp(prefix="mh-index-")
            os.close(fd)
            os.remove(tmp)
            try:
                env = dict(os.environ, GIT_INDEX_FILE=tmp)
                if _git(workdir, "read-tree", tree, env=env).returncode:
                    return False
                stdin = b"\0".join(p.encode("utf-8", "surrogateescape") for p in back) + b"\0"
                if _git(workdir, "checkout-index", "-f", "-z", "--stdin", env=env, stdin=stdin).returncode:
                    return False
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        for st, p in changes:
            if st.startswith("A"):
                fp = Path(workdir, p)
                if fp.is_symlink() or fp.is_file():
                    fp.unlink()
    if _git(workdir, "read-tree", index_tree).returncode:
        return False
    return _worktree_tree(workdir) == tree


def solve(task: str, *, workdir: str, task_id: str, max_turns: int = 60, model: str = "deepseek-v4.1-flash",
          client=None, phase: str = "phase_2_build", temperature: float = 0.0) -> dict:
    import re as _re
    cont = cdir = None
    mounted = True
    m = _re.search(r"docker exec -w (\S+) (\S+) bash -lc", task)
    if m and os.environ.get("MH_CONTAINER_NATIVE", "1") == "1":
        cdir, cont = m.group(1), m.group(2)
        mounted = bool(_re.search(r"mounted into the container", task))
        task += (f"\n\n<harness_note>Your bash and run_tests tools already execute inside the container "
                 f"(cwd {cdir}). Write plain commands, without any docker exec prefix. "
                 + ("read_file/edit/write_file operate on the same files (container paths are accepted)."
                    if mounted else "read_file/grep/glob/edit/write_file operate directly on the container's files.")
                 + "</harness_note>")
    ws = Workspace(workdir, os.path.join(workdir, ".run"), container=cont, container_dir=cdir, mounted=mounted)
    if feature("MH_TESTCMD") and (cont is None or mounted):
        try:
            from .testcmd import discover
            tc = discover(workdir, cont, cdir)
            if tc:
                task += "\n\n<how_to_run_tests>" + tc + "</how_to_run_tests>"
        except Exception:
            pass
    if os.environ.get("MH_MODE") == "terminal":
        task += ("\n\n<terminal_mode>\nThis is a terminal task: changes made with shell commands count as progress. "
                 "Commands may run up to 1800 s (set timeout_s). For anything slower (large builds, training, servers), "
                 "start it in the background: `nohup <cmd> > /tmp/job.log 2>&1 &`, then poll with "
                 "`sleep 60; tail -5 /tmp/job.log`. Before finishing, check the exact end state the task asks for "
                 "(files, outputs, running services) with a command.\n</terminal_mode>")
    if feature("MH_CODE_HINTS"):
        from .locate import hints
        import re as _re
        m = _re.search(r"<issue>(.*?)</issue>", task, _re.S)
        h = hints(m.group(1) if m else task, workdir)
        if h:
            task = task + "\n\n<repo_hints>\n" + h + "\n</repo_hints>"
    finish_check = None
    if cont and mounted:
        from . import gates
        m2 = _re.search(r"<issue>(.*?)</issue>", task, _re.S)
        if m2 and feature("MH_ISSUE_REPRO"):
            rep = gates.issue_repro(m2.group(1), workdir, cont, cdir)
            if rep:
                task += ("\n\n<issue_examples_run_before_any_change>\nThe harness ran the code examples from the "
                         "issue on the unmodified repository:\n" + rep + "\n</issue_examples_run_before_any_change>")
        if m2 and feature("MH_NEAREST_TEST"):
            nt = gates.nearest_test(m2.group(1), workdir)
            if nt:
                task += "\n\n<existing_test_style>\n" + nt + "\n</existing_test_style>"
        if feature("MH_REGRESSION_GATE"):
            fired = {"n": 0, "scope": 0}

            def finish_check(answer, artifacts, ws_):
                if fired["scope"] < 1 and m2 and feature("MH_SCOPE_GATE"):
                    msg = gates.scope_gate(workdir, m2.group(1))
                    if msg:
                        fired["scope"] += 1
                        return msg
                if not fired.get("sites") and feature("MH_RELATED_SITES"):
                    fired["sites"] = 1
                    msg = gates.related_sites(workdir)
                    if msg:
                        return msg
                if fired["n"] < 2:           # at most two gate rejections, then trust the agent
                    msg = gates.regression_gate(workdir, cont, cdir)
                    if msg:
                        fired["n"] += 1
                        return msg
                return None
    client = client or LiveClient(phase)
    ctx = {"task_id": task_id, "model": model, "profile": "deepseek", "role": "worker"}
    import time as _time
    t0 = _time.time()
    if os.environ.get("MH_ENV_SNAPSHOT") == "1":            # v3: saves the first exploration turns (Meta-Harness, Droid)
        try:
            snap, _ = ws._run("pwd; ls -la 2>/dev/null | head -40; echo '---'; "
                              "(python3 --version; pip --version; node --version; gcc --version | head -1; go version; "
                              "cargo --version; java -version) 2>&1 | grep -v 'not found' | head -12; echo '---'; "
                              "df -h . 2>/dev/null | tail -1; free -m 2>/dev/null | sed -n 2p", 30, "bash", record=False)
            task += "\n\n<environment_snapshot>\n" + snap[-2500:] + "\n</environment_snapshot>"
        except Exception:
            pass
    minimal = os.environ.get("MH_HARNESS_PROFILE") == "minimal"
    if os.environ.get("MH_TEMPERATURE") is not None:          # "" = the model's own default
        temperature = float(os.environ["MH_TEMPERATURE"]) if os.environ["MH_TEMPERATURE"] else None
    elif minimal:
        temperature = None                                      # minimal: model default, like the plain baseline
    system = SYSTEM_MIN if (minimal and os.environ.get("MH_SYSTEM") != "full") or os.environ.get("MH_SYSTEM") == "short" else SYSTEM
    if os.environ.get("MH_MULTI_CMD") == "1":
        system += ("\nTurns are limited. When you need several independent facts (file ranges, greps, versions, listings), "
                   "get them in ONE bash_many call instead of one command per turn.")
    if os.environ.get("MH_TERSE") == "1":              # v4: output tokens cost 4x input; keep prose between calls short
        system += ("\nBetween tool calls write at most one short sentence. Put the work in tool arguments, not in "
                   "prose, and do not repeat file contents or command output back.")
    if os.environ.get("MH_OBSERVE") == "1":               # see the bug before editing it (findings 007)
        system += ("\nBefore your first edit, reproduce the problem with probe (a failing test or a short script) "
                   "and read its diagnosis. If the cause is not clear, run trace on the same command to see which "
                   "project functions run. After an edit, use probe to see what still fails.")
    if os.environ.get("MH_SCOPE_PROMPT") == "1":           # the app agent's rule: its patches stayed in scope
        system += ("\nDo what the task asks, nothing more: no extra features, refactors, docs or changelog entries. "
                   "Look before you change anything.")
    if os.environ.get("MH_CODE_FINISH_SAME_TURN") == "1":    # saves the last model call on a solved task
        system += ("\nWhen you expect your check to pass, call it and finish together in the same turn, citing that "
                   "check. If the check fails, the finish is refused and you see the output.")
    if os.environ.get("MH_TODO") == "1":
        system += ("\nFor any task with more than one step, first call todo with the task's concrete requirements "
                   "(files, paths, names, formats, outputs, behaviours) and your steps. Update it as you go. finish is "
                   "refused while items are not done, so list only what the task really needs.")
    # The escalation below resets the repo between attempts. Snapshot the user's state first (uncommitted edits,
    # untracked files, binaries, the index) so a reset goes back to exactly that, never to HEAD.
    can_escalate = feature("MH_ESCALATE") and (cont is None or mounted) and \
        _sp.run(["git", "rev-parse", "--git-dir"], cwd=workdir, capture_output=True).returncode == 0
    before = snapshot(workdir) if can_escalate else None
    r = run(task, client=client, model=model, profile=None, ws=ws, ctx=ctx, system_text=system,
            caps=Caps(max_turns=max_turns), temperature=temperature, finish_check=finish_check)
    usage = dict(r.usage)
    turns, attempts = r.turns, 1
    # Adaptive escalation: an attempt that hits its turn/time cap without a verified finish is almost always wrong.
    # Reset the repo and run ONE fresh attempt inside the same wall-clock budget. Clean finishes cost nothing extra.
    limit = int(os.environ.get("MH_MAX_SECONDS", "0") or 0)
    remaining = (limit - (_time.time() - t0)) if limit else 1800
    if (not r.ok and before and remaining > 600 and r.reason != "need_context"
            and not str(r.reason).startswith("task budget") and not checks_green(ws)):
        first = snapshot(workdir)
        if first and first[0] != before[0]:
            diff = _sp.run(["git", "diff", "--binary", before[0], first[0]], cwd=workdir, capture_output=True).stdout
            Path(workdir, ".run").mkdir(parents=True, exist_ok=True)
            Path(workdir, ".run", "attempt1.patch").write_bytes(diff)
        if first and restore(workdir, before):
            ws2 = Workspace(workdir, os.path.join(workdir, ".run"), container=cont, container_dir=cdir, mounted=mounted)
            task2 = task + ("\n\n<note>A previous attempt at this task ran out of budget without a verified fix. "
                            "Start fresh. Make the smallest change at the root cause early, run the relevant tests, "
                            "and finish as soon as they pass.</note>")
            ctx2 = dict(ctx, task_id=task_id + "-esc")
            r2 = run(task2, client=client, model=model, profile=None, ws=ws2, ctx=ctx2, system_text=system,
                     caps=Caps(max_turns=min(max_turns, int(os.environ.get("MH_ESC_TURNS", "25"))),
                               max_seconds=int(remaining)), temperature=0.6,
                     finish_check=finish_check)
            attempts = 2
            for k in ("prompt_tokens", "completion_tokens"):
                usage[k] = usage.get(k, 0) + r2.usage.get(k, 0)
            turns += r2.turns
            second = snapshot(workdir)
            if not r2.ok and second and second[0] == before[0] and first[0] != before[0]:
                # the fresh attempt produced nothing: put attempt 1 back exactly
                if not restore(workdir, first):
                    r2.reason = f"{r2.reason}; could not restore attempt 1 (saved in .run/attempt1.patch)"
                    r, ws = r2, ws2
            else:
                r, ws = r2, ws2
        elif first:                                         # reset failed: keep attempt 1's files where they are
            restore(workdir, first)
    return {"ok": r.ok, "answer": r.answer, "evidence": r.evidence, "turns": turns, "reason": r.reason,
            "usage": usage, "prefix_sha": r.prefix_sha, "commands": len(ws.commands), "edits": ws.edits,
            "attempts": attempts}
