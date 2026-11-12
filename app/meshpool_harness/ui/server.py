"""Meshpool web app: a local page to set the API key, run tasks and watch the agent work.

Standard library only. Binds to 127.0.0.1. Every API call must carry the per-launch token that is baked into the
page, so other websites open in the same browser cannot drive it. Each task runs as its own process
(`python -m meshpool_harness run ...`), so a crash or Stop never takes the app down, and new settings apply
to the next run.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from meshpool_harness import config as C
from meshpool_harness.ui import agent_api as A
from meshpool_harness.ui import workbench as W

APP = C.APP
HERE = Path(__file__).resolve().parent
RUNS = C.runs_dir()
UPLOADS = RUNS / "_uploads"
TOKEN = secrets.token_urlsafe(24)
UI_VER = secrets.token_hex(4)
JOBS: dict[str, dict] = {}
DESKTOP = False
LAST_PING = [time.time()]
HTTPD = [None]
NATIVE = [False]                    # True while the interface is shown in the app's own window
ON_SHOW: list = []                  # called when a second launch asks this instance to come forward
ON_QUIT: list = []                  # called when the app quits (closes the native window)
LOCK = threading.Lock()
ENGINES = ["code", "data", "sheets", "slides", "browser", "general"]


# ---------- jobs ---------------------------------------------------------------------------------------------------

def _meta_path(jid: str) -> Path:
    return RUNS / jid / "job.json"


def _save_meta(meta: dict) -> None:
    _meta_path(meta["id"]).write_text(json.dumps(meta, indent=1), encoding="utf-8")


def _load_meta(jid: str) -> dict | None:
    p = _meta_path(jid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def start_job(body: dict) -> dict:
    request = (body.get("request") or "").strip()
    if not request:
        raise ValueError("Describe the task first.")
    vals = C.read()
    if not (vals.get("MESHPOOL_API_KEY") or os.environ.get("MESHPOOL_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        raise ValueError("Add your API key in Settings first.")
    engine = body.get("engine") or ""
    if engine and any(e not in ENGINES for e in engine.split(",")):
        raise ValueError(f"Unknown engine: {engine}")
    workdir = (body.get("workdir") or "").strip().strip('"')
    if workdir and not Path(workdir).expanduser().is_dir():
        raise ValueError(f"Working folder not found: {workdir}")
    files = [f for f in body.get("files") or [] if Path(f).exists()]

    jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    jdir = RUNS / jid
    out = jdir / "out"
    out.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "meshpool_harness", "run", request, "--out", str(out)]
    if engine:
        cmd += ["--engine", engine]
    if workdir:
        cmd += ["--workdir", str(Path(workdir).expanduser().resolve())]
    if body.get("format"):
        cmd += ["--format", body["format"]]
    if files:
        cmd += ["--files", *files]

    env = dict(os.environ)
    env.update({k: v for k, v in vals.items() if v != ""})        # saved settings win inside the app
    env.update({"MH_EVENT_LOG": str(jdir / "events.jsonl"), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                "PYTHONPATH": str(APP) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""),
                "MESHPOOL_CONFIG": str(C.config_path())})
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kw["start_new_session"] = True
    stdout = open(jdir / "result.json", "w", encoding="utf-8")
    stderr = open(jdir / "stderr.log", "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=str(APP), env=env, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, **kw)
    meta = {"id": jid, "request": request, "engine": engine or "auto", "workdir": workdir,
            "files": [Path(f).name for f in files], "model": env.get("MH_MODEL", "deepseek-v4.1-flash"),
            "started": time.time(), "ended": None, "status": "running", "exit": None, "pid": proc.pid}
    _save_meta(meta)
    with LOCK:
        JOBS[jid] = {"proc": proc, "files": (stdout, stderr)}
    threading.Thread(target=_watch, args=(jid,), daemon=True).start()
    return meta


def _watch(jid: str) -> None:
    j = JOBS[jid]
    code = j["proc"].wait()
    for f in j["files"]:
        f.close()
    meta = _load_meta(jid) or {"id": jid}
    meta["exit"], meta["ended"] = code, time.time()
    if meta.get("status") != "stopped":
        res = _result(jid)
        if res is None:
            meta["status"] = "error"
        else:
            meta["status"] = "passed" if res.get("ok") else "unfinished"
    _save_meta(meta)


def stop_job(jid: str) -> dict:
    j = JOBS.get(jid)
    meta = _load_meta(jid)
    if not j or not meta or meta.get("status") != "running":
        raise ValueError("That task is not running.")
    meta["status"] = "stopped"
    _save_meta(meta)
    p = j["proc"]
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
    except Exception:
        p.kill()
    return meta


def _result(jid: str) -> dict | None:
    p = RUNS / jid / "result.json"
    try:
        txt = p.read_text(encoding="utf-8")
        return json.loads(txt[txt.find("{"):]) if "{" in txt else None
    except (OSError, ValueError):
        return None


def _events(jid: str, since: int) -> list:
    p = RUNS / jid / "events.jsonl"
    if not p.exists():
        return []
    out = []
    for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines()):
        if i < since or not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _files(jid: str) -> list:
    out = RUNS / jid / "out"
    if not out.exists():
        return []
    rows = []
    for p in sorted(out.rglob("*")):
        rel = p.relative_to(out)
        if p.is_file() and not any(part.startswith(".") for part in rel.parts):
            rows.append({"name": rel.as_posix(), "size": p.stat().st_size})
    return rows[:200]


def job_detail(jid: str, since: int) -> dict:
    meta = _load_meta(jid)
    if not meta:
        raise KeyError(jid)
    if meta.get("status") == "running" and jid not in JOBS:       # app restarted while it ran
        meta["status"] = "error"
        meta["note"] = "The app was closed while this task ran."
    err = (RUNS / jid / "stderr.log")
    tail = err.read_text(encoding="utf-8", errors="replace")[-4000:] if err.exists() else ""
    return {"meta": meta, "events": _events(jid, since), "result": _result(jid) if meta["status"] != "running" else None,
            "stderr": tail, "files": _files(jid)}


def list_jobs() -> list:
    if not RUNS.exists():
        return []
    rows = []
    for d in sorted(RUNS.iterdir(), reverse=True):
        if d.is_dir() and not d.name.startswith("_"):
            m = _load_meta(d.name)
            if m:
                if m.get("status") == "running" and d.name not in JOBS:
                    m["status"] = "error"
                rows.append({k: m.get(k) for k in ("id", "request", "engine", "status", "started", "ended", "model")})
        if len(rows) >= 50:
            break
    return rows


# ---------- settings -----------------------------------------------------------------------------------------------

def get_settings() -> dict:
    vals = C.read()
    fields = []
    for key, label, default, help_ in C.FIELDS:
        v = vals.get(key, "")
        item = {"key": key, "label": label, "default": default, "help": help_}
        if key in C.SECRET:
            item["set"] = bool(v)
            item["hint"] = ("ends in " + v[-4:]) if len(v) >= 8 else ("set" if v else "")
            item["value"] = ""
        else:
            item["value"] = v
        fields.append(item)
    from meshpool_harness.ui import desktop as Dk
    return {"path": str(C.config_path()), "fields": fields, "system": C.system_check(),
            "desktop": DESKTOP, "home": str(C.home()), "cli": Dk.cli_status(),
            "default_project": str(Dk.default_project()),
            "native": NATIVE[0], "platform": sys.platform,
            "has_key": bool(vals.get("MESHPOOL_API_KEY") or os.environ.get("MESHPOOL_API_KEY"))}


def save_settings(body: dict) -> dict:
    allowed = {f[0] for f in C.FIELDS}
    vals = {}
    for k, v in (body.get("values") or {}).items():
        if k not in allowed:
            continue
        v = str(v).strip().replace("\n", "")
        if k in C.SECRET and v == "":
            continue                     # blank key field = keep the saved key
        vals[k] = v
    if vals.get("GITHUB_TOKEN"):
        try:
            from meshpool_harness.ui import agent_api as _A
            _A.mcp_save({"name": "github", "enabled": True})
        except Exception:
            pass
    if vals:
        C.write(vals)
        for k, v in vals.items():
            if v:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
    return get_settings()


def assistant_view() -> dict:
    from meshpool_harness import assistant as AS
    rows = [dict(t, describe=AS.describe(t)) for t in AS.tasks()]
    return {"instructions": AS.instructions(), "memory": AS.memory(), "settings": AS.settings(), "tasks": rows}


def assistant_action(b: dict) -> dict:
    from meshpool_harness import assistant as AS
    act = b.get("action")
    if act == "instructions":
        AS.set_instructions(b.get("text", ""))
    elif act == "memory":
        AS.set_memory(b.get("text", ""))
    elif act == "settings":
        AS.save_settings(**{k: b[k] for k in ("memory_on", "instructions_on") if k in b})
    elif act == "task_add":
        AS.add_task(b.get("name", ""), b.get("prompt", ""), b.get("when", "daily"), b.get("time", "09:00"),
                    b.get("day", "mon"), b.get("at", ""), int(b.get("minutes") or 60), b.get("folder", ""),
                    b.get("harness", ""), b.get("kind", "cowork"))
    elif act == "task_update":
        AS.update_task(b.get("id", ""), **{k: b.get(k) for k in ("name", "prompt", "when", "time", "day", "at",
                                                                   "minutes", "folder", "harness", "kind", "enabled")})
    elif act == "task_delete":
        AS.delete_task(b.get("id", ""))
    elif act == "task_run":
        t = next((x for x in AS.tasks() if x["id"] == b.get("id")), None)
        if not t:
            raise ValueError("That task is gone.")
        return dict(assistant_view(), thread=A.run_scheduled(t))
    else:
        raise ValueError("Unknown action.")
    return assistant_view()


def test_connection() -> dict:
    C.load_into_env(override=True)
    from meshpool_harness.cli import ping
    if not os.environ.get("MESHPOOL_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        return {"ok": False, "detail": "No API key saved yet."}
    return ping()


def route_preview(body: dict) -> dict:
    from meshpool_harness.router.rules import decide
    req = (body.get("request") or "").strip()
    if body.get("engine"):
        return {"engines": body["engine"].split(","), "confidence": 1.0, "reason": "chosen by you"}
    if not req:
        return {"engines": [], "confidence": 0, "reason": ""}
    eng, conf, why = decide(req, body.get("files") or [], None, body.get("workdir") or None)
    return {"engines": eng, "confidence": conf, "reason": why}


def save_upload(name: str, data: bytes) -> dict:
    safe = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(name).name).strip() or "file"
    d = UPLOADS / uuid.uuid4().hex[:8]
    d.mkdir(parents=True, exist_ok=True)
    p = d / safe
    p.write_bytes(data)
    return {"path": str(p), "name": safe, "size": len(data)}


def open_folder(jid: str) -> dict:
    d = RUNS / jid / "out"
    if not d.exists():
        raise KeyError(jid)
    if os.name == "nt":
        os.startfile(str(d))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(d)])
    else:
        subprocess.Popen(["xdg-open", str(d)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True, "path": str(d)}


# ---------- HTTP ---------------------------------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "Meshpool"

    def log_message(self, *a):          # quiet console
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _send(self, code: int, body, ctype="application/json; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 260 * 1024 * 1024:
            raise ValueError("File is larger than 150 MB.")
        return self.rfile.read(n) if n else b""

    def _json(self) -> dict:
        raw = self._body()
        return json.loads(raw.decode("utf-8")) if raw else {}

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            html = (HERE / "index.html").read_text(encoding="utf-8").replace("__MESHPOOL_TOKEN__", TOKEN) \
                .replace("__MESHPOOL_DESKTOP__", "true" if DESKTOP else "false").replace("__MESHPOOL_VER__", UI_VER)
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8",
                              {"Content-Security-Policy": "frame-ancestors 'none'"})
        m = re.fullmatch(r"/ui/(app\.js|app\.css)", u.path)
        if m:
            ctype = "text/javascript; charset=utf-8" if m.group(1).endswith(".js") else "text/css; charset=utf-8"
            return self._send(200, (HERE / m.group(1)).read_bytes(), ctype)
        if u.path == "/oauth/callback":          # the browser comes back here after signing in to a connector
            from meshpool_harness import mcp_apps as CN
            q = parse_qs(u.query)
            try:
                name = CN.finish(q.get("state", [""])[0], q.get("code", [None])[0], q.get("error", [None])[0])
                msg, ok = f"{name} is connected. You can close this tab and go back to Meshpool.", True
            except Exception as e:  # noqa: BLE001
                msg, ok = str(e), False
            page = ("<!doctype html><meta charset=utf-8><title>Meshpool</title><body style=\"font:15px -apple-system,"
                    "Segoe UI,sans-serif;display:grid;place-items:center;height:90vh;color:#0B131F\"><div style=\"max-width:"
                    "460px\"><h2 style=\"margin:0 0 8px\">" + ("Connected" if ok else "Sign-in did not finish") +
                    "</h2><p>" + msg.replace("<", "&lt;") + "</p></div>")
            return self._send(200 if ok else 400, page.encode("utf-8"), "text/html; charset=utf-8")
        if u.path == "/healthz":
            return self._send(200, {"ok": True, "app": "meshpool"})
        if u.path == "/favicon.svg":
            return self._send(200, (HERE / "favicon.svg").read_bytes(), "image/svg+xml")
        m = re.fullmatch(r"/brand/((?:fonts/)?[\w.-]+\.(png|woff2))", u.path)
        if m and (HERE / "brand" / m.group(1)).is_file():
            return self._send(200, (HERE / "brand" / m.group(1)).read_bytes(),
                              "image/png" if m.group(2) == "png" else "font/woff2",
                              {"Cache-Control": "max-age=86400"})
        if u.path == "/raw":                          # a project file, for image and PDF previews
            q = parse_qs(u.query)
            if q.get("t", [""])[0] != TOKEN:
                return self._send(403, {"error": "forbidden"})
            try:
                p = W._inside(W.check_root(q.get("root", [""])[0]), q.get("path", [""])[0])
            except (ValueError, OSError):
                return self._send(404, {"error": "not found"})
            if not p.is_file() or p.stat().st_size > 60_000_000:
                return self._send(404, {"error": "not found"})
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            if not (ctype.startswith("image/") or ctype == "application/pdf"):
                return self._send(415, {"error": "only images and PDFs are served"})
            return self._send(200, p.read_bytes(), ctype, {"Content-Disposition": f'inline; filename="{p.name}"',
                                                          "X-Content-Type-Options": "nosniff",
                                                          "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"})
        if u.path.startswith("/files/"):
            q = parse_qs(u.query)
            if q.get("t", [""])[0] != TOKEN:
                return self._send(403, {"error": "forbidden"})
            parts = unquote(u.path[len("/files/"):]).split("/", 1)
            if len(parts) != 2:
                return self._send(404, {"error": "not found"})
            base = (RUNS / parts[0] / "out").resolve()
            p = (base / parts[1]).resolve()
            if base not in p.parents or not p.is_file():
                return self._send(404, {"error": "not found"})
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            disp = "inline" if ctype.startswith(("image/", "text/")) or ctype == "application/pdf" else "attachment"
            return self._send(200, p.read_bytes(), ctype, {"Content-Disposition": f'{disp}; filename="{p.name}"'})
        if self.headers.get("X-Meshpool-Token") != TOKEN:
            return self._send(403, {"error": "forbidden"})
        try:
            if u.path == "/api/settings":
                return self._send(200, get_settings())
            if u.path == "/api/assistant":
                return self._send(200, assistant_view())
            if u.path == "/api/import":
                from meshpool_harness import importer as IM
                return self._send(200, IM.scan())
            if u.path == "/api/about":
                import meshpool_harness as MH

                def rd(name):
                    try:
                        return (C.APP / name).read_text(encoding="utf-8")
                    except OSError:
                        return ""
                return self._send(200, {"version": MH.__version__, "author": MH.__author__, "notice": rd("NOTICE"),
                                        "license": rd("LICENSE"), "third_party": rd("THIRD_PARTY_NOTICES.md")})
            if u.path == "/api/jobs":
                return self._send(200, {"jobs": list_jobs()})
            if u.path == "/api/agent":
                return self._send(200, {"sessions": A.listing()})
            if u.path == "/api/mcp":
                return self._send(200, A.mcp_info())
            if u.path == "/api/harnesses":
                return self._send(200, A.harness_info())
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/api/models":
                return self._send(200, W.models(refresh=q.get("refresh") == "1"))
            if u.path == "/api/projects":
                return self._send(200, {"projects": W.projects(), "default": str(_default_project())})
            if u.path == "/api/skills":
                from meshpool_harness import skills as SK
                return self._send(200, {"skills": SK.load_all(), "folder": str(SK.folder())})
            if u.path == "/api/skill":
                from meshpool_harness import skills as SK
                sk = SK.get(q.get("id", ""))
                if not sk:
                    raise KeyError(q.get("id"))
                return self._send(200, sk | {"text": Path(sk["path"]).read_text(encoding="utf-8", errors="replace")})
            if u.path == "/api/files":
                return self._send(200, W.list_dir(q.get("root", ""), q.get("path", "")))
            if u.path == "/api/file":
                return self._send(200, W.read_file(q.get("root", ""), q.get("path", "")))
            if u.path == "/api/git":
                return self._send(200, W.git_status(q.get("root", "")))
            if u.path == "/api/connectors":
                from meshpool_harness import mcp_apps as CN
                C.load_into_env(override=True)
                return self._send(200, {"connectors": CN.listing()})
            if u.path == "/api/git/ci":
                return self._send(200, W.git_ci(parse_qs(u.query).get("root", [""])[0]))
            if u.path == "/api/usage":
                return self._send(200, W.usage())
            if u.path == "/api/search":
                return self._send(200, {"hits": A.search(q.get("q", ""))})
            m = re.fullmatch(r"/api/term/(\w+)", u.path)
            if m:
                return self._send(200, W.term_view(m.group(1), int(q.get("since", "0"))))
            m = re.fullmatch(r"/api/agent/([\w-]+)/diff", u.path)
            if m:
                s_ = A.get(m.group(1))
                turn = int(q["turn"]) if q.get("turn") else None
                return self._send(200, s_.cp.diff(turn) if s_.cp else {"files": [], "patch": "", "note": ""})
            m = re.fullmatch(r"/api/agent/([\w-]+)", u.path)
            if m:
                since = int(parse_qs(u.query).get("since", ["0"])[0])
                return self._send(200, A.get(m.group(1)).view(since))
            m = re.fullmatch(r"/api/jobs/([\w-]+)", u.path)
            if m:
                since = int(parse_qs(u.query).get("since", ["0"])[0])
                return self._send(200, job_detail(m.group(1), since))
        except KeyError:
            return self._send(404, {"error": "Not found."})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # keep the app alive, show the cause
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        u = urlparse(self.path)
        if u.path == "/show":                  # a second launch of the app asks this one to come to the front
            shown = any(fn() for fn in list(ON_SHOW))
            return self._send(200, {"ok": True, "native": shown})
        if self.headers.get("X-Meshpool-Token") != TOKEN:
            return self._send(403, {"error": "forbidden"})
        try:
            if u.path == "/api/settings":
                return self._send(200, save_settings(self._json()))
            if u.path == "/api/test":
                return self._send(200, test_connection())
            if u.path == "/api/route":
                return self._send(200, route_preview(self._json()))
            if u.path == "/api/run":
                return self._send(200, start_job(self._json()))
            if u.path == "/api/upload":
                name = unquote(parse_qs(u.query).get("name", ["file"])[0])
                return self._send(200, save_upload(name, self._body()))
            if u.path == "/api/ping":
                LAST_PING[0] = time.time()
                return self._send(200, {"ok": True})
            if u.path == "/api/quit":
                threading.Timer(0.3, _shutdown).start()
                return self._send(200, {"ok": True})
            if u.path == "/api/choose-folder":
                from meshpool_harness.ui.desktop import choose_folder
                return self._send(200, {"path": choose_folder(self._json().get("start", ""))})
            if u.path == "/api/open-data":
                from meshpool_harness.ui.desktop import reveal
                reveal(C.home())
                return self._send(200, {"ok": True})
            if u.path == "/api/assistant":
                return self._send(200, assistant_action(self._json()))
            if u.path == "/api/route-harness":
                from meshpool_harness import harness_router as HRT
                b = self._json()
                return self._send(200, HRT.route(b.get("text", ""), b.get("kind", "cowork")))
            if u.path == "/api/harness-login":
                from meshpool_harness import harnesses as HRS
                b = self._json()
                return self._send(200, HRS.login({"claude-subscription": "claude",
                                                  "chatgpt-subscription": "codex"}.get(b.get("id", ""), b.get("id", ""))))
            if u.path == "/api/import":
                from meshpool_harness import importer as IM
                r = IM.run(self._json().get("keys") or [])
                C.load_into_env(override=True)
                return self._send(200, dict(r, found=IM.scan()))
            if u.path == "/api/web-test":
                from meshpool_harness import web as WEB
                C.load_into_env(override=True)
                t0 = time.time()
                try:
                    rows = WEB.search("Meshpool AI", 5)
                    return self._send(200, {"ok": True, "count": len(rows), "seconds": round(time.time() - t0, 1),
                                            "first": rows[0]["title"] if rows else ""})
                except WEB.WebError as e:
                    return self._send(200, {"ok": False, "error": str(e)})
            if u.path == "/api/permissions":
                from meshpool_harness.ui.desktop import permissions, request_permission
                b = self._json()
                return self._send(200, request_permission(b["id"]) if b.get("id") else permissions())
            if u.path == "/api/install-cli":
                from meshpool_harness.ui.desktop import install_cli
                return self._send(200, install_cli())
            if u.path == "/api/agent":
                return self._send(200, A.create(self._json()))
            if u.path == "/api/mcp":
                return self._send(200, A.mcp_save(self._json()))
            if u.path == "/api/harnesses":
                return self._send(200, A.harness_change(self._json()))
            if u.path == "/api/harnesses/open":
                from meshpool_harness.ui.desktop import reveal
                from meshpool_harness.harnesses import ensure_defaults, folder
                ensure_defaults()
                folder().mkdir(parents=True, exist_ok=True)
                reveal(folder())
                return self._send(200, {"ok": True})
            if u.path == "/api/mcp/test":
                return self._send(200, A.mcp_test(self._json()))
            if u.path == "/api/models/favorite":
                b = self._json()
                return self._send(200, W.set_favorite(b.get("id", ""), bool(b.get("on"))))
            if u.path == "/api/models/default":
                C.write({"MH_MODEL": self._json().get("id", "").strip()})
                C.load_into_env(override=True)
                return self._send(200, W.models())
            if u.path == "/api/projects":
                b = self._json()
                act = b.get("action", "add")
                if act == "pick":
                    from meshpool_harness.ui.desktop import choose_folder
                    path = choose_folder(b.get("start", ""))
                    if not path:
                        return self._send(200, {"projects": W.projects(), "picked": ""})
                    W.add_project(path)
                    return self._send(200, {"projects": W.projects(), "picked": str(Path(path).resolve())})
                if act == "home":                     # the whole home folder ("the whole computer" for one user)
                    home = str(Path.home().resolve())
                    W.add_project(home, "Home")
                    return self._send(200, {"projects": W.projects(), "picked": home})
                if act == "add":
                    W.add_project(b.get("path", ""), b.get("name", ""))
                elif act == "remove":
                    W.remove_project(b.get("path", ""))
                elif act == "rename":
                    W.rename_project(b.get("path", ""), b.get("name", ""))
                return self._send(200, {"projects": W.projects()})
            if u.path == "/api/skills":
                from meshpool_harness import skills as SK
                b = self._json()
                act = b.get("action")
                if act == "create":
                    SK.create(b.get("name", ""), b.get("description", ""), b.get("body", ""))
                elif act == "update":
                    SK.update(b.get("id", ""), b.get("text", ""))
                elif act == "toggle":
                    SK.toggle(b.get("id", ""), bool(b.get("on")))
                elif act == "remove":
                    SK.remove(b.get("id", ""))
                elif act == "upload":
                    import base64
                    SK.add_files([(f["name"], base64.b64decode(f["data"])) for f in b.get("files") or []])
                elif act == "open":
                    from meshpool_harness.ui.desktop import reveal
                    SK.folder().mkdir(parents=True, exist_ok=True)
                    reveal(SK.folder())
                return self._send(200, {"skills": SK.load_all(), "folder": str(SK.folder())})
            if u.path == "/api/file":
                b = self._json()
                return self._send(200, W.write_file(b.get("root", ""), b.get("path", ""), b.get("text", "")))
            if u.path == "/api/git":
                b = self._json()
                return self._send(200, W.git_action(b.get("root", ""), b))
            if u.path == "/api/git/ci":
                b = self._json()
                return self._send(200, W.git_ci_action(b.get("root", ""), b))
            if u.path == "/api/open-file":
                from meshpool_harness.ui.desktop import reveal
                b = self._json()
                p = W._inside(W.check_root(b.get("root", "")), b.get("path", ""))
                if not p.exists():
                    raise ValueError("That file is gone.")
                reveal(p)
                return self._send(200, {"ok": True})
            if u.path == "/api/attach":
                import base64
                b = self._json()
                return self._send(200, W.attach(b.get("root", ""), b.get("name", ""), base64.b64decode(b.get("data", ""))))
            if u.path == "/api/connectors":
                from meshpool_harness import mcp_apps as CN
                b = self._json()
                act, cid = b.get("action"), b.get("id", "")
                if act == "connect":
                    redirect = f"http://127.0.0.1:{HTTPD[0].server_address[1]}/oauth/callback"
                    url = CN.begin(cid, redirect)
                    webbrowser.open(url)
                    return self._send(200, {"url": url, "connectors": CN.listing()})
                if act == "custom":
                    c = CN.add_custom(b.get("name", ""), b.get("url", ""), b.get("token", ""))
                    if c["auth"] == "oauth":
                        redirect = f"http://127.0.0.1:{HTTPD[0].server_address[1]}/oauth/callback"
                        url = CN.begin(c["id"], redirect)
                        webbrowser.open(url)
                        return self._send(200, {"url": url, "id": c["id"], "connectors": CN.listing()})
                    return self._send(200, {"id": c["id"], "connectors": CN.listing()})
                if act == "github_found":
                    r = CN.github_use_found()
                    C.load_into_env(override=True)
                    return self._send(200, {"connectors": CN.listing(), "message": f"Connected as {r['login']} ({r['source']})."})
                if act == "github_device":
                    r = CN.github_device_start()
                    webbrowser.open(r["url"])
                    return self._send(200, r)
                if act == "github_poll":
                    r = CN.github_device_poll()
                    C.load_into_env(override=True)
                    return self._send(200, dict(r, connectors=CN.listing()))
                if act == "token":
                    tok = b.get("token", "").strip()
                    if cid == "github" and tok and not CN._github_check(tok):
                        raise ValueError("GitHub did not accept that token. Check it and try again.")
                    CN.set_token(cid, tok)
                elif act == "enable":
                    CN.set_enabled(cid, bool(b.get("on")))
                elif act == "disconnect":
                    CN.disconnect(cid)
                C.load_into_env(override=True)
                return self._send(200, {"connectors": CN.listing()})
            if u.path == "/api/open-url":
                url = str(self._json().get("url", "")).strip()
                if not re.match(r"https?://", url, re.I):
                    raise ValueError("Only web addresses can be opened.")
                webbrowser.open(url)
                return self._send(200, {"ok": True})
            if u.path == "/api/open-editor":
                return self._send(200, W.open_editor(self._json().get("path", "")))
            if u.path == "/api/open-path":
                from meshpool_harness.ui.desktop import reveal
                reveal(W.check_root(self._json().get("path", "")))
                return self._send(200, {"ok": True})
            if u.path == "/api/term":
                b = self._json()
                return self._send(200, W.term_run(b.get("root", ""), b.get("command", "")))
            m = re.fullmatch(r"/api/term/(\w+)/stop", u.path)
            if m:
                return self._send(200, W.term_stop(m.group(1)))
            m = re.fullmatch(r"/api/threads/([\w-]+)", u.path)
            if m:
                return self._send(200, A.thread_action(m.group(1), self._json()))
            m = re.fullmatch(r"/api/agent/([\w-]+)/(model|plan|revert)", u.path)
            if m:
                s_, act, b = A.get(m.group(1)), m.group(2), self._json()
                if act == "model":
                    s_.set_model(b.get("model", ""))
                elif act == "plan":
                    s_.set_plan(bool(b.get("on")))
                else:
                    s_.revert(int(b.get("turn", 0)))
                return self._send(200, s_.view(int(b.get("since", 10**9))))
            m = re.fullmatch(r"/api/agent/([\w-]+)/(send|stop|answer|mode)", u.path)
            if m:
                s, act, b = A.get(m.group(1)), m.group(2), self._json()
                if act == "send":
                    r = s.send(b.get("text", ""), images=b.get("images") or None, interrupt=bool(b.get("interrupt")))
                    return self._send(200, dict(s.view(int(b.get("since", 10**9))), sent=r))
                elif act == "stop":
                    s.stop()
                elif act == "answer":
                    s.answer(int(b.get("id", 0)), b.get("decision", "no"), b.get("note", ""))
                else:
                    s.set_mode(b.get("mode", "ask"))
                return self._send(200, s.view(int(b.get("since", 10**9))))
            m = re.fullmatch(r"/api/jobs/([\w-]+)/(stop|open)", u.path)
            if m:
                return self._send(200, stop_job(m.group(1)) if m.group(2) == "stop" else open_folder(m.group(1)))
        except (ValueError, KeyError) as e:
            return self._send(400, {"error": str(e).strip("'")})
        except Exception as e:  # keep the app alive, show the cause
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        return self._send(404, {"error": "not found"})


def _busy() -> bool:
    try:
        from meshpool_harness import assistant as AS
        if any(t.get("enabled") for t in AS.tasks()):
            return True                              # scheduled tasks need the app to stay open
    except Exception:  # noqa: BLE001
        pass
    if any(m.get("status") == "running" for m in (_load_meta(j) or {} for j in list(JOBS))):
        return True
    return any(s.state in ("working", "waiting") for s in list(A.LIVE.values()))


def _shutdown():
    if HTTPD[0] is not None:
        threading.Thread(target=HTTPD[0].shutdown, daemon=True).start()
    for fn in list(ON_QUIT):
        try:
            fn()
        except Exception:
            pass


def _watchdog(idle_s: float):
    while True:
        time.sleep(30)
        if time.time() - LAST_PING[0] > idle_s and not _busy():
            _shutdown()
            return


def serve(port: int = 8765, open_browser: bool = True, desktop: bool = False, on_ready=None) -> int:
    global DESKTOP
    DESKTOP = desktop
    RUNS.mkdir(parents=True, exist_ok=True)
    try:
        from meshpool_harness.harnesses import ensure_defaults
        ensure_defaults()
    except Exception:
        pass
    httpd = None
    for p in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        print(f"Could not open a local port between {port} and {port + 19}.")
        return 1
    url = f"http://127.0.0.1:{port}/"
    HTTPD[0] = httpd
    if desktop:
        LAST_PING[0] = time.time()
        threading.Thread(target=_watchdog, args=(float(os.environ.get("MH_IDLE_EXIT_S", "1200")),), daemon=True).start()
    threading.Thread(target=A.scheduler_loop, daemon=True).start()
    threading.Thread(target=A.probe_mcp, daemon=True).start()
    if on_ready:
        on_ready(port)
    print("")
    print("  Meshpool is running.")
    print(f"  Open {url} in your browser if it did not open by itself.")
    print(f"  Settings file: {C.config_path()}")
    print(f"  Task outputs:  {RUNS}")
    print("  Use Quit in the app to stop Meshpool." if desktop else "  Close this window (or press Ctrl+C) to stop Meshpool.")
    print("")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        A.close_all()
        httpd.server_close()
    return 0


def _default_project() -> Path:
    from meshpool_harness.ui.desktop import default_project
    return default_project()
