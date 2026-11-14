"""`meshpool acp`: the Meshpool agent as an Agent Client Protocol (ACP) server on stdin/stdout.

Plug Meshpool into anything that hosts ACP agents: Zed, JetBrains IDEs, VS Code (ACP extension), Meshpool's own
app, or your own tools. Example Zed settings:

    "agent_servers": {"Meshpool": {"command": "meshpool", "args": ["acp"]}}

Supports initialize, session/new (with the client's MCP servers), session/prompt (streamed message chunks, tool
calls with results, plans), session/request_permission for edits and commands, and session/cancel.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import uuid

from meshpool_harness import __version__
from meshpool_harness import config as C

KINDS = {"read_file": "read", "grep": "search", "glob": "search", "web_search": "search", "web_research": "search", "fetch_url": "fetch", "edit": "edit",
         "write_file": "edit", "bash": "execute", "fetch_url": "fetch", "todo_write": "think", "office_task": "other"}


class Server:
    def __init__(self, profile: str = "code", auto: bool = False):
        self.profile, self.auto = profile, auto
        self.out = sys.stdout.buffer
        self.lock = threading.Lock()
        self.sessions: dict[str, dict] = {}
        self._id = 0
        self._wait: dict[int, dict] = {}

    # ---------- wire -----------------------------------------------------------------------------------------
    def send(self, obj):
        with self.lock:
            self.out.write((json.dumps(obj) + "\n").encode("utf-8"))
            self.out.flush()

    def reply(self, rid, result=None, error=None):
        self.send({"jsonrpc": "2.0", "id": rid, **({"error": error} if error else {"result": result})})

    def notify_update(self, sid, update: dict):
        self.send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": update}})

    def ask_client(self, method, params, timeout=600):
        with self.lock:
            self._id += 1
            rid = self._id
            ev = threading.Event()
            self._wait[rid] = {"ev": ev, "msg": None}
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        ev.wait(timeout)
        return (self._wait.pop(rid, {}) or {}).get("msg") or {}

    # ---------- methods --------------------------------------------------------------------------------------
    def initialize(self, p):
        return {"protocolVersion": 1, "agentInfo": {"name": "meshpool", "title": "Meshpool", "version": __version__},
                "agentCapabilities": {"loadSession": False, "promptCapabilities": {"embeddedContext": True},
                                      "mcpCapabilities": {"http": True, "sse": False}},
                "authMethods": []}

    def new_session(self, p):
        from meshpool_harness.agent.core import Agent
        from meshpool_harness.agent.mcp import Manager
        cwd = p.get("cwd") or os.getcwd()
        extra = {}
        for s in p.get("mcpServers") or []:
            if s.get("url"):
                extra[s["name"]] = {"url": s["url"], "headers": {h["name"]: h["value"] for h in s.get("headers") or []}}
            elif s.get("command"):
                extra[s["name"]] = {"command": s["command"], "args": s.get("args") or [],
                                    "env": {e["name"]: e["value"] for e in s.get("env") or []}}
        mcp = Manager(roots=[cwd], extra=extra).start(timeout=60)
        sid = uuid.uuid4().hex
        st = {"id": sid, "busy": False}
        agent = Agent(cwd, mcp=mcp, emit=lambda ev: self.on_event(sid, ev),
                      approve=lambda kind, title, detail: self.on_permission(sid, kind, title, detail),
                      mode="auto" if self.auto else "ask", profile=self.profile)
        st.update(agent=agent, mcp=mcp, calls={})
        self.sessions[sid] = st
        return {"sessionId": sid}

    def prompt(self, p, rid):
        st = self.sessions.get(p.get("sessionId"))
        if not st:
            return self.reply(rid, error={"code": -32602, "message": "unknown session"})
        parts = []
        for b in p.get("prompt") or []:
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif b.get("type") == "resource":
                r = b.get("resource") or {}
                parts.append(f"<file uri=\"{r.get('uri', '')}\">\n{r.get('text', '')}\n</file>")
            elif b.get("type") == "resource_link":
                parts.append(f"(attached: {b.get('uri', '')})")

        def run():
            st["busy"] = True
            a = st["agent"]
            try:
                a.send("\n\n".join(parts))
                self.reply(rid, {"stopReason": "cancelled" if a._stop else "end_turn"})
            except Exception as e:
                self.reply(rid, error={"code": -32603, "message": str(e)})
            finally:
                st["busy"] = False
        threading.Thread(target=run, daemon=True).start()

    # ---------- agent -> client --------------------------------------------------------------------------------
    def on_event(self, sid, ev):
        t = ev.get("type")
        st = self.sessions.get(sid) or {}
        if t == "text_delta":
            self.notify_update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": ev["text"]}})
        elif t == "assistant" and not ev.get("streamed") and ev.get("text"):
            self.notify_update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": ev["text"]}})
        elif t == "tool_start":
            tid = ev.get("id") or uuid.uuid4().hex
            st.setdefault("calls", {})[tid] = ev
            content = [{"type": "content", "content": {"type": "text", "text": ev["diff"]}}] if ev.get("diff") else []
            self.notify_update(sid, {"sessionUpdate": "tool_call", "toolCallId": tid,
                                     "title": f"{ev['name']}: {ev.get('summary', '')}"[:200],
                                     "kind": KINDS.get(ev["name"], "other"), "status": "in_progress", "content": content})
        elif t == "tool_end":
            self.notify_update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": ev.get("id"),
                                     "status": "completed" if ev.get("ok") else "failed",
                                     "content": [{"type": "content", "content": {"type": "text",
                                                                                 "text": ev.get("output", "")[:6000]}}]})
        elif t == "plan":
            self.notify_update(sid, {"sessionUpdate": "plan", "entries": [
                {"content": e["content"], "status": e.get("status", "pending"), "priority": "medium"}
                for e in ev.get("entries") or []]})
        elif t in ("status", "error", "interrupted"):
            self.notify_update(sid, {"sessionUpdate": "agent_thought_chunk",
                                     "content": {"type": "text", "text": ev.get("text", "") + "\n"}})

    def on_permission(self, sid, kind, title, detail):
        st = self.sessions.get(sid) or {}
        calls = st.get("calls") or {}
        tid = next(reversed(calls)) if calls else uuid.uuid4().hex
        r = self.ask_client("session/request_permission", {
            "sessionId": sid,
            "toolCall": {"toolCallId": tid, "title": title, "kind": "edit" if kind == "edit" else "execute",
                         "content": [{"type": "content", "content": {"type": "text", "text": detail[:6000]}}]},
            "options": [{"optionId": "allow_once", "name": "Allow", "kind": "allow_once"},
                        {"optionId": "allow_always", "name": "Allow for this session", "kind": "allow_always"},
                        {"optionId": "reject_once", "name": "Deny", "kind": "reject_once"}]})
        outcome = (r.get("result") or {}).get("outcome") or {}
        choice = outcome.get("optionId") if outcome.get("outcome") == "selected" else None
        return {"allow_once": ("yes", ""), "allow_always": ("always", "")}.get(choice, ("no", ""))

    # ---------- loop -------------------------------------------------------------------------------------------
    def handle(self, msg):
        if "method" not in msg:                                   # reply to one of our requests
            w = self._wait.get(msg.get("id"))
            if w:
                w["msg"] = msg
                w["ev"].set()
            return
        m, p, rid = msg["method"], msg.get("params") or {}, msg.get("id")
        try:
            if m == "initialize":
                return self.reply(rid, self.initialize(p))
            if m == "authenticate":
                return self.reply(rid, {})
            if m == "session/new":
                return self.reply(rid, self.new_session(p))
            if m == "session/prompt":
                return self.prompt(p, rid)
            if m == "session/cancel":
                st = self.sessions.get(p.get("sessionId"))
                if st:
                    st["agent"].stop()
                return None
            if m == "session/set_mode":
                st = self.sessions.get(p.get("sessionId"))
                if st:
                    st["agent"].mode = "auto" if p.get("modeId") == "auto" else "ask"
                return self.reply(rid, {})
            if rid is not None:
                self.reply(rid, error={"code": -32601, "message": f"method not found: {m}"})
        except Exception as e:
            if rid is not None:
                self.reply(rid, error={"code": -32603, "message": str(e)})

    def serve(self):
        for raw in sys.stdin.buffer:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            threading.Thread(target=self.handle, args=(msg,), daemon=True).start() if msg.get("method") == "session/new" \
                else self.handle(msg)
        for st in self.sessions.values():
            try:
                st["mcp"].close()
            except Exception:
                pass


def main(profile: str = "code", auto: bool = False) -> int:
    C.load_into_env()
    # stdout is the protocol channel: anything else printed would corrupt it
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    srv = Server(profile, auto)
    srv.out = real_stdout.buffer
    srv.serve()
    return 0
