"""Model-free helpers for the code engine.

issue_repro (E2): run the code examples from the issue before the first model call, so the agent starts from real
                  observed behaviour instead of a guess.
regression_gate (E3): at finish, run the tests related to the changed modules on the original code and on the
                  patched code; reject the finish if a test that passed before now fails.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

FENCE = re.compile(r"```(?:python|py|python3|pycon)?\s*\n(.*?)```", re.S)


def _snippets(issue: str) -> list[str]:
    out = []
    for block in FENCE.findall(issue):
        code = block.strip("\n")
        if code.startswith(">>>"):                       # doctest style -> plain code
            code = "\n".join(l[4:] for l in code.splitlines() if l.startswith((">>> ", "... ")))
        looks_py = re.search(r"^\s*(import |from |def |class |print\(|\w+\s*=|\w+\()", code, re.M)
        shell_or_trace = re.search(r"^(\$ |pip |Traceback|  File \")", code, re.M)
        if looks_py and not shell_or_trace and len(code) < 4000:
            out.append(code)
    return out[:3]


def _instrument(code: str) -> str:
    """Make each top-level expression print its value or its exception, and keep going (one line per result)."""
    import ast
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code
    lines = code.splitlines()
    out = []
    for node in tree.body:
        src = "\n".join(lines[node.lineno - 1:node.end_lineno])
        label = src.strip().splitlines()[0][:80]
        if isinstance(node, ast.Expr) and not (isinstance(node.value, ast.Call) and getattr(node.value.func, "id", "") == "print"):
            body = f"__r = ({src.strip()})\nprint({label!r}, '->', repr(__r))"
        else:
            body = src
        out.append("try:\n" + "\n".join("    " + l for l in body.splitlines()) +
                   f"\nexcept Exception as __e:\n    print({label!r}, '-> raises', type(__e).__name__ + ':', __e)")
    return "\n".join(out) + "\n"


def _cexec(container, cdir, cmd, timeout=120):
    try:
        r = subprocess.run(["docker", "exec", "-w", cdir, container, "bash", "-lc", cmd], capture_output=True,
                           text=True, timeout=timeout, errors="replace")
        return r.returncode, (r.stdout + r.stderr)
    except subprocess.TimeoutExpired:
        return 124, f"[timed out after {timeout}s]"


def issue_repro(issue: str, host_root: str, container: str, cdir: str) -> str:
    """Runs each Python snippet from the issue as a script in the container. Returns a compact report."""
    snips = _snippets(issue)
    if not snips:
        return ""
    d = Path(host_root) / ".run"
    d.mkdir(parents=True, exist_ok=True)
    parts = []
    for i, code in enumerate(snips, 1):
        (d / f"issue_example_{i}.py").write_text(_instrument(code))
        rc, out = _cexec(container, cdir, f"python .run/issue_example_{i}.py", timeout=90)
        out = out.strip()
        if len(out) > 1500:
            out = out[:600] + "\n...\n" + out[-800:]
        parts.append(f"$ python .run/issue_example_{i}.py   [exit {rc}]\n{out or '(no output)'}")
    return "\n\n".join(parts)


TEST_FILE = re.compile(r"(^|/)(tests?/.*\.py|test_[^/]*\.py|[^/]*_test\.py)$")


def _changed_sources(host_root: str) -> list[str]:
    r = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=host_root, capture_output=True, text=True)
    return [f for f in r.stdout.splitlines() if f.endswith(".py") and not TEST_FILE.search(f) and not f.startswith(".run")]


def _related_tests(host_root: str, sources: list[str], limit: int = 4) -> list[str]:
    root = Path(host_root)
    mods = {Path(s).stem for s in sources if Path(s).stem != "__init__"}
    mods |= {Path(s).parent.name for s in sources if Path(s).stem == "__init__"}
    tracked = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.splitlines()
    tests = [t for t in tracked if TEST_FILE.search(t)]
    scored = []
    for t in tests:
        try:
            body = (root / t).read_text(errors="ignore")
        except OSError:
            continue
        s = sum(len(re.findall(rf"\b(import|from)\s+[\w.]*\b{re.escape(m)}\b", body)) * 3 + (m in Path(t).stem) * 5
                for m in mods)
        if s:
            scored.append((s, t))
    return [t for _, t in sorted(scored, reverse=True)[:limit]]


def _outcomes(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        p = line.strip().split()
        if len(p) >= 2 and p[0] in ("PASSED", "FAILED", "ERROR"):
            out[p[1]] = p[0]
    return out


def regression_gate(host_root: str, container: str, cdir: str, timeout: int = 600) -> str | None:
    """None if no previously-passing related test now fails; else a message for the agent."""
    srcs = _changed_sources(host_root)
    if not srcs:
        return None
    tests = _related_tests(host_root, srcs)
    if not tests:
        return None
    cmd = f"python -m pytest -q -rA -p no:cacheprovider -x --timeout=300 {' '.join(tests)} 2>&1 || true"
    cmd = cmd.replace("--timeout=300 ", "")   # pytest-timeout may not be installed
    cmd = cmd.replace(" -x", "")
    # baseline: stash the agent's changes (tracked files only), run, restore. Pop only a stash made here: when
    # nothing was stashed, a pop would apply an older, unrelated stash.
    top = "git rev-parse -q --verify refs/stash"
    before = _cexec(container, cdir, top, timeout=60)[1].strip()
    rc = _cexec(container, cdir, "git stash -q", timeout=60)[0]
    if rc != 0 or _cexec(container, cdir, top, timeout=60)[1].strip() == before:
        return None                                    # no baseline without a stash: do not gate
    try:
        _, base = _cexec(container, cdir, cmd, timeout=timeout)
    finally:
        rc, out = _cexec(container, cdir, "git stash pop -q", timeout=60)
    if rc != 0:
        return ("GATE: the harness could not put your changes back after the baseline test run (git stash pop "
                f"failed: {out.strip()[:200]}). Run `git stash pop`, resolve it, then finish again.")
    _, now = _cexec(container, cdir, cmd, timeout=timeout)
    b, n = _outcomes(base), _outcomes(now)
    broke = [t for t, o in b.items() if o == "PASSED" and n.get(t) in ("FAILED", "ERROR")]
    if not broke:
        return None
    return (f"GATE regression: your change makes {len(broke)} previously passing test(s) fail in "
            f"{', '.join(tests)}: {', '.join(broke[:6])}. Run them, quote the failure, and fix your change "
            f"(do not edit the tests).")


# ---- learned from harness-engineering breakdowns (Codex just-in-time context; lecture-07/09 scope control)
DOC_FILE = re.compile(r"(^|/)(CHANGELOG|CHANGES|HISTORY|NEWS|README)[^/]*$|(^|/)docs?/", re.I)


def nearest_test(issue: str, host_root: str, max_lines: int = 40) -> str:
    """Just-in-time context: the existing test that best matches the issue's identifiers, so the repro (and the fix)
    follow the project's own call form, fixtures and exception types. Zero model tokens; ~600 prompt tokens."""
    from .locate import identifiers
    ids = [i for i in identifiers(issue) if len(i) >= 4][:10]
    if not ids:
        return ""
    root = Path(host_root)
    tracked = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.splitlines()
    best = (0, None, None)
    for t in (x for x in tracked if TEST_FILE.search(x) and x.endswith(".py")):
        try:
            body = (root / t).read_text(errors="ignore")
        except OSError:
            continue
        for m in re.finditer(r"^(\s*)def (test_\w+)\(.*?(?=^\1def |^\S|\Z)", body, re.M | re.S):
            s = sum(m.group(0).count(i) for i in ids)
            if s > best[0]:
                best = (s, t, m.group(0))
    if not best[1]:
        return ""
    lines = best[2].rstrip().splitlines()[:max_lines]
    return (f"Nearest existing test ({best[1]}). Write your reproduction test in the same style and file, copying its "
            f"call form, fixtures and exception types:\n" + "\n".join(lines))


