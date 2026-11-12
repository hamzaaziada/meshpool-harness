"""Assemble the Meshpool app for Apple silicon, Intel Macs and Windows. One package per platform: the desktop app
with the meshpool terminal command, browser use, computer use, Office output, Pi and Hermes inside.

Third-party runtimes are not in the repo. Put them in a deps folder (default: <repo>/deps, or set MESHPOOL_DEPS);
docs/BUILDING.md lists each one and where it comes from. Output goes to <repo>/dist-src.
"""
import os
import shutil
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
    for name in ("meshpool_harness", "engines", "profiles", "memory", "defaults"):
        shutil.copytree(APP_SRC / name, dst / name, ignore=IGN)
    for f in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / f, dst / f)
    shutil.copy2(APP_SRC / "requirements.txt", dst / "requirements.txt")
    shutil.copytree(VENDOR, dst / "vendor" / "hermes", ignore=IGN)
    shutil.copy2(PKG / "hermes-NOTICE-MESHPOOL.txt", dst / "vendor" / "hermes" / "NOTICE-MESHPOOL.txt")
    shutil.copytree(DEPS / "ocr-models", dst / "vendor" / "ocr")          # PP-OCRv5 recognition models


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
    shutil.copy2(PKG / "mac/Info.plist", root / "Info.plist")
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
    for f in ("Meshpool.exe", "meshpool.cmd", "Meshpool.ico"):
        shutil.copy2(WIN_LAUNCH / f, root / f)
    shutil.copytree(RT["win"], root / "runtime", ignore=IGN)
    shutil.copytree(DEPS / "git-win", root / "git")
    app_code(root / "app")
    pi_and_node(root / "app", root / "node", "win")
    docs(root / "harness-docs")
    (root / "LICENSE.txt").write_text((ROOT / "NOTICE").read_text() + "\n\n" + (ROOT / "LICENSE").read_text(), encoding="utf-8")
    shutil.copy2(PKG / "nsis/installer.nsi", OUT / "app-win" / "installer.nsi")


if __name__ == "__main__":
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    for a in ("mac-arm64", "mac-x64"):
        mac_app(a)
    win_app()
    for p in sorted(OUT.iterdir()):
        print(p.name)
