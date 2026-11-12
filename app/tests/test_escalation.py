import subprocess
from meshpool_harness.core.client import MockClient, tool_call
from engines.code.engine import solve

T = "python -m pytest -q tests"


def test_escalation_resets_and_retries(tmp_path, monkeypatch):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    subprocess.run("git init -q && git add -A && git -c user.email=x@y -c user.name=x commit -qm init", shell=True, cwd=tmp_path)
    monkeypatch.setenv("MH_MEMORY", "0"); monkeypatch.setenv("MH_TESTCMD", "0"); monkeypatch.setenv("MH_MAX_SECONDS", "3600")
    first = [tool_call("read_file", {"path": "calc.py"}, cid="a1"),
             tool_call("edit", {"path": "calc.py", "old": "a - b", "new": "a * b"}, cid="a2"),
             tool_call("glob", {"pattern": "*.py"}, cid="a3"), tool_call("glob", {"pattern": "*"}, cid="a4")]  # never finishes
    second = [tool_call("read_file", {"path": "calc.py"}, cid="b1"),
              tool_call("edit", {"path": "calc.py", "old": "a - b", "new": "a + b"}, cid="b2"),
              tool_call("run_tests", {"command": T}, cid="b3"),
              tool_call("finish", {"answer": "fixed", "evidence": [T]}, cid="b4")]
    c = MockClient(first + second)
    out = solve("fix add", workdir=str(tmp_path), task_id="t", max_turns=4, client=c)
    assert out["attempts"] == 2 and out["ok"]
    assert "a + b" in (tmp_path / "calc.py").read_text()      # second attempt started from a clean repo
