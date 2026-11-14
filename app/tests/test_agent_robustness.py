"""App agent robustness: bad tool arguments, dropped streams, context overflow, plan mode for MCP tools, steers that
arrive late, retries that run out, replies cut off at the output limit, typed brief answers. No network, no model."""
import http.client
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent  # noqa: E402
from meshpool_harness.core.client import classify_error  # noqa: E402


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MH_AGENT_V3", "1")


def _script(agent, replies, finish=None):
    seq, fins = list(replies), list(finish or [])

    def fake(base, key, body, stream):
        return {"choices": [{"message": seq.pop(0), "finish_reason": fins.pop(0) if fins else "stop"}],
                "usage": {}}, False
    agent._post = fake


def _call(name, args, cid="c1", raw=None):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": raw if raw is not None
                                                     else json.dumps(args)}}]}


def _tools(a):
    return [m["content"] for m in a.messages if m["role"] == "tool"]


def test_write_file_with_object_content_does_not_crash_the_turn(tmp_path):
    a = Agent(str(tmp_path), mode="auto")
    _script(a, [_call("write_file", {"path": "cfg.json", "content": {"a": 1}}),
                _call("write_file", {"path": "notes.txt", "content": {"a": 1}}, cid="c2"),
                _call("web_research", {"question": None}, cid="c3"),
                {"role": "assistant", "content": "done"}])
    assert a.send("go") == "done"
    assert json.loads((tmp_path / "cfg.json").read_text()) == {"a": 1}
    outs = _tools(a)
    assert outs[0].startswith("wrote cfg.json") and "must be string" in outs[1] and outs[2].startswith("ERROR")


def test_incomplete_read_is_a_retryable_connection_error(tmp_path, monkeypatch):
    a = Agent(str(tmp_path), mode="auto")

    def urlopen(*a_, **k):
        raise http.client.IncompleteRead(b"partial")
    monkeypatch.setattr(core.urllib.request, "urlopen", urlopen)
    with pytest.raises(RuntimeError, match="HTTP 0"):
        a._post("http://x", "k", {"model": "m", "messages": []}, True)
    assert classify_error("HTTP 0: connection problem (IncompleteRead)") == "transient"


def test_mcp_output_is_capped_and_context_overflow_is_compacted(tmp_path, monkeypatch):
    class FakeMcp:
        last_images = []

        def has(self, n):
            return n.startswith("mcp__")

        def call(self, n, args):
            return "x" * 50000, False

        def schemas(self):
            return []
    a = Agent(str(tmp_path), mode="auto", mcp=FakeMcp())
    _script(a, [_call("mcp__web__fetch", {}), {"role": "assistant", "content": "ok"}])
    a.send("go")
    out = _tools(a)[0]
    assert len(out) < 10000 and "chars truncated" in out

    b = Agent(str(tmp_path), mode="auto")         # an over-long conversation: the endpoint says so once
    big = json.dumps({"path": "f.txt", "content": "y" * 40000})
    b.messages += [{"role": "user", "content": "start"},
                   {"role": "assistant", "content": "", "tool_calls": [
                       {"id": "w", "type": "function", "function": {"name": "write_file", "arguments": big}}]},
                   {"role": "tool", "tool_call_id": "w", "content": "z" * 40000}]
    calls = []

    def post(base, key, body, stream):
        calls.append(sum(len(json.dumps(m)) for m in body["messages"]))
        if len(calls) == 1:
            raise RuntimeError("HTTP 400: This model's maximum context length is 131072 tokens")
        return {"choices": [{"message": {"content": "fine"}, "finish_reason": "stop"}], "usage": {}}, False
    b._post = post
    assert b.send("next") == "fine"
    assert len(calls) == 2 and calls[1] < calls[0] - 60000
    assert json.loads(b.messages[2]["tool_calls"][0]["function"]["arguments"])["path"] == "f.txt"
    assert core._context_size([{"role": "assistant", "content": [{"type": "text", "text": "abc"}],
                                "tool_calls": [{"function": {"arguments": "12345"}}]}]) == 8


