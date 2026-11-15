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



def _sse_server(first_delay, mid_delay):
    """A local streaming endpoint: waits first_delay before any byte, then sends one chunk, waits mid_delay, ends."""
    import http.server
    import threading
    import time

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            time.sleep(first_delay)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunk = {"choices": [{"delta": {"content": "hi"}, "finish_reason": None}]}
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.flush()
            time.sleep(mid_delay)
            done = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
            self.wfile.write(b"data: " + json.dumps(done).encode() + b"\n\ndata: [DONE]\n\n")

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_a_slow_first_byte_waits_longer_than_a_stalled_stream(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_AGENT_IDLE_S", "0.5")
    monkeypatch.setenv("MH_AGENT_FIRST_BYTE_S", "3")
    a = Agent(str(tmp_path), mode="auto")
    srv, base = _sse_server(first_delay=1.2, mid_delay=0)      # slow prefill: past the idle clock, inside first-byte
    resp, streamed = a._post(base, "k", {"model": "m", "messages": []}, True)
    assert resp["choices"][0]["message"]["content"] == "hi" and streamed
    srv.shutdown()
    srv, base = _sse_server(first_delay=0, mid_delay=1.2)      # a stream that goes quiet mid-reply is still cut
    with pytest.raises(RuntimeError, match="HTTP 0.*timed out"):
        a._post(base, "k", {"model": "m", "messages": []}, True)
    srv.shutdown()


def test_repeated_timeouts_on_a_large_context_compact_it(tmp_path, monkeypatch):
    a = Agent(str(tmp_path), mode="auto")
    monkeypatch.setattr(Agent, "_sleep", lambda self, s: None)
    for i in range(30):
        a.messages += [_call("read_file", {"path": f"f{i}"}, cid=f"c{i}"),
                       {"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 8000}]
    sizes = []

    def post(base, key, body, stream):
        sizes.append(core._context_size(body["messages"]))
        if len(sizes) < 3:
            raise RuntimeError("HTTP 0: connection problem (The read operation timed out)")
        return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}, False
    a._post = post
    a._chat({"in": 0, "cached": 0, "out": 0, "calls": 0})
    assert sizes[0] > 150000 and sizes[-1] < sizes[0] // 2


def _big(a, n=30):
    for i in range(n):
        a.messages += [_call("read_file", {"path": f"f{i}"}, cid=f"c{i}"),
                       {"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 8000}]


def test_an_outage_waits_for_the_endpoint_then_resumes_smaller_on_a_fresh_route(tmp_path, monkeypatch):
    a = Agent(str(tmp_path), mode="auto")
    monkeypatch.setattr(Agent, "_sleep", lambda self, s: None)
    _big(a)
    calls, probes, events = [], iter(["HTTP 0: timed out", "HTTP 502", "ok"]), []
    a.emit = events.append

    def post(base, key, body, stream):
        calls.append((stream, body["prompt_cache_key"], core._context_size(body["messages"])))
        if len(calls) <= 6:
            raise RuntimeError("HTTP 0: connection problem (The read operation timed out)")
        return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}, False
    a._post = post
    a._probe = lambda base, key, model: next(probes)
    msg, _ = a._chat({"in": 0, "cached": 0, "out": 0, "calls": 0})
    assert msg["content"] == "ok" and len(calls) == 7
    assert calls[0][0] and not calls[4][0]                        # streaming is dropped after the fourth failure
    assert calls[0][1] == a.id and calls[-1][1] != a.id           # a fresh route after a stuck one
    assert calls[-1][2] < calls[0][2] // 2                        # and a much smaller context
    texts = " ".join(e.get("text", "") for e in events)
    assert "isn't answering" in texts and "answering again" in texts


def test_an_endpoint_that_answers_small_calls_but_not_this_one_is_reported(tmp_path, monkeypatch):
    a = Agent(str(tmp_path), mode="auto")
    monkeypatch.setattr(Agent, "_sleep", lambda self, s: None)

    def post(base, key, body, stream):
        raise RuntimeError("HTTP 0: connection problem (timed out)")
    a._post = post
    a._probe = lambda base, key, model: "ok"
    with pytest.raises(RuntimeError, match="answers small requests"):
        a._chat({"in": 0, "cached": 0, "out": 0, "calls": 0})
    a._probe = lambda base, key, model: "HTTP 0: refused"
    monkeypatch.setenv("MH_RECOVER_MIN", "0")                     # recovery off: the plain error, as before
    with pytest.raises(RuntimeError, match="HTTP 0") as e:
        a._chat({"in": 0, "cached": 0, "out": 0, "calls": 0})
    assert "answers small" not in str(e.value)


def test_stop_closes_the_model_stream_at_once(tmp_path, monkeypatch):
    import threading
    import time
    monkeypatch.setenv("MH_AGENT_FIRST_BYTE_S", "30")
    a = Agent(str(tmp_path), mode="auto")
    srv, base = _sse_server(first_delay=0, mid_delay=20)          # streams one chunk, then hangs
    def stop_soon():
        time.sleep(0.5)
        a._stop = True
    threading.Thread(target=stop_soon, daemon=True).start()
    t0 = time.time()
    with pytest.raises(core.Interrupted):
        a._wait(a._post, base, "k", {"model": "m", "messages": []}, True)
    assert time.time() - t0 < 3 and a._live is None              # the socket was shut, not left to hang
    srv.shutdown()
