"""delegate_task: sub-agents for Meshpool's own agent, modelled on Hermes Agent's delegate_task.

A chat hands self-contained parts of a job to sub-agents. Each starts fresh (it knows nothing of the chat), gets its
goal and context, works with the chat's own tools and permission mode, and returns one report. They run in this
process (nothing is trusted across processes), several at once, and the chat sees their progress live.

- Tools: the chat's tools minus what needs a person or is the chat's own: ask_user, memory, scheduling and (beyond
  the allowed depth) delegate_task. MCP tools come along, minus the assistant server's memory and scheduling.
- Permissions: the chat's mode. A sub-agent cannot ask, so anything that would need a yes is refused (or, with
  "Sub-agents approve their own actions" on, allowed once), as in Hermes.
- Background: in the app, a top-level delegation returns at once and the reports arrive as a message after the
  turn; the chat goes on meanwhile. Headless runs, and sub-agents that delegate, wait for the reports instead.
- Control: action list | steer | stop for running sub-agents; Stop in the chat stops them all.
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
BLOCKED_MCP = ("memory_save", "memory_forget", "schedule_task", "delete_scheduled_task", "run_subtasks")
_PLACEHOLDER = re.compile(r"^\s*(todo|tbd|task\s*\d*|goal\s*\d*|\.\.\.|placeholder)\s*$|<[a-z_ ]+>|\{[a-z_ ]+\}", re.I)


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
    s.update(background=flag("MH_SUBAGENT_BACKGROUND", "1"), auto_approve=flag("MH_SUBAGENT_AUTO_APPROVE", "0"),
             depth=num("MH_SUBAGENT_DEPTH", 1, 1, 3),
             summary_chars=num("MH_SUBAGENT_SUMMARY_CHARS", 24000, 2000, 200000),
             # in-process sub-agents have no MCP call timeout to stay under: Hermes-sized limits
             parallel=num("MH_SUBAGENT_PARALLEL", 4, 1, 10), max_tasks=12,
             steps=num("MH_SUBAGENT_STEPS", 50, 5, 250), minutes=num("MH_SUBAGENT_MINUTES", 30, 0, 240),
             access="read" if access == "read" else "inherit")
    return s


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
        f"Up to {max_children} tasks per call. In the app the call returns at once and the reports arrive as a "
        "message after your turn: finish what does not depend on them, say what you started, and END YOUR TURN; "
        "do not wait or poll. action=list shows running sub-agents, action=steer sends one a message "
        "(subagent_id, message), action=stop stops one. Sub-agents know nothing of this chat, so put everything "
        "they need in goal and context. Their reports are their own account: check important claims (a file, a "
        "link) before you rely on them."),
        "parameters": {"type": "object", "properties": {
            "tasks": {"type": "array", "minItems": 1, "items": task,
                      "description": f"The tasks, up to {max_children}. Required to start sub-agents."},
            "action": {"type": "string", "enum": ["spawn", "list", "steer", "stop"], "description": "Default spawn."},
            "subagent_id": {"type": "string"}, "message": {"type": "string"}}, "required": []}}}


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
    if "enum" in sch and value not in sch["enum"]:
        errs.append(f"{path}: not one of {sch['enum']}")
    if isinstance(value, dict):
        for k in sch.get("required") or []:
            if k not in value:
                errs.append(f"{path}: missing {k!r}")
        for k, sub in (sch.get("properties") or {}).items():
            if k in value and isinstance(sub, dict):
                errs += schema_errors(value[k], sub, f"{path}.{k}")
    if isinstance(value, list) and isinstance(sch.get("items"), dict):
        for i, v in enumerate(value):
            errs += schema_errors(v, sch["items"], f"{path}[{i}]")
    return errs[:10]


def _json_of(text: str):
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    return json.loads(t)


# ---------- MCP tools a sub-agent may use -----------------------------------------------------------------------------
class ChildMcp:
    """The chat's MCP servers, minus the assistant server's memory, scheduling and sub-agent tools."""
    def __init__(self, mcp):
        self.m = mcp
        self.last_images: list = []

    def _ok(self, name: str) -> bool:
        return not any(name.replace("-", "_").endswith("__" + b) for b in BLOCKED_MCP)

    def has(self, name: str) -> bool:
        return self._ok(name) and self.m.has(name)

    def schemas(self) -> list:
        return [s for s in self.m.schemas() if self._ok(s["function"]["name"])]

    def call(self, name: str, args: dict):
        out = self.m.call(name, args)
        self.last_images, self.m.last_images = list(getattr(self.m, "last_images", []) or []), []
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
                if nm in ("write_file", "edit") and ev.get("summary"):
                    rec["files"].add(str(ev["summary"]).split(" ")[0][:200])
                self.parent.emit({"type": "subagent", "delegation": self.id, "sid": sid, "index": index,
                                  "count": len(self.rows), "title": title, "kind": "tool", "tool": nm,
                                  "text": str(ev.get("summary") or "")[:160]})
            elif t == "tool_end" and rec["trace"]:
                rec["trace"][-1]["ok"] = bool(ev.get("ok"))
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
        if cfg["access"] == "read":                      # Settings: read and research only
            names = [n for n in names if n in K.READ_ONLY and n != "office_task"]
        nested = depth + 1 < cfg["depth"]                # this child may delegate in turn
        deleg = self

        def approve(kind, title, detail):                # nobody to ask: refuse, or allow once
            if cfg["auto_approve"]:
                return "yes", ""
            return "no", ("sub-agents cannot ask for approval; this needs the user (or the setting \"Sub-agents "
                          "approve their own actions\")")

        class Child(K.Agent):
            def _tool_names(self):
                return names + (["delegate_task"] if nested else [])

            def _check_stop(self):                       # Stop, the time limit, or action=stop
                if deleg.stopped or rec.get("stop"):
                    raise K.Interrupted()
                super()._check_stop()

        a = Child(str(parent.root), mode=parent.mode, profile=parent.profile, approve=approve,
                  emit=self._relay(sid, index, rec), session_id="sub-" + sid, max_steps=cfg["steps"],
                  mcp=ChildMcp(parent.mcp) if parent.mcp and cfg["access"] != "read" else None)
        a._delegate_depth = depth + 1
        a.model = cfg["model"] or parent.current_model()
        row = self.rows[index]
        block = ("\n\nYou are a focused sub-agent working on one part of a larger job; other sub-agents may work on "
                 "other parts at the same time. You know nothing about the conversation that started you: your task "
                 "and context below are all you have.\n\nYOUR TASK:\n" + row["goal"])
        if row["context"]:
            block += "\n\nCONTEXT:\n" + row["context"]
        block += f"\n\nWORKSPACE: {parent.root}"
        for fn in ("AGENTS.md", "CLAUDE.md", ".cursorrules"):
            p = parent.root / fn
            try:
                if p.is_file():
                    block += f"\n\nProject instructions ({fn}, binding):\n" + p.read_text("utf-8", "replace")[:8000]
            except OSError:
                pass
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
            reply = a.send(row["goal"]) or ""
            if row["schema"] and reply.strip() and not (a._stop or self.stopped or rec["stop"]):
                rec["schema_retries"] = 0
                errs = self._check(reply, row["schema"])
                if errs:                                  # one bounded correction, as in Hermes
                    rec["schema_retries"] = 1
                    reply = a.send("Your reply does not match the output schema: " + "; ".join(errs) +
                                   ". Reply again with only the JSON.") or reply
                    errs = self._check(reply, row["schema"])
                rec["schema_valid"], rec["schema_errors"] = not errs, errs
            stopped = a._stop or self.stopped or rec["stop"]
            hit_steps = any("Stopped after" in e for e in rec["errors"])
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
            rec["summary"] = reply.strip() or (rec["errors"][-1] if rec["errors"] else "(no report)")
        except Exception as e:  # noqa: BLE001 - one failed part must not sink the rest
            rec.update(status="error", exit="error", summary=f"This sub-agent failed: {type(e).__name__}: {e}")
        finally:
            if a is not None:
                shutil.rmtree(a.dir, ignore_errors=True)
                rec["steps"] = sum(1 for m in a.messages if m.get("role") == "assistant")
        return self._entry(sid, rec)

    @staticmethod
    def _check(reply: str, sch: dict) -> list[str]:
        try:
            return schema_errors(_json_of(reply), sch)
        except ValueError as e:
            return [f"not valid JSON ({e})"]

    def _entry(self, sid: str, rec: dict) -> dict:
        p = self.parent
        for k in ("in", "cached", "out", "calls"):        # the chat pays for its sub-agents: count it there
            p.usage[k] = p.usage.get(k, 0) + rec["usage"][k]
        summary = str(rec.get("summary") or "")
        cap = self.cfg["summary_chars"]
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
        for k in ("schema_valid", "schema_errors", "schema_retries"):
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
        try:
            with ThreadPoolExecutor(max(1, min(self.cfg["parallel"], len(self.rows)))) as ex:
                results = list(ex.map(self._one, range(len(self.rows))))
        finally:
            done.set()
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

    def listing(self) -> list[dict]:
        with self.lock:
            return [{"subagent_id": sid, "task_index": r["index"], "goal": r["goal"][:120], "status": r["status"],
                     "seconds": round(time.time() - r["t0"]), "tools_used": len(r["trace"]),
                     "last_tool": (r["trace"][-1]["tool"] if r["trace"] else "")} for sid, r in self.children.items()]


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
    return head + "\n\n" + "\n\n".join(lines) + "\n\n```json\n" + json.dumps(
        {**result, "results": [{k: v for k, v in r.items() if k != "summary"} for r in result["results"]]},
        ensure_ascii=False)[:20000] + "\n```"


def run_tool(parent, args: dict) -> tuple[str, bool]:
    """delegate_task for an Agent. Returns (text for the model, ok)."""
    cfg = settings()
    reg = parent.__dict__.setdefault("_delegations", {})
    action = str(args.get("action") or "spawn").lower()
    if action == "list":
        rows = [dict(c, delegation_id=d.id) for d in reg.values() for c in d.listing()]
        return (json.dumps(rows, indent=1) if rows else "No sub-agents are running."), True
    if action in ("stop", "steer"):
        sid = str(args.get("subagent_id") or "")
        for d in reg.values():
            rec = d.children.get(sid)
            if rec:
                if action == "stop":
                    rec["stop"] = True
                    if rec.get("agent"):
                        rec["agent"].stop()
                    return f"Stopping {sid}; its report (what it had so far) still arrives.", True
                msg = str(args.get("message") or "").strip()
                if not msg or not rec.get("agent") or rec["status"] != "running":
                    return f"{sid} is not running, or there is no message.", False
                rec["agent"].steer("[From the agent that started you] " + msg)
                return f"Sent to {sid}; it reads it before its next step.", True
        return f"No running sub-agent {sid!r}. Use action=list.", False
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
        return (f"Too many tasks ({len(rows)}): at most {cfg['max_tasks']} per call (Settings, Advanced). Split the "
                "work into fewer, larger parts, or call again for the rest."), False
    d = Delegation(parent, rows, cfg, depth)
    reg[d.id] = d
    deliver = getattr(parent, "deliver", None)
    if cfg["background"] and depth == 0 and callable(deliver):
        def bg():
            try:
                res = d.run()
                if d.stopped and getattr(d, "why", "") == "Stop":   # the user stopped them: no new turn
                    parent.emit({"type": "status", "text": f"Sub-agents stopped ({len(rows)} task"
                                                           f"{'s' if len(rows) != 1 else ''})."})
                    return
                text = report(res)
            except Exception as e:  # noqa: BLE001
                text = f"The sub-agents of {d.id} failed: {type(e).__name__}: {e}"
            finally:
                reg.pop(d.id, None)
            n = len(rows)
            deliver(f"Sub-agent results are in ({n} task{'s' if n != 1 else ''}).\n\n​[Meshpool] Results of "
                    f"delegate_task {d.id}. Read them, check what matters, and carry on with the user's request.\n\n"
                    + text)
        threading.Thread(target=bg, daemon=True, name=d.id).start()
        time.sleep(0.05)
        ids = [s for s in d.children]
        return json.dumps({"status": "dispatched", "delegation_id": d.id, "count": len(rows), "subagent_ids": ids,
                           "note": "The sub-agents are working. Their reports arrive as a message after your turn. "
                                   "Finish what does not depend on them, tell the user what you started, and end "
                                   "your turn; do not wait or poll. action=list / steer / stop control them."}), True
    gen = getattr(parent, "_gen", 0)
    try:
        res = d.run(cancelled=lambda: parent._stop or getattr(parent, "_gen", 0) != gen)
    finally:
        reg.pop(d.id, None)
    ok = any(r["status"] == "completed" for r in res["results"])
    return report(res), ok


def stop_all(parent):
    """Stop in the chat: every sub-agent it started, running in the background or not."""
    for d in list(getattr(parent, "_delegations", {}).values()):
        d.stop_all("Stop")
