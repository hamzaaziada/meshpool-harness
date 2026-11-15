"""Error context (MH_ERROR_CONTEXT=1): when a command fails, show the project code where it failed.

Measured on 47 of our Verified transcripts (see core/observe.py): after its first edit the model makes 9.7 re-reads
per task. Most follow a failing run: the output names a file and line, and the next call reads that file. This module
does that read for it, at no extra call. When a command exits non-zero, it finds the places in the output that point
into the project's own files and appends each one as a numbered excerpt, in read_file's format, with the failing line
marked:

  Python tracebacks       File "pkg/mod.py", line 42, in f        (deepest project frames first)
  pytest locations        pkg/mod.py:42: AssertionError
  compilers and linters   src/a.c:12:5: error: ...   (gcc, clang, go, rustc's -->, ruff, mypy, pyright, eslint unix)
  TypeScript              src/a.ts(12,5): error TS2322: ...
  Node stacks             at f (/abs/src/a.js:12:5)

Library frames (site-packages, node_modules, virtualenvs, the standard library) are skipped. A shown excerpt counts as
a read of that file, like read_file with a line range, so the model can edit it straight away. Never raises.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

MAX_LOCATIONS = 3
CONTEXT = 3                 # lines either side of the failing line
LIMIT = 2200                # characters for the whole block

_LIB = re.compile(r"(^|/)(site-packages|dist-packages|node_modules|\.venv|venv|\.tox|\.nox|__pypackages__)/"
                  r"|/lib/python\d|^/usr/(lib|include|local/lib)/|<frozen|<string>|<stdin>|^internal/|^node:")
_PY_FRAME = re.compile(r'^\s*File "([^"]+)", line (\d+)(?:, in (.+))?\s*$')
_PY_EXC = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Warning|Failure|Fault)|AssertionError)"
                     r"(?::\s*(.*))?$")
_TSC = re.compile(r"(?<![\w/.-])([\w./\\-]+\.[A-Za-z0-9]+)\((\d+),(\d+)\):\s*(.*)")
_GEN = re.compile(r"(?:^|[\s(\[\"'])(?:-->\s*)?((?:[A-Za-z]:)?[\w./\\-]*[\w-]\.[A-Za-z0-9]{1,6}):(\d+)(?::(\d+))?"
                  r"(?:\)|(?::|\s+-)\s*(.*))?$")


def enabled() -> bool:
    return os.environ.get("MH_ERROR_CONTEXT") == "1"


def locations(out: str) -> list[tuple[str, int, int, str]]:
    """(path as written, line, column or 0, message) for each place the output points to, most useful first:
    the deepest project frames of the last Python traceback, then other locations in the order they appear."""
    lines = out.splitlines()
    found: list[tuple[str, int, int, str]] = []
    last_tb = max((i for i, l in enumerate(lines) if l.startswith("Traceback (most recent call last)")), default=-1)
    if last_tb >= 0:
        frames, exc = [], ""
        for l in lines[last_tb + 1:]:
            m = _PY_FRAME.match(l)
            if m:
                frames.append((m.group(1), int(m.group(2)), 0, m.group(3) or ""))
                continue
            e = _PY_EXC.match(l.strip())
            if e and frames:
                exc = l.strip()
                break
        frames = [f for f in frames if not _LIB.search(f[0].replace("\\", "/"))]
        for p, n, c, fn in reversed(frames):
            found.append((p, n, c, exc if not found else f"called from here (in {fn})" if fn else ""))
    for i, l in enumerate(lines):
        if _PY_FRAME.match(l):
            continue
        m = _TSC.search(l)
        if m:
            found.append((m.group(1), int(m.group(2)), int(m.group(3)), m.group(4).strip()))
            continue
        m = _GEN.search(l.rstrip())
        if not m:
            continue
        msg = (m.group(4) or "").strip()
        if not msg:                         # rustc: "error[E0425]: ..." sits above "  --> src/main.rs:4:5"
            for back in range(i - 1, max(-1, i - 4), -1):
                if re.match(r"\s*(error|warning)\b", lines[back]):
                    msg = lines[back].strip()
                    break
        found.append((m.group(1), int(m.group(2)), int(m.group(3) or 0), msg))
    seen, uniq = set(), []
    for f in found:
        k = (f[0], f[1])
        if k not in seen and not _LIB.search(f[0].replace("\\", "/")) and f[1] > 0:
            seen.add(k)
            uniq.append(f)
    return uniq


def _cd(command: str) -> str:
    m = re.match(r"\s*cd\s+(\"[^\"]+\"|'[^']+'|\S+)\s*&&", command or "")
    return m.group(1).strip("\"'") if m else ""


def context(out: str, ws, command: str = "") -> str:
    """The block to append to a failing command's output: excerpts of up to MAX_LOCATIONS project files.
    Empty when the output points nowhere in the project."""
    try:
        return _context(out, ws, command)
    except Exception:  # noqa: BLE001 - extra context must never break a tool call
        return ""


def _resolve(ws, p: str, sub: str) -> tuple[str, Path | None] | None:
    """(key for ws.seen, host path or None in an unmounted container) for a project file, or None."""
    p = p.replace("\\", "/") if os.name != "nt" else p
    if ws.container and not ws.mounted:
        cp = p if p.startswith("/") else (f"{ws.cdir.rstrip('/')}/{sub.strip('/') + '/' if sub else ''}{p}")
        if not cp.startswith(ws.cdir.rstrip("/") + "/"):
            return None
        return cp, None
    cands = []
    if os.path.isabs(p):
        if ws.container and ws.cdir and (p.startswith(ws.cdir.rstrip("/") + "/")):
            p = str(ws.root / os.path.relpath(p, ws.cdir))
        cands.append(Path(p))
    else:
        if sub:
            cands.append((ws.root / sub / p) if not os.path.isabs(sub) else Path(sub) / p)
        cands.append(ws.root / p)
    for c in cands:
        try:
            q = c.resolve()
        except OSError:
            continue
        if (ws.root in q.parents) and q.is_file() and ".git" not in q.relative_to(ws.root).parts \
                and q.stat().st_size < 2_000_000:
            return str(q), q
    return None


def _context(out: str, ws, command: str) -> str:
    sub = _cd(command)
    blocks, used = [], 0
    for path, line, col, msg in locations(out):
        if len(blocks) >= MAX_LOCATIONS:
            break
        r = _resolve(ws, path, sub)
        if not r:
            continue
        key, host = r
        try:
            text = host.read_text(errors="replace") if host is not None else ws._cread(key)
        except Exception:  # noqa: BLE001 - one unreadable file must not drop the other locations
            continue
        src = text.splitlines()
        if line > len(src):
            continue
        s, e = max(1, line - CONTEXT), min(len(src), line + CONTEXT)
        rel = os.path.relpath(key, ws.cdir if host is None else ws.root)
        head = f"{rel} line {line}" + (f", column {col}" if col else "") + (f": {msg[:200]}" if msg else "")
        body = "\n".join(f"{'>' if i == line else ' '}{i:>4}  {src[i - 1][:240]}" for i in range(s, e + 1))
        block = f"{head}\n{body}"
        if used + len(block) > LIMIT:
            break
        blocks.append(block)
        used += len(block)
        ws.seen[key] = ws._hash(text)          # like read_file with a line range: the file may now be edited
        if isinstance(getattr(ws, "original", None), dict):
            ws.original.setdefault(key, ws.seen[key])
    if not blocks:
        return ""
    return ("\n\n--- where it failed (project code, > marks the line; you may edit these files directly) ---\n"
            + "\n\n".join(blocks))
