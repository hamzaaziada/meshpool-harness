"""Pi as a Meshpool harness, over Pi's RPC mode (pi --mode rpc: JSON lines on stdin and stdout).

Meshpool ships Pi and a Node runtime inside the app. Before Pi starts, Meshpool writes Pi's own config folder
(<home>/pi): models.json points Pi at the Meshpool endpoint with every model the key can use, mcp.json carries the
same MCP servers and connectors the other harnesses get, settings.json sets the shell, and skills/ holds the
user's Meshpool skills. pi_gate.js turns every tool call that could change something into an RPC confirm dialog,
which Meshpool answers from the thread's access mode or by asking the user.

Pi events become Meshpool UI events:
  message_update text_delta / thinking_delta -> text_delta / thought
  tool_execution_start / _end                -> tool_start / tool_end
  extension_ui_request confirm               -> approval
  agent_end                                  -> turn_end (usage from get_session_stats)
"""
from __future__ import annotations

import difflib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from meshpool_harness import config as C

READ_ONLY = {"read", "grep", "find", "ls", "tool_search", "codemode", "list_mcp_resources",
             "list_mcp_resource_templates", "read_mcp_resource", "mcp__meshpool_office__read_document",
             "mcp__meshpool_office__office_job_status", "mcp__meshpool_tools__current_time", "mcp__meshpool_tools__calculate",
             "mcp__meshpool_web__web_search", "mcp__meshpool_web__web_fetch", "mcp__meshpool_web__web_research",
             "mcp__meshpool_assistant__memory_view", "mcp__meshpool_assistant__memory_save",
             "mcp__meshpool_assistant__memory_forget", "mcp__meshpool_assistant__schedule_task",
             "mcp__meshpool_assistant__search_chats", "mcp__meshpool_assistant__list_scheduled_tasks",
             "mcp__meshpool_assistant__run_subtasks"}
LABEL = {"read": "Read", "write": "Wrote", "edit": "Edited", "bash": "Ran", "powershell": "Ran", "grep": "Searched",
         "find": "Listed", "ls": "Listed", "tool_search": "Found tools", "codemode": "Ran a script"}



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

class PiError(Exception):
    pass


def node_exe() -> str | None:
    """The Node runtime bundled with the app, else one on PATH (development)."""
    base = C.APP.parent / "node"
    for p in (base / "node.exe", base / "bin" / "node"):
        if p.is_file():
            return str(p)
    return shutil.which("node")


def pi_cli() -> Path | None:
    for base in (C.APP / "vendor" / "pi", C.APP.parent / "pi"):
        p = base / "node_modules" / "@earendil-works" / "pi-coding-agent" / "dist" / "bundle" / "cli.js"
        if p.is_file():
            return p
    return None


def available() -> bool:
    return bool(node_exe() and pi_cli())


def pi_home() -> Path:
    return C.home() / "pi"


def _models() -> list[dict]:
    try:
        from meshpool_harness.ui import workbench as W
        rows = W.models().get("models") or []
    except Exception:  # noqa: BLE001
        rows = []
    cur = os.environ.get("MH_MODEL") or "deepseek-v4.1-flash"
    if not any(r.get("id") == cur for r in rows):
        rows = [{"id": cur}] + rows
    out = []
    for r in rows:
        caps = r.get("caps") or []
        out.append({"id": r["id"], "name": r["id"], "reasoning": "reasoning" in caps,
                    "input": ["text", "image"] if "vision" in caps else ["text"],
                    "contextWindow": int(r.get("context") or 128000), "maxTokens": 16384,
                    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}})
    return out


def _mcp_for_pi(root: Path) -> dict:
    from meshpool_harness.harnesses.acp import mcp_servers_for_acp, office_server
    servers = {}
    rows = mcp_servers_for_acp([str(root)]) + [office_server([str(root)])]
    for s in rows:
        name = s["name"]
        if s.get("url"):
            spec = {"url": s["url"], "headers": {h["name"]: h["value"] for h in s.get("headers") or []},
                    "exposure": "deferred"}
        else:
            env = {e["name"]: e["value"] for e in s.get("env") or []}
            spec = {"command": s["command"], "args": s.get("args") or [], "env": env,
                    "timeout": 1800 if name == "meshpool-assistant" else 300,
                    "exposure": "direct"}
        servers[name] = spec
    return {"mcpServers": servers, "autoEnableCodemode": False}


