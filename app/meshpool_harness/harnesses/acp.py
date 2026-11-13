"""ACP client: runs any Agent Client Protocol agent (Hermes, Gemini CLI, Goose, Claude Code/Codex adapters, ...)
as a Meshpool harness. Standard library only.

The agent is a child process speaking newline-delimited JSON-RPC 2.0 on stdin/stdout. This class presents the same
surface as the built-in Agent (send / stop / reset / save / usage / title / mode), and turns ACP session updates into
Meshpool UI events:

  agent_message_chunk -> text_delta, then one assistant event per turn
  agent_thought_chunk -> thought
  tool_call / tool_call_update -> tool_start / tool_end
  plan -> plan
  usage_update -> context
  session/request_permission -> approval (the agent's own options become the buttons)
"""
from __future__ import annotations

import json
import re
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from meshpool_harness import __version__ as _VERSION
from meshpool_harness import config as C

PROTOCOL_VERSION = 1



def _kill_tree(proc) -> None:
    """End a harness process and everything it started (commands, MCP servers)."""
    if not proc:
        return
    try:
        import psutil
        root = psutil.Process(proc.pid)
        kids = root.children(recursive=True)
    except Exception:  # noqa: BLE001
        kids = []
    for k in kids:
        try:
            k.kill()
        except Exception:  # noqa: BLE001
            pass

class ACPError(Exception):
    pass


class ACPStopped(ACPError):
    pass


def _text_of(content) -> str:
    """Flatten ACP tool-call content into text for the UI."""
    out = []
    for c in content or []:
        t = c.get("type")
        if t == "content":
            inner = c.get("content") or {}
            if inner.get("type") == "text":
                out.append(inner.get("text", ""))
            elif inner.get("type") == "resource":
                out.append((inner.get("resource") or {}).get("text", ""))
            elif inner.get("type") == "resource_link":
                out.append(f"[{inner.get('name', '')}] {inner.get('uri', '')}")
        elif t == "diff":
            path = c.get("path", "")
            old = (c.get("oldText") or "").splitlines()
            new = (c.get("newText") or "").splitlines()
            import difflib
            d = list(difflib.unified_diff(old, new, f"a/{path}", f"b/{path}", n=2, lineterm=""))
            out.append("\n".join(d[:200]) if d else f"(no change to {path})")
        elif t == "terminal":
            out.append(f"[terminal {c.get('terminalId', '')}]")
    return "\n".join(x for x in out if x)


def _label(u: dict) -> tuple[str, str]:
    """Tool label for the UI: MCP tools read as 'make_deck (meshpool office)'; others use the ACP kind."""
    title = str(u.get("title") or "")
    kind = u.get("kind") or "tool"
    if title.startswith("mcp__"):
        parts = title.split("__")
        return parts[-1], parts[1].replace("_", " ") if len(parts) > 2 else ""
    if kind in ("other", "tool") and title:
        head, _, rest = title.partition(":")
        return (head.strip(), rest.strip()) if rest else (title[:60], "")
    return kind, title


def _diff_of(content) -> str:
    return "\n".join(_text_of([c]) for c in content or [] if c.get("type") == "diff")


