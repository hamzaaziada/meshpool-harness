"""Harness v3 in the interactive agent: bash_many, todo follow-through, loop breaker, environment snapshot.
No network: the transport is replaced by a script."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _sessions_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "test-key-1234")


def _call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _run(tmp_path, replies, mode="auto", text="do it"):
    a = Agent(str(tmp_path), mode=mode)
    seq, seen = list(replies), []

    def fake(base, key, body, stream):
        seen.append(body["messages"])
        msg = seq.pop(0)
        return {"choices": [{"message": msg, "finish_reason": "stop"}], "usage": {}}, False
    a._post = fake
    out = a.send(text)
    return a, out, seen


def test_bash_many_runs_each_command(tmp_path):
    a, out, seen = _run(tmp_path, [
        {"content": "", "tool_calls": [_call(1, "bash_many", {"commands": ["echo one", "false", "echo three"]})]},
        {"content": "done"}])
    tool = [m for m in a.messages if m.get("role") == "tool"][0]["content"]
    assert "one" in tool and "three" in tool and "[exit 1]" in tool and out == "done"


def test_bash_many_asks_once_and_shares_bash_always(tmp_path):
    asked = []
    a = Agent(str(tmp_path), mode="ask", approve=lambda k, t, d: (asked.append((k, t, d)), ("always", ""))[1])
    ok, _ = a._permit("bash_many", {"commands": ["ls", "pwd"]})
    assert ok and asked[0][0] == "bash" and "ls\npwd" == asked[0][2]
    ok, _ = a._permit("bash", {"command": "echo hi"})          # "always" for commands covers both tools
    assert ok and len(asked) == 1


def test_open_todos_get_one_nudge(tmp_path):
    todo = {"items": [{"content": "write a.txt", "status": "completed"}, {"content": "write b.txt", "status": "pending"}]}
    a, out, seen = _run(tmp_path, [
        {"content": "", "tool_calls": [_call(1, "todo_write", todo)]},
        {"content": "all done"},              # ends with an open item: nudged once
        {"content": "b.txt is not needed"},   # second end of turn is accepted
    ])
    nudges = [m for m in a.messages if m.get("role") == "user" and "not completed" in str(m.get("content"))]
    assert len(nudges) == 1 and "write b.txt" in nudges[0]["content"] and out == "b.txt is not needed"


def test_old_todos_do_not_nudge_a_new_request(tmp_path):
    todo = {"items": [{"content": "x", "status": "pending"}]}
    a, out, _ = _run(tmp_path, [{"content": "", "tool_calls": [_call(1, "todo_write", todo)]}, {"content": "q?"},
                                {"content": "ok"}])
    a._post = lambda *x: ({"choices": [{"message": {"content": "hello"}}], "usage": {}}, False)
    assert a.send("something else") == "hello"


def test_repeated_calls_get_a_loop_note(tmp_path):
    same = {"content": "", "tool_calls": [_call(1, "glob", {"pattern": "*.nothing"})]}
    a, out, _ = _run(tmp_path, [same, same, same, {"content": "stopped"}])
    notes = [m for m in a.messages if m.get("role") == "user" and "repeating the same tool calls" in str(m.get("content"))]
    assert len(notes) == 1


def test_environment_snapshot_in_first_request_only(tmp_path):
    (tmp_path / "main.py").write_text("print(1)\n")
    a, _, seen = _run(tmp_path, [{"content": "hi"}])
    first = seen[0][1]["content"]
    assert "<environment_snapshot>" in first and "main.py" in first
    a._post = lambda base, key, body, stream: (seen.append(body["messages"]) or
                                                {"choices": [{"message": {"content": "again"}}], "usage": {}}, False)
    a.send("next")
    assert "<environment_snapshot>" not in seen[-1][-1]["content"]


def test_v3_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("MH_AGENT_V3", "0")
    import importlib
    m = importlib.reload(core)
    try:
        assert "bash_many" not in m.BUILTIN and not m.V3
    finally:
        monkeypatch.delenv("MH_AGENT_V3")
        importlib.reload(core)