def scope_gate(host_root: str, issue: str, max_src_lines: int = 60) -> str | None:
    """Reject an over-engineered diff once: docs/changelog edits, or many changed source lines. Zero model tokens."""
    r = subprocess.run(["git", "diff", "--numstat", "HEAD", "--", ".", ":!.run", ":!.junie"], cwd=host_root,
                       capture_output=True, text=True)
    new = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "--", ".", ":!.run", ":!.junie"],
                         cwd=host_root, capture_output=True, text=True).stdout.split()
    rows, docs, total = [], [], 0
    for line in r.stdout.splitlines():
        p = line.split("\t")
        if len(p) != 3 or not p[0].isdigit():
            continue
        n, f = int(p[0]) + int(p[1]), p[2]
        if DOC_FILE.search(f) and f.split("/")[-1] not in issue:
            docs.append(f)
        elif not TEST_FILE.search(f):
            total += n
            rows.append(f"{f}: {n} lines")
    for f in new:
        if not TEST_FILE.search(f) and f.endswith(".py"):
            try:
                n = len(Path(host_root, f).read_text(errors="ignore").splitlines())
            except OSError:
                continue
            total += n
            rows.append(f"{f}: {n} lines (new file)")
    # v2: size is not a failure signal (Phase 1: failed patches were usually SMALLER than competitors' passing ones);
    # only stray docs/changelog edits are blocked
    if not docs:
        return None
    why = [f"it edits docs/changelog files the issue did not ask for ({', '.join(docs)})"]
    return ("Scope check: " + " and ".join(why) + ". Hidden tests check the behaviour the issue asks for; extra changes "
            "add risk. Revert every hunk the issue does not need (keep tests you added). If every change is needed, "
            "finish again unchanged.")


def related_sites(host_root: str, limit: int = 8) -> str | None:
    """Before finishing: other places that use or define the functions the diff changed (Phase 1: competitors fixed
    every related call site, we often fixed one). Zero model tokens; shown once."""
    diff = subprocess.run(["git", "diff", "-U0", "HEAD", "--", ".", ":!.run", ":!.junie"], cwd=host_root,
                          capture_output=True, text=True).stdout
    names, changed = set(), set()
    cur = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:]
            if not TEST_FILE.search(cur):
                changed.add(cur)
        elif line.startswith("@@") and cur and not TEST_FILE.search(cur):
            m = re.search(r"def (\w+)|class (\w+)", line)
            if m:
                names.add(m.group(1) or m.group(2))
        elif line.startswith(("+", "-")) and cur and not TEST_FILE.search(cur):
            m = re.match(r"[+-]\s*(?:async\s+)?def (\w+)", line)
            if m:
                names.add(m.group(1))
    names = {n for n in names if len(n) > 3 and not n.startswith("__")}
    if not names:
        return None
    hits = []
    for n in sorted(names)[:6]:
        r = subprocess.run(["git", "grep", "-n", "-w", n, "--", "*.py"], cwd=host_root, capture_output=True, text=True)
        for l in r.stdout.splitlines():
            f = l.split(":", 1)[0]
            if f not in changed and not TEST_FILE.search(f):
                hits.append(l[:160])
    if not hits:
        return None
    return ("Before finishing: these places outside your diff also use the functions you changed. Check whether "
            "the issue needs them updated too (same bug, same pattern); if not, finish again unchanged.\n"
            + "\n".join(hits[:limit]))