def mcp_servers_for_acp(roots: list[str]) -> list[dict]:
    """Meshpool's enabled MCP servers in ACP's session/new format, so every harness sees the same tools."""
    from meshpool_harness.agent import mcp as M
    os.environ["MESHPOOL_PROJECT"] = str(Path(roots[0]).resolve()) if roots else os.getcwd()
    try:
        cfg = M.active_config()
    except (ValueError, OSError):
        return []
    out = []
    for name, spec in cfg.items():
        if not isinstance(spec, dict) or spec.get("disabled"):
            continue
        if M.recently_failed(name, spec) is not None:
            continue                                 # a broken server would make the harness wait and retry it
        spec = M._expand(spec)
        if spec.get("url"):
            out.append({"type": "http", "name": name, "url": spec["url"],
                        "headers": [{"name": k, "value": str(v)} for k, v in (spec.get("headers") or {}).items()]})
        elif spec.get("command"):
            cmd = spec["command"]
            env = dict(spec.get("env") or {})
            if cmd in ("python", "python3", "py"):
                cmd = sys.executable
                env["PYTHONPATH"] = str(C.APP)
                env.setdefault("MESHPOOL_HOME", str(C.home()))
                env.setdefault("MESHPOOL_CONFIG", str(C.config_path()))
                env.setdefault("MESHPOOL_PROJECT", os.environ["MESHPOOL_PROJECT"])
                env.setdefault("PYTHONUTF8", "1")
                if os.environ.get("PYTHONPYCACHEPREFIX"):        # never write caches inside the signed app
                    env["PYTHONPYCACHEPREFIX"] = os.environ["PYTHONPYCACHEPREFIX"]
                else:
                    env["PYTHONDONTWRITEBYTECODE"] = "1"
            cmd = shutil.which(cmd) or cmd
            out.append({"name": name, "command": cmd, "args": [str(a) for a in spec.get("args", [])],
                        "env": [{"name": k, "value": str(v)} for k, v in env.items()]})
    return out


