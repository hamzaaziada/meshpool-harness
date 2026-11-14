"""Find Mac programs and libraries in an app that need a newer macOS than the app promises.

Usage: python3 check_minos.py Meshpool.app MIN [--arch arm64|x86_64] [--exempt GLOB=VERSION ...]
  MIN       the macOS the app promises (its LSMinimumSystemVersion): 11.0 for Apple silicon, 10.15 for Intel.
  --arch    read that architecture's slice of universal (fat) files. A universal2 library's Apple silicon slice
            always needs macOS 11, while its Intel slice often runs on 10.13; without --arch the highest slice counts.
  --exempt  files matching GLOB (relative to the app, fnmatch: * also matches /) may need up to VERSION instead of
            MIN. For the parts the app turns off on older systems with a clear message (on Intel: Node and what only
            Node or Playwright loads). An exempt file that needs more than its VERSION still stops the build.
A launcher that needs a newer macOS than the user's makes Finder show the app crossed out, and a library that does
breaks the feature that loads it, so the build stops on any file that needs more than it is allowed.
"""
from __future__ import annotations

import fnmatch
import struct
import sys
from pathlib import Path

LC_VERSION_MIN_MACOSX, LC_BUILD_VERSION = 0x24, 0x32
MH_DSYM = 0xA                                    # debug symbols (pyobjc ships them): never loaded, so never checked
THIN = {0xFEEDFACE: ("<", 28), 0xFEEDFACF: ("<", 32), 0xCEFAEDFE: (">", 28), 0xCFFAEDFE: (">", 32)}
CPU = {"x86_64": 0x01000007, "arm64": 0x0100000C}


def ver(v: int) -> tuple:
    return (v >> 16, (v >> 8) & 0xFF)


def parse(s: str) -> tuple:
    return tuple(int(x) for x in (s.split(".") + ["0"])[:2])


def minos_thin(b: bytes, off: int):
    magic = struct.unpack_from("<I", b, off)[0]
    if magic not in THIN:
        return None
    e, hdr = THIN[magic]
    if struct.unpack_from(e + "I", b, off + 12)[0] == MH_DSYM:
        return None
    ncmds = struct.unpack_from(e + "I", b, off + 16)[0]
    p = off + hdr
    for _ in range(ncmds):
        cmd, size = struct.unpack_from(e + "II", b, p)
        if cmd == LC_BUILD_VERSION:
            return ver(struct.unpack_from(e + "I", b, p + 12)[0])
        if cmd == LC_VERSION_MIN_MACOSX:
            return ver(struct.unpack_from(e + "I", b, p + 8)[0])
        if size <= 0:
            return None
        p += size
    return None


def minos(path: Path, arch: str | None = None):
    """The macOS a Mach-O file needs, or None for any other file. With arch, a universal file is judged by that
    architecture's slice (the one that runs on such a Mac); without it, or when it has no such slice, by its highest."""
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
    try:
        if m == 0xCAFEBABE:                      # fat binary
            n = struct.unpack_from(">I", b, 4)[0]
            if n > 20:                           # a Java class file shares the magic
                return None
            slices = [struct.unpack_from(">III", b, 8 + 20 * i) for i in range(n)]   # cputype, subtype, offset
            want = CPU.get(arch or "")
            mine = [minos_thin(b, off) for cpu, _, off in slices if cpu == want]
            if mine:
                vs = [v for v in mine if v]
                return max(vs) if vs else None
            vs = [v for v in (minos_thin(b, off) for _, _, off in slices) if v]
            return max(vs) if vs else None
        return minos_thin(b, 0)
    except struct.error:                         # truncated or not really Mach-O
        return None


def args(argv: list[str]):
    pos, arch, exempt = [], None, []
    it = iter(argv)
    for a in it:
        if a == "--arch":
            arch = next(it)
        elif a == "--exempt":
            glob, _, v = next(it).rpartition("=")
            if not glob:
                sys.exit(f"check_minos: --exempt wants GLOB=VERSION, got {a}")
            exempt.append((glob, parse(v)))
        else:
            pos.append(a)
    if len(pos) != 2:
        sys.exit(__doc__)
    return Path(pos[0]), parse(pos[1]), arch, exempt


def main():
    app, want, arch, exempt = args(sys.argv[1:])
    show = lambda v: f"{v[0]}.{v[1]}"
    core = [app / "Contents/MacOS/Meshpool", app / "Contents/Resources/node/bin/node",
            *(app / "Contents/Resources/python/bin").glob("python3*")]
    bad, used = [], set()
    for p in sorted(x for x in app.rglob("*") if x.is_file() and not x.is_symlink()):
        v = minos(p, arch)
        if not v:
            continue
        rel = p.relative_to(app).as_posix()
        hit = next(((g, mx) for g, mx in exempt if fnmatch.fnmatch(rel, g)), None)
        limit = hit[1] if hit else want
        if hit and v > want:
            used.add(hit[0])
        if v > limit:
            why = f", the app promises {show(want)}" if p in core else ""
            why += f" (exempt up to {show(limit)})" if hit else ""
            bad.append(f"ERROR: {rel} needs macOS {show(v)}{why}")
    for line in bad:
        print(line)
    missing = [p for p in core[:2] if not p.exists()]
    for p in missing:
        print(f"ERROR: {p.relative_to(app)} is missing")
    for g, mx in exempt:
        print(f"exempt up to {show(mx)}: {g}{'' if g in used else ' (no file needed it)'}")
    node = minos(core[1], arch) if core[1].exists() else None
    print(f"{app.name} ({arch or 'all slices'}, macOS {show(want)}): launcher {minos(core[0], arch)}, node {node}, "
          f"{len(bad)} file(s) above what they may need")
    sys.exit(1 if bad or missing else 0)


if __name__ == "__main__":
    main()
