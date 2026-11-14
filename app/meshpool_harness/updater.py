"""Update Meshpool from its GitHub releases, from inside the app.

check()   -> the latest release and whether it is newer than this copy
install() -> brings this copy up to that release, the safest way this kind of install allows:
  - command-line install (~/.meshpool-cli) and Windows app: the release's app code replaces this copy's code in place
    (the bundled runtimes, Pi and Hermes are kept); new Python packages, if the release lists any, are installed for
    the command-line copy, while the Windows app gets the full installer instead;
  - Mac app: downloads the release's disk image and opens it (changing files inside a signed Mac app breaks its
    signature, so the Mac app is always replaced whole);
  - a developer checkout (a git clone): nothing is changed; it says to run git pull.
  Installers are checked against the release's SHA256SUMS.txt when it has one. A code update swaps each code folder by
  renaming, keeping the old folders until every new one is in place, and puts the old ones back if any step fails.
restart() -> starts a fresh copy of the app and closes this one, so the new code runs.

Nothing here runs on its own: the page calls check() at start (at most once every 6 hours) and install() only when
the user presses Update.
"""
from __future__ import annotations

import hashlib
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
API = os.environ.get("MESHPOOL_UPDATE_API") or f"https://api.github.com/repos/{REPO}/releases?per_page=30"
SRC = os.environ.get("MESHPOOL_UPDATE_SRC") or f"https://codeload.github.com/{REPO}/tar.gz/refs/tags/{{tag}}"
APP = C.APP                                  # the folder that holds meshpool_harness/
CODE_DIRS = ("meshpool_harness", "engines", "profiles", "defaults")   # what a code update replaces; vendor/ is kept
CLI_REQ = "requirements-cli.txt"     # the package list the command line and the bundled runtimes are installed from
SUMS = "SHA256SUMS.txt"              # published with each release by packaging/release.sh
_CACHE: dict = {}
_PRE = re.compile(r"\d[.-]?(alpha|beta|preview|pre|rc|dev|a|b|c)(?![a-z])\.?(\d*)", re.I)


def _ver(s: str) -> tuple:
    """(major, minor, patch) of a version or tag such as v0.2.1 or 0.3.0rc1."""
    m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", s or "")
    return tuple(int(x or 0) for x in m.groups()) if m else (0, 0, 0)


def _is_pre(s: str) -> bool:
    """A prerelease version or tag: 0.3.0rc1, v0.3.0-beta.2 and the like."""
    return bool(_PRE.search(s or ""))


def _key(s: str) -> tuple:
    """Sort key: by number, and a prerelease before the release it leads to (0.3.0rc1 < 0.3.0)."""
    m = _PRE.search(s or "")
    return _ver(s) + ((0, int(m.group(2) or 0)) if m else (1, 0))


