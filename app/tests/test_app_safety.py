"""Embedding the harness in an app must not crash the app or leave stray files (meshpool_harness.run_task)."""
import os

from meshpool_harness import run_task


class GatewayDown:
    def chat(self, **kw):
        raise RuntimeError("meshpool HTTP 503: upstream unavailable")


def test_run_task_returns_error_instead_of_raising(tmp_path, monkeypatch):
    monkeypatch.delenv("MH_TIMING_LOG", raising=False)
    monkeypatch.setenv("MH_FAILURES_LOG", "off")
    (tmp_path / "a.py").write_text("x = 1\n")
    r = run_task("Fix the bug in a.py", workdir=str(tmp_path), client=GatewayDown(), max_turns=3)
    assert r["ok"] is False and "503" in r["error"] and r["wall_s"] >= 0


def test_bad_input_never_raises(tmp_path):
    assert run_task("", workdir=str(tmp_path))["error"].startswith("ValueError")
    assert "unknown engine" in run_task("x", workdir=str(tmp_path), engine="nope")["error"]
    assert "does not exist" in run_task("x", workdir=str(tmp_path / "missing"))["error"]


def test_no_timing_file_unless_asked(tmp_path, monkeypatch):
    from meshpool_harness.core import loop
    monkeypatch.delenv("MH_TIMING_LOG", raising=False)
    pkg_log = os.path.join(os.path.dirname(loop.__file__), "..", "..", "timing.jsonl")
    before = os.path.getsize(pkg_log) if os.path.exists(pkg_log) else None
    run_task("Fix the bug", workdir=str(tmp_path), client=GatewayDown(), max_turns=2)
    after = os.path.getsize(pkg_log) if os.path.exists(pkg_log) else None
    assert before == after


def test_failure_log_survives_read_only_location(tmp_path, monkeypatch):
    from meshpool_harness.core.guards import log_failure
    monkeypatch.setenv("MH_FAILURES_LOG", str(tmp_path / "no_such_dir" / "failures.csv"))
    log_failure({"task_id": "t"}, "time_cap", False, "x")          # must not raise


def test_run_dir_deleted_mid_task_does_not_crash(tmp_path):
    """A task that runs `git clean -fdx` removes .run; the next command must still work (seen on sphinx-10449)."""
    import shutil
    from meshpool_harness.core.tools import Workspace
    ws = Workspace(str(tmp_path), str(tmp_path / ".run"))
    ws.bash("echo one")
    shutil.rmtree(tmp_path / ".run")
    assert "two" in ws.bash("echo two")
    assert "x" in ws.bash("python3 -c \"print('x' * 20000)\"")       # capped output file path too