def prepare(root: Path) -> Path:
    """Write Pi's config folder for this launch. Returns the folder."""
    h = pi_home()
    (h / "sessions").mkdir(parents=True, exist_ok=True)
    base = (os.environ.get("MESHPOOL_BASE_URL") or "https://llm.meshpool.ai/v1").rstrip("/")
    models = {"providers": {"meshpool": {
        "baseUrl": base, "api": "openai-completions", "apiKey": "$MESHPOOL_API_KEY",
        "compat": {"supportsDeveloperRole": False}, "models": _models()}}}
    (h / "models.json").write_text(json.dumps(models, indent=1), encoding="utf-8")
    try:
        mcp = _mcp_for_pi(root)
    except Exception:  # noqa: BLE001
        mcp = {"mcpServers": {}}
    p = h / "mcp.json"
    p.write_text(json.dumps(mcp, indent=1), encoding="utf-8")
    try:
        os.chmod(p, 0o600)                         # carries connector tokens
    except OSError:
        pass
    sp = h / "settings.json"
    try:
        settings = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    except ValueError:
        settings = {}
    settings.update({"defaultProvider": "meshpool", "defaultModel": os.environ.get("MH_MODEL") or "deepseek-v4.1-flash",
                     "defaultTools": ["read", "bash", "edit", "write", "grep", "find", "ls"]})
    if os.name == "nt" and os.environ.get("MH_BASH"):
        settings["shellPath"] = os.environ["MH_BASH"]
    sp.write_text(json.dumps(settings, indent=1), encoding="utf-8")
    try:
        from meshpool_harness import skills as SK
        SK.sync_to(h / "skills" / "meshpool")
    except Exception:  # noqa: BLE001
        pass
    return h


def _edit_diff(path: str, inp: dict, root: Path) -> str:
    edits = inp.get("edits") or ([{"oldText": inp.get("oldText", ""), "newText": inp.get("newText", "")}]
                                  if "oldText" in inp else [])
    try:
        p = Path(path) if Path(path).is_absolute() else root / path
        old = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        old = ""
    new = old
    for e in edits:
        new = new.replace(e.get("oldText", ""), e.get("newText", ""), 1) if e.get("oldText") else new
    if new == old and edits:
        return "\n".join(f"- {e.get('oldText', '')}\n+ {e.get('newText', '')}" for e in edits)[:4000]
    return "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{path}", f"b/{path}", n=2,
                                          lineterm=""))[:6000]


def _write_diff(path: str, content: str, root: Path) -> str:
    try:
        p = Path(path) if Path(path).is_absolute() else root / path
        old = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
    except OSError:
        old = ""
    return "\n".join(difflib.unified_diff(old.splitlines(), (content or "").splitlines(), f"a/{path}", f"b/{path}",
                                          n=2, lineterm=""))[:6000]


def _summary(name: str, args: dict) -> str:
    for k in ("command", "path", "pattern", "query", "url"):
        if isinstance(args.get(k), str) and args[k]:
            return args[k].splitlines()[0][:160]
    return ""


def _result_text(res) -> str:
    if isinstance(res, dict):
        parts = [c.get("text", "") for c in res.get("content") or [] if isinstance(c, dict) and c.get("type") == "text"]
        if parts:
            return "\n".join(parts)
        return json.dumps(res.get("details") or {}, ensure_ascii=False)[:2000] if res.get("details") else ""
    return str(res or "")


