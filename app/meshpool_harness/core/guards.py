"""Failure-catalog guards implemented as code. Each returns a message to inject (or None) and logs to failures.csv."""
from __future__ import annotations

import csv
import os
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FAILURES = ROOT / "failures.csv"
FIELDS = ["timestamp", "model", "profile", "task_id", "category", "caught", "detail"]
TEST_PATH = re.compile(r"(^|/)(tests?|spec|__tests__)/|(^|/)test_[^/]*\.py$|_test\.(py|go|ts|js)$|\.spec\.(ts|js)$|\.test\.(ts|js)$")
ERR_LINE = re.compile(r"(Error|Exception|error:|FAILED|FAIL:|AssertionError|Traceback|panic:|fatal:|E   )")


class Abort(RuntimeError):
    """A guard ends the run (the loop stops; any other tool exception is reported to the model)."""


def log_failure(ctx: dict, category: str, caught: bool, detail: str = ""):
    """Append one row to the failure catalog. Logging must never stop a run: in an app the package folder can be
    read-only, so any file error is ignored. MH_FAILURES_LOG moves the file; MH_FAILURES_LOG=off disables it."""
    import os
    dest = os.environ.get("MH_FAILURES_LOG")
    if dest == "off":
        return
    path = Path(dest) if dest else FAILURES
    try:
        new = not path.exists()
        with path.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new:
                w.writeheader()
            w.writerow({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "model": ctx.get("model", ""),
                        "profile": ctx.get("profile", ""), "task_id": ctx.get("task_id", ""), "category": category,
                        "caught": caught, "detail": detail[:300]})
    except OSError:
        pass


# Every guard can be switched on or off for ablation. MH_GUARDS=all|none|comma list overrides the default set.
ALL_GUARDS = {"progress", "repeat_search", "repeat_failure", "error_quote", "finish_check", "test_guard", "deliverables",
              "explore_lock"}
DEFAULT_GUARDS = {"repeat_failure", "test_guard", "deliverables"}     # safety and deliverables only


def enabled(name: str) -> bool:
    g = os.environ.get("MH_GUARDS")
    if g:
        return g == "all" or (g != "none" and name in g.split(","))
    return name in DEFAULT_GUARDS


def test_guard_mode() -> str:
    """additive (default): new tests, even inside an existing test file, are allowed; changed or deleted existing test
    lines are rejected. strict: any change to a tracked test file is rejected. off: no check."""
    return os.environ.get("MH_TEST_GUARD") or "additive"


def key_error_line(output: str) -> str | None:
    lines = [l.strip() for l in output.splitlines() if l.strip()]
    hits = [l for l in lines if ERR_LINE.search(l)]
    return hits[-1] if hits else None


