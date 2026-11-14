"""Find Mac programs and libraries in an app that need a newer macOS than the app promises.

Usage: python3 check_minos.py Meshpool.app 11.0
A launcher that needs a newer macOS than the user's makes Finder show the app crossed out, so the launcher, Python
and Node must not need more than the minimum; the build stops if they do. Anything else above it is listed.
"""
import struct
import sys
from pathlib import Path

LC_VERSION_MIN_MACOSX, LC_BUILD_VERSION = 0x24, 0x32
THIN = {0xFEEDFACE: ("<", 28), 0xFEEDFACF: ("<", 32), 0xCEFAEDFE: (">", 28), 0xCFFAEDFE: (">", 32)}


def ver(v: int) -> tuple:
    return (v >> 16, (v >> 8) & 0xFF)


def minos_thin(b: bytes, off: int):
    magic = struct.unpack_from("<I", b, off)[0]
    if magic not in THIN:
        return None
    e, hdr = THIN[magic]
    ncmds = struct.unpack_from(e + "I", b, off + 16)[0]
    p = off + hdr
    for _ in range(ncmds):
        cmd, size = struct.unpack_from(e + "II", b, p)
        if cmd == LC_BUILD_VERSION:
            return ver(struct.unpack_from(e + "I", b, p + 12)[0])
        if cmd == LC_VERSION_MIN_MACOSX:
            return ver(struct.unpack_from(e + "I", b, p + 8)[0])
        p += size
    return None


def minos(path: Path):
    try:
        with open(path, "rb") as f:
            head = f.read(4)
            if len(head) < 4:
                return None
            m = struct.unpack(">I", head)[0]
            if m not in (0xCAFEBABE, *THIN) and struct.unpack("<I", head)[0] not in THIN:
                return None
            b = head + f.read()
    except OSError:
        return None
    if m == 0xCAFEBABE:                          # fat binary: the highest of its slices
        n = struct.unpack_from(">I", b, 4)[0]
        if n > 20:                               # a Java class file shares the magic
            return None
        vs = [minos_thin(b, struct.unpack_from(">I", b, 8 + 20 * i + 8)[0]) for i in range(n)]
        vs = [v for v in vs if v]
        return max(vs) if vs else None
    return minos_thin(b, 0)


def main():
    app, want = Path(sys.argv[1]), tuple(int(x) for x in sys.argv[2].split(".")[:2])
    core = [app / "Contents/MacOS/Meshpool", app / "Contents/Resources/node/bin/node",
            *(app / "Contents/Resources/python/bin").glob("python3*")]
    bad_core, other = [], []
    for p in sorted(x for x in app.rglob("*") if x.is_file() and not x.is_symlink()):
        v = minos(p)
        if v and v > want:
            (bad_core if p in core else other).append((p.relative_to(app), v))
    for p, v in other:
        print(f"note: {p} needs macOS {v[0]}.{v[1]}")
    for p, v in bad_core:
        print(f"ERROR: {p} needs macOS {v[0]}.{v[1]}, the app promises {want[0]}.{want[1]}")
    missing = [p for p in core[:2] if not p.exists()]
    for p in missing:
        print(f"ERROR: {p.relative_to(app)} is missing")
    print(f"{app.name}: launcher {minos(core[0])}, node {minos(core[1])}, {len(other)} other file(s) above {sys.argv[2]}")
    sys.exit(1 if bad_core or missing else 0)


if __name__ == "__main__":
    main()
