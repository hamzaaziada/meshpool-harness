"""The app's workbench services: projects (folders on this computer), models from the Meshpool endpoint, the file
browser, Git, a terminal, and usage totals. server.py exposes them as /api/... routes."""
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
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from meshpool_harness import config as C

NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ======================================== projects ================================================================
def _projects_file() -> Path:
    return C.home() / "projects.json"


def projects() -> list[dict]:
    try:
        rows = json.loads(_projects_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []
    out, seen = [], set()
    for r in rows:
        p = str(Path(r.get("path", "")).expanduser())
        if p in seen:
            continue
        seen.add(p)
        out.append({"path": p, "name": r.get("name") or Path(p).name or p, "added": r.get("added", 0),
                    "exists": Path(p).is_dir()})
    return out


def _save_projects(rows: list[dict]):
    _projects_file().parent.mkdir(parents=True, exist_ok=True)
    _projects_file().write_text(json.dumps([{k: r[k] for k in ("path", "name", "added")} for r in rows], indent=1),
                                encoding="utf-8")


def add_project(path: str, name: str = "") -> dict:
    p = Path(path.strip().strip('"')).expanduser()
    if not p.is_dir():
        raise ValueError(f"Folder not found: {path}")
    p = p.resolve()
    rows = projects()
    for r in rows:
        if r["path"] == str(p):
            return r
    r = {"path": str(p), "name": name or p.name or str(p), "added": time.time(), "exists": True}
    rows.insert(0, r)
    _save_projects(rows)
    return r


def remove_project(path: str):
    _save_projects([r for r in projects() if r["path"] != path])


def rename_project(path: str, name: str):
    rows = projects()
    for r in rows:
        if r["path"] == path:
            r["name"] = name.strip() or r["name"]
    _save_projects(rows)


def known_roots() -> list[Path]:
    from meshpool_harness.ui import agent_api as A
    from meshpool_harness.ui.desktop import default_project
    roots = [Path(r["path"]) for r in projects()]
    d = default_project()
    try:
        d.mkdir(parents=True, exist_ok=True)       # the built-in "Meshpool Projects" folder is always usable
        roots.append(d)
    except OSError:
        pass
    with A.LOCK:
        roots += [Path(s.workdir) for s in A.LIVE.values()]
    return roots


def check_root(root: str) -> Path:
    """Only folders the user added as projects (or a live session's folder) are browsable from the page."""
    p = Path(root).expanduser().resolve()
    if not any(p == r.resolve() for r in known_roots()):
        raise ValueError("That folder is not one of your projects.")
    return p


def _inside(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if p != root and root not in p.parents:
        raise ValueError("Path is outside the project.")
    return p


# ======================================== models ==================================================================
_MODELS: dict = {"t": 0, "key": "", "rows": [], "error": ""}


_VENDORS = {"openai": "OpenAI", "anthropic": "Anthropic", "google": "Google", "deepseek": "DeepSeek",
            "qwen": "Qwen", "meta-llama": "Meta", "meta": "Meta", "mistralai": "Mistral", "mistral": "Mistral",
            "x-ai": "xAI", "xai": "xAI", "moonshotai": "Moonshot", "moonshot": "Moonshot", "z-ai": "Zhipu",
            "zhipu": "Zhipu", "minimax": "MiniMax", "amazon": "Amazon", "cohere": "Cohere", "nvidia": "NVIDIA"}


def _provider_of(mid: str, owned: str) -> str:
    if "/" in mid:
        v = mid.split("/", 1)[0]
        return _VENDORS.get(v.lower(), v)
    m = mid.lower()
    for k, v in (("gpt", "OpenAI"), ("o1", "OpenAI"), ("o3", "OpenAI"), ("o4", "OpenAI"), ("claude", "Anthropic"),
                 ("gemini", "Google"), ("gemma", "Google"), ("deepseek", "DeepSeek"), ("qwen", "Qwen"),
                 ("llama", "Meta"), ("mistral", "Mistral"), ("codestral", "Mistral"), ("grok", "xAI"),
                 ("kimi", "Moonshot"), ("glm", "Zhipu"), ("minimax", "MiniMax"), ("nova", "Amazon")):
        if m.startswith(k):
            return v
    if owned and owned not in ("system", "organization-owner", "user"):
        return _VENDORS.get(owned.lower(), owned)
    return "Other"


def models(refresh: bool = False) -> dict:
    C.load_into_env(override=True)
    base = (os.environ.get("MESHPOOL_BASE_URL") or "https://llm.meshpool.ai/v1").rstrip("/")
    key = os.environ.get("MESHPOOL_API_KEY") or ""
    cache_key = base + "|" + key[-6:]
    vals = C.read()
    fav = [m for m in (vals.get("MH_FAVORITE_MODELS") or "").split(",") if m]
    default = vals.get("MH_MODEL") or os.environ.get("MH_MODEL") or "deepseek-v4.1-flash"
    if not refresh and _MODELS["key"] == cache_key and time.time() - _MODELS["t"] < 300 and _MODELS["rows"]:
        rows, err = _MODELS["rows"], ""
    elif not key:
        rows, err = [], "Add your API key to see your models."
    else:
        rows, err = [], ""
        try:
            req = urllib.request.Request(base + "/models", headers={"Authorization": f"Bearer {key}",
                                                                    "User-Agent": "Meshpool"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8"))
            items = data.get("data") if isinstance(data, dict) else data
            for it in items or []:
                mid = it.get("id") if isinstance(it, dict) else str(it)
                if not mid:
                    continue
                caps = []
                meta = it if isinstance(it, dict) else {}
                arch = meta.get("architecture") or {}
                mods = (arch.get("input_modalities") or meta.get("input_modalities") or meta.get("modalities") or [])
                if "image" in mods or re.search(r"vision|-vl\b|-vl-|gpt-4o|gpt-4\.1|gpt-5|claude|gemini|llava|pixtral", mid, re.I):
                    caps.append("vision")
                if re.search(r"reason|thinking|-r1\b|o1|o3|o4|deepseek-r|qwq", mid, re.I) or meta.get("reasoning"):
                    caps.append("reasoning")
                rows.append({"id": mid, "name": meta.get("name") or mid,
                             "provider": _provider_of(mid, meta.get("owned_by", "")),
                             "context": meta.get("context_length") or meta.get("context_window"), "caps": caps})
            rows.sort(key=lambda r: (r["provider"].lower(), r["id"].lower()))
            _MODELS.update(t=time.time(), key=cache_key, rows=rows, error="")
        except urllib.error.HTTPError as e:
            err = f"The endpoint answered HTTP {e.code} when listing models." + (
                " Check your API key." if e.code in (401, 403) else "")
        except Exception as e:  # noqa: BLE001
            err = f"Could not list models: {e}"
    from meshpool_harness import providers as PV
    dprov, dbare = PV.split(default)
    ids = {r["id"] for r in rows}
    if dprov == "key" and dbare not in ids and key:
        rows = [{"id": dbare, "name": dbare, "provider": _provider_of(dbare, ""), "caps": [],
                 "context": None, "configured": True}] + rows
    # One section per provider (the key endpoint, ChatGPT, Claude, Gemini), ready or with what to set up.
    groups = PV.groups(rows, err, refresh=refresh)
    flat = [m for g in groups for m in g["models"]]
    return {"models": flat, "groups": groups, "favorites": fav, "default": default,
            "error": "" if any(g["ready"] for g in groups) else err, "base_url": base}


def set_favorite(mid: str, on: bool) -> dict:
    vals = C.read()
    fav = [m for m in (vals.get("MH_FAVORITE_MODELS") or "").split(",") if m]
    fav = [m for m in fav if m != mid] + ([mid] if on else [])
    C.write({"MH_FAVORITE_MODELS": ",".join(fav)})
    return models()


# ======================================== files ===================================================================
HIDE = {".git", "node_modules", "__pycache__", ".venv", "venv", ".DS_Store", ".mypy_cache", ".pytest_cache", ".next"}


def list_dir(root: str, rel: str = "") -> dict:
    r = check_root(root)
    d = _inside(r, rel)
    if not d.is_dir():
        raise ValueError("Not a folder.")
    rows = []
    try:
        for p in sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if p.name in HIDE:
                continue
            try:
                rows.append({"name": p.name, "path": p.relative_to(r).as_posix(), "dir": p.is_dir(),
                             "size": p.stat().st_size if p.is_file() else None})
            except OSError:
                continue
    except PermissionError:
        raise ValueError(_perm_hint(d))
    return {"root": str(r), "path": rel, "entries": rows[:2000]}


def _perm_hint(p: Path) -> str:
    if sys.platform == "darwin":
        return (f"macOS did not let Meshpool read {p}. Allow it when macOS asks, or turn on Full Disk Access for "
                "Meshpool in Settings, Permissions.")
    return f"Meshpool cannot read {p}: permission denied."


def read_file(root: str, rel: str) -> dict:
    r = check_root(root)
    p = _inside(r, rel)
    if not p.is_file():
        raise ValueError("Not a file.")
    size = p.stat().st_size
    from meshpool_harness import docread
    if docread.is_document(p) and p.suffix.lower() not in (".html", ".htm", ".csv", ".tsv", ".ipynb"):
        if size > 60_000_000:
            return {"path": rel, "binary": True, "size": size, "text": "", "note": "File is larger than 60 MB."}
        return {"path": rel, "binary": False, "readonly": True, "size": size,
                "text": docread.extract(p, max_chars=400_000), "note": "Preview of the document's text"}
    if size > 2_000_000:
        return {"path": rel, "binary": True, "size": size, "text": "", "note": "File is larger than 2 MB."}
    try:
        data = p.read_bytes()
    except PermissionError:
        raise ValueError(_perm_hint(p))
    if b"\0" in data[:8000]:
        return {"path": rel, "binary": True, "size": size, "text": ""}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:                   # editing would rewrite every non-UTF-8 byte: show it read-only
        return {"path": rel, "binary": False, "readonly": True, "size": size, "text": data.decode("utf-8", "replace"),
                "note": "Not UTF-8 text, so it is read-only here. Open it in your editor to change it."}
    return {"path": rel, "binary": False, "size": size, "text": text}


def write_file(root: str, rel: str, text: str) -> dict:
    r = check_root(root)
    p = _inside(r, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = text.replace("\r\n", "\n")
    try:                                         # keep the file's line endings: a one-line edit is a one-line diff
        if b"\r\n" in p.read_bytes()[:65536]:
            text = text.replace("\n", "\r\n")
    except OSError:
        pass
    p.write_bytes(text.encode("utf-8"))
    return {"ok": True, "path": rel, "size": p.stat().st_size}


# ======================================== git =====================================================================
def attach(root: str, name: str, data: bytes) -> dict:
    """Save a file the user attached in the message box into the project, without overwriting anything."""
    r = check_root(root)
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", Path(name or "file").name).strip(". ") or "file"
    if len(data) > 150 * 1024 * 1024:
        raise ValueError("Attachments can be up to 150 MB. Put bigger files in the project folder instead.")
    p = r / name
    stem, suf, i = p.stem, p.suffix, 2
    while p.exists():
        p = r / f"{stem} ({i}){suf}"
        i += 1
    p.write_bytes(data)
    return {"path": p.name}


def git_exe() -> str | None:
    """A Git that will not pop up the macOS 'install developer tools' dialog."""
    if os.name == "nt":
        return shutil.which("git")
    for c in ("/opt/homebrew/bin/git", "/usr/local/bin/git"):
        if os.path.exists(c):
            return c
    g = shutil.which("git")
    if not g:
        return None
    if sys.platform == "darwin" and g == "/usr/bin/git":
        r = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True)
        if r.returncode != 0:
            return None
    return g


def _git(root: Path, *args, check=True, timeout=60) -> str:
    g = git_exe()
    if not g:
        raise ValueError("Git is not installed." + (" On a Mac, run: xcode-select --install" if sys.platform == "darwin" else ""))
    r = subprocess.run([g, *args], cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, creationflags=NO_WINDOW, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    if check and r.returncode != 0:
        raise ValueError((r.stderr or r.stdout).strip()[-800:] or f"git {args[0]} failed")
    return r.stdout


def git_status(root: str) -> dict:
    r = check_root(root)
    if not git_exe():
        return {"repo": False, "git": False}
    try:
        top = _git(r, "rev-parse", "--show-toplevel").strip()
    except ValueError:
        return {"repo": False, "git": True}
    branch = _git(r, "branch", "--show-current", check=False).strip() or "(detached)"
    porcelain = _git(r, "status", "--porcelain", check=False)
    files = [{"status": line[:2].strip(), "path": line[3:]} for line in porcelain.splitlines() if line.strip()]
    ahead = behind = 0
    try:
        ab = _git(r, "rev-list", "--left-right", "--count", "@{upstream}...HEAD").split()
        behind, ahead = int(ab[0]), int(ab[1])
        upstream = True
    except (ValueError, IndexError):
        upstream = False
    remote = _git(r, "remote", "get-url", "origin", check=False).strip()
    branches = [b.strip().lstrip("* ").strip() for b in _git(r, "branch", "--format=%(refname:short)", check=False).splitlines() if b.strip()]
    return {"repo": True, "git": True, "top": top, "branch": branch, "files": files[:500], "dirty": len(files),
            "ahead": ahead, "behind": behind, "upstream": upstream, "remote": remote, "branches": branches[:200],
            "github": _github_slug(remote)}


def _github_slug(remote: str) -> str:
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?$", remote or "")
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def git_action(root: str, body: dict) -> dict:
    r = check_root(root)
    act = body.get("action")
    if act == "init":
        _git(r, "init")
    elif act == "checkout":
        name = body.get("branch", "").strip()
        if not name:
            raise ValueError("Give a branch name.")
        if name.startswith("-") or subprocess.run(["git", "check-ref-format", "--branch", name], cwd=str(r),
                                                  capture_output=True, creationflags=NO_WINDOW).returncode:
            raise ValueError(f"\"{name}\" is not a valid branch name.")
        if body.get("create"):
            _git(r, "switch", "-c", name)
        else:
            _git(r, "switch", name)
    elif act == "commit":
        msg = (body.get("message") or "").strip()
        if not msg:
            raise ValueError("Write a commit message.")
        _git(r, "add", "-A")
        _git(r, "-c", "user.name=" + (_git(r, "config", "user.name", check=False).strip() or "Meshpool"),
             "-c", "user.email=" + (_git(r, "config", "user.email", check=False).strip() or "meshpool@localhost"),
             "commit", "-m", msg)
    elif act == "push":
        branch = _git(r, "branch", "--show-current").strip()
        _git(r, "push", "-u", "origin", branch, timeout=180)
    elif act == "pull":
        _git(r, "pull", "--ff-only", timeout=180)
    elif act == "pr":
        return _open_pr(r, body)
    else:
        raise ValueError("Unknown Git action.")
    return git_status(str(r))


def _open_pr(r: Path, body: dict) -> dict:
    st = git_status(str(r))
    slug = st.get("github")
    if not slug:
        raise ValueError("Pull requests work with GitHub remotes. This project's origin is not on GitHub.")
    title = (body.get("title") or "").strip() or _git(r, "log", "-1", "--pretty=%s").strip()
    desc = body.get("body") or ""
    if st["ahead"] or not st["upstream"]:
        _git(r, "push", "-u", "origin", st["branch"], timeout=180)
    gh = shutil.which("gh")
    from meshpool_harness import mcp_apps as CN
    token = CN.github_token()
    if token:
        req = urllib.request.Request(f"https://api.github.com/repos/{slug}", headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "Meshpool"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            base = json.loads(resp.read())["default_branch"]
        data = json.dumps({"title": title, "head": st["branch"], "base": body.get("base") or base,
                           "body": desc}).encode()
        req = urllib.request.Request(f"https://api.github.com/repos/{slug}/pulls", data=data, method="POST", headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "Meshpool",
            "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                pr = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise ValueError(f"GitHub said: {e.read().decode('utf-8', 'replace')[:400]}")
        return st | {"pr": pr.get("html_url")}
    if gh:
        out = subprocess.run([gh, "pr", "create", "--title", title, "--body", desc or title], cwd=str(r),
                             capture_output=True, text=True, timeout=120, creationflags=NO_WINDOW)
        if out.returncode != 0:
            raise ValueError(out.stderr.strip()[-600:])
        return st | {"pr": out.stdout.strip().splitlines()[-1]}
    return st | {"pr": f"https://github.com/{slug}/compare/{st['branch']}?expand=1",
                 "note": "Connect GitHub (Settings, Connectors) to open pull requests from Meshpool. This link opens the form."}


# ======================================== terminal ================================================================
TERMS: dict[str, dict] = {}


SHELLS_WIN = {"powershell": "PowerShell", "cmd": "Command Prompt", "bash": "Git Bash"}


def shells() -> dict:
    """The shells the Terminal panel offers on this computer, and the default."""
    if os.name == "nt":
        have = {k: v for k, v in SHELLS_WIN.items() if k != "bash" or (os.environ.get("MH_BASH")
                                                                        and os.path.exists(os.environ["MH_BASH"]))}
        return {"shells": have, "default": "powershell"}
    sh = os.environ.get("SHELL") or "/bin/bash"
    return {"shells": {"login": Path(sh).name if os.path.exists(sh) else "sh"}, "default": "login"}


def _shell_cmd(command: str, shell: str = "", nt: bool | None = None) -> list[str]:
    nt = os.name == "nt" if nt is None else nt
    if nt:
        bash = os.environ.get("MH_BASH")
        shell = shell or "powershell"
        if shell == "bash" and bash and os.path.exists(bash):
            return [bash, "-lc", command]
        if shell == "cmd":
            return ["cmd", "/d", "/s", "/c", command]
        # PowerShell, with UTF-8 output so accents and symbols come through
        return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command]
    sh = os.environ.get("SHELL") or "/bin/bash"
    if not os.path.exists(sh):
        sh = "/bin/sh"
    return [sh, "-lc", command]


def term_run(root: str, command: str, shell: str = "") -> dict:
    r = check_root(root)
    if not command.strip():
        raise ValueError("Type a command.")
    tid = uuid.uuid4().hex[:8]
    t = {"id": tid, "cwd": str(r), "command": command, "out": [], "base": 0, "rc": None, "start": time.time(),
         "p": None, "lock": threading.Lock()}
    TERMS[tid] = t
    env = dict(C.user_env(), TERM="dumb", FORCE_COLOR="0", NO_COLOR="1")
    p = subprocess.Popen(_shell_cmd(command, shell), cwd=str(r), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, env=env, creationflags=NO_WINDOW,
                         **({} if os.name == "nt" else {"start_new_session": True}))
    t["p"] = p

    def pump():
        for line in iter(p.stdout.readline, b""):
            with t["lock"]:
                t["out"].append(line.decode("utf-8", "replace"))
                if len(t["out"]) > 5000:             # keep the last lines; "base" keeps every line's number stable
                    del t["out"][:1000]
                    t["base"] += 1000
        rc = p.wait()
        with t["lock"]:
            t["rc"] = rc
    threading.Thread(target=pump, daemon=True).start()
    return term_view(tid, 0)


def term_view(tid: str, since: int) -> dict:
    t = TERMS.get(tid)
    if not t:
        raise KeyError(tid)
    with t["lock"]:                              # lines and exit code from the same moment
        start = max(since, t["base"])
        out = t["out"][start - t["base"]:]
        rc = t["rc"]
    return {"id": tid, "command": t["command"], "cwd": t["cwd"], "rc": rc, "lines": out, "next": start + len(out),
            "running": rc is None}


def term_stop(tid: str) -> dict:
    t = TERMS.get(tid)
    if t and t["p"] and t["rc"] is None:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(t["p"].pid)], capture_output=True,
                               creationflags=NO_WINDOW)
            else:
                import signal
                os.killpg(os.getpgid(t["p"].pid), signal.SIGTERM)
        except Exception:  # noqa: BLE001
            t["p"].kill()
    return term_view(tid, 0) if t else {}


# ======================================== usage ===================================================================
def usage() -> dict:
    from meshpool_harness.agent.core import SESSIONS
    rows, total = [], {"in": 0, "cached": 0, "out": 0, "calls": 0}
    by_model: dict[str, dict] = {}
    for p in SESSIONS.glob("*/session.json") if SESSIONS.exists() else []:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        u = d.get("usage") or {}
        m = d.get("model") or d.get("harness") or "unknown"
        for k in total:
            total[k] += int(u.get(k) or 0)
        bm = by_model.setdefault(m, {"in": 0, "cached": 0, "out": 0, "calls": 0, "threads": 0})
        for k in ("in", "cached", "out", "calls"):
            bm[k] += int(u.get(k) or 0)
        bm["threads"] += 1
        rows.append({"id": d.get("id"), "title": d.get("title") or "New chat", "model": m,
                     "updated": d.get("updated", 0), **{k: int(u.get(k) or 0) for k in total}})
    rows.sort(key=lambda r: -r["updated"])
    return {"total": total, "by_model": by_model, "threads": rows[:200]}


EDITORS = [("Cursor", "cursor", "Cursor"), ("Visual Studio Code", "code", "Visual Studio Code"), ("Windsurf", "windsurf", "Windsurf"),
           ("Zed", "zed", "Zed"), ("Sublime Text", "subl", "Sublime Text"), ("PyCharm", "pycharm", "PyCharm"),
           ("IntelliJ IDEA", "idea", "IntelliJ IDEA")]


def open_editor(root: str) -> dict:
    """Open a project folder in the first code editor found on this computer."""
    r = check_root(root)
    for name, cli, mac_app in EDITORS:
        exe = shutil.which(cli)
        if exe:
            subprocess.Popen([exe, str(r)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             **({"creationflags": 0x08000000} if os.name == "nt" else {}))
            return {"ok": True, "message": f"Opened in {name}."}
        if sys.platform == "darwin":
            for base in (Path("/Applications"), Path.home() / "Applications"):
                if (base / f"{mac_app}.app").exists():
                    subprocess.Popen(["open", "-a", mac_app, str(r)])
                    return {"ok": True, "message": f"Opened in {name}."}
        if os.name == "nt":
            la = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs"
            for exe in (la / "Microsoft VS Code" / "Code.exe", la / "cursor" / "Cursor.exe"):
                if exe.exists():
                    subprocess.Popen([str(exe), str(r)], creationflags=0x08000000)
                    return {"ok": True, "message": f"Opened in {exe.stem}."}
    raise ValueError("No code editor found. Install VS Code, Cursor or another editor, or open the folder yourself.")


# ======================================== pull request, CI and deployments =======================================

def _gh_api(path: str, token: str, method: str = "GET", body: dict | None = None):
    h = {"Accept": "application/vnd.github+json", "User-Agent": "Meshpool", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request("https://api.github.com" + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")[:300]
        if e.code in (401, 403, 404) and not token:
            raise ValueError("Connect GitHub (Settings, Connectors) to see pull requests and checks for this repository.")
        raise ValueError(f"GitHub said ({e.code}): {msg}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ValueError(f"Could not reach GitHub: {e}")


def git_ci(root: str) -> dict:
    """The current branch's pull request, its CI checks and workflow runs, and recent deployments."""
    from meshpool_harness import mcp_apps as CN
    st = git_status(root)
    if not st.get("repo"):
        return {"available": False, "note": "This folder is not a Git repository."}
    slug = st.get("github")
    if not slug:
        return {"available": False, "note": "Checks and deployments show for repositories on GitHub."}
    token = CN.github_token()
    r = check_root(root)
    sha = _git(r, "rev-parse", "HEAD", check=False).strip()
    owner = slug.split("/")[0]
    out = {"available": True, "slug": slug, "branch": st["branch"], "sha": sha[:7], "connected": bool(token),
           "pr": None, "checks": [], "runs": [], "deployments": [],
           "repo_url": f"https://github.com/{slug}"}
    try:
        prs = _gh_api(f"/repos/{slug}/pulls?head={owner}:{urllib.parse.quote(st['branch'])}&state=all&per_page=1", token)
    except ValueError as e:
        out["error"] = str(e)
        prs = []
    if prs:
        p = prs[0]
        out["pr"] = {"number": p["number"], "title": p["title"], "url": p["html_url"],
                     "state": "merged" if p.get("merged_at") else p["state"], "draft": p.get("draft", False),
                     "base": p["base"]["ref"], "author": (p.get("user") or {}).get("login", "")}
        sha = p["head"]["sha"] or sha
    if sha:
        try:
            cr = _gh_api(f"/repos/{slug}/commits/{sha}/check-runs?per_page=50", token)
            out["checks"] = [{"name": c["name"], "status": c["status"], "conclusion": c.get("conclusion"),
                              "url": c.get("html_url") or c.get("details_url"),
                              "app": (c.get("app") or {}).get("name", "")} for c in cr.get("check_runs") or []]
        except ValueError:
            pass
        try:
            cs = _gh_api(f"/repos/{slug}/commits/{sha}/status", token)
            out["checks"] += [{"name": x["context"], "status": "completed" if x["state"] != "pending" else "in_progress",
                               "conclusion": {"success": "success", "failure": "failure", "error": "failure"}.get(x["state"]),
                               "url": x.get("target_url"), "app": "status"} for x in cs.get("statuses") or []]
        except ValueError:
            pass
    try:
        runs = _gh_api(f"/repos/{slug}/actions/runs?branch={urllib.parse.quote(st['branch'])}&per_page=6", token)
        out["runs"] = [{"id": w["id"], "name": w.get("name") or w.get("display_title"), "status": w["status"],
                        "conclusion": w.get("conclusion"), "url": w["html_url"], "event": w.get("event"),
                        "created": w.get("created_at")} for w in runs.get("workflow_runs") or []]
    except ValueError:
        pass
    try:
        deps = _gh_api(f"/repos/{slug}/deployments?per_page=6", token)
        for d in deps:
            state, url = "unknown", ""
            try:
                sts = _gh_api(f"/repos/{slug}/deployments/{d['id']}/statuses?per_page=1", token)
                if sts:
                    state, url = sts[0].get("state", "unknown"), sts[0].get("environment_url") or sts[0].get("target_url") or ""
            except ValueError:
                pass
            out["deployments"].append({"environment": d.get("environment"), "ref": d.get("ref"), "state": state,
                                       "url": url, "created": d.get("created_at"), "sha": (d.get("sha") or "")[:7]})
    except ValueError:
        pass
    fails = [c for c in out["checks"] if c["conclusion"] in ("failure", "timed_out", "cancelled", "action_required")]
    running = [c for c in out["checks"] if c["status"] != "completed"]
    out["summary"] = "failing" if fails else "running" if running else "passing" if out["checks"] else "none"
    return out


def git_ci_action(root: str, body: dict) -> dict:
    from meshpool_harness import mcp_apps as CN
    st = git_status(root)
    slug = st.get("github")
    token = CN.github_token()
    if not (slug and token):
        raise ValueError("Connect GitHub (Settings, Connectors) first.")
    act = body.get("action")
    if act == "rerun":
        _gh_api(f"/repos/{slug}/actions/runs/{int(body['run'])}/rerun-failed-jobs", token, "POST", {})
    elif act == "merge":
        _gh_api(f"/repos/{slug}/pulls/{int(body['pr'])}/merge", token, "PUT", {"merge_method": body.get("method") or "squash"})
    elif act == "ready":
        raise ValueError("Mark the pull request ready for review on GitHub.")
    else:
        raise ValueError("Unknown action.")
    return git_ci(root)
