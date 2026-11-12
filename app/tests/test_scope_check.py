"""MH_SCOPE_CHECK: one-time scope report at the first finish when the change spreads (no model calls)."""
import subprocess

from meshpool_harness.core.client import MockClient, tool_call
from meshpool_harness.core.loop import Caps, run
from meshpool_harness.core.tools import Workspace

CTX = {"task_id": "t", "model": "mock", "profile": "deepseek"}
T = "python -m pytest -q tests"
FIN = tool_call("finish", {"answer": "fixed", "evidence": [T]})


def _repo(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "other.py").write_text("X = 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    subprocess.run("git init -q && git add -A && git -c user.email=x@y -c user.name=x commit -qm init", shell=True,
                   cwd=tmp_path)
    return tmp_path


def _go(d, extra_edit, monkeypatch, flag="1"):
    if flag:
        monkeypatch.setenv("MH_SCOPE_CHECK", flag)
    else:
        monkeypatch.delenv("MH_SCOPE_CHECK", raising=False)
    script = [tool_call("read_file", {"path": "calc.py"}),
              tool_call("edit", {"path": "calc.py", "old": "a - b", "new": "a + b"})]
    if extra_edit:
        script += [tool_call("read_file", {"path": "other.py"}),
                   tool_call("edit", {"path": "other.py", "old": "X = 1", "new": "X = 2"})]
    script += [tool_call("run_tests", {"command": T}), FIN, FIN]
    c = MockClient(script)
    r = run("fix add", client=c, model="mock", profile=None, ws=Workspace(str(d), str(d / ".run")), ctx=CTX,
            system_text="sys", caps=Caps(max_turns=10))
    tools = [m["content"] for m in c.requests[-1]["messages"] if m["role"] == "tool"]
    return r, tools


def test_small_fix_not_shown(tmp_path, monkeypatch):
    r, tools = _go(_repo(tmp_path), False, monkeypatch)
    assert r.ok and not any("Scope check" in x for x in tools)


def test_two_source_files_shown_once(tmp_path, monkeypatch):
    r, tools = _go(_repo(tmp_path), True, monkeypatch)
    assert r.ok
    shown = [x for x in tools if x.startswith("Scope check")]
    assert len(shown) == 1 and "other.py" in shown[0] and "calc.py" in shown[0] and "tests/" not in shown[0]


def test_off_by_default(tmp_path, monkeypatch):
    r, tools = _go(_repo(tmp_path), True, monkeypatch, flag=None)
    assert r.ok and not any("Scope check" in x for x in tools)
