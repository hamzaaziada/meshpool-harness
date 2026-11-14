"""MCP client (Model Context Protocol), standard library only.

Transports:
- stdio: {"command": "npx", "args": [...], "env": {...}}   (the server is a child process; JSON-RPC over its pipes)
- streamable HTTP: {"url": "https://...", "headers": {"Authorization": "Bearer ${TOKEN}"}}
- HTTP with SSE (the 2024-11-05 transport): {"url": "https://.../sse", "type": "sse"}; also used on its own when a
  server refuses the streamable HTTP POST, as the MCP spec's backwards-compatibility section describes.

Config file: mcp.json next to meshpool.config, in the common shape most MCP clients use:
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


def _servers_key(data: dict) -> str:
    """The key a config file keeps its servers under: the common "mcpServers", or VS Code's "servers"."""
    return "mcpServers" if "mcpServers" in data else "servers" if "servers" in data else "mcpServers"


def load_config(path: Path | None = None) -> dict:
    path = path or config_file()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    a, b = data.get("mcpServers"), data.get("servers")
    if isinstance(a, dict) and isinstance(b, dict):
        return {**b, **a}                      # a file that ended up with both keys: show every server
    return a or b or {}


_CFG_LOCK = threading.Lock()


def write_text_atomic(path: Path, text: str, mode: int | None = None) -> None:
    """Write a file so a reader never sees half of it: a temporary file next to it, then a rename."""
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if mode is None and path.exists():
            mode = path.stat().st_mode & 0o777
        try:
            os.chmod(tmp, 0o644 if mode is None else mode)
        except OSError:
            pass
        C.replace_file(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def edit_config(fn, path: Path | None = None, create: bool = True, whole: bool = False):
    """The one way to change mcp.json: load it, let fn change the servers dict in place (fn(servers), or
    fn(servers, document) with whole=True), and write it back atomically. The servers stay under the key the file
    already uses ("mcpServers", else VS Code's "servers"); a file that ended up with both is merged into one. Nothing
    is written when fn changed nothing. A missing file is created (create=True) or left alone (returns None).
    Returns what fn returned. Raises ValueError when the file is not valid JSON."""
    path = Path(path or config_file())
    with _CFG_LOCK:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            cfg = json.loads(text) if text.strip() else {}
        elif create:
            cfg = {}
        else:
            return None
        if not isinstance(cfg, dict):
            raise ValueError(f"{path.name} must hold a JSON object")
        before = json.dumps(cfg, sort_keys=True)
        key = _servers_key(cfg)
        had = key in cfg
        if had and not isinstance(cfg[key], dict):
            raise ValueError(f'"{key}" in {path.name} must be an object of servers')
        servers = cfg.setdefault(key, {})
        other = cfg.get("servers") if key == "mcpServers" else None
        if isinstance(other, dict):                # repair a file an older version gave a second key
            servers.update({k: v for k, v in other.items() if k not in servers})
            del cfg["servers"]
        out = fn(servers, cfg) if whole else fn(servers)
        if not had and not cfg.get(key):
            cfg.pop(key, None)
            if not path.exists():
                cfg[key] = {}
        if path.exists() and json.dumps(cfg, sort_keys=True) == before:
            return out
        write_text_atomic(path, json.dumps(cfg, indent=2) + "\n")
        return out


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
            d[name] = {"sig": _sig(spec), "ok": bool(ok), "error": redact(err)[:300], "t": now}
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


def _short_hash(*parts: str) -> str:
    import hashlib
    return hashlib.sha1("\0".join(parts).encode("utf-8")).hexdigest()[:8]


def tool_name(server: str, tool: str) -> str:
    """The function name a provider sees: letters, digits, _ and -, at most 64 characters. A name that had to be cut
    ends in a short hash of the full server and tool names, so two long names never become the same."""
    n = re.sub(r"[^A-Za-z0-9_-]", "_", f"mcp__{server}__{tool}")
    return n if len(n) <= 64 else n[:55] + "_" + _short_hash(server, tool)


def unique_tool_names(pairs) -> dict:
    """{(server, tool): function name} for every pair, every name distinct. When two pairs come out the same after
    sanitising ("a.b" and "a_b"), the later one gets a hash suffix."""
    out, used = {}, set()
    for server, tool in pairs:
        if (server, tool) in out:
            continue
        n = tool_name(server, tool)
        i = 0
        while n in used:
            base = re.sub(r"[^A-Za-z0-9_-]", "_", f"mcp__{server}__{tool}")[:55]
            n = base + "_" + _short_hash(server, tool, str(i))
            i += 1
        used.add(n)
        out[(server, tool)] = n
    return out


_SECRET_Q = re.compile(r"([?&;](?:[A-Za-z0-9_.-]*(?:key|token|secret|password|passwd|auth|sig|signature|code)"
                       r"[A-Za-z0-9_.-]*)=)[^&#\s\"'<>]+", re.I)


def redact(text) -> str:
    """Hide secrets carried in URL query strings (?apikey=..., &access_token=...) in messages people see or save."""
    return _SECRET_Q.sub(r"\1***", str(text or ""))


class MCPError(Exception):
    pass


class _AuthRefused(MCPError):
    """HTTP 401/403: the credentials were refused (a connector's token may only need refreshing)."""


class _SessionLost(MCPError):
    """The server forgot the session (HTTP 404 with a session id, or the legacy event stream closed) before it saw
    the request, so it is safe to connect again and resend."""


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
        try:
            for raw in self.p.stdout:
                try:
                    msg = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue                               # servers sometimes print banners
                if not isinstance(msg, dict):              # a stray "42" or "true" on stdout is not a message
                    continue
                rid = msg.get("id")
                if not isinstance(rid, (int, str)) or isinstance(rid, bool):
                    continue                               # notifications, and ids no request of ours could have
                if "method" in msg:                        # server -> client request
                    try:
                        self._answer(msg)
                    except MCPError:
                        pass                               # stdin closed: the loop ends when stdout does
                    continue
                with self.cv:
                    self.pending[rid] = msg
                    self.cv.notify_all()
        except Exception as e:  # noqa: BLE001  never leave waiters hanging until their timeout
            self.reader_error = f"{type(e).__name__}: {e}"
            try:
                self.p.kill()
            except Exception:  # noqa: BLE001
                pass
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
                    if self.reader_error:
                        raise MCPError(f"Meshpool stopped reading the server's replies ({self.reader_error})")
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
                raise _AuthRefused(f"HTTP {e.code}: the server refused the credentials. Sign in again under Settings, "
                                   "Connectors, or check the headers in mcp.json")
            if e.code == 404 and self.session:     # the session expired: the spec says start a new one
                raise _SessionLost(f"HTTP 404: the server ended the session ({redact(body)})")
            raise MCPError(f"HTTP {e.code}: {redact(body)}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise MCPError(redact(f"could not reach {self.url}: {e}"))
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
        try:                                    # the timeout covers connecting and the response headers only
            self.r = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise _AuthRefused(f"HTTP {e.code}: the server refused the credentials. Sign in again under "
                                   "Settings, Connectors, or check the headers in mcp.json")
            raise MCPError(f"HTTP {e.code} opening the event stream")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise MCPError(redact(f"could not reach {self.url}: {e}"))
        if "text/event-stream" not in self.r.headers.get("Content-Type", ""):
            self.r.close()
            raise MCPError("the server is not an MCP server (no event stream)")
        self._join = urllib.parse.urljoin
        end = time.time() + timeout
        threading.Thread(target=self._reader, daemon=True).start()
        with self.cv:
            while self.endpoint is None and self.alive and time.time() < end:
                self.cv.wait(0.3)
        if not self.endpoint:
            self.close()
            raise MCPError("the event stream did not say where to send messages")

    def _sock(self):
        try:
            return self.r.fp.raw._sock
        except AttributeError:
            return None

    def _no_read_timeout(self):
        """An event stream may be silent for minutes between replies; the connect timeout must not end it. Each
        request still waits only its own timeout for its reply."""
        s = self._sock()
        if s is not None:
            try:
                s.settimeout(None)
            except OSError:
                pass

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
                                self._no_read_timeout()
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
            body = redact(e.read()[:200].decode('utf-8', 'replace'))
            if e.code in (401, 403):
                raise _AuthRefused(f"HTTP {e.code}: the server refused the credentials. Sign in again under "
                                   "Settings, Connectors, or check the headers in mcp.json")
            if e.code == 404:                   # the server no longer knows this stream's session
                raise _SessionLost(f"HTTP 404: the server ended the session ({body})")
            raise MCPError(f"HTTP {e.code}: {body}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise MCPError(redact(f"could not reach {self.endpoint}: {e}"))

    def request(self, rid, method, params, timeout):
        if not self.alive:                      # it died while idle: the caller connects again and resends
            raise _SessionLost("the server closed the event stream")
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        end = time.time() + timeout
        with self.cv:
            while rid not in self.pending:
                if not self.alive:              # after sending: the request may have run, so do not resend it
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
        self._conn = threading.RLock()         # one reconnect or token refresh at a time
        self._gen = 0                          # bumped on each reconnect, so racing callers reconnect once
        self.cancelled = self.closed = False
        self.t = None
        self.images: list[dict] = []           # images from the latest tool call
        self.log_dir, self.roots = log_dir, roots

    def _rpc(self, method, params, timeout):
        retried = set()
        while True:
            with self._lock:
                self._id += 1
                rid = self._id
                gen = self._gen
            try:
                msg = self.t.request(rid, method, params, timeout)
                break
            except _AuthRefused:
                if "auth" in retried or self.closed or not self._refresh_auth():
                    raise
                retried.add("auth")
            except _SessionLost:
                if "session" in retried or self.closed or method == "initialize":
                    raise
                retried.add("session")
                self._reconnect(gen, timeout)
        if "error" in msg:
            e = msg["error"]
            raise MCPError(f"{e.get('message', 'error')} (code {e.get('code')})")
        return msg.get("result") or {}

    def _init_params(self) -> dict:
        return {"protocolVersion": PROTOCOL, "clientInfo": CLIENT, "capabilities": {"roots": {"listChanged": False}}}

    def _handshake(self, timeout):
        res = self._rpc("initialize", self._init_params(), timeout)
        self.info = res.get("serverInfo") or {}
        if isinstance(self.t, _Http):
            self.t.version = res.get("protocolVersion") or PROTOCOL
        self.t.notify("notifications/initialized")

    def _reconnect(self, gen: int, timeout: float):
        """The server dropped the session (HTTP 404) or the legacy event stream closed: start a new session once."""
        with self._conn:
            if self._gen != gen:               # another call already reconnected
                return
            if isinstance(self.t, _Http):
                self.t.session = self.t.version = None
            elif isinstance(self.t, _Sse):
                self.t.close()
                self.t = _Sse(self.name, self.spec, min(timeout, 30))
            self._handshake(min(timeout, 60))
            self._gen += 1

    def _refresh_auth(self) -> bool:
        """A connector's token was refused (it expires after about an hour): fetch a fresh one and use it."""
        if not self.spec.get("connector"):
            return False
        with self._conn:
            try:
                from meshpool_harness import mcp_apps as CN
                headers = CN.refresh_headers(self.name)
            except Exception:  # noqa: BLE001
                return False
            if not headers or headers == getattr(self.t, "headers", None):
                return False
            self.t.headers = {k: str(v) for k, v in headers.items()}
            self.spec["headers"] = dict(self.t.headers)
            return True

    def start(self, timeout: float = 90):
        try:
            if self.spec.get("url") and _sse_spec(self.spec):
                self.t = _Sse(self.name, self.spec, min(timeout, 30))
                self._handshake(timeout)
            elif self.spec.get("url"):
                self.t = _Http(self.name, self.spec)
                try:
                    self._handshake(timeout)
                except MCPError as e:
                    # An older server: POST is refused, so try the HTTP+SSE transport at the same address.
                    if not re.match(r"HTTP (400|404|405)\b", str(e)):
                        raise
                    try:
                        self.t = _Sse(self.name, self.spec, min(timeout, 30))
                        self._handshake(timeout)
                    except MCPError:
                        raise e from None              # not an SSE server either: report what the POST said
            elif self.spec.get("command"):
                self.t = _Stdio(self.name, self.spec, self.log_dir, self.roots)
                self._handshake(timeout)
            else:
                raise MCPError("needs either a command or a url")
            tools, cursor = [], None
            for _ in range(20):
                r = self._rpc("tools/list", {"cursor": cursor} if cursor else {}, 60)
                tools += r.get("tools") or []
                cursor = r.get("nextCursor")
                if not cursor:
                    break
            with self._lock:
                if self.cancelled:                 # the caller gave up waiting: stay an error, do not appear late
                    raise MCPError(self.error or "cancelled")
                self.tools = [t for t in tools if isinstance(t, dict) and isinstance(t.get("name"), str)]
                self.status = "connected"
        except Exception as e:
            with self._lock:
                if not self.cancelled:
                    self.status, self.error = "error", redact(e)[:400]
            self.close()

    def cancel(self, reason: str):
        """Give up on a server whose start is taking too long. Its start thread may still finish later; it then
        closes the connection instead of marking the server connected."""
        with self._lock:
            if self.status == "connected":
                return
            self.cancelled = True
            self.status, self.error = "error", reason
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
        self.closed = True
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
        self._names: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()
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
        end = time.time() + timeout + 5
        for s, th in threads:
            th.join(max(0.0, end - time.time()))
            if th.is_alive():
                s.cancel(f"did not start within {int(timeout)} s")
        record_health({n: (cfg.get(n), s.status == "connected", s.error) for n, s in self.servers.items()
                       if not str(s.error).startswith("skipped")})
        self._rebuild()
        return self

    def _rebuild(self):
        """Tool name -> (server, tool) for every connected server. schemas() reads the same table, so the model is
        never offered a tool that call() does not know."""
        with self._lock:
            pairs = [(s.name, t["name"]) for s in self.servers.values() if s.status == "connected" for t in s.tools]
            names = unique_tool_names(pairs)
            self._names = names
            self.map = {n: pair for pair, n in names.items()}

    def add(self, name: str, spec: dict, timeout: float = 60) -> "Server":
        """Start one more server in a running session (after /connect) and make its tools available at once."""
        old = self.servers.pop(name, None)
        if old:
            old.close()
        s = Server(name, spec, C.runs_dir() / "mcp-logs", self.roots)
        s.start(timeout)
        self.servers[name] = s
        self._rebuild()
        return s

    def schemas(self) -> list[dict]:
        out = []
        with self._lock:
            names = dict(self._names)
        for s in list(self.servers.values()):
            for t in s.tools:
                fname = names.get((s.name, t["name"]))
                if fname is None:              # not connected, or connected after the table was built
                    continue
                params = dict(t.get("inputSchema") or {"type": "object", "properties": {}})
                params.pop("$schema", None)
                params.setdefault("type", "object")
                params.setdefault("properties", {})
                desc = (t.get("description") or t.get("title") or t["name"]).strip()
                out.append({"type": "function", "function": {
                    "name": fname, "description": f"[MCP server '{s.name}'] {desc}"[:1024], "parameters": params}})
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
            return f"MCP error from '{sname}': {redact(e)}", True

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
