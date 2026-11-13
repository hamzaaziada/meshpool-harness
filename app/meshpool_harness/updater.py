"""Update Meshpool from its GitHub releases, from inside the app.

check()   -> the latest release and whether it is newer than this copy
install() -> brings this copy up to that release, the safest way this kind of install allows:
  - command-line install (~/.meshpool-cli) and Windows app: the release's app code replaces this copy's code in place
    (the bundled runtimes, Pi and Hermes are kept); new Python packages, if the release lists any, are installed for
    the command-line copy, while the Windows app gets the full installer instead;
  - Mac app: downloads the release's disk image and opens it (changing files inside a signed Mac app breaks its
    signature, so the Mac app is always replaced whole);
  - a developer checkout (a git clone): nothing is changed; it says to run git pull.
restart() -> starts a fresh copy of the app and closes this one, so the new code runs.

Nothing here runs on its own: the page calls check() at start (at most once every 6 hours) and install() only when
the user presses Update.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

import meshpool_harness as MH
from meshpool_harness import config as C

REPO = os.environ.get("MESHPOOL_REPO", "hamzaaziada/meshpool-harness")
API = os.environ.get("MESHPOOL_UPDATE_API") or f"https://api.github.com/repos/{REPO}/releases/latest"
SRC = os.environ.get("MESHPOOL_UPDATE_SRC") or f"https://codeload.github.com/{REPO}/tar.gz/refs/tags/{{tag}}"
APP = C.APP                                  # the folder that holds meshpool_harness/
CODE_DIRS = ("meshpool_harness", "engines", "profiles", "defaults")   # what a code update replaces; vendor/ is kept
_CACHE: dict = {}


def _ver(s: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:3]) or (0,)


def _get(url: str, timeout: float = 20):
    req = urllib.request.Request(url, headers={"User-Agent": "meshpool-updater", "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def kind() -> str:
    """How this copy was installed: dev, mac-app, win-app or cli."""
    if (APP.parent / ".git").exists():
        return "dev"
    if C.packaged():
        return "mac-app" if sys.platform == "darwin" else "win-app"
    return "cli"


def check(force: bool = False) -> dict:
    if not force and _CACHE.get("t", 0) > time.time() - 6 * 3600:
        return _CACHE["v"]
    cur = MH.__version__
    try:
        with _get(API) as r:
            rel = json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "current": cur, "error": f"Could not reach GitHub to check for updates ({type(e).__name__})."}
    tag = rel.get("tag_name") or ""
    v = {"ok": True, "current": cur, "latest": tag.lstrip("v"), "tag": tag, "newer": _ver(tag) > _ver(cur),
         "notes": (rel.get("body") or "")[:4000], "url": rel.get("html_url") or f"https://github.com/{REPO}/releases",
         "published": rel.get("published_at"), "kind": kind(),
         "assets": [{"name": a.get("name"), "url": a.get("browser_download_url"), "size": a.get("size")}
                    for a in rel.get("assets") or []]}
    _CACHE.update(t=time.time(), v=v)
    return v


def _asset(v: dict) -> dict | None:
    names = [a for a in v.get("assets") or [] if a.get("url")]
    if sys.platform == "darwin":
        intel = platform.machine() == "x86_64"
        dmgs = [a for a in names if a["name"].lower().endswith(".dmg")]
        pick = [a for a in dmgs if ("intel" in a["name"].lower()) == intel]
        return (pick or dmgs or [None])[0]
    if os.name == "nt":
        return next((a for a in names if a["name"].lower().endswith(".exe")), None)
    return None


def _download(url: str, dst: Path) -> Path:
    with _get(url, timeout=60) as r, open(dst, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    return dst


def _open(p: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    elif os.name == "nt":
        os.startfile(str(p))  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(p)])


def _installer(v: dict, why: str) -> dict:
    a = _asset(v)
    if not a:
        return {"ok": False, "error": f"{why} This release has no installer for this computer. Download it from the "
                                      f"release page.", "url": v["url"]}
    d = Path.home() / "Downloads"
    d = d if d.is_dir() else Path(tempfile.gettempdir())
    p = _download(a["url"], d / a["name"])
    _open(p)
    step = ("Drag Meshpool into Applications and replace the old copy, then open it again."
            if sys.platform == "darwin" else "Follow the installer; it replaces this copy.")
    return {"ok": True, "action": "installer", "file": str(p),
            "message": f"Downloaded Meshpool {v['latest']} and opened the installer. {why} {step}"}


def _swap(src: Path, dst: Path) -> None:
    """Replace dst with src. A rename is atomic; if Windows holds the folder open, copy over it instead."""
    old = dst.with_name(dst.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    try:
        if dst.exists():
            dst.rename(old)
        src.rename(dst)
    except OSError:
        if old.exists() and not dst.exists():
            old.rename(dst)
        shutil.copytree(src, dst, dirs_exist_ok=True)
    shutil.rmtree(old, ignore_errors=True)


def install() -> dict:
    v = check(force=True)
    if not v.get("ok"):
        return v
    if not v["newer"]:
        return {"ok": True, "action": "none", "message": f"Meshpool {v['current']} is the latest version."}
    k = v["kind"]
    if k == "dev":
        return {"ok": False, "error": "This copy runs from a git checkout. Run git pull in it to update."}
    if k == "mac-app":
        return _installer(v, "")
    if not os.access(APP, os.W_OK):
        return _installer(v, "This copy's folder is read-only.") if k == "win-app" else \
            {"ok": False, "error": f"Meshpool cannot write to {APP}. Run the installer again to update."}
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        tgz = _download(SRC.replace("{tag}", v["tag"]), t / "src.tgz")
        with tarfile.open(tgz) as tf:
            tf.extractall(t, filter="data")
        src = next((d for d in t.iterdir() if d.is_dir() and (d / "app" / "meshpool_harness").is_dir()), None)
        if not src:
            return {"ok": False, "error": "The release download did not contain the app. Nothing was changed."}
        new = src / "app"
        got = re.search(r'__version__ = "([^"]+)"', (new / "meshpool_harness" / "__init__.py").read_text())
        if not got or _ver(got.group(1)) != _ver(v["tag"]):
            return {"ok": False, "error": "The release download has a different version than its tag. Nothing was "
                                          "changed."}
        req_new = (new / "requirements.txt").read_text(encoding="utf-8") if (new / "requirements.txt").exists() else ""
        req_old = (APP / "requirements.txt").read_text(encoding="utf-8") if (APP / "requirements.txt").exists() else ""
        if req_new.strip() != req_old.strip():
            if k == "win-app":
                return _installer(v, "This release needs new bundled packages.")
            r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(new / "requirements.txt")],
                               capture_output=True, text=True, timeout=900)
            if r.returncode:
                return {"ok": False, "error": "Installing the new release's packages failed; nothing else was changed. "
                                              + r.stderr[-400:]}
        for name in CODE_DIRS:
            if (new / name).is_dir():
                staged = APP / f".{name}.new"
                shutil.rmtree(staged, ignore_errors=True)
                shutil.copytree(new / name, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                _swap(staged, APP / name)
        for f in ("requirements.txt",):
            if (new / f).exists():
                shutil.copy2(new / f, APP / f)
        for f in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
            if (src / f).exists() and (APP / f).exists():
                shutil.copy2(src / f, APP / f)
    _CACHE.clear()
    return {"ok": True, "action": "restart", "message": f"Updated to Meshpool {v['latest']}. Restart to use it."}


def restart(on_quit=()) -> None:
    """Start a new copy of the app (it waits for this one to close) and close this one."""
    env = dict(os.environ, MESHPOOL_RESTART_WAIT="1", PYTHONPATH=str(APP))
    kw = {"creationflags": 0x00000008 | 0x00000200} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen([sys.executable, "-m", "meshpool_harness", *sys.argv[1:]], env=env, cwd=os.getcwd(),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    for f in on_quit:
        try:
            f()
        except Exception:  # noqa: BLE001
            pass
    time.sleep(0.5)
    os._exit(0)
