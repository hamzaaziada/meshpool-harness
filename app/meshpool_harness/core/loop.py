"""The agent loop: byte-stable prefix, validated tool calls, guards, caps, deterministic compaction."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
from dataclasses import dataclass, field

from .guards import Guards, enabled, log_failure
from .tools import CORE, ToolError, Workspace, schemas, validate


EXPLORATION_LOCK = 25   # hard guard: soft nudges at 12/20 were ignored on django-15098 (60 turns, no edit)


@dataclass
class Caps:
    max_turns: int = 40
    max_output_tokens: int = 8192
    compact_chars: int = 600_000      # ~150k tokens; DeepSeek context is large and cached input is cheap
    max_seconds: int = 0              # wall-clock cap (0 = none); runners set the same limit the baseline gets
    keep_recent: int = 12             # tool results kept verbatim at compaction


@dataclass
class LoopResult:
    ok: bool
    answer: str = ""
    evidence: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    turns: int = 0
    reason: str = ""
    need_context: dict | None = None
    prefix_sha: str = ""
    usage: dict = field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0})


def build_prefix(system_text: str, tool_names: list[str], skill_index: dict[str, str]) -> tuple[str, list, str]:
    """Byte-stable: no timestamps, ids, or file listings. Same engine+profile -> same bytes -> cache hits."""
    idx = "\n".join(f"- {k}: {v.splitlines()[0][:120]}" for k, v in sorted(skill_index.items()))
    sys_msg = system_text.rstrip() + ("\n\nSkills (load with load_skill when needed):\n" + idx if idx else "")
    tools = schemas(tool_names)
    sha = hashlib.sha256((sys_msg + json.dumps(tools, sort_keys=True)).encode()).hexdigest()[:16]
    return sys_msg, tools, sha


def _compact(messages: list, keep_recent: int) -> list:
    """Elide old tool outputs (they are saved to files already). Keeps system, task, and recent turns verbatim."""
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    for i in tool_idx[:-keep_recent]:
        c = messages[i].get("content") or ""
        if len(c) > 400 and not c.startswith("[elided"):
            messages[i] = dict(messages[i], content="[elided at compaction] " + c[:300])
    return messages


def _sanitize_calls(calls: list) -> list:
    """A tool call with invalid JSON arguments (e.g. truncated output, or two calls merged by a stream) makes the next
    request invalid (gateway HTTP 400). Keep the call, with arguments the API accepts; the tool result still reports
    the problem to the model, because validation sees the replaced arguments."""
    out = []
    for i, c in enumerate(calls):
        c = json.loads(json.dumps(c))
        f = c.setdefault("function", {})
        try:
            ok = isinstance(json.loads(f.get("arguments") or "{}"), dict)
        except (ValueError, TypeError):
            ok = False
        if not ok:
            f["arguments"] = "{}"
        if not c.get("id"):
            c["id"] = f"call_{i}"
        c["type"] = "function"
        out.append(c)
    return out


def _endstate_report(task_text: str, ws) -> str:
    """P1 light end-state check (MH_ENDSTATE_CHECK=1): shown once at the first finish, never enforced. Re-runs the
    last check command if files changed after it, and lists the absolute paths the task names."""
    import re as _re
    parts = []
    checks = [c for c in getattr(ws, "commands", []) if c.get("kind") == "check"]
    if checks and checks[-1].get("after_edit", 0) < getattr(ws, "edits", 0):
        out, code = ws._run(checks[-1]["cmd"], 300, "check")
        parts.append(f"$ {checks[-1]['cmd']}  (re-run: files changed after it last ran)\n{out[-1500:]}")
    paths = []
    for m in _re.findall(r"(?<![\w.])(/(?:[\w.\-]+/)*[\w.\-]+)", task_text):
        if m not in paths and not m.startswith(("/dev/", "/proc/", "/usr/", "/bin/", "/etc/")) and len(paths) < 8:
            paths.append(m)
    if paths:
        out, _ = ws._run("ls -ld " + " ".join(json.dumps(x) for x in paths) + " 2>&1", 30, "bash", record=False)
        parts.append("Paths named in the task:\n" + out[-1200:])
    return "\n\n".join(parts)


def run(*a, **kw):
    """Full-history archive (Meta-Harness / Harness²): every run's transcript is saved next to its workspace so an
    offline optimizer can grep raw traces instead of summaries. Local disk only; no tokens."""
    box = {}
    t0 = time.time()
    res = None
    try:
        res = _run(*a, _box=box, **kw)
    finally:
        try:                                     # where the time goes: model calls vs tools (MH_TIMING_LOG)
            if res is not None and os.environ.get("MH_TIMING", "1") == "1":
                ctx = kw.get("ctx") or {}
                path = Path(os.environ.get("MH_TIMING_LOG") or Path(__file__).resolve().parents[2] / "timing.jsonl")
                with open(path, "a") as f:
                    f.write(json.dumps({"task_id": ctx.get("task_id", ""), "profile": os.environ.get("MH_HARNESS_PROFILE", "v1"),
                                        "turns": res.turns, "wall_s": round(time.time() - t0, 1),
                                        "model_s": round(res.usage.get("model_s", 0.0), 1),
                                        "tool_s": round(res.usage.get("tool_s", 0.0), 1),
                                        "model_calls": res.usage.get("model_calls", 0), "reason": res.reason}) + "\n")
        except Exception:
            pass
        try:
            ws, ctx = kw.get("ws"), kw.get("ctx") or {}
            if ws is not None and box.get("messages") is not None:
                d = Path(ws.run_dir if hasattr(ws, "run_dir") else os.path.join(ws.root, ".run"))
                d.mkdir(parents=True, exist_ok=True)
                with open(d / f"transcript-{ctx.get('task_id', 'run')}.jsonl", "w") as f:
                    for m in box["messages"]:
                        f.write(json.dumps(m)[:20000] + "\n")
        except Exception:
            pass
    return res


def _run(task_text: str, *, client, model: str, profile, ws: Workspace, ctx: dict, system_text: str,
        tool_names: list[str] | None = None, caps: Caps | None = None, allow_test_edits=False,
        require_check=True, temperature=None, extra=None, finish_check=None, _box=None) -> LoopResult:
    """finish_check(answer, artifacts, ws) -> error message or None: engine-specific deterministic verifier."""
    caps = caps or Caps()
    tool_names = tool_names or (CORE + (["load_skill"] if ws.skills else []))
    sys_msg, tools, sha = build_prefix(system_text, tool_names, ws.skills)
    messages = [{"role": "system", "content": sys_msg}, {"role": "user", "content": task_text}]
    if _box is not None:
        _box["messages"] = messages
    guards = Guards(ctx, ws, allow_test_edits=allow_test_edits, require_check=require_check)
    res = LoopResult(ok=False, prefix_sha=sha)
    bad_format = 0
    import os as _os
    if not caps.max_seconds and _os.environ.get("MH_MAX_SECONDS"):
        caps.max_seconds = int(_os.environ["MH_MAX_SECONDS"])
    t_start = time.time()
    for turn in range(1, caps.max_turns + 1):
        res.turns = turn
        if caps.max_seconds and time.time() - t_start > caps.max_seconds:
            res.reason = f"wall-clock cap {caps.max_seconds}s reached"
            log_failure(ctx, "time_cap", False, res.reason)
            return res
        if sum(len(json.dumps(m)) for m in messages) > caps.compact_chars:
            messages = _compact(messages, caps.keep_recent)
            if _box is not None:
                _box["messages"] = messages
        _tc = time.time()
        resp = client.chat(model=model, messages=messages, tools=tools, task_id=ctx.get("task_id", ""),
                           job_id=ctx.get("job_id", ""), role=ctx.get("role", "worker"),
                           profile=ctx.get("profile", "deepseek"), max_tokens=caps.max_output_tokens,
                           temperature=temperature, extra=extra)
        res.usage["model_s"] = res.usage.get("model_s", 0.0) + time.time() - _tc
        res.usage["model_calls"] = res.usage.get("model_calls", 0) + 1
        u = resp.get("usage") or {}
        res.usage["prompt_tokens"] += u.get("prompt_tokens", 0)
        res.usage["completion_tokens"] += u.get("completion_tokens", 0)
        _cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or u.get("prompt_cache_hit_tokens") or 0
        res.usage["cached_tokens"] = res.usage.get("cached_tokens", 0) + _cached
        _budget = float(_os.environ.get("MH_BUDGET_USD", "0") or 0)       # per-task spend cap (0 = off)
        if _budget:
            _f = float(_os.environ.get("MH_BILLING_FACTOR", "2.19"))     # measured gateway/list ratio
            _spent = _f * ((res.usage["prompt_tokens"] - res.usage["cached_tokens"]) * 0.15
                           + res.usage["cached_tokens"] * 0.003 + res.usage["completion_tokens"] * 0.6) / 1e6
            if _spent > _budget:
                res.reason = f"task budget ${_budget:.2f} reached"
                log_failure(ctx, "budget_cap", False, res.reason)
                return res
        msg = resp["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        keep = {"role": "assistant", "content": msg.get("content") or ""}
        if calls and os.environ.get("MH_SANITIZE_CALLS", "1") == "1":
            calls = _sanitize_calls(calls)
        if calls:
            keep["tool_calls"] = calls
        messages.append(keep)
        nag = guards.check_quotes_error((msg.get("content") or "") + json.dumps(calls)[:2000]
                                        + (msg.get("reasoning_content") or "")[-3000:])
        if not calls:
            bad_format += 1
            if bad_format >= 3:
                res.reason = "model stopped calling tools"
                log_failure(ctx, "no_tool_call", False, (msg.get("content") or "")[:200])
                return res
            messages.append({"role": "user", "content": "Use the tools to continue. When done, call finish with "
                                                        "your answer and the exact passing check commands."
                                                        + (f"\n{nag}" if nag else "")})
            continue
        bad_format = 0
        injected = [nag] if nag else []
        _tt = time.time()
        for c in calls:
            name = c.get("function", {}).get("name", "")
            raw = c.get("function", {}).get("arguments", "{}")
            out = None
            try:
                args = validate(name, raw, tool_names)
                if name == "finish" and os.environ.get("MH_ENDSTATE_CHECK") == "1" and not res.usage.get("endstate_shown"):
                    res.usage["endstate_shown"] = 1
                    rep = _endstate_report(task_text, ws)
                    if rep:
                        messages.append({"role": "tool", "tool_call_id": c.get("id", ""),
                                         "content": "End-state check (shown once; finish is accepted next time):\n"
                                                    + rep + "\nIf this matches what the task asks for, call finish "
                                                            "again. If not, fix it first."})
                        continue
                if name == "finish":
                    g = guards.check_finish(args.get("evidence", []))
                    if not g:
                        g = guards.check_deliverables(task_text)
                    if not g and finish_check:
                        g = finish_check(args["answer"], args.get("artifacts", []), ws)
                        if g:
                            log_failure(ctx, "engine_check", True, g[:200])
                    if g:
                        out = g
                    else:
                        res.ok, res.answer = True, args["answer"]
                        res.evidence, res.artifacts = args.get("evidence", []), args.get("artifacts", [])
                        res.reason = "finished"
                        messages.append({"role": "tool", "tool_call_id": c.get("id", ""), "content": "accepted"})
                        return res
                elif name == "need_context":
                    res.need_context = args
                    res.reason = "need_context"
                    return res
                elif (name in ("bash", "run_tests", "grep", "glob") and require_check and ws.edits == 0
                      and turn > EXPLORATION_LOCK and os.environ.get("MH_MODE") not in ("terminal", "light")
                      and enabled("explore_lock")):
                    out = (f"ERROR: exploration budget exhausted ({EXPLORATION_LOCK} turns without changing any file). "
                           "Make your best edit now with edit/write_file (read_file is still available); commands "
                           "unlock after the first edit.")
                    log_failure(ctx, "exploration_lock", True, name)
                elif name in ("bash", "run_tests"):
                    out, code = ws._run(args["command"], args.get("timeout_s"), "check" if name == "run_tests" else "bash")
                    g = guards.after_command(args["command"], code, out)
                    if g:
                        injected.append(g)
                    g = guards.near_duplicate(args["command"])
                    if g:
                        injected.append(g)
                else:
                    out = getattr(ws, name)(**args)
            except ToolError as e:
                out = f"ERROR: {e}"
                log_failure(ctx, "tool_format" if "argument" in str(e) or "JSON" in str(e) else "tool_error",
                            True, str(e))
                if "stale edit" in str(e):
                    log_failure(ctx, "stale_edit", True, str(e))
            except RuntimeError as e:
                res.reason = str(e)
                return res
            messages.append({"role": "tool", "tool_call_id": c.get("id", ""), "content": str(out)})
        res.usage["tool_s"] = res.usage.get("tool_s", 0.0) + time.time() - _tt
        g = guards.progress(turn, caps.max_turns, ws.edits, require_check)
        if g:
            injected.append(g)
        if injected:
            messages.append({"role": "user", "content": "\n".join(injected)})
    res.reason = f"turn cap {caps.max_turns} reached"
    log_failure(ctx, "turn_cap", False, res.reason)
    return res
