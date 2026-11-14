"""Model-free test-runner discovery: probe the repo and its container once, tell the agent the exact command form.

Found on SWE-bench Verified: Django repos have no pytest; agents burned their whole turn budget searching for a way to
run tests (`tests/runtests.py` is the runner). Every repo family has its own convention, so detect it up front.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _cexec(container, cdir, cmd, timeout=60):
    try:
        r = subprocess.run(["docker", "exec", "-w", cdir, container, "bash", "-lc", cmd], capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode
    except subprocess.TimeoutExpired:
        return 124


def discover(host_root: str, container: str | None, cdir: str | None) -> str:
    root = Path(host_root)

    def run(code: str) -> int:                       # exit code of `python -c code`, in the container or here
        if container:
            return _cexec(container, cdir, f"python -c '{code}' 2>/dev/null")
        try:                                         # list args: no shell syntax, so it works under Windows too
            return subprocess.run(["python", "-c", code], cwd=root, capture_output=True, timeout=60).returncode
        except (OSError, subprocess.TimeoutExpired):
            return 1
    lines = []
    if (root / "tests" / "runtests.py").exists() and (root / "django").is_dir():
        lines.append("This is Django: run tests with `python tests/runtests.py <app_label or dotted test path> "
                     "--verbosity 1` from the repo root, e.g. `python tests/runtests.py i18n.tests.MiscTests` "
                     "(paths are relative to tests/, dotted, without .py). pytest is not the runner here.")
    elif (root / "bin" / "test").exists() and (root / "sympy").is_dir():
        lines.append("This is SymPy: run tests with `bin/test <path/to/test_file.py> -k <name>` "
                     "(pytest may also work: check once).")
    has_pytest = run("import pytest") == 0
    if not lines:
        if has_pytest:
            lines.append("Run tests with `python -m pytest -q <path/to/test_file.py>::<test_name>`.")
        else:
            lines.append("pytest is NOT installed in this environment. Run tests with "
                         "`python -m unittest <dotted.module.path>` or the project's own runner (look for "
                         "runtests.py, tox.ini, Makefile); do not install packages.")
    elif not has_pytest:
        lines.append("pytest is not installed; do not try to install it.")
    return " ".join(lines)
