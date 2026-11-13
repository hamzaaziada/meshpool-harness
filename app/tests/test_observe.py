"""MH_OBSERVE tools: probe (failure digest) and trace (project functions that ran). Real processes, no model."""
import subprocess

import pytest

from meshpool_harness.core.observe import digest, probe_command, trace_command
from meshpool_harness.core.tools import ToolError, Workspace


def _repo(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "calc.py").write_text(
        "def scale(values, factor):\n    total = sum(values)\n    return [v / total * factor for v in values]\n\n"
        "def helper():\n    return 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text(
        "from pkg.calc import scale\n\ndef test_ok():\n    assert scale([1, 1], 2) == [1, 1]\n\n"
        "def test_empty():\n    assert scale([0, 0], 2) == [0, 0]\n")
    (tmp_path / "run.py").write_text("from pkg.calc import scale, helper\nhelper()\nprint(scale([2, 2], 1))\n")
    return Workspace(str(tmp_path), str(tmp_path / ".run"))


def test_probe_shows_project_frame_and_locals(tmp_path):
    ws = _repo(tmp_path)
    out = ws.probe("python -m pytest -q tests")
    assert out.startswith("[exit 1]")
    assert "FAILED tests/test_calc.py::test_empty" in out
    assert "ZeroDivisionError" in out and "total" in out and "= 0" in out      # the local that explains it
    assert "_pytest" not in out                                                 # library frames dropped
    assert ws.commands[-1]["kind"] == "check"


def test_probe_passing_run_is_short(tmp_path):
    ws = _repo(tmp_path)
    out = ws.probe("python -m pytest -q tests -k ok")
    assert out.startswith("[exit 0]") and "passed" in out and len(out) < 2000


def test_trace_lists_project_functions_in_order(tmp_path):
    ws = _repo(tmp_path)
    out = ws.trace("python run.py")
    assert "=== trace:" in out and "[0.5, 0.5]" in out
    lines = [l for l in out.splitlines() if l.startswith("pkg/calc.py")]
    assert lines[0].startswith("pkg/calc.py:5 helper") and lines[1].startswith("pkg/calc.py:1 scale")


def test_trace_pytest_form(tmp_path):
    ws = _repo(tmp_path)
    out = ws.trace("pytest -q tests/test_calc.py -k empty")
    assert "pkg/calc.py:1 scale" in out


@pytest.mark.parametrize("cmd", ["ls", "python run.py | head", "make test", "python -c 'print(1)'"])
def test_trace_rejects_non_python(tmp_path, cmd):
    ws = _repo(tmp_path)
    with pytest.raises(ToolError):
        ws.trace(cmd)


def test_probe_command_rewrite():
    assert probe_command("python -m pytest -q x").startswith("python -m pytest --tb=long --showlocals")
    assert probe_command("cd /t && pytest x | tail -5").startswith("cd /t && pytest --tb=long")
    assert probe_command("pytest --tb=short x") == "pytest --tb=short x"
    assert probe_command("python run.py") == "python run.py"
    assert trace_command("cd /t && FOO=1 python3 -m pytest -q a") is not None


def test_digest_plain_traceback_drops_library_frames():
    out = ('[exit 1]\nTraceback (most recent call last):\n  File "/app/run.py", line 3, in <module>\n    main()\n'
           '  File "/usr/lib/python3.11/site-packages/lib/x.py", line 9, in f\n    g()\n'
           '  File "/app/pkg/m.py", line 4, in g\n    1/0\nZeroDivisionError: division by zero\n')
    d = digest(out)
    assert "/app/pkg/m.py" in d and "site-packages" not in d and "ZeroDivisionError" in d


def test_tools_only_with_flag(tmp_path, monkeypatch):
    from meshpool_harness.core.client import MockClient, tool_call
    from meshpool_harness.core.loop import Caps, run
    ws = _repo(tmp_path)
    subprocess.run("git init -q && git add -A && git -c user.email=x@y -c user.name=x commit -qm i", shell=True,
                   cwd=tmp_path)
    for flag, expect in (("0", False), ("1", True)):
        monkeypatch.setenv("MH_OBSERVE", flag)
        c = MockClient([tool_call("finish", {"answer": "x", "evidence": []})] * 4)
        run("t", client=c, model="m", profile=None, ws=ws, ctx={"task_id": "t"}, system_text="s",
            caps=Caps(max_turns=2))
        names = [t["function"]["name"] for t in c.requests[0]["tools"]]
        assert ("probe" in names) is expect and ("trace" in names) is expect
