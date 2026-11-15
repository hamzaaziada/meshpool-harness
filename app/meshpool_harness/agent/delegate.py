"""delegate_task: sub-agents for Meshpool's own agent, modelled on Hermes Agent's delegate_task.

A chat hands self-contained parts of a job to sub-agents. Each starts fresh (it knows nothing of the chat), gets its
goal and context, works with the chat's own tools and permission mode, and returns one report. They run in this
process (nothing is trusted across processes), several at once, and the chat shows their progress live.

- Tools: the chat's tools minus what needs a person or is the chat's own: ask_user, memory, scheduling and (beyond
  the allowed depth) delegate_task. MCP tools come along, minus the assistant server's memory and scheduling.
  Settings can limit them to editing without commands, or to reading and research.
- Permissions: the chat's mode and plan mode as they are at each step. A sub-agent cannot ask, so anything that
  would need a yes is refused (or, with "Sub-agents approve their own actions" on, allowed once), as in Hermes.
  Sub-agents never read outside the project.
- They work inside the chat's turn, unlike Hermes's background delegation: the turn's checkpoint, diff and Undo
  cover what they change, Stop stops them (finished reports still come back), and goals, scheduled tasks and
  restarts need nothing special.
- Results: per task a status, the report (capped; the full text goes to a file), steps, time, tokens, files
  written and a short tool trace. Their tokens count toward the chat's usage.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid

BLOCKED = {"ask_user"}                                  # needs a person
ASSISTANT_MCP = "mcp__meshpool_assistant__"           # memory, other chats, schedules, run_subtasks: none for them
# a goal that is nothing but a placeholder ("TODO", "task 3", "<goal>", "{task}"); "<title> tags" or
# format("{user}") inside a real goal are fine (Hermes narrowed its check for the same reason)
_PLACEHOLDER = re.compile(r"^\s*(todo|tbd|task\s*\d*|goal\s*\d*|\.\.\.|placeholder|<[^<>]{1,40}>|\{[^{}]{1,40}\})\s*$",
                          re.I)
# office_task counts as a command for sub-agents: its engines run model-written code
NO_COMMANDS = {"bash", "bash_many", "probe", "trace", "run_tests", "office_task"}
REPORTS_BUDGET = 60000
_USAGE_LOCK = threading.Lock()


# ---------- settings ----------------------------------------------------------------------------------------------
def settings() -> dict:
    """Sub-agent settings (Settings, Advanced; meshpool.config), read from the file so changes apply at once."""
    from meshpool_harness import assistant as A
    s = A.subagent_settings()
    from meshpool_harness import config as C
    try:
        saved = C.read()
    except Exception:  # noqa: BLE001
        saved = {}

    def get(key, default=""):
        return (saved[key] if key in saved else os.environ.get(key)) or default

    def flag(key, default):
        return str(get(key, default)).strip().lower() not in ("0", "off", "false", "no")

    def num(key, default, lo, hi):
        try:
            return max(lo, min(hi, int(float(get(key, default)))))
        except ValueError:
            return default
    access = str(get("MH_SUBAGENT_ACCESS", "inherit")).strip().lower()
    s.update(auto_approve=flag("MH_SUBAGENT_AUTO_APPROVE", "0"),
             depth=num("MH_SUBAGENT_DEPTH", 1, 1, 3),
             summary_chars=num("MH_SUBAGENT_SUMMARY_CHARS", 24000, 2000, 200000),
             # in-process sub-agents have no MCP call timeout to stay under: Hermes-sized limits
             parallel=num("MH_SUBAGENT_PARALLEL", 4, 1, 10), max_tasks=12,
             steps=num("MH_SUBAGENT_STEPS", 50, 5, 250), minutes=num("MH_SUBAGENT_MINUTES", 30, 0, 240),
             access=access if access in ("read", "edit") else "inherit")
    return s


# Meshpool's office server reads local files and runs without asking; other servers' "path" is often remote (a repo,
# a bucket, an API) and they need a yes from a sub-agent anyway (and in Full access it has bash)
OFFICE_MCP = "mcp__meshpool_office__"
PATH_KEYS = ("path", "paths", "file", "files", "file_path", "filename", "to", "deck")


def _mcp_paths_outside(root, args) -> bool:
    """An MCP tool given a file path outside the project (its servers resolve them the same way: ~ and relative to
    the project); the files the user attached are fine."""
    from meshpool_harness import config as C
    vals = []
    for k in PATH_KEYS:
        v = args.get(k)
        vals += [x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str) and x.strip()]
    if not vals:
        return False
    try:
        uploads = (C.runs_dir() / "_uploads").resolve()
    except Exception:  # noqa: BLE001
        uploads = None
    return any(_files_outside(root, {"files": [v]}) and not _under(v, uploads) for v in vals)


def _under(f, base) -> bool:
    from pathlib import Path
    try:
        return base is not None and Path(str(f)).expanduser().resolve().is_relative_to(base)
    except (OSError, ValueError):
        return False


def _files_outside(root, args) -> bool:
    from pathlib import Path
    for f in args.get("files") or []:
        try:
            p = Path(str(f)).expanduser()                 # "~/x" is the home folder, as office_task reads it
            p = (p if p.is_absolute() else Path(root) / p).resolve()
            if not p.is_relative_to(Path(root).resolve()):
                return True
        except (OSError, ValueError):
            return True
    return False


def _sub_sessions():
    """Sub-agent sessions: in the user's data folder (private), outside the chat list; ones a crash left are swept."""
    from meshpool_harness import config as C
    d = C.runs_dir() / "subagent-sessions"
    try:
        d.mkdir(parents=True, exist_ok=True)
        for old in d.iterdir():
            if time.time() - old.stat().st_mtime > 86400:
                shutil.rmtree(old, ignore_errors=True)
    except OSError:
        pass
    return d


