"""Compiler-style feedback and code navigation for code tasks (open-source tools, both MIT).

MH_LINT_EDIT=1  After an edit or write to a Python file, run Ruff's error rules on the file before and after the
                change and report only the problems the edit introduced (undefined names, bad imports,
                redefinitions, syntax). The model learns what it broke at once, with no test run. Existing warnings
                in the file are never shown. The edit result also names the files changed so far.
MH_NAV=1        `symbol <name>`: where a Python function, class or variable is defined (path, line, signature) and
                every place it is used, in one call, via Jedi (the engine behind several Python language servers).

Both run on this machine over the project's files. Without Ruff, the check falls back to Python's own compile();
without Jedi, `symbol` reports that it is unavailable. Neither ever raises into the agent loop.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from collections import Counter
from pathlib import Path

# High-signal rules only: syntax (E9), undefined or misused names (F82*), redefinition (F811), comparisons and
# statements that are certainly wrong (F63*, F7*). No style rules, no unused-import or unused-variable noise.
RULES = "E9,F63,F7,F811,F821,F822,F823"


def _ruff(path: Path, text: str) -> list[tuple[int, str, str]] | None:
    exe = shutil.which("ruff")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "check", "--isolated", "--select", RULES, "--output-format", "json",
                            "--stdin-filename", str(path), "-"], input=text, capture_output=True, text=True,
                           timeout=15)
        rows = json.loads(r.stdout or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return [(d.get("location", {}).get("row", 0), d.get("code") or "E999", d.get("message", "")) for d in rows]


def _compile(path: Path, text: str) -> list[tuple[int, str, str]]:
    try:
        compile(text, str(path), "exec", dont_inherit=True)
        return []
    except SyntaxError as e:
        return [(e.lineno or 0, "E999", f"SyntaxError: {e.msg}")]
    except (ValueError, TypeError):
        return []


def problems(path: Path, text: str) -> list[tuple[int, str, str]]:
    if Path(path).suffix != ".py":
        return []
    found = _ruff(Path(path), text)
    return _compile(Path(path), text) if found is None else found


def new_problems(path, before: str | None, after: str) -> str:
    """Problems in `after` that `before` did not have (matched by rule and message, so moved lines do not count).
    Empty string when the edit added none."""
    try:
        old = Counter((c, m) for _, c, m in problems(path, before)) if before is not None else Counter()
        out = []
        for line, code, msg in problems(path, after):
            if old[(code, msg)] > 0:
                old[(code, msg)] -= 1
                continue
            out.append(f"  line {line}: {code} {msg}")
        if not out:
            return ""
        return "New problems in this file after your change:\n" + "\n".join(out[:10])
    except Exception:  # noqa: BLE001 - feedback must never break an edit
        return ""


def symbol(root: str, name: str, path: str | None = None, limit_refs: int = 30) -> str:
    """Definitions and references of `name` in the project, via Jedi. Plain text for the model."""
    try:
        import jedi
    except ImportError:
        return "symbol is unavailable here (the jedi package is not installed); use grep instead."
    box: dict = {}

    def work():
        try:
            proj = jedi.Project(root)
            defs = []
            if path:
                p = Path(root) / path
                src = p.read_text(errors="replace")
                for i, line in enumerate(src.splitlines(), 1):
                    col = line.find(name)
                    if col >= 0 and (line.lstrip().startswith(("def ", "class ", "async def ")) or "=" in line):
                        defs = jedi.Script(src, path=str(p), project=proj).goto(i, col + 1, follow_imports=True)
                        if defs:
                            break
            if not defs:
                defs = [d for d in proj.search(name, all_scopes=True)
                        if d.name == name.split(".")[-1] and d.module_path
                        and str(d.module_path).startswith(str(Path(root).resolve()))][:5]
            lines = []
            for d in defs[:5]:
                rel = Path(str(d.module_path)).resolve().relative_to(Path(root).resolve())
                sig = ""
                try:
                    sigs = d.get_signatures()
                    sig = sigs[0].to_string() if sigs else (d.get_line_code() or "").strip()
                except Exception:  # noqa: BLE001
                    sig = (d.get_line_code() or "").strip()
                lines.append(f"definition: {rel}:{d.line}  {d.type}  {sig[:200]}")
                try:
                    src = Path(str(d.module_path)).read_text(errors="replace")
                    refs = jedi.Script(src, path=str(d.module_path), project=proj).get_references(
                        d.line, d.column + 1, scope="project")
                except Exception:  # noqa: BLE001
                    refs = []
                uses = [r for r in refs if not (r.line == d.line and str(r.module_path) == str(d.module_path))]
                lines.append(f"  used in {len(uses)} place(s):")
                for r in uses[:limit_refs]:
                    try:
                        rr = Path(str(r.module_path)).resolve().relative_to(Path(root).resolve())
                    except ValueError:
                        continue
                    lines.append(f"  {rr}:{r.line}: {(r.get_line_code() or '').strip()[:160]}")
                if len(uses) > limit_refs:
                    lines.append(f"  ... {len(uses) - limit_refs} more")
            box["out"] = "\n".join(lines) or f"no definition of {name!r} found in the project"
        except Exception as e:  # noqa: BLE001
            box["out"] = f"symbol failed ({type(e).__name__}: {str(e)[:200]}); use grep instead."
    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(45)
    return box.get("out", "symbol timed out on this project; use grep instead.")
