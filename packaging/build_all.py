"""Assemble the Meshpool app for Apple silicon, Intel Macs and Windows. One package per platform: the desktop app
with the meshpool terminal command, browser use, computer use, Office output, Pi and Hermes inside.

The version has one source, __version__ in app/meshpool_harness/__init__.py: this script stamps it into Info.plist and
installer.nsi (their @VERSION@ placeholders), and stops if packaging/win/launcher/winres/winres.json (the version
inside Meshpool.exe) disagrees. `python packaging/build_all.py --winres` rewrites winres.json to match; then regenerate
the launcher's .syso and rebuild Meshpool.exe (docs/RELEASING.md).

Third-party runtimes are not in the repo. Put them in a deps folder (default: <repo>/deps, or set MESHPOOL_DEPS);
docs/BUILDING.md lists each one and where it comes from. Output goes to <repo>/dist-src.
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPS = Path(os.environ.get("MESHPOOL_DEPS") or ROOT / "deps")
APP_SRC = ROOT / "app"
VENDOR = DEPS / "vendor-hermes"                           # Hermes Agent with packaging/hermes-acp-server.patch applied
OUT = ROOT / "dist-src"
IGN = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
PKG = ROOT / "packaging"
WIN_LAUNCH = DEPS / "win/Meshpool"                        # Meshpool.exe built from packaging/win/launcher
PI = DEPS / "pi/node_modules"                             # Pi coding agent (npm), platform-neutral JS + wasm
NODE = {"mac-arm64": DEPS / "node-mac-arm64", "mac-x64": DEPS / "node-mac-x64", "win": DEPS / "node-win"}
PI_NATIVE = {"mac-arm64": "darwin-arm64", "mac-x64": "darwin-x64", "win": "win32-x64"}
RT = {"mac-arm64": DEPS / "rt-mac/python", "mac-x64": DEPS / "rt-macx64/python", "win": DEPS / "rt-win/python"}
WINRES = PKG / "win/launcher/winres/winres.json"


def version() -> str:
    m = re.search(r'^__version__ = "([^"]+)"', (APP_SRC / "meshpool_harness/__init__.py").read_text(), re.M)
    if not m or not re.match(r"\d+\.\d+\.\d+", m.group(1)):
        sys.exit("build_all: app/meshpool_harness/__init__.py needs __version__ = \"X.Y.Z...\"")
    return m.group(1)


VERSION = version()
VERSION_NUM = re.match(r"\d+\.\d+\.\d+", VERSION).group(0)          # Windows and macOS want plain numbers


def stamp(src: Path, dst: Path) -> None:
    """Copy a text file with its version placeholders filled in."""
    text = src.read_text(encoding="utf-8")
    text = (text.replace("@VERSION_NUM@", VERSION_NUM).replace("@VERSION@", VERSION)
            .replace("@BETA@", " beta" if VERSION.startswith("0.") else ""))
    left = sorted(set(re.findall(r"@[A-Z_]+@", text)))
    if left:
        sys.exit(f"build_all: {src.relative_to(ROOT)} has unknown placeholders: {', '.join(left)}")
    dst.write_text(text, encoding="utf-8")


def winres_versions(data: dict) -> dict:
    m = data["RT_MANIFEST"]["#1"]["0409"]
    v = data["RT_VERSION"]["#1"]["0000"]
    info = v["info"]["0409"]
    return {"manifest identity": m["identity"]["version"], "file_version": v["fixed"]["file_version"],
            "product_version": v["fixed"]["product_version"], "FileVersion": info["FileVersion"],
            "ProductVersion": info["ProductVersion"]}


def winres_want() -> dict:
    four = VERSION_NUM + ".0"
    return {"manifest identity": four, "file_version": four, "product_version": four, "FileVersion": VERSION,
            "ProductVersion": VERSION}


def check_winres(fix: bool = False) -> None:
    """Meshpool.exe carries its own version (winres.json, compiled into the launcher's .syso): it must match."""
    data = json.loads(WINRES.read_text(encoding="utf-8"))
    have, want = winres_versions(data), winres_want()
    if have == want:
        return
    if fix:
        m = data["RT_MANIFEST"]["#1"]["0409"]
        v = data["RT_VERSION"]["#1"]["0000"]
        m["identity"]["version"] = want["manifest identity"]
        v["fixed"]["file_version"], v["fixed"]["product_version"] = want["file_version"], want["product_version"]
        v["info"]["0409"]["FileVersion"], v["info"]["0409"]["ProductVersion"] = VERSION, VERSION
        WINRES.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"Updated {WINRES.relative_to(ROOT)} to {VERSION}. Now regenerate the .syso and rebuild Meshpool.exe "
              "(docs/RELEASING.md).")
        return
    bad = ", ".join(f"{k} {have[k]} (want {want[k]})" for k in want if have[k] != want[k])
    sys.exit(f"build_all: {WINRES.relative_to(ROOT)} does not match __version__ {VERSION}: {bad}.\n"
             "Run `python packaging/build_all.py --winres`, regenerate the .syso and rebuild Meshpool.exe "
             "(docs/RELEASING.md).")