def schema(max_children: int) -> dict:
    task = {"type": "object", "properties": {
        "goal": {"type": "string", "description": "What this sub-agent should accomplish. Be specific and "
                 "self-contained: it knows nothing about this conversation."},
        "context": {"type": "string", "description": "Background this sub-agent needs: file paths, error messages, "
                    "constraints, the language and tone of the result. Each sub-agent sees only its own context."},
        "output_schema": {"type": "object", "description": "Optional JSON Schema the final answer must match "
                          "(the sub-agent then replies with JSON only)."}}, "required": ["goal"]}
    return {"type": "function", "function": {"name": "delegate_task", "description": (
        "Hand self-contained parts of a job to sub-agents that work at the same time, each in a fresh context with "
        "your tools and permissions; only each one's final report comes back. Use it for independent parts: "
        "researching several companies or sources, reviewing several files, trying alternatives. Do not use it "
        "for a single quick step or for work that needs the user (sub-agents cannot ask questions). "
        f"Up to {max_children} tasks per call; the call returns when all have reported. Sub-agents know nothing of "
        "this chat, so put everything they need in goal and context. Their reports are their own account: check "
        "important claims (a file, a link) before you rely on them."),
        "parameters": {"type": "object", "properties": {
            "tasks": {"type": "array", "minItems": 1, "items": task,
                      "description": f"The tasks, up to {max_children}."}}, "required": ["tasks"]}}}


