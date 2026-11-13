"""MCP client (Model Context Protocol), standard library only.

Transports:
- stdio: {"command": "npx", "args": [...], "env": {...}}   (the server is a child process; JSON-RPC over its pipes)
- streamable HTTP: {"url": "https://...", "headers": {"Authorization": "Bearer ${TOKEN}"}}
- HTTP with SSE (the 2024-11-05 transport): {"url": "https://.../sse", "type": "sse"}; also used on its own when a
  server refuses the streamable HTTP POST, as the MCP spec's backwards-compatibility section describes.

Config file: mcp.json next to meshpool.config, in the same shape Claude Desktop and Claude Code use:
    {"mcpServers": {"name": {...}, ...}}
Extra keys: "disabled": true skips a server; "timeout": seconds per tool call (default 120).
${VAR} in any string is replaced from the environment (meshpool.config values are in the environment).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from meshpool_harness import __version__ as _VERSION
from meshpool_harness import config as C

PROTOCOL = "2025-06-18"
CLIENT = {"name": "meshpool", "version": _VERSION}
UA = {"User-Agent": f"Meshpool/{_VERSION} (MCP client)"}   # Python's default agent is blocked by many CDNs


def config_file() -> Path:
    p = os.environ.get("MESHPOOL_MCP")
    return Path(p) if p else C.home() / "mcp.json"


def _expand(v):
    if isinstance(v, str):
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: os.environ.get(m.group(1), ""), v)
    if isinstance(v, list):
        return [_expand(x) for x in v]
    if isinstance(v, dict):
        return {k: _expand(x) for k, x in v.items()}
    return v


def load_config(path: Path | None = None) -> dict:
    path = path or config_file()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("mcpServers") or data.get("servers") or {}


def active_config(path: Path | None = None) -> dict:
    """mcp.json plus the connectors the user signed in to (GitHub, Jira, Linear, ...), with fresh credentials."""
    cfg = load_config(path)
    try:
        from meshpool_harness import mcp_apps as CN
        for name, spec in CN.servers().items():
            cur = cfg.get(name)
            if not isinstance(cur, dict) or cur.get("disabled"):
                cfg[name] = spec
    except Exception:  # noqa: BLE001  a connector problem must never block the other servers
        pass
    return cfg


# ---------- health: remember which servers failed, so one broken server does not slow every new thread ----------

HEALTH_TTL = 30 * 60
_HLOCK = threading.Lock()


def _health_file() -> Path:
    return C.home() / "mcp-health.json"


def _sig(spec) -> str:
    import hashlib
    return hashlib.sha1(json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _read_health() -> dict:
    try:
        d = json.loads(_health_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def record_health(rows: dict) -> None:
    """rows: {name: (spec, ok, error)}"""
    if not rows:
        return
    with _HLOCK:
        d = _read_health()
        now = time.time()
        for name, (spec, ok, err) in rows.items():
            d[name] = {"sig": _sig(spec), "ok": bool(ok), "error": str(err or "")[:300], "t": now}
        try:
            _health_file().write_text(json.dumps(d, indent=1), encoding="utf-8")
        except OSError:
            pass


def recently_failed(name: str, spec) -> str | None:
    """The error, if this exact server config failed in the last 30 minutes; else None."""
    h = _read_health().get(name)
    if h and not h.get("ok") and h.get("sig") == _sig(spec) and time.time() - float(h.get("t") or 0) < HEALTH_TTL:
        return h.get("error") or "failed"
    return None


def tool_name(server: str, tool: str) -> str:
    n = re.sub(r"[^A-Za-z0-9_-]", "_", f"mcp__{server}__{tool}")
    return n[:64]


class MCPError(Exception):
    pass


# ---------- transports --------------------------------------------------------------------------------------------

class _Stdio:
    def __init__(self, name: str, spec: dict, log_dir: Path, roots: list[str]):
        cmd = spec["command"]
        if cmd in ("python", "python3", "py"):
            cmd = sys.executable                          # the same Python Meshpool runs on
        exe = shutil.which(cmd) or cmd                    # Windows: npx -> npx.cmd
        args = [exe, *[str(a) for a in spec.get("args", [])]]
        env = dict(os.environ)
        env.update({k: str(v) for k, v in (spec.get("env") or {}).items()})
        env.setdefault("PYTHONUTF8", "1")
        if exe == sys.executable:
            env["PYTHONPATH"] = str(C.APP) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log = log_dir / f"{name}.log"
        self.err = open(self.log, "a", encoding="utf-8", errors="replace")
        self.log_start = self.err.tell()
        kw = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
        try:
            self.p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.err,
                                      env=env, cwd=spec.get("cwd") or None, **kw)
        except FileNotFoundError:
            raise MCPError(f"command not found: {spec['command']}"
                           + (" (install Node.js from nodejs.org)" if spec["command"] in ("npx", "node") else "")
                           + (" (install uv from docs.astral.sh/uv)" if spec["command"] in ("uvx", "uv") else ""))
        self.roots = roots
        self.pending: dict[int, dict] = {}
        self.cv = threading.Condition()
        self.alive = True
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for raw in self.p.stdout:
            try:
                msg = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue                                   # servers sometimes print banners
            if "method" in msg and "id" in msg:            # server -> client request
                self._answer(msg)
                continue
            if "id" in msg:
                with self.cv:
                    self.pending[msg["id"]] = msg
                    self.cv.notify_all()
        with self.cv:
            self.alive = False
            self.cv.notify_all()

    def _answer(self, msg):
        m = msg.get("method")
        if m == "roots/list":
            res = {"result": {"roots": [{"uri": Path(r).resolve().as_uri(), "name": Path(r).name} for r in self.roots]}}
        elif m == "ping":
            res = {"result": {}}
        else:
            res = {"error": {"code": -32601, "message": f"method not supported: {m}"}}
        self._write({"jsonrpc": "2.0", "id": msg["id"], **res})

    def _write(self, obj):
        try:
            self.p.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
            self.p.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            raise MCPError(self._exit_reason())

    def _exit_reason(self) -> str:
        """Why the server stopped, from the last meaningful line it printed (its whole log is in runs/mcp-logs)."""
        try:
            self.err.flush()
            with open(self.log, encoding="utf-8", errors="replace") as f:
                f.seek(self.log_start)
                lines = [ln.strip() for ln in f.read()[-20000:].splitlines()]
        except OSError:
            lines = []
        skip = re.compile(r"^(npm (warn|notice)|at |node:|Node\.js v|File \"|\^+$|~+|\{|\}|status:|signal:|output:|"
                          r"pid:|stdout:|stderr:|throw err|warning: The `UV_|Traceback|Installed|Resolved|Prepared|"
                          r"Downloading|Downloaded|Updated|Updating|Building|Built)")
        why = []
        for ln in lines:
            if ln.startswith("{") and ln.endswith("}"):           # a JSON log line: keep its message
                try:
                    j = json.loads(ln)
                    ln = str(j.get("message") or j.get("msg") or j.get("error") or "")
                except ValueError:
                    pass
            if ln and not skip.match(ln) and not ln.startswith("Error: Command failed") and \
                    not re.match(r"^[A-Za-z_]+: ['\"\d-]", ln):     # object dumps such as "port: 1"
                why.append(ln)
        bad = [ln for ln in why if re.search(r"error|fatal|fail|invalid|denied|missing|refused|not found|unauthori",
                                             ln, re.I)]
        msg = (bad or why or [""])[-1][:300]
        return "the server process exited" + (f": {msg}" if msg else "") + f" (log: {self.log.name} in runs/mcp-logs)"

    def request(self, rid: int, method: str, params: dict, timeout: float) -> dict:
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        end = time.time() + timeout
        with self.cv:
            while rid not in self.pending:
                if not self.alive:
                    raise MCPError(self._exit_reason())
                left = end - time.time()
                if left <= 0:
                    raise MCPError(f"no reply to {method} within {int(timeout)} s")
                self.cv.wait(min(left, 0.5))
            return self.pending.pop(rid)

    def notify(self, method: str, params: dict | None = None):
        self._write({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def close(self):
        try:
            self.p.stdin.close()
        except Exception:
            pass
        try:
            self.p.terminate()
            self.p.wait(timeout=3)
        except Exception:
            try:
                self.p.kill()
            except Exception:
                pass
        self.err.close()


class _Http:
    def __init__(self, name: str, spec: dict):
        self.url = spec["url"]
        self.headers = {k: str(v) for k, v in (spec.get("headers") or {}).items()}
        self.session = None
        self.version = None

    def _post(self, obj: dict, timeout: float, want_id=None) -> dict | None:
        h = {**UA, "Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self.headers}
        if self.session:
            h["Mcp-Session-Id"] = self.session
        if self.version:
            h["MCP-Protocol-Version"] = self.version
        req = urllib.request.Request(self.url, data=json.dumps(obj).encode(), headers=h, method="POST")
        try:
            r = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf-8", "replace")
            if e.code in (401, 403):
                raise MCPError(f"HTTP {e.code}: the server refused the credentials. Sign in again under Settings, Connectors, "
                               "or check the headers in mcp.json")
            raise MCPError(f"HTTP {e.code}: {body}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise MCPError(f"could not reach {self.url}: {e}")
        with r:
            sid = r.headers.get("Mcp-Session-Id")
            if sid:
                self.session = sid
            if want_id is None:
                return None
            ctype = r.headers.get("Content-Type", "")
            if "text/event-stream" in ctype:
                data = []
                for raw in r:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif line == "" and data:
                        try:
                            msg = json.loads("\n".join(data))
                        except ValueError:
                            msg = {}
                        data = []
                        if msg.get("id") == want_id:
                            return msg
                raise MCPError("the event stream ended without a reply")
            txt = r.read().decode("utf-8", "replace")
            msg = json.loads(txt) if txt.strip() else {}
            if isinstance(msg, list):
                msg = next((m for m in msg if m.get("id") == want_id), {})
            return msg

    def request(self, rid, method, params, timeout):
        return self._post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}, timeout, want_id=rid)

    def notify(self, method, params=None):
        try:
            self._post({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})}, 30)
        except MCPError:
            pass

    def close(self):
        if self.session:
            try:
                h = {**UA, "Mcp-Session-Id": self.session, **self.headers}
                urllib.request.urlopen(urllib.request.Request(self.url, headers=h, method="DELETE"), timeout=5)
            except Exception:
                pass


class _Sse:
    """The older HTTP+SSE transport: a GET event stream that first names the URL to POST messages to, then carries
    the replies."""

    def __init__(self, name: str, spec: dict, timeout: float = 30):
        import urllib.parse
        self.url = spec["url"]
        self.headers = {k: str(v) for k, v in (spec.get("headers") or {}).items()}
        self.pending: dict = {}
        self.cv = threading.Condition()
        self.alive = True
        self.endpoint = None
        req = urllib.request.Request(self.url, headers={**UA, "Accept": "text/event-stream", **self.headers})
        try:
            self.r = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise MCPError(f"HTTP {e.code}: the server refused the credentials. Sign in again under Settings, "
                               "Connectors, or check the headers in mcp.json")
            raise MCPError(f"HTTP {e.code} opening the event stream")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise MCPError(f"could not reach {self.url}: {e}")
        if "text/event-stream" not in self.r.headers.get("Content-Type", ""):
            self.r.close()
            raise MCPError("the server is not an MCP server (no event stream)")
        self._join = urllib.parse.urljoin
        threading.Thread(target=self._reader, daemon=True).start()
        end = time.time() + timeout
        with self.cv:
            while self.endpoint is None and self.alive and time.time() < end:
                self.cv.wait(0.3)
        if not self.endpoint:
            self.close()
            raise MCPError("the event stream did not say where to send messages")

    def _reader(self):
        event, data = "message", []
        try:
            for raw in self.r:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip())
                elif line == "":
                    if data:
                        payload = "\n".join(data)
                        with self.cv:
                            if event == "endpoint":
                                self.endpoint = self._join(self.url, payload.strip())
                            else:
                                try:
                                    msg = json.loads(payload)
                                except ValueError:
                                    msg = {}
                                if isinstance(msg, dict) and "id" in msg and "method" not in msg:
                                    self.pending[msg["id"]] = msg
                            self.cv.notify_all()
                    event, data = "message", []
        except Exception:  # noqa: BLE001  the stream closed
            pass
        with self.cv:
            self.alive = False
            self.cv.notify_all()

    def _send(self, obj: dict):
        req = urllib.request.Request(self.endpoint, data=json.dumps(obj).encode(), method="POST",
                                     headers={**UA, "Content-Type": "application/json", **self.headers})
        try:
            urllib.request.urlopen(req, timeout=30).close()
        except urllib.error.HTTPError as e:
            raise MCPError(f"HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise MCPError(f"could not reach {self.endpoint}: {e}")

    def request(self, rid, method, params, timeout):
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        end = time.time() + timeout
        with self.cv:
            while rid not in self.pending:
                if not self.alive:
                    raise MCPError("the server closed the event stream")
                left = end - time.time()
                if left <= 0:
                    raise MCPError(f"no reply to {method} within {int(timeout)} s")
                self.cv.wait(min(left, 0.5))
            return self.pending.pop(rid)

    def notify(self, method, params=None):
        try:
            self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})
        except MCPError:
            pass

    def close(self):
        self.alive = False
        try:                                    # shut the socket first: close() alone waits for the reader thread
            import socket
            self.r.fp.raw._sock.shutdown(socket.SHUT_RDWR)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.r.close()
        except Exception:  # noqa: BLE001
            pass


def _sse_spec(spec: dict) -> bool:
    return str(spec.get("type") or spec.get("transport") or "").lower() == "sse" or \
        str(spec.get("url", "")).rstrip("/").endswith("/sse")


# ---------- one server ---------------------------------------------------------------------------------------------

class Server:
    def __init__(self, name: str, spec: dict, log_dir: Path, roots: list[str]):
        self.name, self.spec = name, _expand(spec)
        self.status, self.error, self.tools, self.info = "starting", "", [], {}
        self._id = 0
        self._lock = threading.Lock()
        self.t = None
        self.images: list[dict] = []           # images from the latest tool call
        self.log_dir, self.roots = log_dir, roots

    def _rpc(self, method, params, timeout):
        with self._lock:
            self._id += 1
            rid = self._id
        msg = self.t.request(rid, method, params, timeout)
        if "error" in msg:
            e = msg["error"]
            raise MCPError(f"{e.get('message', 'error')} (code {e.get('code')})")
        return msg.get("result") or {}

    def start(self, timeout: float = 90):
        try:
            init = {"protocolVersion": PROTOCOL, "clientInfo": CLIENT, "capabilities": {"roots": {"listChanged": False}}}
            if self.spec.get("url") and _sse_spec(self.spec):
                self.t = _Sse(self.name, self.spec, min(timeout, 30))
                res = self._rpc("initialize", init, timeout)
            elif self.spec.get("url"):
                self.t = _Http(self.name, self.spec)
                try:
                    res = self._rpc("initialize", init, timeout)
                except MCPError as e:
                    # An older server: POST is refused, so try the HTTP+SSE transport at the same address.
                    if not re.match(r"HTTP (400|404|405)\b", str(e)):
                        raise
                    try:
                        self.t = _Sse(self.name, self.spec, min(timeout, 30))
                        res = self._rpc("initialize", init, timeout)
                    except MCPError:
                        raise e from None              # not an SSE server either: report what the POST said
            elif self.spec.get("command"):
                self.t = _Stdio(self.name, self.spec, self.log_dir, self.roots)
                res = self._rpc("initialize", init, timeout)
            else:
                raise MCPError("needs either a command or a url")
            self.info = res.get("serverInfo") or {}
            if isinstance(self.t, _Http):
                self.t.version = res.get("protocolVersion") or PROTOCOL
            self.t.notify("notifications/initialized")
            tools, cursor = [], None
            for _ in range(20):
                r = self._rpc("tools/list", {"cursor": cursor} if cursor else {}, 60)
                tools += r.get("tools") or []
                cursor = r.get("nextCursor")
                if not cursor:
                    break
            self.tools = tools
            self.status = "connected"
        except Exception as e:
            self.status, self.error = "error", str(e)[:400]
            self.close()

    def call(self, tool: str, args: dict) -> tuple[str, bool]:
        r = self._rpc("tools/call", {"name": tool, "arguments": args}, float(self.spec.get("timeout", 120)))
        self.images = []
        parts = []
        for c in r.get("content") or []:
            t = c.get("type")
            if t == "text":
                parts.append(c.get("text", ""))
            elif t == "resource":
                res = c.get("resource") or {}
                parts.append(res.get("text") or f"[resource {res.get('uri', '')}]")
            elif t == "resource_link":
                parts.append(f"[link {c.get('name', '')}: {c.get('uri', '')}]")
            elif t == "image" and c.get("data"):
                self.images.append({"mime": c.get("mimeType") or "image/png", "data": c["data"]})
                parts.append("[image attached]")
            elif t in ("image", "audio"):
                parts.append(f"[{t} returned, {c.get('mimeType', '')}; not shown to the model]")
        if not parts and r.get("structuredContent") is not None:
            parts.append(json.dumps(r["structuredContent"], indent=1))
        return "\n".join(parts) or "(no content)", bool(r.get("isError"))

    def close(self):
        if self.t:
            try:
                self.t.close()
            except Exception:
                pass


# ---------- all servers --------------------------------------------------------------------------------------------

class Manager:
    """Starts the configured servers in parallel and exposes their tools as OpenAI function tools."""

    def __init__(self, roots: list[str] | None = None, path: Path | None = None, extra: dict | None = None):
        self.extra = extra or {}                       # servers supplied by an ACP client for this session
        self.path = path or config_file()
        self.roots = roots or [os.getcwd()]
        self.servers: dict[str, Server] = {}
        self.disabled: list[str] = []
        self.config_error = ""
        self.map: dict[str, tuple[str, str]] = {}
        self.last_images: list[dict] = []

    def start(self, timeout: float = 90, use_health: bool = True) -> "Manager":
        try:
            cfg = active_config(self.path)
        except (ValueError, OSError) as e:
            self.config_error = f"{self.path.name} is not valid JSON: {e}"
            cfg = {}
        cfg = {**cfg, **self.extra}
        log_dir = C.runs_dir() / "mcp-logs"
        os.environ["MESHPOOL_PROJECT"] = str(Path(self.roots[0]).resolve())   # usable as ${MESHPOOL_PROJECT}
        threads = []
        for name, spec in cfg.items():
            if not isinstance(spec, dict) or spec.get("disabled"):
                self.disabled.append(name)
                continue
            s = Server(name, spec, log_dir, self.roots)
            self.servers[name] = s
            bad = recently_failed(name, spec) if use_health else None
            if bad is not None:                       # failed a moment ago: do not make every thread wait for it
                s.status, s.error = "error", f"skipped, it failed recently ({bad}). Check it in Settings, MCP servers."
                continue
            th = threading.Thread(target=s.start, args=(timeout,), daemon=True)
            th.start()
            threads.append((s, th))
        for s, th in threads:
            th.join(timeout + 5)
            if th.is_alive():
                s.status, s.error = "error", f"did not start within {int(timeout)} s"
        record_health({n: (cfg.get(n), s.status == "connected", s.error) for n, s in self.servers.items()
                       if not str(s.error).startswith("skipped")})
        self.map = {}
        for s in self.servers.values():
            for t in s.tools:
                self.map[tool_name(s.name, t["name"])] = (s.name, t["name"])
        return self

    def add(self, name: str, spec: dict, timeout: float = 60) -> "Server":
        """Start one more server in a running session (after /connect) and make its tools available at once."""
        old = self.servers.pop(name, None)
        if old:
            old.close()
        s = Server(name, spec, C.runs_dir() / "mcp-logs", self.roots)
        s.start(timeout)
        self.servers[name] = s
        self.map = {k: v for k, v in self.map.items() if v[0] != name}
        for t in s.tools:
            self.map[tool_name(name, t["name"])] = (name, t["name"])
        return s

    def schemas(self) -> list[dict]:
        out = []
        for s in self.servers.values():
            if s.status != "connected":
                continue
            for t in s.tools:
                params = dict(t.get("inputSchema") or {"type": "object", "properties": {}})
                params.pop("$schema", None)
                params.setdefault("type", "object")
                params.setdefault("properties", {})
                desc = (t.get("description") or t.get("title") or t["name"]).strip()
                out.append({"type": "function", "function": {
                    "name": tool_name(s.name, t["name"]),
                    "description": f"[MCP server '{s.name}'] {desc}"[:1024], "parameters": params}})
        return out

    def has(self, name: str) -> bool:
        return name in self.map

    def call(self, name: str, args: dict) -> tuple[str, bool]:
        """Run a tool. Images it returned are left in self.last_images for the agent to show the model."""
        sname, tool = self.map[name]
        s = self.servers[sname]
        self.last_images = []
        if s.status != "connected":
            return f"MCP server '{sname}' is not connected: {s.error}", True
        try:
            out = s.call(tool, args)
            self.last_images = list(s.images)
            return out
        except MCPError as e:
            return f"MCP error from '{sname}': {e}", True

    def status(self) -> list[dict]:
        rows = [{"name": s.name, "status": s.status, "tools": [t["name"] for t in s.tools], "error": s.error,
                 "transport": ("sse" if isinstance(s.t, _Sse) else "http") if s.spec.get("url") else "stdio",
                 "server": (s.info.get("name") or "") + (" " + s.info.get("version", "") if s.info.get("version") else "")}
                for s in self.servers.values()]
        rows += [{"name": n, "status": "disabled", "tools": [], "error": "", "transport": "", "server": ""}
                 for n in self.disabled]
        return rows

    def close(self):
        for s in self.servers.values():
            s.close()