def _get(url: str, timeout: float = 20):
    req = urllib.request.Request(url, headers={"User-Agent": "meshpool-updater", "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def kind() -> str:
    """How this copy was installed: dev, mac-app, win-app or cli."""
    if (APP.parent / ".git").exists():
        return "dev"
    # The command-line install (~/.meshpool-cli) keeps its virtual environment beside the app. It may also hold a
    # python/ folder (the copy install.sh downloads when the computer has none), which is what config.packaged()
    # looks for, so test for the command-line install first.
    if (APP.parent / "venv").is_dir() or (APP / "meshpool_launch.py").is_file():
        return "cli"
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
    rel = _pick(rel if isinstance(rel, list) else [rel], cur)
    if not rel:
        v = {"ok": True, "current": cur, "latest": cur, "tag": "", "newer": False, "notes": "", "kind": kind(),
             "url": f"https://github.com/{REPO}/releases", "assets": []}
        _CACHE.update(t=time.time(), v=v)
        return v
    tag = rel.get("tag_name") or ""
    v = {"ok": True, "current": cur, "latest": tag.lstrip("v"), "tag": tag, "newer": _key(tag) > _key(cur),
         "notes": (rel.get("body") or "")[:4000], "url": rel.get("html_url") or f"https://github.com/{REPO}/releases",
         "published": rel.get("published_at"), "kind": kind(),
         "assets": [{"name": a.get("name"), "url": a.get("browser_download_url"), "size": a.get("size")}
                    for a in rel.get("assets") or []]}
    _CACHE.update(t=time.time(), v=v)
    return v


def _legacy(r: dict, releases: list, cur: str) -> bool:
    """A build from the 2.x line that came before the 0.x renumbering. Its number looks higher than every 0.x and
    1.x release, but it is older: a 0.x copy never moves to 2.x, and a later copy only to a 2.x published after the
    first release of the renumbered line."""
    if _ver(r["tag_name"])[0] < 2:
        return False
    if _ver(cur)[0] < 1:
        return True
    first = min((x["published_at"] for x in releases if isinstance(x, dict) and x.get("published_at")
                 and _ver(x.get("tag_name") or "")[0] < 2), default="")
    return bool(first) and (not r.get("published_at") or r["published_at"] < first)


def _pick(releases: list, cur: str) -> dict | None:
    """The newest published release this copy may move to, or None. Never a draft, never a release that is not
    newer than this copy, never one from the old 2.x line, and a prerelease only when this copy is a prerelease."""
    ok = [r for r in releases if isinstance(r, dict) and not r.get("draft") and r.get("tag_name")]
    if not _is_pre(cur):
        ok = [r for r in ok if not r.get("prerelease") and not _is_pre(r["tag_name"])]
    ok = [r for r in ok if not _legacy(r, releases, cur) and _key(r["tag_name"]) > _key(cur)]
    return max(ok, key=lambda r: _key(r["tag_name"]), default=None)


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


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sums(v: dict) -> dict | None:
    """{file name: sha256} from the release's SHA256SUMS.txt, or None when the release has none."""
    a = next((a for a in v.get("assets") or [] if a.get("name") == SUMS and a.get("url")), None)
    if not a:
        return None
    with _get(a["url"]) as r:
        text = r.read(1 << 20).decode("utf-8", "replace")
    out = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            out[parts[1].strip().lstrip("*")] = parts[0].lower()
    return out


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
    try:
        sums = _sums(v)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Could not read the release's checksums ({type(e).__name__}). Try again, or "
                                      "download it from the release page.", "url": v["url"]}
    if sums is not None and a["name"] not in sums:
        return {"ok": False, "error": f"The release's checksums do not list {a['name']}. Download it from the release "
                                      "page.", "url": v["url"]}
    dl = Path.home() / "Downloads"
    p = (dl if dl.is_dir() else Path(tempfile.gettempdir())) / a["name"]
    part = p.with_name(p.name + ".part")
    try:
        try:
            _download(a["url"], part)
        except PermissionError:                 # macOS privacy settings can refuse the Downloads folder
            p = Path(tempfile.gettempdir()) / a["name"]
            part = p.with_name(p.name + ".part")
            _download(a["url"], part)
        if sums is not None and _sha256(part) != sums[a["name"]]:
            part.unlink()
            return {"ok": False, "error": f"The download of {a['name']} did not match the release's checksum, so it "
                                          "was deleted. Try again.", "url": v["url"]}
        C.replace_file(part, p)
    except OSError as e:
        part.unlink(missing_ok=True)
        return {"ok": False, "error": f"Could not download {a['name']} ({type(e).__name__}). Try again.",
                "url": v["url"]}
    _open(p)
    step = ("Drag Meshpool into Applications and replace the old copy, then open it again."
            if sys.platform == "darwin" else "Follow the installer; it replaces this copy.")
    return {"ok": True, "action": "installer", "file": str(p),
            "message": f"Downloaded Meshpool {v['latest']} and opened the installer. {why} {step}"}


def _swap_all(pairs: list) -> None:
    """Move each staged folder into place by renaming. Every old folder is kept as a backup until all the new ones
    are in; if any rename fails, the folders already swapped are put back as they were and the error is raised."""
    done = []                                           # (dst, backup or None)
    try:
        for staged, dst in pairs:
            old = dst.with_name(f".{dst.name}.old")
            shutil.rmtree(old, ignore_errors=True)
            had = dst.exists()
            if had:
                dst.rename(old)
            try:
                staged.rename(dst)
            except OSError:
                if had:
                    old.rename(dst)
                raise
            done.append((dst, old if had else None))
    except OSError:
        for dst, old in reversed(done):
            failed = dst.with_name(f".{dst.name}.failed")
            shutil.rmtree(failed, ignore_errors=True)
            try:
                dst.rename(failed)
                if old:
                    old.rename(dst)
            except OSError:
                pass
            shutil.rmtree(failed, ignore_errors=True)
        raise
    for _, old in done:
        if old:
            shutil.rmtree(old, ignore_errors=True)


def _requirements(src: Path, new: Path) -> tuple:
    """(the release's package list, this copy's record of its own). The command line and the Windows app are
    installed from packaging/requirements-cli.txt (a copy sits in the app folder); app/requirements.txt is the
    developer list. A copy without that record is treated as changed, so new packages are never skipped."""
    rel = src / "packaging" / CLI_REQ
    if rel.exists():
        return rel, APP / CLI_REQ
    return new / "requirements.txt", APP / "requirements.txt"   # a release from before the record existed


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
    if k == "win-app" and not (APP.parent / "bin" / "meshpool.cmd").exists():
        # An install from before the terminal command moved to its own bin folder: a code update cannot fix the
        # launcher or the PATH entry (with Meshpool.exe beside it, `meshpool` would open the app), the installer does.
        return _installer(v, "This update also changes how Meshpool is installed.")
    if not os.access(APP, os.W_OK):
        return _installer(v, "This copy's folder is read-only.") if k == "win-app" else \
            {"ok": False, "error": f"Meshpool cannot write to {APP}. Run the installer again to update."}
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        tgz = _download(SRC.replace("{tag}", v["tag"]), t / "src.tgz")
        with tarfile.open(tgz) as tf:
            try:
                tf.extractall(t, filter="data")
            except TypeError:                         # Python without the tarfile filter: check paths ourselves
                for m in tf.getmembers():
                    if m.name.startswith(("/", "\\")) or ".." in Path(m.name).parts or m.issym() or m.islnk():
                        return {"ok": False, "error": "The release download looks unsafe. Nothing was changed."}
                tf.extractall(t)
        src = next((d for d in t.iterdir() if d.is_dir() and (d / "app" / "meshpool_harness").is_dir()), None)
        if not src:
            return {"ok": False, "error": "The release download did not contain the app. Nothing was changed."}
        new = src / "app"
        got = re.search(r'__version__ = "([^"]+)"', (new / "meshpool_harness" / "__init__.py").read_text())
        if not got or _ver(got.group(1)) != _ver(v["tag"]):
            return {"ok": False, "error": "The release download has a different version than its tag. Nothing was "
                                          "changed."}
        req_new_p, req_old_p = _requirements(src, new)
        req_new = req_new_p.read_text(encoding="utf-8") if req_new_p.exists() else ""
        req_old = req_old_p.read_text(encoding="utf-8") if req_old_p.exists() else None
        if req_new.strip() and (req_old is None or req_new.strip() != req_old.strip()):
            if k == "win-app":
                return _installer(v, "This release needs new bundled packages.")
            r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(req_new_p)],
                               capture_output=True, text=True, timeout=900)
            if r.returncode:
                return {"ok": False, "error": "Installing the new release's packages failed; nothing else was changed. "
                                              + r.stderr[-400:]}
        pairs = []
        try:
            for name in CODE_DIRS:
                if (new / name).is_dir():
                    staged = APP / f".{name}.new"
                    shutil.rmtree(staged, ignore_errors=True)
                    shutil.copytree(new / name, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                    pairs.append((staged, APP / name))
            _swap_all(pairs)
        except OSError as e:
            for staged, _ in pairs:
                shutil.rmtree(staged, ignore_errors=True)
            if k == "win-app":
                return _installer(v, "Meshpool could not replace its files while it runs.")
            return {"ok": False, "error": f"Could not replace Meshpool's files ({type(e).__name__}: {e}). Nothing was "
                                          "changed. Close other Meshpool windows and try again."}
        for f in ("requirements.txt",):
            if (new / f).exists():
                shutil.copy2(new / f, APP / f)
        if (src / "packaging" / CLI_REQ).exists():
            shutil.copy2(src / "packaging" / CLI_REQ, APP / CLI_REQ)
        for f in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
            if (src / f).exists() and (APP / f).exists():
                shutil.copy2(src / f, APP / f)
    _CACHE.clear()
    return {"ok": True, "action": "restart", "message": f"Updated to Meshpool {v['latest']}. Restart to use it."}


def _relaunch_cmd() -> list:
    """The command that starts this app again. It runs in the user's folder, so the folder must not go on sys.path:
    a json.py or argparse.py there would be imported in place of Python's own. -P keeps it off (Python 3.11 and
    later); on 3.10 a tiny bootstrap removes it before anything is imported."""
    exe = sys.executable
    if os.name == "nt" and sys.argv[1:2] == ["app"]:     # the Windows app runs windowless: back under pythonw.exe
        w = Path(exe).with_name("pythonw.exe")
        exe = str(w) if w.exists() else exe
    if sys.version_info >= (3, 11):
        return [exe, "-P", "-m", "meshpool_harness", *sys.argv[1:]]
    boot = ("import sys, runpy\n"
            "if sys.path and sys.path[0] in ('', '.'):\n    del sys.path[0]\n"
            "runpy.run_module('meshpool_harness', run_name='__main__', alter_sys=True)\n")
    return [exe, "-c", boot, *sys.argv[1:]]


def restart(on_quit=()) -> None:
    """Start a new copy of the app (it waits for this one to close) and close this one."""
    env = dict(os.environ, MESHPOOL_RESTART_WAIT="1", PYTHONPATH=str(APP))
    kw = {"creationflags": 0x00000008 | 0x00000200} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen(_relaunch_cmd(), env=env, cwd=os.getcwd(),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    for f in on_quit:
        try:
            f()
        except Exception:  # noqa: BLE001
            pass
    time.sleep(0.5)
    os._exit(0)