def pi_and_node(app_dir: Path, node_dst: Path, plat: str):
    """Pi in app/vendor/pi (only this platform's native prebuilds) and the Node runtime beside the app."""
    keep = PI_NATIVE[plat]

    def ign(d, names):
        out = set(IGN(d, names))
        if "/prebuilds" in d.replace("\\", "/") and d.rstrip("/").endswith("prebuilds"):
            out |= {n for n in names if n != keep}
        if d.replace("\\", "/").endswith("pi-tui/native"):
            out |= {n for n in names if n != {"darwin-arm64": "darwin", "darwin-x64": "darwin", "win32-x64": "win32"}[keep]}
        return out
    shutil.copytree(PI, app_dir / "vendor" / "pi" / "node_modules", ignore=ign, symlinks=True)
    shutil.copytree(NODE[plat], node_dst, symlinks=True, ignore=shutil.ignore_patterns("corepack*", "include", "share"))


def app_code(dst: Path):
    dst.mkdir(parents=True)
    for name in ("meshpool_harness", "engines", "profiles", "defaults"):
        shutil.copytree(APP_SRC / name, dst / name, ignore=IGN)
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", dst / "THIRD_PARTY_NOTICES.md")
    shutil.copy2(APP_SRC / "requirements.txt", dst / "requirements.txt")
    # The package list the bundled runtime is built from (docs/BUILDING.md); the in-app update compares it.
    shutil.copy2(PKG / "requirements-cli.txt", dst / "requirements-cli.txt")
    shutil.copytree(VENDOR, dst / "vendor" / "hermes", ignore=IGN)
    shutil.copy2(PKG / "hermes-NOTICE-MESHPOOL.txt", dst / "vendor" / "hermes" / "NOTICE-MESHPOOL.txt")
    if (DEPS / "ocr-models").is_dir():                   # extra-script OCR models (optional; Latin/CJK built in)
        shutil.copytree(DEPS / "ocr-models", dst / "vendor" / "ocr")


def docs(dst: Path):
    d = PKG / "harness-docs"
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(d / "README.md", dst / "README.md")
    shutil.copytree(d / "ci", dst / "ci")
    shutil.copytree(d / "editors", dst / "editors")


def mac_app(arch: str):
    root = OUT / f"app-{arch}" / "Meshpool.app" / "Contents"
    (root / "MacOS").mkdir(parents=True)
    (root / "Resources").mkdir()
    stamp(PKG / "mac/Info.plist", root / "Info.plist")
    shutil.copy2(PKG / "mac/meshpool", root / "Resources/meshpool")
    shutil.copy2(PKG / "icons/Meshpool.icns", root / "Resources/Meshpool.icns")
    shutil.copytree(RT[arch], root / "Resources/python", symlinks=True, ignore=IGN)
    app_code(root / "Resources/app")
    pi_and_node(root / "Resources/app", root / "Resources/node", arch)
    docs(root / "Resources/harness-docs")
    # Contents/MacOS/Meshpool is compiled on the Mac from this source (see build_mac.sh)
    shutil.copy2(PKG / "mac/launcher/main.c", OUT / f"app-{arch}" / "launcher.c")


def win_app():
    root = OUT / "app-win" / "Meshpool"
    root.mkdir(parents=True)
    shutil.copy2(WIN_LAUNCH / "Meshpool.exe", root / "Meshpool.exe")
    shutil.copy2(PKG / "icons/Meshpool.ico", root / "Meshpool.ico")
    # The terminal command in a folder of its own: only bin goes on PATH, since next to Meshpool.exe the name
    # `meshpool` would start the app (Windows tries .EXE before .CMD).
    (root / "bin").mkdir()
    shutil.copy2(PKG / "win/meshpool.cmd", root / "bin" / "meshpool.cmd")
    shutil.copytree(RT["win"], root / "runtime", ignore=IGN)
    shutil.copytree(DEPS / "git-win", root / "git")
    app_code(root / "app")
    pi_and_node(root / "app", root / "node", "win")
    docs(root / "harness-docs")
    stamp(PKG / "nsis/installer.nsi", OUT / "app-win" / "installer.nsi")
    shutil.copy2(PKG / "nsis/winsetup.ps1", OUT / "app-win" / "winsetup.ps1")


if __name__ == "__main__":
    if "--winres" in sys.argv[1:]:
        check_winres(fix=True)
        sys.exit(0)
    check_winres()
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    only = next((x.split("=", 1)[1] for x in sys.argv[1:] if x.startswith("--only=")), "mac-arm64,mac-x64,win")
    for a in ("mac-arm64", "mac-x64"):
        if a in only.split(","):
            mac_app(a)
    if "win" in only.split(","):
        win_app()
    for p in sorted(OUT.iterdir()):
        print(p.name)
