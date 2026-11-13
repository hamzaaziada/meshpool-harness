"""meshpool-code: repository and terminal work on the shared core loop."""
from __future__ import annotations

import os
import re
import sys
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
            "MH_RELATED_SITES": ("1", "0"), "MH_MEMORY": ("1", "0"), "MH_ESCALATE": ("1", "0")}


def feature(name: str) -> bool:
    v1, mini = FEATURES[name]
    return os.environ.get(name, mini if os.environ.get("MH_HARNESS_PROFILE") == "minimal" else v1) == "1"


def f2p_evidence(ws) -> bool:
    """Proof that a fix works, from the agent's own log: a check that failed before the first edit passes after the
    last edit (the same command). Costs nothing to compute."""
    cmds = getattr(ws, "commands", [])
    failed_before = {c["cmd"] for c in cmds if c.get("code") not in (0, None) and c.get("after_edit") == 0}
    return any(c["cmd"] in failed_before and c.get("code") == 0 and c.get("after_edit") == ws.edits and ws.edits
               for c in cmds)


def checks_green(ws) -> bool:
    """v2: attempt 1 ran out of budget but its last check after the last edit passed: keep it, do not restart."""
    if not getattr(ws, "edits", 0):
        return False
    after = [c for c in ws.commands if c.get("kind") == "check" and c.get("after_edit") == ws.edits]
    return bool(after) and after[-1].get("code") == 0


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
            if os.environ.get("MH_FASTPATH") == "1":          # single-request first draft (opt-in)
                from .fastpath import draft
                note = draft(m.group(1) if m else task, h, workdir, client or LiveClient(phase), model, task_id)
                if note:
                    task += "\n\n" + note
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

            reviewer = os.environ.get("MH_REVIEWER")       # e.g. glm-5.3 / kimi-k3: E5 cross-family review
            reviewed = {"done": False}

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
                if reviewer and not reviewed["done"] and m2:
                    reviewed["done"] = True  # one review per task: bounded cost
                    return gates.cross_review(m2.group(1), workdir, client, reviewer, task_id + "-review")
                return None
    if feature("MH_MEMORY"):
        from meshpool_harness.memory import block as _lessons
        task += _lessons("code", task[:4000])
    client = client or LiveClient(phase)
    ctx = {"task_id": task_id, "model": model, "profile": "deepseek", "role": "worker"}
    import time as _time
    import subprocess as _sp
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
    r = run(task, client=client, model=model, profile=None, ws=ws, ctx=ctx, system_text=system,
            caps=Caps(max_turns=max_turns), temperature=temperature, finish_check=finish_check)
    usage = dict(r.usage)
    turns, attempts = r.turns, 1
    # Adaptive escalation: an attempt that hits its turn/time cap without a verified finish is almost always wrong.
    # Reset the repo and run ONE fresh attempt inside the same wall-clock budget. Clean finishes cost nothing extra.
    limit = int(os.environ.get("MH_MAX_SECONDS", "0") or 0)
    remaining = (limit - (_time.time() - t0)) if limit else 1800
    is_git = _sp.run(["git", "rev-parse", "--git-dir"], cwd=workdir, capture_output=True).returncode == 0
    if (not r.ok and feature("MH_ESCALATE") and (cont is None or mounted) and is_git
            and remaining > 600 and r.reason != "need_context" and not str(r.reason).startswith("task budget")
            and not checks_green(ws)):
        ex = [":!.run", ":!.junie"]
        _sp.run(["git", "add", "-A", "--", ".", *ex], cwd=workdir, capture_output=True)
        first = _sp.run(["git", "diff", "--cached", "--", ".", *ex], cwd=workdir, capture_output=True, text=True).stdout
        _sp.run(["git", "reset", "-q"], cwd=workdir, capture_output=True)
        Path(workdir, ".run", "attempt1.patch").write_text(first)
        _sp.run(["git", "checkout", "--", "."], cwd=workdir, capture_output=True)
        _sp.run(["git", "clean", "-fdq", "-e", ".run", "-e", ".junie"], cwd=workdir, capture_output=True)
        ws2 = Workspace(workdir, os.path.join(workdir, ".run"), container=cont, container_dir=cdir, mounted=mounted)
        task2 = task + ("\n\n<note>A previous attempt at this task ran out of budget without a verified fix. Start "
                        "fresh. Make the smallest change at the root cause early, run the relevant tests, and finish "
                        "as soon as they pass.</note>")
        ctx2 = dict(ctx, task_id=task_id + "-esc")
        r2 = run(task2, client=client, model=model, profile=None, ws=ws2, ctx=ctx2, system_text=system,
                 caps=Caps(max_turns=min(max_turns, int(os.environ.get("MH_ESC_TURNS", "25"))),
                           max_seconds=int(remaining)), temperature=0.6,
                 finish_check=finish_check)
        attempts = 2
        for k in ("prompt_tokens", "completion_tokens"):
            usage[k] = usage.get(k, 0) + r2.usage.get(k, 0)
        turns += r2.turns
        _sp.run(["git", "add", "-A", "--", ".", *ex], cwd=workdir, capture_output=True)
        second = _sp.run(["git", "diff", "--cached", "--", ".", *ex], cwd=workdir, capture_output=True, text=True).stdout
        _sp.run(["git", "reset", "-q"], cwd=workdir, capture_output=True)
        if not r2.ok and not second.strip() and first.strip():     # fresh attempt produced nothing: restore first
            _sp.run(["git", "apply", "--whitespace=nowarn", str(Path(workdir, ".run", "attempt1.patch"))],
                    cwd=workdir, capture_output=True)
        else:
            r, ws = r2, ws2
    # v3 evidence-driven second attempt (MH_EVIDENCE_RETRY=1): attempt 1 finished but its log has no
    # failing-then-passing check. Spend only the turns it left unused (total stays within the cap) on a second attempt
    # that must reproduce first; keep it only if it shows that proof, otherwise restore attempt 1 unchanged.
    turns_left = max_turns - turns
    remaining = (limit - (_time.time() - t0)) if limit else 1800
    if (os.environ.get("MH_EVIDENCE_RETRY") == "1" and r.ok and attempts == 1 and (cont is None or mounted) and is_git
            and turns_left >= 15 and remaining > 300 and not f2p_evidence(ws)):
        ex = [":!.run", ":!.junie"]
        _sp.run(["git", "add", "-A", "--", ".", *ex], cwd=workdir, capture_output=True)
        first = _sp.run(["git", "diff", "--cached", "--binary", "--", ".", *ex], cwd=workdir, capture_output=True,
                        text=True).stdout
        _sp.run(["git", "reset", "-q"], cwd=workdir, capture_output=True)
        Path(workdir, ".run", "attempt1.patch").write_text(first)
        _sp.run(["git", "checkout", "--", "."], cwd=workdir, capture_output=True)
        _sp.run(["git", "clean", "-fdq", "-e", ".run", "-e", ".junie"], cwd=workdir, capture_output=True)
        ws2 = Workspace(workdir, os.path.join(workdir, ".run"), container=cont, container_dir=cdir, mounted=mounted)
        task2 = task + ("\n\n<note>Before changing any code, reproduce the problem: write or run a test or script "
                        "that FAILS on the current code because of the issue. Then fix the root cause and rerun the "
                        "same command until it passes. Finish only after that same command passes.</note>")
        r2 = run(task2, client=client, model=model, profile=None, ws=ws2, ctx=dict(ctx, task_id=task_id + "-ev"),
                 system_text=system, caps=Caps(max_turns=turns_left, max_seconds=int(remaining)),
                 temperature=temperature, finish_check=finish_check)
        attempts = 2
        for k in ("prompt_tokens", "completion_tokens", "cached_tokens"):
            usage[k] = usage.get(k, 0) + r2.usage.get(k, 0)
        turns += r2.turns
        if r2.ok and f2p_evidence(ws2):
            r, ws = r2, ws2
        else:                                              # no proof: put attempt 1 back exactly
            _sp.run(["git", "checkout", "--", "."], cwd=workdir, capture_output=True)
            _sp.run(["git", "clean", "-fdq", "-e", ".run", "-e", ".junie"], cwd=workdir, capture_output=True)
            if first.strip():
                _sp.run(["git", "apply", "--whitespace=nowarn", str(Path(workdir, ".run", "attempt1.patch"))],
                        cwd=workdir, capture_output=True)
    return {"ok": r.ok, "answer": r.answer, "evidence": r.evidence, "turns": turns, "reason": r.reason,
            "usage": usage, "prefix_sha": r.prefix_sha, "commands": len(ws.commands), "edits": ws.edits,
            "attempts": attempts}
