"""Meshpool web app: the local server behind the desktop window and `meshpool ui`.

Standard library only. Binds to 127.0.0.1. Every API call must carry the per-launch token that is baked into the
page, so other websites open in the same browser cannot drive it. Chats run in ui/agent_api.py.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from meshpool_harness import config as C
from meshpool_harness import harnesses as HR
from meshpool_harness.ui import agent_api as A
from meshpool_harness.ui import workbench as W

APP = C.APP
HERE = Path(__file__).resolve().parent
RUNS = C.runs_dir()
UPLOADS = RUNS / "_uploads"
TOKEN = secrets.token_urlsafe(24)
UI_VER = secrets.token_hex(4)
DESKTOP = False
LAST_PING = [time.time()]
HTTPD = [None]
NATIVE = [False]                    # True while the interface is shown in the app's own window
ON_SHOW: list = []                  # called when a second launch asks this instance to come forward
ON_QUIT: list = []                  # called when the app quits (closes the native window)
LOCK = threading.Lock()


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
            "native": NATIVE[0], "platform": sys.platform, "shells": W.shells(), "user_home": str(Path.home()),
            "has_key": bool(vals.get("MESHPOOL_API_KEY") or os.environ.get("MESHPOOL_API_KEY")),
            "ready": HR.ready(), "provider": HR.provider(), "providers": HR.PROVIDER_NAMES,
            "signed_in": {p: HR.signed_in(w) for p, w in HR.LOGINS.items()}}


def save_settings(body: dict) -> dict:
    allowed = {f[0] for f in C.FIELDS}
    vals = {}
    for k, v in (body.get("values") or {}).items():
        if k not in allowed:
            continue
        v = re.sub(r"[\r\n]", "", str(v)).strip()
        if k in C.SECRET:                # accept a key the way people paste it: quoted, or with "Bearer " in front
            v = re.sub(r"^(?:bearer\s+)", "", v.strip("'\" "), flags=re.I)
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


def test_connection(values: dict | None = None) -> dict:
    """Check the saved key and endpoint, or (with values) a key and endpoint before they are saved."""
    C.load_into_env(override=True)
    from meshpool_harness.cli import ping
    keep = {k: os.environ.get(k) for k in ("MESHPOOL_API_KEY", "MESHPOOL_BASE_URL", "MH_MODEL")}
    try:
        for k, v in (values or {}).items():
            if k in keep and str(v).strip():
                os.environ[k] = str(v).strip()
        if not os.environ.get("MESHPOOL_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
            return {"ok": False, "detail": "No API key saved yet."}
        return ping()
    finally:
        if values:
            for k, v in keep.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def save_upload(name: str, data: bytes) -> dict:
    safe = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(name).name).strip() or "file"
    d = UPLOADS / uuid.uuid4().hex[:8]
    d.mkdir(parents=True, exist_ok=True)
    p = d / safe
    p.write_bytes(data)
    return {"path": str(p), "name": safe, "size": len(data)}


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

    def _company(self, u):
        from meshpool_harness.ui import company_api as CO
        try:
            return self._send(*CO.handle(self.command, u, self._body() if self.command == "POST" else b""))
        except (ValueError, KeyError) as e:
            return self._send(400, {"error": str(e).strip("'\"")})
        except Exception as e:  # noqa: BLE001 - keep the app alive, show the cause
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            html = (HERE / "index.html").read_text(encoding="utf-8").replace("__MESHPOOL_TOKEN__", TOKEN) \
                .replace("__MESHPOOL_DESKTOP__", "true" if DESKTOP else "false").replace("__MESHPOOL_VER__", UI_VER)
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8",
                              {"Content-Security-Policy": "frame-ancestors 'none'"})
        m = re.fullmatch(r"/ui/(app\.js|app\.css|company\.js)", u.path)
        if m:
            ctype = "text/javascript; charset=utf-8" if m.group(1).endswith(".js") else "text/css; charset=utf-8"
            return self._send(200, (HERE / m.group(1)).read_bytes(), ctype)
        if u.path == "/oauth/callback":          # the browser comes back here after signing in to a connector
            from meshpool_harness import mcp_apps as CN
            q = parse_qs(u.query)
            try:
                name = CN.finish(q.get("state", [""])[0], q.get("code", [None])[0], q.get("error", [None])[0])
                msg, ok = f"{name} is connected. You can close this tab and go back to Meshpool.", True
                cid = CN.LAST_FINISHED[0]
                r = CN.check(cid) if cid else {"ok": True, "tools": []}
                if r["ok"]:
                    msg = f"{name} is connected ({len(r['tools'])} tools). You can close this tab and go back to Meshpool."
                else:
                    msg, ok = f"{name} signed in, but the connection test failed: {r['error']}", False
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
        if u.path.startswith("/api/company"):        # Settings > Company, the company look for decks
            return self._company(u)
        try:
            if u.path == "/api/settings":
                return self._send(200, get_settings())
            if u.path == "/api/assistant":
                return self._send(200, assistant_view())
            if u.path == "/api/import":
                from meshpool_harness import importer as IM
                return self._send(200, IM.scan())
            if u.path == "/api/import-context":
                from meshpool_harness import importer as IM
                return self._send(200, {"prompt": IM.context_prompt()})
            if u.path == "/api/import-chats":         # Claude Code and Codex sessions found on this computer
                from meshpool_harness import chat_import as CI
                return self._send(200, CI.scan_local())
            if u.path == "/api/about":
                import meshpool_harness as MH

                def rd(name):
                    for base in (C.APP, C.APP.parent):      # packaged: beside the app; from source: the repo root
                        try:
                            return (base / name).read_text(encoding="utf-8")
                        except OSError:
                            continue
                    return ""
                return self._send(200, {"version": MH.__version__, "author": MH.__author__, "notice": rd("NOTICE"),
                                        "license": rd("LICENSE"), "third_party": rd("THIRD_PARTY_NOTICES.md")})
            if u.path == "/api/agent":
                return self._send(200, {"sessions": A.listing()})
            if u.path == "/api/mcp":
                return self._send(200, A.mcp_info())
            if u.path == "/api/mcp-library":
                from meshpool_harness import mcp_library as ML
                return self._send(200, ML.api_view())
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
            if u.path == "/api/plugins":
                from meshpool_harness import plugins as PL
                return self._send(200, PL.api_view())
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
                return self._send(200, A.view(m.group(1), since))
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
        if u.path.startswith("/api/company"):        # Settings > Company, the company look for decks
            return self._company(u)
        try:
            if u.path == "/api/settings":
                return self._send(200, save_settings(self._json()))
            if u.path == "/api/provider":            # the provider picked at sign-up or in Settings
                HR.use_provider((self._json() or {}).get("provider", ""))
                return self._send(200, get_settings())
            if u.path == "/api/test":
                return self._send(200, test_connection((self._json() or {}).get("values")))
            if u.path == "/api/update/check":
                from meshpool_harness import updater as UP
                return self._send(200, UP.check(force=bool(self._json().get("force"))))
            if u.path == "/api/update/install":
                from meshpool_harness import updater as UP
                if _running():
                    return self._send(200, {"ok": False, "error": "A task is still running. Update when it finishes."})
                return self._send(200, UP.install())
            if u.path == "/api/update/restart":
                from meshpool_harness import updater as UP
                self._send(200, {"ok": True})
                threading.Thread(target=UP.restart, args=([A.close_all, *ON_QUIT],), daemon=True).start()
                return
            if u.path == "/api/upload":
                name = unquote(parse_qs(u.query).get("name", ["file"])[0])
                return self._send(200, save_upload(name, self._body()))
            if u.path == "/api/ping":
                LAST_PING[0] = time.time()
                return self._send(200, {"ok": True})
            if u.path == "/api/quit":
                threading.Timer(0.3, _shutdown).start()
                return self._send(200, {"ok": True})
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
                hid = HRS.LEGACY.get(b.get("id", ""), b.get("id", ""))
                which = "codex" if hid == "codex" else HRS.LOGINS.get(hid, hid)
                return self._send(200, HRS.login(which))     # by harness id (codex) or by provider (chatgpt)
            if u.path == "/api/import-context":
                # context from ChatGPT, Claude or another assistant: pasted answer, pasted instructions, or an export
                from meshpool_harness import importer as IM
                b = self._json()
                if b.get("file"):
                    import base64
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tf:
                        tf.write(base64.b64decode(b["file"].get("data", "")))
                    try:
                        return self._send(200, IM.import_export(tf.name))
                    finally:
                        os.unlink(tf.name)
                if b.get("instructions"):
                    return self._send(200, {"skill": IM.import_instructions_as_skill(
                        b.get("name", ""), b["instructions"], b.get("description", ""))})
                return self._send(200, IM.import_context_text(b.get("text", "")))
            if u.path == "/api/import-file":          # a ChatGPT or Claude export, streamed to disk (they get large)
                from meshpool_harness import chat_import as CI
                import tempfile
                n = int(self.headers.get("Content-Length") or 0)
                if n > 8 * 1024 ** 3:
                    raise ValueError("That file is larger than 8 GB.")
                q = parse_qs(u.query)
                with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tf:
                    left = n
                    while left > 0:
                        chunk = self.rfile.read(min(left, 1 << 20))
                        if not chunk:
                            break
                        tf.write(chunk)
                        left -= len(chunk)
                want_ctx = q.get("context", ["1"])[0] != "0"
                try:
                    r = CI.import_export(tf.name, chats=q.get("chats", ["1"])[0] != "0", context=False)
                except Exception:
                    os.unlink(tf.name)
                    raise
                if not want_ctx:
                    os.unlink(tf.name)
                    return self._send(200, r)
                # The chats are in. Projects (as skills) and lasting facts (one model call) follow in the background
                # and land in Skills and in Instructions and memory a little later.
                from meshpool_harness import importer as IM

                def learn(path=tf.name):
                    try:
                        IM.import_export(path)
                    except Exception:  # noqa: BLE001
                        pass
                    finally:
                        os.unlink(path)
                threading.Thread(target=learn, daemon=True).start()
                return self._send(200, dict(r, context_pending=True))
            if u.path == "/api/import-chats":
                from meshpool_harness import chat_import as CI
                b = self._json()
                return self._send(200, dict(CI.import_local(b.get("sources") or None), found=CI.scan_local()))
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
                return self._send(200, install_cli(admin=True))
            if u.path == "/api/agent":
                return self._send(200, A.create(self._json()))
            if u.path == "/api/mcp":
                return self._send(200, A.mcp_save(self._json()))
            if u.path == "/api/mcp-library":
                from meshpool_harness import mcp_library as ML
                return self._send(200, ML.api_action(self._json()))
            if u.path == "/api/harnesses":
                return self._send(200, A.harness_change(self._json()))
            if u.path == "/api/mcp/test":
                return self._send(200, A.mcp_test(self._json()))
            if u.path == "/api/models/favorite":
                b = self._json()
                return self._send(200, W.set_favorite(b.get("id", ""), bool(b.get("on"))))
            if u.path == "/api/models/default":
                C.write({"MH_MODEL": self._json().get("id", "").strip()})   # may carry a provider prefix
                C.load_into_env(override=True)
                return self._send(200, W.models())
            if u.path == "/api/projects":
                b = self._json()
                act = b.get("action", "add")
                if act == "pick":
                    from meshpool_harness.ui.desktop import choose_folder, has_folder_picker
                    if not has_folder_picker():          # the UI asks the user to type or paste the path instead
                        return self._send(200, {"projects": W.projects(), "picked": "", "no_picker": True})
                    path = choose_folder(b.get("start", ""))
                    if not path:
                        return self._send(200, {"projects": W.projects(), "picked": ""})
                    W.add_project(path)
                    return self._send(200, {"projects": W.projects(), "picked": str(Path(path).resolve())})
                if act == "dirs":                     # the in-app folder browser: the folders inside one folder
                    here = Path(b.get("path") or Path.home()).expanduser()
                    try:
                        here = here.resolve()
                        dirs = sorted((d.name for d in here.iterdir() if d.is_dir() and not d.name.startswith(".")),
                                      key=str.lower)
                    except OSError as e:
                        raise ValueError(f"Can't open that folder ({e.strerror or e}).")
                    return self._send(200, {"path": str(here), "parent": str(here.parent) if here.parent != here else "",
                                            "dirs": dirs[:500], "home": str(Path.home())})
                if act == "home":                     # the whole home folder ("the whole computer" for one user)
                    home = str(Path.home().resolve())
                    W.add_project(home, "Home")
                    return self._send(200, {"projects": W.projects(), "picked": home})
                if act == "add":
                    added = W.add_project(b.get("path", ""), b.get("name", ""))
                    return self._send(200, {"projects": W.projects(), "picked": added["path"]})
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
            if u.path == "/api/plugins":
                from meshpool_harness import plugins as PL
                return self._send(200, PL.api_action(self._json()))
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
                if p.suffix.lower() in RUNNABLE:     # opening would run it, outside the approval for commands
                    reveal(p.parent)
                    return self._send(200, {"ok": True, "message": f"{p.name} is a script, so its folder opened "
                                                                   "instead. Ask the agent to run it."})
                try:
                    reveal(p)
                except OSError:
                    return self._send(400, {"error": f"There's no app on this computer to open {p.name}. It is at: {p}"})
                return self._send(200, {"ok": True})
            if u.path == "/api/attach":
                import base64
                b = self._json()
                return self._send(200, W.attach(b.get("root", ""), b.get("name", ""), base64.b64decode(b.get("data", ""))))
            if u.path == "/api/connectors":
                from meshpool_harness import mcp_apps as CN
                b = self._json()
                act, cid = b.get("action"), b.get("id", "")
                redirect = f"http://127.0.0.1:{HTTPD[0].server_address[1]}/oauth/callback"
                if act in ("connect", "client"):
                    if act == "client":                  # the user's own client, for apps that do not register others
                        CN.set_client(cid, b.get("client_id", ""), b.get("client_secret", ""))
                    try:
                        url = CN.begin(cid, redirect)
                    except CN.NeedsClient as e:
                        return self._send(200, {"needs_client": cid, "redirect": e.redirect, "message": str(e),
                                                "connectors": CN.listing()})
                    _open_web(url)
                    return self._send(200, {"url": url, "connectors": CN.listing()})
                if act == "check":
                    r = CN.check(cid)
                    return self._send(200, dict(r, connectors=CN.listing()))
                if act == "custom":
                    c = CN.add_custom(b.get("name", ""), b.get("url", ""), b.get("token", ""),
                                      b.get("client_id", ""), b.get("client_secret", ""))
                    n = len(c.get("tools") or [])
                    if c["auth"] == "oauth":
                        try:
                            url = CN.begin(c["id"], redirect)
                        except CN.NeedsClient as e:
                            return self._send(200, {"id": c["id"], "needs_client": c["id"], "redirect": e.redirect,
                                                    "message": str(e), "connectors": CN.listing()})
                        _open_web(url)
                        return self._send(200, {"url": url, "id": c["id"], "connectors": CN.listing()})
                    return self._send(200, {"id": c["id"], "tools": n, "connectors": CN.listing(),
                                            "message": f"Connected: {n} tool{'s' * (n != 1)}. New chats can use it."})
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
                _open_web(url)
                return self._send(200, {"ok": True})
            if u.path == "/api/open-editor":
                return self._send(200, W.open_editor(self._json().get("path", "")))
            if u.path == "/api/open-path":
                from meshpool_harness.ui.desktop import reveal
                target = W.check_root(self._json().get("path", ""))
                try:
                    reveal(target)
                except OSError as e:                      # no file manager here (e.g. a headless Linux machine)
                    return self._send(400, {"error": f"Could not open the folder on this computer ({e.strerror or e}). "
                                                     f"It is at: {target}"})
                return self._send(200, {"ok": True})
            if u.path == "/api/term":
                b = self._json()
                return self._send(200, W.term_run(b.get("root", ""), b.get("command", ""), b.get("shell", "")))
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
            m = re.fullmatch(r"/api/agent/([\w-]+)/reply", u.path)     # an answer to an ask_user question
            if m:
                s, b = A.get(m.group(1)), self._json()
                s.reply(b.get("question_id", ""), b.get("answers"))
                return self._send(200, s.view(int(b.get("since", 10**9))))
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
        except (ValueError, KeyError) as e:
            return self._send(400, {"error": str(e).strip("'")})
        except Exception as e:  # keep the app alive, show the cause
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        return self._send(404, {"error": "not found"})


# Files the operating system runs rather than shows when "opened"
RUNNABLE = {".py", ".pyw", ".js", ".jse", ".vbs", ".vbe", ".wsf", ".wsh", ".bat", ".cmd", ".ps1", ".exe", ".msi",
            ".com", ".scr", ".sh", ".command", ".app", ".jar", ".lnk", ".reg", ".hta", ".cpl", ".url", ".tool"}


def _open_web(url: str) -> None:
    """Open a sign-in page. The address comes from the app's server metadata: only ever a web page."""
    if not re.match(r"https?://", url or "", re.I):
        raise ValueError("That app's sign-in address is not a web page, so Meshpool did not open it.")
    webbrowser.open(url)


class _Server(ThreadingHTTPServer):
    # Windows' SO_REUSEADDR lets a second server bind a port already in use, so the free-port search never moved on
    allow_reuse_address = os.name != "nt"


def _running() -> bool:
    return any(s.state in ("working", "waiting") for s in list(A.LIVE.values()))


def _busy() -> bool:
    """Keep the app open: a chat is running, or a scheduled task needs the app to fire it."""
    try:
        from meshpool_harness import assistant as AS
        if any(t.get("enabled") for t in AS.tasks()):
            return True
    except Exception:  # noqa: BLE001
        pass
    return _running()


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
            httpd = _Server(("127.0.0.1", p), Handler)
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
        threading.Timer(0.8, lambda: _open_web(url)).start()
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
