"""Swap installed packages built for a newer macOS for their macOS 11 builds of the same version.

Usage: <the runtime's python> old_macos_wheels.py arm64|x86_64
pip on the build machine picks the wheel for the newest macOS it runs (numpy, for one, ships a macOS 14 build next to
its macOS 11 build). The app promises macOS 11, so each such package is reinstalled from the oldest-macOS wheel of the
same version. A package with no such wheel is listed; packaging/mac/check_minos.py then stops the build.
"""
import re
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

MIN = (11, 0)


def platforms(arch: str) -> list[str]:
    if arch == "arm64":
        return ["macosx_11_0_arm64", "macosx_11_0_universal2"]
    return [f"macosx_10_{m}_{a}" for m in range(9, 16) for a in ("x86_64", "intel", "universal2")] + \
        ["macosx_11_0_x86_64", "macosx_11_0_universal2"]


def needs_newer(dist) -> bool:
    for tag in re.findall(r"^Tag: (.+)$", dist.read_text("WHEEL") or "", re.M):
        m = re.search(r"macosx_(\d+)_(\d+)_", tag)
        if m and (int(m.group(1)), int(m.group(2))) > MIN:
            return True
    return False


def main():
    arch = sys.argv[1]
    todo = sorted({(d.metadata["Name"], d.version) for d in metadata.distributions() if needs_newer(d)})
    left = []
    for name, ver in todo:
        with tempfile.TemporaryDirectory() as tmp:
            cmd = [sys.executable, "-m", "pip", "download", "-q", "--no-deps", "--only-binary=:all:", "--python-version",
                   f"{sys.version_info.major}.{sys.version_info.minor}", "-d", tmp, f"{name}=={ver}"]
            for p in platforms(arch):
                cmd += ["--platform", p]
            wheels = list(Path(tmp).glob("*.whl")) if subprocess.run(cmd).returncode == 0 else []
            if wheels:
                subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--force-reinstall",
                                str(wheels[0])], check=True)
                print(f"{name} {ver}: now the macOS 11 build ({wheels[0].name})")
            else:
                left.append(f"{name} {ver}")
    for x in left:
        print(f"{x}: no macOS 11 build of this version")


if __name__ == "__main__":
    main()