def check_goals(tasks) -> list[dict]:
    if isinstance(tasks, str):
        try:
            tasks = json.loads(tasks)
        except ValueError:
            tasks = [{"goal": tasks}]
    rows = []
    for t in tasks or []:
        t = {"goal": t} if isinstance(t, str) else t
        if not isinstance(t, dict):
            continue
        goal = str(t.get("goal") or t.get("prompt") or "").strip()
        if not goal:
            continue
        if _PLACEHOLDER.search(goal):
            raise ValueError(f"The goal {goal[:60]!r} is a placeholder. Write out what the sub-agent should do.")
        if len(tasks) > 1 and len(goal) < 10:
            raise ValueError(f"The goal {goal!r} is too short to work on alone. Make each goal self-contained.")
        if isinstance(t.get("output_schema"), str):     # sent as a JSON string: read it
            try:
                t = {**t, "output_schema": json.loads(t["output_schema"])}
            except ValueError:
                raise ValueError("output_schema is not valid JSON. Give it as a JSON Schema object.")
        rows.append({"goal": goal, "context": str(t.get("context") or "").strip(),
                     "schema": t.get("output_schema") if isinstance(t.get("output_schema"), dict) else None,
                     "title": str(t.get("title") or goal.splitlines()[0])[:80]})
    if not rows:
        raise ValueError("Give at least one task with a goal.")
    return rows


# ---------- a small JSON Schema check (type, required, properties, items, enum) ------------------------------------
_TYPES = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool,
          "null": type(None)}


def schema_errors(value, sch, path="$") -> list[str]:
    errs = []
    t = sch.get("type")
    if isinstance(t, str) and t in _TYPES:
        ok = isinstance(value, _TYPES[t]) and not (t in ("integer", "number") and isinstance(value, bool))
        if not ok:
            return [f"{path}: expected {t}"]
    # a schema the model wrote may be malformed (enum: 20, required: "name"): check only what is well formed
    if isinstance(sch.get("enum"), list) and value not in sch["enum"]:
        errs.append(f"{path}: not one of {sch['enum']}")
    if isinstance(value, dict):
        req = sch.get("required")
        for k in (req if isinstance(req, list) else []):
            if isinstance(k, str) and k not in value:
                errs.append(f"{path}: missing {k!r}")
        props = sch.get("properties")
        for k, sub in (props.items() if isinstance(props, dict) else ()):
            if k in value and isinstance(sub, dict):
                errs += schema_errors(value[k], sub, f"{path}.{k}")
    if isinstance(value, list) and isinstance(sch.get("items"), dict):
        for i, v in enumerate(value):
            errs += schema_errors(v, sch["items"], f"{path}[{i}]")
    return errs[:10]


def _json_of(text: str):
    t = (text or "").strip()
    try:                                                  # plain JSON (a string value may hold a ``` fence)
        return json.loads(t)
    except ValueError:
        pass
    m = re.match(r"```(?:json)?\s*(.*)```\s*$", t, re.S)   # the whole reply in one fence
    if m:
        return json.loads(m.group(1).strip())
    return json.loads(t)


# ---------- MCP tools a sub-agent may use -----------------------------------------------------------------------------
class ChildMcp:
    """The chat's MCP servers, minus the assistant server (memory, other chats, schedules, sub-agents)."""
    def __init__(self, mcp):
        self.m = mcp
        self.last_images: list = []

    def _ok(self, name: str) -> bool:
        return not name.replace("-", "_").startswith(ASSISTANT_MCP)

    def has(self, name: str) -> bool:
        return self._ok(name) and self.m.has(name)

    def schemas(self) -> list:
        return [s for s in self.m.schemas() if self._ok(s["function"]["name"])]

    def call(self, name: str, args: dict):
        if hasattr(self.m, "call_with_images"):          # this call's own images, not a sibling's
            out, self.last_images = self.m.call_with_images(name, args)
            return out
        out = self.m.call(name, args)
        self.last_images = list(getattr(self.m, "last_images", []) or [])
        return out

    def __getattr__(self, k):
        return getattr(self.m, k)