class PiAgent:
    """One Pi process serving one Meshpool thread. Same surface as ACPAgent."""

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
        self.context = None
        self.events: list[dict] = []
        self.messages = [None]
        self.mcp = None
        self.agent_info = {"name": "Pi"}
        self.model: str | None = None
        self.available_models = None
        self.plan = False
        self.resume = False
        self._p = None
        self._rid = 0
        self._replies: dict[str, dict] = {}
        self._cv = threading.Condition()
        self._alive = False
        self._stop = False
        self._done = threading.Event()
        self._text: list[str] = []
        self._always: set[str] = set()
        self._denied: set[str] = set()
        self._lock = threading.Lock()
        self._turn = {"in": 0, "cached": 0, "out": 0, "calls": 0}

    # ---------- process ----------------------------------------------------------------------------------------
    def _emit(self, ev: dict):
        if ev.get("type") not in ("text_delta", "thought", "model_start", "model_end", "context"):
            self.events.append({k: v for k, v in ev.items() if k not in ("i", "t")})
        self.emit_cb(ev)

    def _start(self):
        if self._p and self._p.poll() is None:
            return
        node, cli = node_exe(), pi_cli()
        if not (node and cli):
            raise PiError("Pi is not included in this copy of Meshpool. Reinstall Meshpool to get it back.")
        home = prepare(self.root)
        sess = self.dir / "pi"
        sess.mkdir(parents=True, exist_ok=True)
        args = [node, str(cli), "--mode", "rpc", "--session-dir", str(sess), "--no-approve",
                "-e", str(Path(__file__).with_name("pi_gate.js"))]
        if self.resume and any(sess.glob("*.jsonl")):
            args.append("--continue")
        env = dict(os.environ)
        for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONNOUSERSITE"):
            env.pop(k, None)
        env.update({"PI_CODING_AGENT_DIR": str(home), "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0",
                    "MESHPOOL_API_KEY": os.environ.get("MESHPOOL_API_KEY", "")})
        nb = str(Path(node).parent)
        env["PATH"] = nb + os.pathsep + env.get("PATH", "")      # npx and node for MCP servers the user adds
        log_dir = C.runs_dir() / "harness-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._err = open(log_dir / "pi.log", "a", encoding="utf-8", errors="replace")
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {"start_new_session": True}
        self._p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._err,
                                   cwd=str(self.root), env=env, **kw)
        self._alive = True
        threading.Thread(target=self._reader, daemon=True).start()
        st = self._request({"type": "get_state"}, timeout=120)
        try:
            ms = self._request({"type": "get_available_models"}, timeout=30).get("models") or []
            self.available_models = [{"id": m.get("id"), "name": m.get("name") or m.get("id")}
                                     for m in ms if m.get("provider") == "meshpool"]
        except PiError:
            pass
        self._apply_model(initial=(st.get("model") or {}).get("id"))

    def _reader(self):
        buf = b""
        while True:
            chunk = self._p.stdout.read1(65536) if hasattr(self._p.stdout, "read1") else self._p.stdout.read(65536)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:                    # LF-only framing (never split on U+2028)
                line, buf = buf.split(b"\n", 1)
                line = line.rstrip(b"\r")
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue
                try:
                    self._on(msg)
                except Exception as e:  # noqa: BLE001  a bad event must not stop the reader
                    print(f"Pi event error: {e}", file=sys.stderr)
        with self._cv:
            self._alive = False
            self._cv.notify_all()
        self._done.set()

    def _write(self, obj: dict):
        try:
            with self._lock:
                self._p.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
                self._p.stdin.flush()
        except (BrokenPipeError, OSError, ValueError, AttributeError):
            raise PiError("Pi stopped. Its log is in runs/harness-logs/pi.log.")

    def _request(self, cmd: dict, timeout: float = 60) -> dict:
        with self._cv:
            self._rid += 1
            rid = f"m{self._rid}"
        self._write(dict(cmd, id=rid))
        end = time.time() + timeout
        with self._cv:
            while rid not in self._replies:
                if not self._alive:
                    raise PiError("Pi exited. See runs/harness-logs/pi.log.")
                if time.time() > end:
                    raise PiError(f"Pi did not answer {cmd.get('type')} within {int(timeout)} s.")
                self._cv.wait(0.3)
            r = self._replies.pop(rid)
        if not r.get("success"):
            raise PiError(str(r.get("error") or "Pi refused the request."))
        return r.get("data") or {}

    # ---------- events -----------------------------------------------------------------------------------------
    def _on(self, m: dict):
        t = m.get("type")
        if t == "response":
            if m.get("id"):
                with self._cv:
                    self._replies[m["id"]] = m
                    self._cv.notify_all()
            return
        if t == "message_update":
            e = m.get("assistantMessageEvent") or {}
            if e.get("type") == "text_delta" and e.get("delta"):
                self._text.append(e["delta"])
                self._emit({"type": "text_delta", "text": e["delta"]})
            elif e.get("type") == "thinking_delta" and e.get("delta"):
                self._emit({"type": "thought", "text": e["delta"]})
        elif t == "message_end":
            msg = m.get("message") or {}
            if msg.get("role") == "assistant":
                u = msg.get("usage") or {}
                self._turn["calls"] += 1
                self._turn["in"] += int(u.get("input") or 0) + int(u.get("cacheRead") or 0)
                self._turn["cached"] += int(u.get("cacheRead") or 0)
                self._turn["out"] += int(u.get("output") or 0)
                has_tools = any(isinstance(c, dict) and c.get("type") == "toolCall" for c in msg.get("content") or [])
                self._flush_text(has_tools)
                if msg.get("stopReason") == "error" and msg.get("errorMessage"):
                    self._emit({"type": "error", "text": str(msg["errorMessage"])[:600]})
        elif t == "tool_execution_start":
            self._flush_text(True)
            name, args = m.get("toolName", ""), m.get("args") or {}
            mcp = name.startswith("mcp__")
            label = name.split("__", 2)[-1] if mcp else LABEL.get(name, name)
            diff = ""
            if name == "edit":
                diff = _edit_diff(args.get("path", ""), args, self.root)
            elif name == "write":
                diff = _write_diff(args.get("path", ""), args.get("content", ""), self.root)
            self._emit({"type": "tool_start", "id": m.get("toolCallId"), "name": label, "summary": _summary(name, args),
                        "mcp": mcp, "diff": diff})
        elif t == "tool_execution_end":
            tid = m.get("toolCallId")
            denied = tid in self._denied
            self._emit({"type": "tool_end", "id": tid, "name": m.get("toolName", ""), "ok": not m.get("isError"),
                        "denied": denied, "output": _result_text(m.get("result"))[:6000]})
        elif t == "extension_ui_request":
            threading.Thread(target=self._ui, args=(m,), daemon=True).start()
        elif t == "agent_end":
            if not m.get("willRetry"):
                self._done.set()
        elif t == "auto_retry_start":
            self._emit({"type": "status", "text": f"The model call failed ({str(m.get('errorMessage', ''))[:160]}). "
                                                  f"Retrying ({m.get('attempt')}/{m.get('maxAttempts')})."})
        elif t == "compaction_start":
            self._emit({"type": "status", "text": "Summarizing earlier messages to make room..."})
        elif t == "extension_error":
            self._emit({"type": "status", "text": f"Pi extension error: {str(m.get('error', ''))[:300]}"})

    def _ui(self, m: dict):
        rid, method = m.get("id"), m.get("method")
        try:
            if method == "confirm" and str(m.get("title", "")).startswith("meshpool:"):
                ok = self._gate(m)
                self._write({"type": "extension_ui_response", "id": rid, "confirmed": ok})
            elif method == "confirm":
                choice, _ = self.approve("mcp", m.get("title") or "Pi asks", m.get("message") or "") \
                    if self.approve else ("no", "")
                self._write({"type": "extension_ui_response", "id": rid, "confirmed": choice in ("yes", "always")})
            elif method == "select" and self.approve:
                opts = [{"id": str(o), "label": str(o), "kind": ""} for o in m.get("options") or []]
                choice, _ = self.approve("mcp", m.get("title") or "Pi asks you to choose", "", opts)
                if choice in (None, "", "no"):
                    self._write({"type": "extension_ui_response", "id": rid, "cancelled": True})
                else:
                    self._write({"type": "extension_ui_response", "id": rid, "value": choice})
            elif method == "notify":
                self._emit({"type": "status", "text": str(m.get("message", ""))[:400]})
            elif method in ("select", "input", "editor"):
                self._write({"type": "extension_ui_response", "id": rid, "cancelled": True})
        except PiError:
            pass

    def _gate(self, m: dict) -> bool:
        try:
            d = json.loads(m.get("message") or "{}")
        except ValueError:
            d = {}
        name, inp, tid = d.get("name") or m["title"].split(":", 1)[1], d.get("input") or {}, d.get("id")
        kind = "edit" if name in ("write", "edit") else "bash" if name in ("bash", "powershell") else "mcp"
        if name in READ_ONLY:
            return True
        if self.plan and kind == "edit":
            self._denied.add(tid)
            return False
        if self.mode == "auto" or kind in self._always or (self.mode == "edits" and kind == "edit"):
            return True
        if not self.approve or self._stop:
            self._denied.add(tid)
            return False
        if kind == "edit":
            path = inp.get("path", "")
            title = f"{'Edit' if name == 'edit' else 'Write'} {path}?"
            detail = _edit_diff(path, inp, self.root) if name == "edit" else _write_diff(path, inp.get("content", ""), self.root)
        elif kind == "bash":
            title, detail = "Run this command?", inp.get("command", "")
        else:
            title = f"Use {name.split('__', 2)[-1].replace('_', ' ')}" + \
                (f" ({name.split('__')[1]})" if name.startswith("mcp__") else "") + "?"
            detail = json.dumps(inp, indent=1, ensure_ascii=False)[:3000]
        choice, _note = self.approve(kind, title, detail)
        if choice == "always":
            self._always.add(kind)
        if choice in ("yes", "always"):
            return True
        self._denied.add(tid)
        return False

    def _flush_text(self, has_tools=False):
        if self._text:
            txt = "".join(self._text)
            self._text = []
            self._emit({"type": "assistant", "text": txt, "streamed": True, "has_tools": has_tools})

    # ---------- model ------------------------------------------------------------------------------------------
    def _apply_model(self, initial: str | None = None):
        want = self.model or os.environ.get("MH_MODEL") or "deepseek-v4.1-flash"
        if want == initial:
            return
        try:
            self._request({"type": "set_model", "provider": "meshpool", "modelId": want}, timeout=30)
        except PiError as e:
            self._emit({"type": "status", "text": f"Pi could not switch to {want}: {e}"})

    def set_model(self, model: str):
        self.model = model or None
        if self._p and self._p.poll() is None and self._alive:
            self._apply_model()

    def current_model(self) -> str:
        return self.model or os.environ.get("MH_MODEL") or "deepseek-v4.1-flash"

    # ---------- turns ------------------------------------------------------------------------------------------
    def send(self, text: str, images: list | None = None) -> str:
        self._stop = False
        if not self.title:
            self.title = text.strip().splitlines()[0][:80] if text.strip() else "Session"  # first line only
        shown = text.split("\n\n\u200b")[0]          # attached skill text and image OCR go to the agent only
        self._emit({"type": "user", "text": shown, "images": [im.get("name", "image") for im in images or []],
                    "plan": self.plan})
        body = text
        if self.plan:
            body = ("[Plan mode] Investigate as needed, then reply with a clear, numbered plan for the request below. "
                    "Do not change any files; the user will review the plan first.\n\n" + text)
        self._text, self._denied = [], set()
        self._turn = {"in": 0, "cached": 0, "out": 0, "calls": 0}
        final = ""
        try:
            self._emit({"type": "model_start"})
            self._start()
            self._done.clear()
            cmd = {"type": "prompt", "message": body}
            if images:
                cmd["images"] = [{"type": "image", "data": im["data"], "mimeType": im.get("mime") or "image/png"}
                                 for im in images[:6]]
            self._request(cmd, timeout=120)
            while not self._done.wait(0.5):
                if not self._alive:
                    raise PiError("Pi exited during the turn. See runs/harness-logs/pi.log.")
            final = "".join(self._text)
            self._flush_text()
            if self._stop:
                self._emit({"type": "interrupted", "text": "Stopped. Send a message to continue."})
            try:
                st = self._request({"type": "get_session_stats"}, timeout=20)
                cu = st.get("contextUsage") or {}
                if cu.get("contextWindow"):
                    self.context = {"used": int(cu.get("tokens") or 0), "size": int(cu["contextWindow"])}
                    self._emit({"type": "context", **self.context})
            except PiError:
                pass
        except PiError as e:
            self._flush_text()
            self._emit({"type": "error", "text": str(e)})
        finally:
            for k in self.usage:
                self.usage[k] += self._turn[k]
            self._emit({"type": "turn_end", "turn": dict(self._turn), "session": dict(self.usage),
                        "context": self.context})
            self.save()
        return final

    def steer(self, text: str, images: list | None = None):
        """Add to the request while Pi works: Pi reads it after the current tool calls (RPC steer)."""
        cmd = {"type": "steer", "message": "[Added by the user while you were working] " + text}
        if images:
            cmd["images"] = [{"type": "image", "data": im["data"], "mimeType": im.get("mime") or "image/png"}
                             for im in images[:6]]
        self._request(cmd, timeout=30)
        self._emit({"type": "user", "text": text.split("\n\n\u200b")[0],
                    "images": [im.get("name", "image") for im in images or []], "plan": self.plan, "steer": True})

    def stop(self):
        self._stop = True
        if self._p and self._p.poll() is None:
            try:
                self._write({"type": "abort"})
            except PiError:
                pass

    def reset(self):
        self.events = []
        self.title = ""

    def close(self):
        p = self._p
        if not p:
            return
        _kill_tree(p)                                 # commands it started must not outlive the thread
        try:
            p.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass

    def save(self):
        if getattr(self, "deleted", False):           # the user deleted this thread; never bring it back
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "session.json").write_text(json.dumps({
                "id": self.id, "workdir": str(self.root), "title": self.title, "created": self.created,
                "updated": time.time(), "harness": self.manifest.get("id"), "kind": self.manifest.get("mode", "code"),
                "usage": self.usage, "model": self.model, "events": self.events[-2000:]}, ensure_ascii=False),
                encoding="utf-8")
        except OSError:
            pass

    @classmethod
    def resume(cls, data: dict, manifest: dict, **kw) -> "PiAgent":
        a = cls(manifest, data["workdir"], session_id=data["id"], **kw)
        a.title = data.get("title", "")
        a.created = data.get("created", a.created)
        a.events = data.get("events") or []
        a.usage = data.get("usage") or a.usage
        a.model = data.get("model")
        a.resume = True
        return a