def office_server(roots: list[str]) -> dict:
    """Meshpool Office MCP server (PowerPoint, Excel, Word, data analysis) for ACP harnesses."""
    env = {"PYTHONPATH": str(C.APP), "MESHPOOL_PROJECT": str(Path(roots[0]).resolve()) if roots else os.getcwd(),
           "MESHPOOL_HOME": str(C.home()), "MESHPOOL_CONFIG": str(C.config_path()), "PYTHONUTF8": "1",
           "PYTHONIOENCODING": "utf-8"}
    for k in ("MESHPOOL_API_KEY", "MESHPOOL_BASE_URL", "MH_MODEL", "PYTHONPYCACHEPREFIX", "PATH"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    if not os.environ.get("PYTHONPYCACHEPREFIX"):
        env["PYTHONDONTWRITEBYTECODE"] = "1"
    return {"name": "meshpool-office", "command": sys.executable, "args": ["-m", "meshpool_harness.agent.office_mcp_server"],
            "env": [{"name": k, "value": v} for k, v in env.items()]}


def _auth_failure(text: str) -> bool:
    """A short reply that is only an authentication error from the model endpoint (HTTP 401/403)."""
    import re
    s = (text or "").strip()
    return len(s) < 400 and bool(re.search(r"\b40[13]\b", s)) and bool(
        re.search(r"(?i)api.?key|bad key|unauthori[sz]ed|authenticat|forbidden|invalid.{0,20}key", s))


class ACPAgent:
    """One ACP agent process serving one Meshpool session."""

    def __init__(self, manifest: dict, workdir: str, *, emit=None, approve=None, mode: str | None = None,
                 session_id: str | None = None):
        self.manifest = manifest
        self.root = Path(workdir).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError(f"Project folder not found: {workdir}")
        self.id = session_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.dir = C.runs_dir() / "sessions" / self.id
        self.emit_cb = emit or (lambda ev: None)
        self.approve = approve
        self.mode = mode or os.environ.get("MH_AGENT_PERMISSIONS", "ask")
        self.title = ""
        self.created = time.time()
        self.usage = {"in": 0, "cached": 0, "out": 0, "calls": 0}
        self.context = None                    # {"used": n, "size": n} from usage_update
        self.events: list[dict] = []           # saved transcript (the agent keeps its own history)
        self.messages = [None]                 # Session.view() compatibility
        self.mcp = None
        self.acp_session = None
        self.loaded_from = None                # ACP session id to load on first send (resume)
        self.agent_info = {}
        self.capabilities = {}
        self.model: str | None = None          # chosen in the app; applied with session/set_model
        self.available_models = None           # [{"id", "name"}] the harness offers, from session/new
        self.plan = False
        self._p = None
        self._id = 0
        self._pending: dict[int, dict] = {}
        self._cv = threading.Condition()
        self._alive = False
        self._stop = False
        self._text = []
        self._replaying = False
        self._tools: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ---------- process + JSON-RPC ----------------------------------------------------------------------------
    def _emit(self, ev: dict):
        if self._replaying:
            return
        if ev.get("type") not in ("text_delta", "thought", "model_start", "model_end", "context"):
            self.events.append({k: v for k, v in ev.items() if k not in ("i", "t")})
        self.emit_cb(ev)

    def _start(self):
        if self._p and self._p.poll() is None:
            return
        m = self.manifest
        if m.get("prepare") == "hermes":
            from meshpool_harness.harnesses import prepare_hermes
            prepare_hermes()
        elif m.get("prepare") == "codex":
            from meshpool_harness.harnesses import prepare_codex
            prepare_codex()
        cmd = m.get("command", "")
        exe = shutil.which(cmd) or (cmd if Path(cmd).exists() else None)
        if not exe:
            raise ACPError(f"{m.get('name', 'This harness')} is not installed (command '{cmd}' not found). "
                           + (m.get("install") or ""))
        env = dict(os.environ)
        env.update({k: str(v) for k, v in (m.get("env") or {}).items()})
        for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONNOUSERSITE", "PYTHONPYCACHEPREFIX"):
            if k not in (m.get("env") or {}):
                env.pop(k, None)               # never leak Meshpool's Python into another Python app
        for k in m.get("env_unset") or []:
            env.pop(k, None)                   # e.g. a subscription harness must not bill an API key instead
        for k in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
            if env.get(k) and not (Path(self.root) / env[k]).exists():   # as the harness sees it from its cwd
                env.pop(k)                     # a stale CA path breaks every HTTPS client in the harness
        log_dir = C.runs_dir() / "harness-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._err = open(log_dir / f"{m.get('id', 'acp')}.log", "a", encoding="utf-8", errors="replace")
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {"start_new_session": True}
        self._p = subprocess.Popen([exe, *[str(a) for a in m.get("args", [])]], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=self._err, cwd=str(self.root), env=env, **kw)
        self._alive = True
        threading.Thread(target=self._reader, daemon=True).start()
        init = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}, "terminal": False},
            "clientInfo": {"name": "meshpool", "title": "Meshpool", "version": _VERSION}}, timeout=300)
        self.agent_info = init.get("agentInfo") or {}
        self.capabilities = init.get("agentCapabilities") or {}

    def _reader(self):
        for raw in self._p.stdout:
            try:
                msg = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if "method" in msg and "id" in msg:
                threading.Thread(target=self._serve_request, args=(msg,), daemon=True).start()
            elif "method" in msg:
                try:
                    self._notification(msg)
                except Exception as e:  # a malformed update must not kill the reader
                    print(f"ACP update error: {e}", file=sys.stderr)
            elif "id" in msg:
                with self._cv:
                    self._pending[msg["id"]] = msg
                    self._cv.notify_all()
        with self._cv:
            self._alive = False
            self._cv.notify_all()

    def _write(self, obj):
        try:
            with self._lock:
                self._p.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
                self._p.stdin.flush()
        except (BrokenPipeError, OSError, ValueError, AttributeError):
            raise ACPError(f"{self.manifest.get('name', 'The harness')} stopped. Its log is in runs/harness-logs.")

    def _request(self, method: str, params: dict, timeout: float | None = None) -> dict:
        with self._cv:
            self._id += 1
            rid = self._id
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        end = time.time() + timeout if timeout else None
        with self._cv:
            while rid not in self._pending:
                if not self._alive:
                    raise ACPError(f"{self.manifest.get('name', 'The harness')} exited. "
                                   f"See runs/harness-logs/{self.manifest.get('id', 'acp')}.log")
                if end and time.time() > end:
                    raise ACPError(f"No reply to {method} within {int(timeout)} s.")
                if method.startswith("session/") and self._stop and time.time() - getattr(self, "_stop_t", 1e18) > 3:
                    break                                # the harness ignored the cancel: stop it hard below
                self._cv.wait(0.3)
            else:
                msg = self._pending.pop(rid)
                if "error" in msg:
                    e = msg["error"]
                    raise ACPError(f"{e.get('message', 'error')}" + (f": {e.get('data')}" if e.get("data") else ""))
                return msg.get("result") or {}
        self._hard_restart()
        raise ACPStopped("stopped")

    def _hard_restart(self):
        """End the harness process (and what it started); the next message restarts it and reloads the session."""
        sid = self.acp_session
        try:
            import psutil
            root = psutil.Process(self._p.pid)
            procs = root.children(recursive=True) + [root]
        except Exception:  # noqa: BLE001
            procs = []
        for pr in procs:
            try:
                pr.kill()
            except Exception:  # noqa: BLE001
                pass
        if not procs and self._p:
            try:
                self._p.kill()
            except Exception:  # noqa: BLE001
                pass
        if sid:
            self.loaded_from, self.acp_session = sid, None

    def _respond(self, rid, result=None, error=None):
        self._write({"jsonrpc": "2.0", "id": rid, **({"error": error} if error else {"result": result})})

    # ---------- agent -> client ---------------------------------------------------------------------------------
    def _serve_request(self, msg):
        m, p, rid = msg.get("method"), msg.get("params") or {}, msg["id"]
        try:
            if m == "session/request_permission":
                return self._respond(rid, {"outcome": self._permission(p)})
            if m == "fs/read_text_file":
                path = Path(p["path"])
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
                start = max(0, int(p.get("line") or 1) - 1)
                lim = p.get("limit")
                return self._respond(rid, {"content": "".join(lines[start:start + int(lim)] if lim else lines[start:])})
            if m == "fs/write_text_file":
                path = Path(p["path"]).resolve()
                if self.root not in path.parents and path != self.root:
                    return self._respond(rid, error={"code": -32602, "message": "writes are limited to the project folder"})
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(p.get("content", ""), encoding="utf-8")
                return self._respond(rid, None)
            self._respond(rid, error={"code": -32601, "message": f"method not supported: {m}"})
        except ACPError:
            pass
        except Exception as e:
            try:
                self._respond(rid, error={"code": -32603, "message": str(e)})
            except ACPError:
                pass

    def _permission(self, p: dict) -> dict:
        options = p.get("options") or []
        tc = p.get("toolCall") or {}
        allow = next((o for o in options if o.get("kind") == "allow_once"), None) or \
            next((o for o in options if str(o.get("kind", "")).startswith("allow")), None)
        if self.mode == "auto" and allow:
            return {"outcome": "selected", "optionId": allow["optionId"]}
        if self.mode == "edits" and allow and (tc.get("kind") == "edit" or _diff_of(tc.get("content"))):
            return {"outcome": "selected", "optionId": allow["optionId"]}
        if not self.approve or self._stop:
            return {"outcome": "cancelled"}
        title = tc.get("title") or "The harness asks for permission"
        raw = tc.get("rawInput")
        detail = _diff_of(tc.get("content")) or _text_of(tc.get("content")) or \
            (json.dumps(raw, indent=1, ensure_ascii=False)[:3000] if raw else "")
        kind = "edit" if (tc.get("kind") == "edit" or _diff_of(tc.get("content"))) else \
            ("bash" if tc.get("kind") == "execute" else "mcp")
        choice, _note = self.approve(kind, title, detail,
                                     [{"id": o["optionId"], "label": o.get("name") or o["optionId"],
                                       "kind": o.get("kind", "")} for o in options])
        if choice in (None, "", "no"):
            reject = next((o for o in options if o.get("kind") == "reject_once"), None)
            return {"outcome": "selected", "optionId": reject["optionId"]} if reject else {"outcome": "cancelled"}
        if choice == "yes" and allow:
            return {"outcome": "selected", "optionId": allow["optionId"]}
        if any(o["optionId"] == choice for o in options):
            return {"outcome": "selected", "optionId": choice}
        return {"outcome": "cancelled"}

    def _notification(self, msg):
        if msg.get("method") != "session/update":
            return
        u = (msg.get("params") or {}).get("update") or {}
        kind = u.get("sessionUpdate")
        if kind == "agent_message_chunk":
            c = u.get("content") or {}
            if c.get("type") == "text" and c.get("text"):
                if re.match(r"\s*Warning: Model metadata for .* not found", c["text"]):
                    return                         # Codex's own start-up notice about a custom model, not a reply
                self._text.append(c["text"])
                self._emit({"type": "text_delta", "text": c["text"]})
        elif kind == "agent_thought_chunk":
            c = u.get("content") or {}
            if c.get("type") == "text" and c.get("text"):
                self._emit({"type": "thought", "text": c["text"]})
        elif kind == "tool_call":
            self._flush_text(has_tools=True)
            tid = u.get("toolCallId") or uuid.uuid4().hex
            self._tools[tid] = u
            name, summary = _label(u)
            self._emit({"type": "tool_start", "id": tid, "name": name, "summary": summary,
                        "mcp": False, "diff": _diff_of(u.get("content"))})
            if u.get("status") in ("completed", "failed"):
                self._tool_end(tid, u)
        elif kind == "tool_call_update":
            tid = u.get("toolCallId")
            if tid in self._tools:
                self._tools[tid].update({k: v for k, v in u.items() if v is not None})
            if u.get("status") in ("completed", "failed"):
                self._tool_end(tid, self._tools.get(tid, u))
        elif kind == "plan":
            self._emit({"type": "plan", "entries": [{"content": e.get("content", ""), "status": e.get("status", "pending")}
                                                    for e in u.get("entries") or []]})
        elif kind == "usage_update":
            self.context = {"used": u.get("used", 0), "size": u.get("size", 0)}
            self._emit({"type": "context", **self.context})
        elif kind == "session_info_update" and u.get("title"):
            self.title = self.title or str(u["title"])[:80]

    def _tool_end(self, tid, u):
        out = _text_of(u.get("content"))
        self._emit({"type": "tool_end", "id": tid, "name": _label(u)[0], "ok": u.get("status") != "failed",
                    "denied": False, "output": out[:6000]})

    def _flush_text(self, has_tools=False):
        if self._text:
            txt = "".join(self._text)
            self._text = []
            self._emit({"type": "assistant", "text": txt, "streamed": True, "has_tools": has_tools})

    # ---------- session ------------------------------------------------------------------------------------------
    def _ensure_session(self):
        self._start()
        if self.acp_session:
            return
        mcp = mcp_servers_for_acp([str(self.root)]) if self.manifest.get("share_mcp", True) else []
        if self.manifest.get("office", True):
            mcp.append(office_server([str(self.root)]))
        if self.loaded_from and self.capabilities.get("loadSession"):
            try:
                self._replaying = True
                rl = self._request("session/load", {"sessionId": self.loaded_from, "cwd": str(self.root),
                                                    "mcpServers": mcp}, timeout=180)
                self.acp_session = self.loaded_from
                self._ask_mode((rl or {}).get("modes") or {})
                self._reloaded = True
                return
            except ACPError:
                pass
            finally:
                self._replaying = False
        try:
            r = self._request("session/new", {"cwd": str(self.root), "mcpServers": mcp}, timeout=180)
        except ACPError as e:
            if "auth" in str(e).lower():
                raise ACPError(f"{self.manifest.get('name')} needs you to sign in first. "
                               + (self.manifest.get("setup") or "Run its setup command in a terminal."))
            raise
        self.acp_session = r["sessionId"]
        self._ask_mode(r.get("modes") or {})
        ms = r.get("models") or {}
        if ms.get("availableModels"):
            self.available_models = [{"id": m.get("modelId"), "name": m.get("name") or m.get("modelId")}
                                     for m in ms["availableModels"] if m.get("modelId")]
        self._apply_model()

    def _ask_mode(self, modes: dict):
        """Run the harness in its own ask-before-acting mode, so every edit and command comes to Meshpool, which
        applies the user's permission setting (Supervised, Auto-accept edits, Full access) the same way for every
        harness. Without this Codex, for one, edits files on its own."""
        ids = [m.get("id") for m in modes.get("availableModes") or [] if m.get("id")]
        self.modes_seen, self.mode_now = ids, modes.get("currentModeId")
        want = (self.manifest.get("modes") or {}).get(self.mode)        # e.g. Codex: a mode per Meshpool setting
        pick = want if want in ids else next((i for w in ("default", "ask", "untrusted") for i in ids
                                              if i.lower() == w), None)
        if pick and pick != modes.get("currentModeId"):
            try:
                self._request("session/set_mode", {"sessionId": self.acp_session, "modeId": pick}, timeout=30)
                self.mode_now = pick
            except ACPError:
                pass

    def apply_mode(self):
        """The user changed Supervised / Auto-accept edits / Full access: tell a harness that has its own modes."""
        if self.acp_session and self._p and self._p.poll() is None and getattr(self, "modes_seen", None):
            self._ask_mode({"availableModes": [{"id": i} for i in self.modes_seen], "currentModeId": self.mode_now})

    def _apply_model(self):
        if not (self.model and self.acp_session):
            return
        try:
            self._request("session/set_model", {"sessionId": self.acp_session, "modelId": self.model}, timeout=60)
        except ACPError as e:
            self._emit({"type": "status", "text": f"{self.manifest.get('name')} kept its own model ({e})."})

    def set_model(self, model: str):
        self.model = model or None
        if self.acp_session and self._p and self._p.poll() is None:
            self._apply_model()

    def current_model(self) -> str:
        if self.model:
            return self.model
        if self.manifest.get("models") == "meshpool":
            return os.environ.get("MH_MODEL") or "deepseek-v4.1-flash"
        return self.agent_info.get("title") or self.agent_info.get("name") or self.manifest.get("name", "")

    def _descendants(self) -> set:
        try:
            import psutil
            return {c.pid for c in psutil.Process(self._p.pid).children(recursive=True)} if self._p else set()
        except Exception:  # noqa: BLE001
            return set()

    def _kill_new_children(self, before: set):
        """Stop commands the harness started during this turn (a cancel only stops the agent between steps;
        a long command would otherwise run to the end). MCP servers started earlier are left alone."""
        try:
            import psutil
            procs = [c for c in psutil.Process(self._p.pid).children(recursive=True) if c.pid not in before]
        except Exception:  # noqa: BLE001
            return
        for c in procs:
            try:
                c.kill()
            except Exception:  # noqa: BLE001
                pass

    def send(self, text: str, images: list | None = None) -> str:
        self._stop = False
        self._before = self._descendants()
        if not self.title:
            self.title = text.strip().splitlines()[0][:80] if text.strip() else "Session"  # first line only
        shown = text.split("\n\n\u200b")[0]          # attached skill text and image OCR go to the agent only
        self._emit({"type": "user", "text": shown, "images": [im.get("name", "image") for im in images or []],
                    "plan": self.plan})
        body = text
        if self.plan:
            body = ("[Plan mode] Investigate as needed, then reply with a clear, numbered plan for the request below. "
                    "Do not change any files or run commands that change anything; the user will review the plan "
                    "first.\n\n" + text)
        self._text = []
        final = ""
        self.usage["calls"] += 1
        try:
            self._emit({"type": "model_start"})
            self._ensure_session()
            prompt = [{"type": "text", "text": body}]
            if images:
                if (self.capabilities.get("promptCapabilities") or {}).get("image"):
                    prompt += [{"type": "image", "mimeType": im.get("mime") or "image/png", "data": im["data"]}
                               for im in images[:6]]
                else:
                    self._emit({"type": "status", "text": f"{self.manifest.get('name')} does not take images; "
                                                          "they were left out."})
            r = self._request("session/prompt", {"sessionId": self.acp_session, "prompt": prompt})
            if r.get("stopReason") == "refusal" and getattr(self, "_reloaded", False) and not self._text:
                self.acp_session, self.loaded_from = None, None     # the reloaded session was gone: start fresh
                self._ensure_session()
                r = self._request("session/prompt", {"sessionId": self.acp_session, "prompt": prompt})
            self._reloaded = False
            final = "".join(self._text)
            self._flush_text()
            stop = r.get("stopReason", "end_turn")
            if _auth_failure(final):
                # Some harnesses (Hermes) reply with the raw HTTP error as plain text. Say what to do instead.
                self._emit({"type": "error", "text": "The endpoint rejected the API key. Check it in Settings, "
                                                     "Endpoint and key, then send your message again."})
            if stop == "cancelled" or self._stop:
                self._emit({"type": "interrupted", "text": "Stopped. Send a message to continue."})
            elif stop in ("max_tokens", "max_turn_requests"):
                self._emit({"type": "status", "text": f"The harness stopped early ({stop.replace('_', ' ')})."})
            elif stop == "refusal":
                self._emit({"type": "error", "text": "The harness refused this request."})
        except ACPStopped:
            self._flush_text()
            self._emit({"type": "interrupted", "text": "Stopped. Send a message to continue."})
        except ACPError as e:
            self._flush_text()
            if self._stop:                               # an error while stopping is just the stop
                self._emit({"type": "interrupted", "text": "Stopped. Send a message to continue."})
            else:
                self._emit({"type": "error", "text": str(e)})
        finally:
            self._emit({"type": "turn_end", "turn": {"in": 0, "cached": 0, "out": 0, "calls": 0},
                        "session": dict(self.usage), "context": self.context})
            self.save()
        return final

    def stop(self):
        self._stop = True
        self._stop_t = time.time()
        if self.acp_session and self._p and self._p.poll() is None:
            try:
                self._write({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": self.acp_session}})
            except ACPError:
                pass
            before = getattr(self, "_before", None)
            if before is not None:
                threading.Timer(1.0, self._kill_new_children, args=(before,)).start()

    def reset(self):
        self.acp_session = None
        self.loaded_from = None
        self.events = []
        self.title = ""

    def close(self):
        p = self._p
        if not p:
            return
        _kill_tree(p)                                 # commands it started must not outlive the thread
        try:
            p.stdin.close()
        except Exception:
            pass
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

    def save(self):
        if getattr(self, "deleted", False):           # the user deleted this thread; never bring it back
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "session.json").write_text(json.dumps({
                "id": self.id, "workdir": str(self.root), "title": self.title, "created": self.created,
                "updated": time.time(), "harness": self.manifest.get("id"), "kind": self.manifest.get("mode", "cowork"),
                "acp_session": self.acp_session or self.loaded_from, "usage": self.usage,
                "events": self.events[-2000:]}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    @classmethod
    def resume(cls, data: dict, manifest: dict, **kw) -> "ACPAgent":
        a = cls(manifest, data["workdir"], session_id=data["id"], **kw)
        a.title = data.get("title", "")
        a.created = data.get("created", a.created)
        a.events = data.get("events") or []
        a.usage = data.get("usage") or a.usage
        a.loaded_from = data.get("acp_session")
        return a
