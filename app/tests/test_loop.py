import json, subprocess
from pathlib import Path
import pytest
from meshpool_harness.core.client import MockClient, tool_call
from meshpool_harness.core.loop import run, Caps, build_prefix
from meshpool_harness.core.tools import Workspace, validate, ToolError

CTX = {"task_id": "t", "model": "mock", "profile": "deepseek"}


def repo(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    subprocess.run("git init -q && git add -A && git -c user.email=x@y -c user.name=x commit -qm init", shell=True, cwd=tmp_path)
    return tmp_path


def go(tmp_path, script, **kw):
    ws = Workspace(str(tmp_path), str(tmp_path / ".run"))
    c = MockClient(script)
    r = run("fix add", client=c, model="mock", profile=None, ws=ws, ctx=CTX, system_text="sys", caps=Caps(max_turns=12), **kw)
    return r, c, ws


T = "python -m pytest -q tests"


def test_happy_path(tmp_path):
    d = repo(tmp_path)
    r, c, ws = go(d, [tool_call("read_file", {"path": "calc.py"}),
                      tool_call("edit", {"path": "calc.py", "old": "a - b", "new": "a + b"}),
                      tool_call("run_tests", {"command": T}),
                      tool_call("finish", {"answer": "fixed", "evidence": [T]})])
    assert r.ok and "a + b" in (d / "calc.py").read_text()


def test_finish_without_check_rejected(tmp_path):
    d = repo(tmp_path)
    r, c, ws = go(d, [tool_call("read_file", {"path": "calc.py"}),
                      tool_call("edit", {"path": "calc.py", "old": "a - b", "new": "a + b"}),
                      tool_call("finish", {"answer": "done", "evidence": ["trust me"]}),
                      tool_call("run_tests", {"command": T}),
                      tool_call("finish", {"answer": "done", "evidence": [T]})])
    assert r.ok
    tool_msgs = [m["content"] for m in c.requests[3]["messages"] if m["role"] == "tool"]
    assert any("finish_without_check" in m for m in tool_msgs)


def test_stale_edit_rejected(tmp_path):
    d = repo(tmp_path)
    ws = Workspace(str(d), str(d / ".run"))
    ws.read_file("calc.py")
    (d / "calc.py").write_text("def add(a, b):\n    return a - b  # changed\n")
    with pytest.raises(ToolError, match="stale edit"):
        ws.edit("calc.py", "a - b", "a + b")


def test_edit_requires_read_and_unique(tmp_path):
    d = repo(tmp_path)
    ws = Workspace(str(d), str(d / ".run"))
    with pytest.raises(ToolError, match="read calc.py before"):
        ws.edit("calc.py", "a", "b")
    ws.read_file("calc.py")
    with pytest.raises(ToolError, match="occurs"):
        ws.edit("calc.py", "a", "b")


def test_test_tampering_rejected(tmp_path):
    d = repo(tmp_path)
    r, c, ws = go(d, [tool_call("read_file", {"path": "tests/test_calc.py"}),
                      tool_call("edit", {"path": "tests/test_calc.py", "old": "== 5", "new": "== -1"}),
                      tool_call("run_tests", {"command": T}),
                      tool_call("finish", {"answer": "done", "evidence": [T]}),
                      tool_call("bash", {"command": "git checkout -- tests/test_calc.py"}),
                      tool_call("read_file", {"path": "calc.py"}),
                      tool_call("edit", {"path": "calc.py", "old": "a - b", "new": "a + b"}),
                      tool_call("run_tests", {"command": T}),
                      tool_call("finish", {"answer": "done", "evidence": [T]})])
    assert any("test_tampering" in m["content"] for m in c.requests[4]["messages"] if m["role"] == "tool")


def test_repeat_failure_guard(tmp_path):
    d = repo(tmp_path)
    script = [tool_call("run_tests", {"command": T}, cid=f"c{i}") for i in range(5)]
    r, c, ws = go(d, script)
    assert not r.ok and "aborted" in r.reason
    assert any("repeat_failure" in m["content"] for m in c.requests[3]["messages"] if m["role"] == "user")


def test_error_not_quoted_guard(tmp_path):
    d = repo(tmp_path)
    r, c, ws = go(d, [tool_call("run_tests", {"command": T}),
                      tool_call("glob", {"pattern": "*.py"}),
                      tool_call("read_file", {"path": "calc.py"}),
                      tool_call("edit", {"path": "calc.py", "old": "a - b", "new": "a + b"}),
                      tool_call("run_tests", {"command": T}),
                      tool_call("finish", {"answer": "ok", "evidence": [T]})])
    assert r.ok
    assert any("error_not_quoted" in m["content"] for m in c.requests[2]["messages"] if m["role"] == "user")


def test_invalid_tool_args_precise_error(tmp_path):
    with pytest.raises(ToolError, match="missing required argument 'path'"):
        validate("read_file", "{}", ["read_file"])
    with pytest.raises(ToolError, match="not valid JSON"):
        validate("read_file", "{path:", ["read_file"])
    with pytest.raises(ToolError, match="unknown tool"):
        validate("rm_rf", "{}", ["read_file"])


def test_prefix_is_byte_stable():
    a = build_prefix("sys", ["read_file", "bash"], {"pptx": "Slides skill\nmore"})
    b = build_prefix("sys", ["read_file", "bash"], {"pptx": "Slides skill\nmore"})
    assert a[2] == b[2] and a[0] == b[0]


def test_truncation_saves_full_output(tmp_path):
    d = repo(tmp_path)
    ws = Workspace(str(d), str(d / ".run"))
    out = ws.bash("python -c \"print('x'*20000)\"")
    assert "truncated" in out and list((d / ".run").glob("out_*"))


def test_writes_outside_workdir_blocked(tmp_path):
    d = repo(tmp_path)
    ws = Workspace(str(d), str(d / ".run"))
    with pytest.raises(ToolError, match="inside the workdir"):
        ws.write_file("/etc/evil.txt", "x")