def test_plan_mode_refuses_mcp_tools_that_change_things(tmp_path):
    ran = []

    class FakeMcp:
        last_images = []

        def has(self, n):
            return n.startswith("mcp__")

        def call(self, n, args):
            ran.append(n)
            return "ok", False

        def schemas(self):
            return []
    a = Agent(str(tmp_path), mode="auto", mcp=FakeMcp())
    a.plan = True
    _script(a, [_call("mcp__browser__browser_click", {"ref": "e1"}),
                _call("mcp__meshpool_assistant__memory_forget", {"id": 1}, cid="c2"),
                _call("mcp__meshpool_web__web_search", {"query": "q"}, cid="c3"),
                {"role": "assistant", "content": "plan"}])
    a.send("plan it")
    outs = _tools(a)
    assert "not available in plan mode" in outs[0] and "not available in plan mode" in outs[1]
    assert ran == ["mcp__meshpool_web__web_search"]


def test_a_late_steer_is_answered(tmp_path):
    a = Agent(str(tmp_path), mode="auto")
    replies = iter([{"role": "assistant", "content": "first"}, {"role": "assistant", "content": "second"}])

    def post(base, key, body, stream):
        msg = next(replies)
        if msg["content"] == "first":
            a.steer("also this")                     # arrives while the final reply is being written
        return {"choices": [{"message": msg, "finish_reason": "stop"}], "usage": {}}, False
    a._post = post
    assert a.send("go") == "second" and not a.has_steers()
    assert a.resume_steers() is None
    with a._inbox_lock:
        a._inbox.append(("after the turn", []))
    _script(a, [{"role": "assistant", "content": "third"}])
    assert a.resume_steers() == "third" and "after the turn" in a.messages[-2]["content"]


def test_retries_that_all_wait_raise_the_last_error(tmp_path, monkeypatch):
    a = Agent(str(tmp_path), mode="auto")
    monkeypatch.setattr(Agent, "_sleep", lambda self, s: None)

    def post(base, key, body, stream):
        raise RuntimeError("HTTP 429: slow down")
    a._post = post
    with pytest.raises(RuntimeError, match="HTTP 429"):
        a._chat({"in": 0, "cached": 0, "out": 0, "calls": 0})


def test_vision_is_kept_after_an_unrelated_400(tmp_path):
    a = Agent(str(tmp_path), mode="auto")
    a.messages.append({"role": "user", "content": [{"type": "text", "text": "see"}, {
        "type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]})
    n = []

    def post(base, key, body, stream):
        n.append(1)
        if len(n) == 1:
            raise RuntimeError("HTTP 400: rejected as invalid")
        return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}, False
    a._post = post
    a._sleep = lambda s: None
    a._chat({"in": 0, "cached": 0, "out": 0, "calls": 0})
    assert not getattr(a, "_no_vision", False) and core._has_images(a.messages)
    assert classify_error("HTTP 400: image input is not supported") == "vision"
    assert classify_error("HTTP 400: bad json") == "bad_request"


def test_output_limit_cut_offs(tmp_path):
    a = Agent(str(tmp_path), mode="auto")
    _script(a, [_call("write_file", {}, raw='{"path": "big.py", "content": "print(1)\\npri'),
                {"role": "assistant", "content": "Here is the first half"},
                {"role": "assistant", "content": " and the second half."}],
            finish=["length", "length", "stop"])
    assert a.send("go") == "Here is the first half and the second half."
    out = _tools(a)[0]
    assert "cut off at the model's output limit" in out and "append=true" in out
    assert not (tmp_path / "big.py").exists()


def test_typed_brief_answer_reaches_the_engine(tmp_path):
    a = Agent(str(tmp_path), mode="ask", profile="work")
    a.ask = lambda qid, qs: "for my team, 6 slides, forest look, dense pre-read"
    req = a._deck_brief({"engine": "slides", "request": "Deck on the Q3 results"})
    assert "for my team, 6 slides, forest look" in req and "navy theme" not in req


def test_loop_reports_any_tool_exception_to_the_model(tmp_path, monkeypatch):
    from meshpool_harness.core.client import MockClient, tool_call
    from meshpool_harness.core.loop import run
    from meshpool_harness.core.tools import Workspace
    (tmp_path / "d").mkdir()
    client = MockClient([tool_call("write_file", {"path": "d", "content": "x"}), tool_call("glob", {"pattern": "/abs/*.py"}),
                         tool_call("finish", {"answer": "done", "evidence": []})])
    r = run("look", client=client, model="m", profile=None, ws=Workspace(str(tmp_path), str(tmp_path / ".run")),
            ctx={"task_id": "x"}, system_text="s", require_check=False)
    outs = [m["content"] for m in client.requests[-1]["messages"] if m["role"] == "tool"]
    assert r.ok and outs[0].startswith("ERROR: IsADirectoryError") and outs[1].startswith("ERROR: NotImplementedError")

