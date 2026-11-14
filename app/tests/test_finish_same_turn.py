"""MH_CODE_FINISH_SAME_TURN: a check and finish in one turn; the guard still refuses a failing check (no model calls)."""
import subprocess

from meshpool_harness.core.client import MockClient, tool_call
from meshpool_harness.core.loop import Caps, run
from meshpool_harness.core.tools import Workspace

CTX = {"task_id": "t", "model": "mock", "profile": "deepseek"}
T = "python -m pytest -q tests"


def _repo(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    subprocess.run("git init -q && git add -A && git -c user.email=x@y -c user.name=x commit -qm init", shell=True,
                   cwd=tmp_path)
    return tmp_path


def _both(answer="fixed"):
    a, b = tool_call("run_tests", {"command": T}, "c1"), tool_call("finish", {"answer": answer, "evidence": [T]}, "c2")
    return {"role": "assistant", "content": None, "tool_calls": a["tool_calls"] + b["tool_calls"]}


def _go(d, script):
    c = MockClient(script)
    r = run("fix add", client=c, model="mock", profile=None, ws=Workspace(str(d), str(d / ".run")), ctx=CTX,
            system_text="sys", caps=Caps(max_turns=8))
    return r, c


def test_pass_and_finish_in_one_turn(tmp_path):
    d = _repo(tmp_path)
    r, c = _go(d, [tool_call("read_file", {"path": "calc.py"}),
                   tool_call("edit", {"path": "calc.py", "old": "a - b", "new": "a + b"}), _both()])
    assert r.ok and r.turns == 3 and len(c.requests) == 3


def test_failing_check_refuses_same_turn_finish(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_GUARDS", "all")                          # finish_check is off by default
    d = _repo(tmp_path)
    r, c = _go(d, [tool_call("read_file", {"path": "calc.py"}),
                   tool_call("edit", {"path": "calc.py", "old": "a - b", "new": "a * b"}), _both("wrong"),
                   tool_call("edit", {"path": "calc.py", "old": "a * b", "new": "a + b"}), _both()])
    assert r.ok and r.answer == "fixed"
    tool_msgs = [m for m in c.requests[3]["messages"] if m["role"] == "tool"]
    assert "finish_without_check" in tool_msgs[-1]["content"] and "[exit 1]" in tool_msgs[-2]["content"]


def test_prompt_line_only_with_flag(monkeypatch):
    import importlib
    eng = importlib.import_module("engines.code.engine")
    src = open(eng.__file__).read()
    assert 'os.environ.get("MH_CODE_FINISH_SAME_TURN") == "1"' in src
    assert "finish together in the same turn" not in eng.SYSTEM
