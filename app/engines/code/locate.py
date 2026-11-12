"""Model-free localisation hints: identifiers from the issue -> where they are defined/used. Saves exploratory turns."""
from __future__ import annotations

import re
import subprocess
from collections import Counter
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "build", "dist", ".tox", ".run", ".junie",
             "site-packages", ".mypy_cache", ".pytest_cache", "docs", "doc"}
CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".php", ".c", ".h", ".cpp", ".cc",
            ".cs", ".kt", ".scala", ".swift"}
STOP = {"self", "None", "True", "False", "return", "import", "from", "class", "def", "print", "assert", "error",
        "Error", "value", "values", "data", "test", "tests", "file", "line", "this", "that", "with", "should",
        "would", "could", "expected", "actual", "string", "number", "list", "dict", "type", "types", "object",
        "function", "method", "issue", "example", "using", "when", "where", "which", "there", "their", "version",
        "python", "install", "pip", "github", "https", "http", "com", "org", "true", "false", "none", "null"}


def identifiers(issue: str, limit: int = 14) -> list[str]:
    toks = []
    toks += re.findall(r"`([A-Za-z_][\w.]{2,60})(?:\(\))?`", issue)                 # `backticked`
    toks += re.findall(r"\b([A-Za-z_][\w]*\.[A-Za-z_][\w.]*)\b", issue)              # dotted.names
    toks += re.findall(r"\b([a-z]+_[a-z0-9_]+)\b", issue)                            # snake_case
    toks += re.findall(r"\b([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)\b", issue)            # CamelCase
    toks += re.findall(r"\b([A-Za-z_]\w{2,})\s*\(", issue)                           # calls(
    toks += re.findall(r'File "([^"]+)", line \d+', issue)                           # traceback files
    out = []
    for t in toks:
        for part in [t] + t.split("."):
            part = part.strip(".")
            if len(part) >= 3 and part not in STOP and not part.isdigit():
                out.append(part)
    return [w for w, _ in Counter(out).most_common(limit)]


def _files(root: Path) -> list[Path]:
    r = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        fs = [root / l for l in r.stdout.splitlines()]
    else:
        fs = [p for p in root.rglob("*") if p.is_file()]
    return [f for f in fs if f.suffix in CODE_EXT and not (set(f.relative_to(root).parts) & SKIP_DIRS)]


def hints(issue: str, workdir: str, max_lines: int = 40) -> str:
    root = Path(workdir)
    ids = identifiers(issue)
    files = _files(root)
    if not ids or not files:
        return ""
    defs, uses = [], Counter()
    pats = {i: re.compile(rf"^\s*(?:async\s+)?(?:def|class|function|func|fn|const|let|var|interface|type)\s+{re.escape(i.split('.')[-1])}\b")
            for i in ids}
    for f in files:
        try:
            lines = f.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        rel = str(f.relative_to(root))
        is_test = bool(re.search(r"(^|/)tests?/|test_|_test\.", rel))
        for n, line in enumerate(lines, 1):
            for i, p in pats.items():
                if p.search(line):
                    defs.append(f"{rel}:{n}: {line.strip()[:110]}")
            if not is_test:
                for i in ids:
                    if i.split(".")[-1] in line:
                        uses[rel] += 1
    out = []
    if defs:
        out.append("Definitions of identifiers mentioned in the task:")
        out += [f"  {d}" for d in defs[:max_lines // 2]]
    top = [f"  {p} ({c} mentions)" for p, c in uses.most_common(8)]
    if top:
        out.append("Source files that mention them most:")
        out += top
    tests = sorted({str(f.relative_to(root)) for f in files if re.search(r"(^|/)tests?/", str(f.relative_to(root)))})
    if tests:
        out.append(f"Test files: {len(tests)} (e.g. {', '.join(tests[:6])})")
    return "\n".join(out[:max_lines])
