"""Coding agent and MCP client: no network, no model calls (the transport is replaced by a script)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness.agent import mcp as M  # noqa: E402
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _sessions_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")


def _cfg(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"mcpServers": {
        "demo": {"command": "python", "args": ["-m", "meshpool_harness.agent.demo_mcp_server"]},
        "off": {"command": "nope", "disabled": True},
        "broken": {"command": "definitely-not-a-command-xyz"}}}))
    return p


def test_mcp_stdio_demo_server(tmp_path):
    m = M.Manager(roots=[str(tmp_path)], path=_cfg(tmp_path)).start(timeout=30)
    try:
        st = {s["name"]: s for s in m.status()}
        assert st["demo"]["status"] == "connected" and "calculate" in st["demo"]["tools"]
        assert st["off"]["status"] == "disabled"
        assert st["broken"]["status"] == "error" and "not found" in st["broken"]["error"]
        names = [t["function"]["name"] for t in m.schemas()]
        assert "mcp__demo__calculate" in names
        out, err = m.call("mcp__demo__calculate", {"expression": "(1200*0.15)/12"})
        assert not err and out.endswith("= 15.0")
        out, err = m.call("mcp__demo__calculate", {"expression": "__import__('os')"})
        assert err
    finally:
        m.close()


def test_env_expansion(monkeypatch):
    monkeypatch.setenv("TOKEN_X", "abc")
    assert M._expand({"h": ["Bearer ${TOKEN_X}", "${MISSING_Y}"]}) == {"h": ["Bearer abc", ""]}


def _script(agent, replies):
    """Replace the network call with scripted assistant messages."""
    seq = list(replies)

    def fake(base, key, body, stream):
        msg = seq.pop(0)
        if msg.get("content") and stream:
            agent.emit({"type": "text_delta", "text": msg["content"]})
        return {"choices": [{"message": msg, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                          "prompt_tokens_details": {"cached_tokens": 60}}}, bool(msg.get("content"))
    agent._post = fake


def _call(name, args, cid="c1"):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def test_agent_edits_with_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    events, asked = [], []

    def approve(kind, title, detail):
        asked.append((kind, detail))
        return "always", ""
    a = Agent(str(tmp_path), emit=events.append, approve=approve, mode="ask")
    _script(a, [_call("write_file", {"path": "x.py", "content": "print(1)\n"}),
                _call("write_file", {"path": "y.py", "content": "print(2)\n"}, "c2"),
                {"role": "assistant", "content": "Done."}])
    assert a.send("make files") == "Done."
    assert (tmp_path / "x.py").read_text() == "print(1)\n" and (tmp_path / "y.py").exists()
    assert len(asked) == 1 and asked[0][0] == "edit" and "+print(1)" in asked[0][1]   # "always" covers the 2nd
    assert a.usage["in"] == 300 and a.usage["cached"] == 180
    assert [e["type"] for e in events].count("tool_end") == 2
    assert (a.dir / "session.json").exists()


def test_agent_denied_command_and_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    a = Agent(str(tmp_path), approve=lambda *x: ("no", "use echo"), mode="ask")
    _script(a, [_call("bash", {"command": "rm -rf build"}), {"role": "assistant", "content": "OK, I will not."}])
    a.send("clean up")
    tool_msg = [m for m in a.messages if m["role"] == "tool"][0]
    assert "denied" in tool_msg["content"] and "use echo" in tool_msg["content"]
    b = Agent.resume(str(tmp_path), a.id)
    assert b is not None and len(b.messages) == len(a.messages)


def test_agent_reads_without_asking_and_blocks_writes_outside(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    (tmp_path / "a.txt").write_text("hello\n")
    a = Agent(str(tmp_path), approve=None, mode="auto")
    _script(a, [_call("read_file", {"path": "a.txt"}),
                _call("write_file", {"path": "../outside.txt", "content": "x"}, "c2"),
                {"role": "assistant", "content": "ok"}])
    a.send("go")
    outs = [m["content"] for m in a.messages if m["role"] == "tool"]
    assert "hello" in outs[0] and "inside the workdir" in outs[1]
    assert not (tmp_path.parent / "outside.txt").exists()


def test_repair_after_interrupt(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    a = Agent(str(tmp_path), mode="auto")
    a.messages.append({"role": "user", "content": "x"})
    a.messages.append(_call("bash", {"command": "sleep 100"}))
    a._repair()
    assert a.messages[-1]["role"] == "tool" and a.messages[-1]["tool_call_id"] == "c1"


def test_cli_routes_bare_command_to_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "none.config"))
    from meshpool_harness import cli
    seen = {}
    import meshpool_harness.agent.tui as tui
    monkeypatch.setattr(tui, "main", lambda *a: seen.setdefault("args", a) and 0)
    cli.main(["--auto", "-p", "hi"])
    assert seen["args"][1] == "hi" and seen["args"][2] is True