class Guards:
    def __init__(self, ctx: dict, ws, allow_test_edits: bool = False, require_check: bool = True):
        self.ctx, self.ws = ctx, ws
        self.allow_test_edits = allow_test_edits
        self.require_check = require_check
        self.fail_counts: dict[str, int] = {}
        self.pending_error: str | None = None
        self.reminded = False

    # progress: exploration budget, near-duplicate searches, turn-cap warning (found on pyvo-204: 40 turns, no edit)
    def progress(self, turn: int, max_turns: int, edits: int, require_edit: bool) -> str | None:
        if not enabled("progress"):
            return None
        msgs = []
        if edits != getattr(self, "_last_edits", 0):
            self._last_edits, self._last_edit_turn = edits, turn
        since = turn - getattr(self, "_last_edit_turn", 0)
        terminal = os.environ.get("MH_MODE") in ("terminal", "light")   # shell-made changes are invisible to the edit count
        if require_edit and edits == 0 and since in (12, 20) and not terminal:
            log_failure(self.ctx, "exploration_overrun", True, f"{since} turns without an edit")
            msgs.append(("GUARD progress: " + (f"{since} turns without changing any file. State your best hypothesis "
                         "in one sentence and make the edit now; you can refine it after running the check."
                         if since == 12 else "20 turns without an edit. Stop searching. Edit the most likely "
                         "location now and verify.")))
        if turn == max_turns - 5:
            msgs.append("GUARD budget: 5 turns left. Make your best fix now, run the check, and finish.")
        return "\n".join(msgs) or None

    def stall(self, turn: int) -> str | None:
        """Loop breaker (MH_STALL=1): progress = a file change or a passing command not run before. After 12 turns
        without progress, nudge once to change approach; again at 24. Never blocks anything."""
        if os.environ.get("MH_STALL") != "1":
            return None
        seen = getattr(self, "_stall_seen", set())
        cmds = [c for c in self.ws.commands if c.get("code") == 0 and c["cmd"] not in seen]
        for c in cmds:
            seen.add(c["cmd"])
        self._stall_seen = seen
        edits = getattr(self.ws, "edits", 0)
        if cmds or edits != getattr(self, "_stall_edits", 0) or not hasattr(self, "_stall_last"):
            self._stall_last, self._stall_edits = turn, edits
            return None
        since = turn - self._stall_last
        if since in (12, 24):
            log_failure(self.ctx, "stall", True, f"{since} turns without progress")
            return (f"GUARD stall: {since} turns without progress (no file changed, no new command succeeded). "
                    "Your current approach is not converging. In one sentence, state a different hypothesis or a "
                    "simpler route to the goal, then try it.")
        return None

    def repeat_calls(self, calls: list) -> str | None:
        """MH_DOOM=1: 3 identical tool calls in a row, or a 2-3 call pattern repeated 3 times -> one reminder."""
        if os.environ.get("MH_DOOM") != "1" or not calls:
            return None
        hist = getattr(self, "_call_hist", [])
        for c in calls:
            f = c.get("function", {})
            hist.append(f.get("name", "") + ":" + str(f.get("arguments", ""))[:500])
        self._call_hist = hist[-12:]
        h = self._call_hist
        hit = len(h) >= 3 and h[-1] == h[-2] == h[-3]
        for k in (2, 3):
            if not hit and len(h) >= 3 * k and h[-k:] == h[-2 * k:-k] == h[-3 * k:-2 * k]:
                hit = True
        if hit and getattr(self, "_doom_turn", -10) < len(h) - 6:
            self._doom_turn = len(h)
            log_failure(self.ctx, "repeat_calls", True, h[-1][:120])
            return ("GUARD loop: you are repeating the same tool calls and getting the same results. Stop and reconsider: "
                    "use a different tool, different arguments, or a different approach.")
        return None

    def near_duplicate(self, cmd: str) -> str | None:
        """Third near-identical search (same core pattern) gets a reminder instead of silent repetition."""
        if not enabled("repeat_search"):
            return None
        core = re.sub(r"\s*\|.*$|;\s*echo.*$|2>.*$|\s+", " ", cmd).strip()
        m = re.search(r"grep\s+(?:-\S+\s+)*[\"']?([^\"'\s]+)", core)
        if not m:
            return None
        key = m.group(1)
        self._seen_pat = getattr(self, "_seen_pat", {})
        self._seen_pat[key] = self._seen_pat.get(key, 0) + 1
        if self._seen_pat[key] == 3:
            log_failure(self.ctx, "repeat_search", True, key)
            return (f"GUARD repeat_search: you have searched for '{key}' three times. Its results will not change. "
                    "Use what you found, or read the file region directly with read_file.")
        return None

    # repeat_failure: 3 identical failures force a strategy change; 5 abort
    def after_command(self, cmd: str, code: int, output: str) -> str | None:
        if code == 0:
            self.pending_error = None
            return None
        if not enabled("repeat_failure"):
            return None
        real = key_error_line(output)
        line = real or f"exit {code}"
        self.pending_error = real          # only nag about real error lines, not '[exit 1]' or timeouts
        sig = cmd.strip() + "|" + re.sub(r"\d+", "N", line)[:200]
        self.fail_counts[sig] = self.fail_counts.get(sig, 0) + 1
        n = self.fail_counts[sig]
        if n == 3:
            log_failure(self.ctx, "repeat_failure", True, cmd)
            return ("GUARD repeat_failure: this exact command has failed 3 times with the same error. Do not run it "
                    "again unchanged. State a different hypothesis, inspect different code, or change approach.")
        if n >= 5:
            raise Abort(f"aborted: command failed {n} times identically: {cmd[:120]}")
        return None

    # error_not_quoted: the turn after a failure must reference the key error line
    def check_quotes_error(self, assistant_text: str) -> str | None:
        if not self.pending_error or not enabled("error_quote"):
            self.pending_error = None
            return None
        err = self.pending_error
        frag = [w for w in re.findall(r"[A-Za-z_][\w.]{4,}", err)][:6]
        if any(f in (assistant_text or "") for f in frag) or err[:40] in (assistant_text or ""):
            self.pending_error = None
            return None
        self.pending_error = None
        log_failure(self.ctx, "error_not_quoted", True, err)
        return (f"GUARD error_not_quoted: the last command failed with: `{err[:200]}`. Before acting, quote this "
                f"line and explain what it means.")

    # finish_without_check + test_tampering
    def check_finish(self, evidence: list[str]) -> str | None:
        cmds = self.ws.commands
        if self.require_check and enabled("finish_check"):
            last_edit = self.ws.edits
            passing = [c for c in cmds if c["code"] == 0 and c["after_edit"] == last_edit]
            if not passing:
                log_failure(self.ctx, "finish_without_check", True, "no passing command after last edit")
                return ("GUARD finish_without_check: finish rejected. Run a check (tests, script, or render/QA "
                        "command) after your last edit and finish only when it passes. Cite the exact command.")
            cited = [e for e in evidence if any(e.strip() and (e.strip() in c["cmd"] or c["cmd"] in e)
                                                for c in passing)]
            if not cited:
                log_failure(self.ctx, "finish_unverified_evidence", True, "; ".join(evidence)[:200])
                return ("GUARD finish_evidence: your evidence does not match any passing command run after your "
                        f"last edit. Passing commands: {[c['cmd'][:80] for c in passing][:5]}")
        if not self.allow_test_edits and enabled("test_guard") and test_guard_mode() != "off":
            git = self._git_test_changes()
            if git is None:   # not a git repo: fall back to files the agent wrote
                touched = [p.replace(str(self.ws.root) + "/", "") for p in self.ws.written
                           if p not in self.ws.created and TEST_PATH.search(p.replace(str(self.ws.root) + "/", ""))]
            else:             # git repo: judge by the actual diff, so a revert clears the flag
                touched = git
            touched = sorted(set(touched))
            if touched:
                log_failure(self.ctx, "test_tampering", True, ", ".join(touched))
                return (f"GUARD test_tampering: you modified test files {touched[:5]} but the task did not ask for "
                        f"test changes. Revert them (git checkout -- <file>) and fix the code instead.")
        return None

    def check_deliverables(self, task_text: str) -> str | None:
        """Files the task explicitly asks to write must exist (and JSON must parse) before finish."""
        if not enabled("deliverables"):
            return None
        import json as _json
        names = set(re.findall(r"\b(?:[Ww]rite|[Ss]ave|[Cc]reate|[Pp]roduce|[Oo]utput)\b[^.\n]{0,60}?\b([\w\-]+\.(?:json|txt|csv|xlsx|pptx|png|md))\b", task_text))
        missing, bad = [], []
        for n in sorted(names):
            p = self.ws.root / n
            exists = p.exists() or (self.ws.container and not self.ws.mounted and
                                    subprocess.run(["docker", "exec", self.ws.container, "test", "-e", self.ws._cpath(n)]).returncode == 0)
            if not exists:
                missing.append(n)
            elif n.endswith(".json") and p.exists():
                try:
                    _json.loads(p.read_text())
                except ValueError as e:
                    bad.append(f"{n} ({e})")
        if missing or bad:
            log_failure(self.ctx, "missing_deliverable", True, ", ".join(missing + bad))
            return ("GUARD deliverables: the task asks for " + ", ".join(sorted(names)) + ". "
                    + (f"Missing in {self.ws.root}: {', '.join(missing)}. " if missing else "")
                    + (f"Invalid JSON: {', '.join(bad)}. " if bad else "") + "Write it, then finish.")
        return None

    def _git_test_changes(self) -> list[str] | None:
        try:
            r = subprocess.run(["git", "status", "--porcelain"], cwd=self.ws.root, capture_output=True,
                               text=True, timeout=30)
            if r.returncode != 0:
                return None
            names = [l[3:].strip() for l in r.stdout.splitlines()]
            # only modified/deleted tracked tests count; new test files the agent adds are allowed
            mod = [l[3:].strip() for l in r.stdout.splitlines() if l[:2].strip() in ("M", "D", "MM", "AM", "R")]
            tests = [n for n in mod if TEST_PATH.search(n)]
            if test_guard_mode() == "additive":         # only removed or changed existing lines count as tampering
                kept = []
                for n in tests:
                    d = subprocess.run(["git", "diff", "--numstat", "HEAD", "--", n], cwd=self.ws.root,
                                       capture_output=True, text=True, timeout=30).stdout.split()
                    if not d or d[1] == "-" or int(d[1]) > 0:   # deleted lines (or binary/unknown): tampering
                        kept.append(n)
                tests = kept
            return tests
        except (OSError, subprocess.TimeoutExpired):
            return None
