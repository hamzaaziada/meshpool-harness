"""Swap installed packages built for a newer macOS than the app promises for older builds of the same version.

Usage: <the runtime's python> old_macos_wheels.py arm64|x86_64 [MIN]
  MIN defaults to the app's minimum for that architecture: 11.0 on Apple silicon, 10.15 on Intel.
pip on the build machine picks the wheel for the newest macOS it runs (numpy, for one, ships a macOS 14 build next to
its older one). Each package whose libraries need more than MIN (read from the files themselves, this architecture's
slice of universal ones; the wheel tag when it lists none) is reinstalled from a wheel of the same version tagged for
MIN or older. A package with no such wheel is listed; packaging/mac/check_minos.py then stops the build, unless the
package is one the app turns off on older systems (its --exempt list).
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_minos import minos, parse  # noqa: E402

DEFAULT_MIN = {"arm64": (11, 0), "x86_64": (10, 15)}


def platforms(arch: str, want: tuple) -> list[str]:
    """pip --platform tags for wheels that run on macOS `want` (and older) on this architecture."""
    if arch == "arm64":
        majors = range(11, max(11, want[0]) + 1)
        return [f"macosx_{m}_0_{a}" for m in majors for a in ("arm64", "universal2")]
    out = [f"macosx_10_{m}_{a}" for m in range(9, (want[1] if want[0] == 10 else 15) + 1)
           for a in ("x86_64", "intel", "universal2")]
    out += [f"macosx_{m}_0_{a}" for m in range(11, want[0] + 1) for a in ("x86_64", "universal2")]
    return out


def tags(dist) -> set[str]:
    return set(re.findall(r"^Tag: (.+)$", dist.read_text("WHEEL") or "", re.M))


def wheel_tags(whl: Path) -> set[str]:
    py, abi, plat = whl.stem.split("-")[-3:]
    return {f"{a}-{b}-{c}" for a in py.split(".") for b in abi.split(".") for c in plat.split(".")}


def needs(dist, arch: str):
    """The highest macOS this package's programs and libraries need on this architecture, or None when it has none.
    Read from the installed files (wheel tags can be wrong either way); from the wheel tag when it lists no files."""
    if dist.files is None:
        vs = [(int(a), int(b)) for a, b in re.findall(r"macosx_(\d+)_(\d+)_", " ".join(tags(dist)))]
        return max(vs) if vs else None
    paths = (Path(dist.locate_file(f)) for f in dist.files if not str(f).endswith((".py", ".pyc", ".pyi")))
    vs = [v for v in (minos(p, arch) for p in paths if p.is_file() and not p.is_symlink()) if v]
    return max(vs) if vs else None


def main():
    arch = sys.argv[1]
    want = parse(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_MIN[arch]
    show = f"{want[0]}.{want[1]}"
    todo = {}
    for d in metadata.distributions():
        v = needs(d, arch)
        if v and v > want:
            todo[(d.metadata["Name"], d.version)] = (v, d)
    left = []
    for (name, ver), (v, dist) in sorted(todo.items(), key=lambda kv: kv[0]):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = [sys.executable, "-m", "pip", "download", "-q", "--no-deps", "--only-binary=:all:", "--python-version",
                   f"{sys.version_info.major}.{sys.version_info.minor}", "-d", tmp, f"{name}=={ver}"]
            for p in platforms(arch, want):
                cmd += ["--platform", p]
            wheels = list(Path(tmp).glob("*.whl")) if subprocess.run(cmd).returncode == 0 else []
            wheels = [w for w in wheels if not wheel_tags(w) & tags(dist)]      # the same build again: no help
            if wheels:
                subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--force-reinstall",
                                str(wheels[0])], check=True)
                print(f"{name} {ver}: needed macOS {v[0]}.{v[1]}, now the macOS {show} build ({wheels[0].name})")
            else:
                left.append(f"{name} {ver}: needs macOS {v[0]}.{v[1]}, no macOS {show} build of this version")
    for x in left:
        print(x)


if __name__ == "__main__":
    main()