# ---------- one delegation (a call with one or more tasks) ----------------------------------------------------------
class Delegation:
    def __init__(self, parent, rows: list[dict], cfg: dict, depth: int):
        self.parent, self.rows, self.cfg, self.depth = parent, rows, cfg, depth
        self.id = "deleg_" + uuid.uuid4().hex[:8]
        self.children: dict[str, dict] = {}            # subagent_id -> {agent, row, index, status, ...}
        self.lock = threading.Lock()
        self.stopped = False
        self.t0 = time.time()

    # progress relayed into the chat, tagged with the sub-agent it comes from
    def _relay(self, sid: str, index: int, rec: dict):
        title = self.rows[index]["title"]

        def emit(ev):
            t = ev.get("type")
            if t == "tool_start":
                nm = str(ev.get("name") or "")
                nm = nm.split("__", 2)[-1] if nm.startswith("mcp__") else nm
                rec["trace"].append({"tool": nm, "target": str(ev.get("summary") or "")[:120], "ok": None})
                self.parent.emit({"type": "subagent", "delegation": self.id, "sid": sid, "index": index,
                                  "count": len(self.rows), "title": title, "kind": "tool", "tool": nm,
                                  "text": str(ev.get("summary") or "")[:160]})
            elif t == "tool_end" and rec["trace"]:
                rec["trace"][-1]["ok"] = bool(ev.get("ok"))
                path = rec.pop("pending_path", None)
                if ev.get("ok") and path and str(ev.get("name")) in ("write_file", "edit"):
                    rec["files"].add(str(path)[:300])
            elif t == "usage":
                for k in ("in", "cached", "out", "calls"):
                    rec["usage"][k] += int(ev.get(k) or 0)
            elif t == "error":
                rec["errors"].append(str(ev.get("text") or "")[:300])
        return emit

    def _child(self, sid: str, index: int, rec: dict):
        from meshpool_harness.agent import core as K
        parent, cfg, depth = self.parent, self.cfg, self.depth
        names = [n for n in parent._tool_names() if n not in BLOCKED and n != "delegate_task"]
        if cfg["access"] == "read":                      # read and research only
            names = [n for n in names if n in K.READ_ONLY and n != "office_task"]
        elif cfg["access"] == "edit":                    # read and edit files, never run commands
            names = [n for n in names if n not in NO_COMMANDS]
        nested = depth + 1 < cfg["depth"]                # this child may delegate in turn
        top = getattr(parent, "_delegate_top", parent)   # the chat itself, however deep this sub-agent is
        deleg = self

        def approve(kind, title, detail):                # nobody to ask: refuse, or allow once
            if cfg["auto_approve"]:
                return "yes", ""
            return "no", ("sub-agents cannot ask for approval; this needs the user (or the setting \"Sub-agents "
                          "approve their own actions\")")

        class Child(K.Agent):
            final_only = False

            def _tool_names(self):
                return names + (["delegate_task"] if nested else [])

            def _permit(self, name, args):              # unattended: never read outside the project, any mode
                if self.final_only:                      # out of steps: only the report is left
                    return False, "you are out of steps: no more tool calls; reply with your report now"
                web = name.replace("-", "_") in ("fetch_url", "mcp__meshpool_web__web_fetch")
                if (self._outside(name, args) and not web) or \
                        (name == "office_task" and _files_outside(parent.root, args)) or \
                        (name.replace("-", "_").startswith(OFFICE_MCP) and _mcp_paths_outside(parent.root, args)):
                    return False, ("sub-agents only read inside the project; ask the user to attach the file")
                if name in ("write_file", "edit"):
                    rec["pending_path"] = args.get("path")
                self.mode = top.mode                     # the chat's mode now: the user may have tightened it
                if getattr(top, "plan", False) and name not in K.PLAN_TOOLS and \
                        name.replace("-", "_") not in K.PLAN_MCP:   # what the chat itself may do in plan mode
                    return False, "the chat is in plan mode now: nothing may change"
                if name == "office_task" and self.mode != "auto" and "office_task" not in self.always:
                    # its engines run model-written code: for a sub-agent that is a command, so it needs approval
                    decision, note = self.approve("bash", "Run an office engine job?",
                                                  json.dumps(args, ensure_ascii=False)[:3000])
                    return decision in ("yes", "always"), note or ""
                return super()._permit(name, args)

            def _check_stop(self):                       # Stop or the time limit
                if deleg.stopped or rec.get("stop"):
                    raise K.Interrupted()
                super()._check_stop()

        a = Child(str(parent.root), mode=parent.mode, profile=parent.profile, approve=approve,
                  emit=self._relay(sid, index, rec), session_id="sub-" + sid, max_steps=cfg["steps"],
                  sessions_dir=str(_sub_sessions()),     # the user's own data folder, not the chat list
                  mcp=ChildMcp(parent.mcp) if parent.mcp and cfg["access"] == "inherit" else None)
        a._delegate_depth = depth + 1
        a._delegate_top = top
        a.always = top.always                            # "Allow ... in this chat" grants cover its sub-agents
        grep = a.ws.grep

        def grep_in_project(pattern, path=".", glob=None):   # a symlink must not carry a read outside the project
            return grep(pattern, path, glob, inside_only=True)
        a.ws.grep = grep_in_project
        glob = a.ws.glob

        def glob_in_project(pattern):                    # nor a symlinked folder a listing outside it
            return glob(pattern, inside_only=True)
        a.ws.glob = glob_in_project
        a.model = cfg["model"] or parent.current_model()
        row = self.rows[index]
        block = ("\n\nYou are a focused sub-agent working on one part of a larger job; other sub-agents may work on "
                 "other parts at the same time. You know nothing about the conversation that started you: your task "
                 "and context below are all you have.\n\nYOUR TASK:\n" + row["goal"])
        if row["context"]:
            block += "\n\nCONTEXT:\n" + row["context"]
        block += f"\n\nWORKSPACE: {parent.root} (the project's instructions above apply to you too)"
        block += ("\n\nWhen you are done, reply with a tight report: what you did, what you found (with a file path "
                  "or link for every fact), files you created or changed, and anything you could not do. You cannot "
                  "ask the user anything; if something needs them, say so in the report.")
        if row["schema"]:
            block += ("\n\nOUTPUT CONTRACT (checked by a machine): your final reply must be only JSON matching this "
                      "schema, with no other text:\n" + json.dumps(row["schema"])[:4000])
        a.messages[0]["content"] += block
        return a

    def _one(self, index: int) -> dict:
        row = self.rows[index]
        sid = f"sa-{index}-{uuid.uuid4().hex[:8]}"
        rec = {"index": index, "status": "running", "trace": [], "files": set(), "errors": [], "stop": False,
               "usage": {"in": 0, "cached": 0, "out": 0, "calls": 0}, "t0": time.time(), "goal": row["goal"]}
        with self.lock:
            self.children[sid] = rec
        self.parent.emit({"type": "subagent", "delegation": self.id, "sid": sid, "index": index,
                          "count": len(self.rows), "title": row["title"], "kind": "start"})
        if self.stopped:
            rec.update(status="interrupted", summary="Not started: the sub-agents were stopped.")
            return self._entry(sid, rec)
        a = None
        try:
            a = rec["agent"] = self._child(sid, index, rec)
            cfg_steps0 = a.max_steps
            reply = a.send(row["goal"]) or ""
            if row["schema"] and reply.strip() and not (a._stop or self.stopped or rec["stop"]):
                rec["schema_retries"] = 0
                errs = self._check(reply, row["schema"])
                if errs:                                  # one bounded correction, as in Hermes
                    rec["schema_retries"] = 1
                    used = sum(1 for m in a.messages if m.get("role") == "assistant")
                    a.max_steps = max(1, cfg_steps0 - used)   # within the same step budget, not a fresh one
                    reply = a.send("Your reply does not match the output schema: " + "; ".join(errs) +
                                   ". Reply again with only the JSON.") or reply
                    errs = self._check(reply, row["schema"])
                rec["schema_valid"], rec["schema_errors"] = not errs, errs
            stopped = a._stop or self.stopped or rec["stop"]
            hit_steps = any("Stopped after" in e for e in rec["errors"])
            if hit_steps and not stopped:                 # out of steps: one last answer, no tools, with the report
                a.final_only, a._no_tools, a.max_steps = True, True, 1   # tools stay listed; tool_choice "none"
                final = a.send("You have used all your steps. Stop working now and reply with your report: what "
                               "you did, what you found, files you changed, and what is left undone." +
                               (" Your reply must still be only JSON matching the output schema." if row["schema"]
                                else "")) or ""
                stopped = a._stop or self.stopped or rec["stop"]
                if final.strip() or stopped:
                    reply = final
                else:                                     # no report even then: say so, never stale narration
                    last = next((m.get("content") for m in reversed(a.messages) if m.get("role") == "assistant"
                                 and isinstance(m.get("content"), str) and m["content"].strip()), "")
                    reply = (f"This sub-agent used all {cfg_steps0} of its steps without writing a report. Its last "
                             "actions: " + ", ".join(f"{t['tool']}({t['target'][:40]})" for t in rec["trace"][-5:])
                             + (f"\n\nIts last note: {last.strip()[:1000]}" if last.strip() else ""))
                if row["schema"]:                         # the report still answers to the output contract
                    errs = self._check(reply, row["schema"])
                    rec["schema_valid"], rec["schema_errors"] = not errs, errs
                    rec.setdefault("schema_retries", 0)
            if stopped:
                rec["status"], rec["exit"] = "interrupted", "interrupted"
            elif hit_steps:
                rec["status"], rec["exit"] = "completed", "max_steps"
            elif not reply.strip() or (rec["errors"] and not reply.strip()):
                rec["status"], rec["exit"] = "failed", "error"
            else:
                rec["status"], rec["exit"] = "completed", "completed"
            if not reply.strip():                         # stopped or failed: what it had written so far
                reply = next((m.get("content") for m in reversed(a.messages) if m.get("role") == "assistant"
                              and isinstance(m.get("content"), str) and m["content"].strip()), "")
                if rec["status"] == "failed" and rec["errors"]:   # say why, so the chat can decide to retry
                    rec["error"] = rec["errors"][-1]
                    reply = (f"Error: {rec['error']}" + (f"\n\nWritten before it failed:\n{reply.strip()}"
                                                         if reply.strip() else ""))
            rec["summary"] = reply.strip() or (rec["errors"][-1] if rec["errors"] else "(no report)")
        except Exception as e:  # noqa: BLE001 - one failed part must not sink the rest
            rec.update(status="error", exit="error", error=f"{type(e).__name__}: {e}"[:300],
                       summary=f"This sub-agent failed: {type(e).__name__}: {e}")
        finally:
            if a is not None:
                shutil.rmtree(a.dir, ignore_errors=True)
                rec["steps"] = sum(1 for m in a.messages if m.get("role") == "assistant")
        return self._entry(sid, rec)

    @staticmethod
    def _check(reply: str, sch: dict) -> list[str]:
        try:
            value = _json_of(reply)
        except ValueError as e:
            return [f"not valid JSON ({e})"]
        try:
            return schema_errors(value, sch)
        except Exception:  # noqa: BLE001 - a schema too broken to check must not lose the report
            return []

    def _entry(self, sid: str, rec: dict) -> dict:
        p = self.parent
        turn = getattr(p, "_turn_usage", None)            # and in this turn's total
        with _USAGE_LOCK:                                 # children finish on several threads at once
            for k in ("in", "cached", "out", "calls"):    # the chat pays for its sub-agents: count it there
                p.usage[k] = p.usage.get(k, 0) + rec["usage"][k]
                if isinstance(turn, dict):
                    turn[k] = turn.get(k, 0) + rec["usage"][k]
        if getattr(p, "_delegate_depth", 0) > 0:          # a sub-agent's own sub-agents: pass it up the chain
            p.emit({"type": "usage", **{k: rec["usage"][k] for k in ("in", "cached", "out", "calls")}})
        summary = str(rec.get("summary") or "")
        # every report of one call shares a budget (about 60k characters) so the result fits the chat's context;
        # a report cut to fit is kept in full in a file the model can read
        cap = min(self.cfg["summary_chars"], max(3000, REPORTS_BUDGET // max(1, len(self.rows))))
        truncated = len(summary) > cap
        if truncated:                                      # head and tail; the full report goes to a file
            f = p.ws.run_dir / f"subagent-{sid}.txt"
            try:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(summary, encoding="utf-8")
                where = f" The full report is in {f} (read it with read_file)."
            except OSError:
                where = ""
            summary = (summary[:cap * 3 // 4] + f"\n\n[... {len(summary) - cap} characters left out.{where} ...]\n\n"
                       + summary[-cap // 4:])
        e = {"task_index": rec["index"], "subagent_id": sid, "goal": rec["goal"][:200], "status": rec["status"],
             "exit_reason": rec.get("exit", rec["status"]), "summary": summary, "truncated": truncated,
             "duration_seconds": round(time.time() - rec["t0"], 1), "steps": rec.get("steps", 0),
             "tokens": {"input": rec["usage"]["in"], "output": rec["usage"]["out"]},
             "files_written": sorted(rec["files"])[:50],
             "tool_trace": [f"{t['tool']}({t['target'][:60]}){'' if t['ok'] is not False else ' failed'}"
                            for t in rec["trace"][-30:]]}
        for k in ("error", "schema_valid", "schema_errors", "schema_retries"):
            if k in rec:
                e[k] = rec[k]
        rec["entry"] = e
        self.parent.emit({"type": "subagent", "delegation": self.id, "sid": sid, "index": rec["index"],
                          "count": len(self.rows), "title": self.rows[rec["index"]]["title"], "kind": "done",
                          "status": e["status"], "text": f"{e['status']} in {e['duration_seconds']:.0f} s"})
        return e

    def run(self, cancelled=lambda: False) -> dict:
        """Run every task (at most cfg['parallel'] at once) until done, the time limit, or Stop."""
        from concurrent.futures import ThreadPoolExecutor
        deadline = self.t0 + self.cfg["minutes"] * 60 if self.cfg["minutes"] else None
        done = threading.Event()

        def watch():
            while not done.is_set():
                if not self.stopped and ((deadline and time.time() > deadline) or cancelled()):
                    self.stop_all("the time limit" if deadline and time.time() > deadline else "Stop")
                time.sleep(0.25)
        threading.Thread(target=watch, daemon=True).start()
        ex = ThreadPoolExecutor(max(1, min(self.cfg["parallel"], len(self.rows))))
        futs = [ex.submit(self._one, i) for i in range(len(self.rows))]
        try:
            results = [f.result() for f in futs]
        except BaseException:                            # Ctrl+C in the terminal agent: stop them all, wait, go on
            self.stop_all("Stop")
            while True:
                try:
                    ex.shutdown(wait=True)
                    break
                except BaseException:  # noqa: BLE001 - a second Ctrl+C while they stop: keep waiting (about 1 s)
                    continue
            self.why = "Ctrl+C"
            self.partial = self._result([f.result() for f in futs if f.done() and not f.exception()])
            raise
        finally:
            done.set()
            ex.shutdown(wait=False)
        return self._result(results)

    def _result(self, results: list) -> dict:
        out = {"delegation_id": self.id, "results": sorted(results, key=lambda r: r["task_index"]),
               "total_duration_seconds": round(time.time() - self.t0, 1)}
        if getattr(self, "why", ""):
            out["note"] = f"The sub-agents were stopped by {self.why}; unfinished parts report what they had."
        return out

    def stop_all(self, why: str = "Stop"):
        self.stopped, self.why = True, why
        with self.lock:
            agents = [r.get("agent") for r in self.children.values()]
        for a in agents:
            if a is not None and not getattr(a, "_stop", False):
                a.stop()


# ---------- the tool -------------------------------------------------------------------------------------------------
def report(result: dict) -> str:
    """The results as the model reads them: a short text, then the JSON."""
    lines = []
    for r in result["results"]:
        lines.append(f"## Task {r['task_index'] + 1}: {r['goal'][:80]}\nstatus: {r['status']} ({r['exit_reason']}), "
                     f"{r['duration_seconds']} s, {r['steps']} steps"
                     + (f", files: {', '.join(r['files_written'][:10])}" if r["files_written"] else "")
                     + "\n\n" + r["summary"])
    head = f"{len(result['results'])} sub-agent report(s) (delegation {result['delegation_id']})."
    if result.get("note"):
        head += " " + result["note"]
    return head + "\n\n" + "\n\n".join(lines) + "\n\n```json\n" + _json_block(result) + "\n```"


def _json_block(result: dict, limit: int = 8000) -> str:
    """The machine-readable part, always whole JSON: made smaller step by step (tool traces, then file lists,
    then goals) until it fits, never cut in the middle."""
    rows = [{k: v for k, v in r.items() if k != "summary"} for r in result["results"]]
    steps = [lambda r: r.update(tool_trace=r.get("tool_trace", [])[-5:]),
             lambda r: r.pop("tool_trace", None),
             lambda r: r.update(files_written=r.get("files_written", [])[:5]),
             lambda r: r.update(goal=str(r.get("goal", ""))[:60]),
             lambda r: r.pop("schema_errors", None),
             lambda r: r.update(files_written=[f[-80:] for f in r.get("files_written", [])][:2])]
    for shrink in [None] + steps:
        if shrink:
            for r in rows:
                shrink(r)
        text = json.dumps({**result, "results": rows}, ensure_ascii=False)
        if len(text) <= limit:
            return text
    return text


def run_tool(parent, args: dict) -> tuple[str, bool]:
    """delegate_task for an Agent: runs the sub-agents and returns (text for the model, ok) when all have reported,
    or as soon as they stop (Stop in the chat, the time limit): finished reports always come back."""
    cfg = settings()
    if not cfg["on"]:
        return "Sub-agents are off in Settings, Advanced. Do the parts yourself, one after another.", False
    depth = getattr(parent, "_delegate_depth", 0)
    if depth >= cfg["depth"]:
        return f"Sub-agents cannot start more sub-agents here (depth limit {cfg['depth']}).", False
    try:
        rows = check_goals(args.get("tasks"))
    except ValueError as e:
        return str(e), False
    if len(rows) > cfg["max_tasks"]:
        return (f"Too many tasks ({len(rows)}): at most {cfg['max_tasks']} per call. Split the work into fewer, "
                "larger parts, or call again for the rest."), False
    parent.__dict__.pop("_delegate_partial", None)       # never an older call's reports
    d = Delegation(parent, rows, cfg, depth)
    reg = parent.__dict__.setdefault("_delegations", {})
    reg[d.id] = d
    gen = getattr(parent, "_gen", 0)
    try:
        res = d.run(cancelled=lambda: parent._stop or getattr(parent, "_gen", 0) != gen)
    except KeyboardInterrupt:                             # Ctrl+C: the turn ends, but what finished is kept as the
        if getattr(d, "partial", None):                   # tool's result (Agent._repair puts it in)
            parent._delegate_partial = report(d.partial)
        raise
    finally:
        reg.pop(d.id, None)
    ok = any(r["status"] in ("completed", "interrupted") for r in res["results"])   # reports came back
    return report(res), ok


def stop_all(parent):
    """Stop in the chat: every sub-agent it is running."""
    for d in list(getattr(parent, "_delegations", {}).values()):
        d.stop_all("Stop")
