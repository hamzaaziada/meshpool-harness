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

_REAL_PROBE = Agent._probe                                        # before conftest stubs it per test


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
    monkeypatch.setattr(core, "_urlopen", lambda req, timeout, sink: urlopen())
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


def _big(a, n=30):
    for i in range(n):
        a.messages += [_call("read_file", {"path": f"f{i}"}, cid=f"c{i}"),
                       {"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 8000}]


def _model(a):
    from meshpool_harness import providers as PV
    return PV.split(a._body()["model"])[1]


def _turn():
    return {"in": 0, "cached": 0, "out": 0, "calls": 0}


def _ok():
    return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}, False


def test_a_stuck_gateway_is_waited_out_without_blind_retries(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_RECOVER_MIN", "30")
    monkeypatch.setattr(core.GW, "BACKOFF_S", 0.01)
    a = Agent(str(tmp_path), mode="auto")
    monkeypatch.setattr(Agent, "_sleep", lambda self, s: None)
    _big(a)
    calls, probes, events = [], iter(["HTTP 0: timed out", "HTTP 502", "ok"]), []
    a.emit = events.append

    def post(base, key, body, stream):
        calls.append((body["prompt_cache_key"], core._context_size(body["messages"])))
        if len(calls) == 1:
            raise RuntimeError("HTTP 0: connection problem (The read operation timed out)")
        return _ok()
    a._post = post
    a._probe = lambda base, key, model: next(probes)
    msg, _ = a._chat(_turn())
    assert msg["content"] == "ok" and len(calls) == 2              # one failed call, then wait: no pile-up
    assert calls[0][0] == a.id and calls[1][0] != a.id             # a fresh route after a stuck one
    assert calls[1][1] < calls[0][1] // 2                          # and a much smaller context
    texts = " ".join(e.get("text", "") for e in events)
    assert "isn't answering" in texts and "answering again" in texts


def test_a_slow_endpoint_gets_more_time_a_new_route_then_no_streaming(tmp_path, monkeypatch):
    a = Agent(str(tmp_path), mode="auto")
    monkeypatch.setattr(Agent, "_sleep", lambda self, s: None)
    a._probe = lambda base, key, model: "ok"                       # the endpoint answers small calls
    seen = []

    def post(base, key, body, stream):
        seen.append((a._first_scale, body["prompt_cache_key"], stream))
        if len(seen) <= 3:
            raise RuntimeError("HTTP 0: connection problem (The read operation timed out)")
        return _ok()
    a._post = post
    a._chat(_turn())
    assert [x[0] for x in seen] == [1.0, 2.5, 5.0, 5.0]            # a longer first-byte wait each time
    assert seen[1][1] == a.id and seen[2][1] != a.id               # new route after the second failure
    assert seen[2][2] and not seen[3][2]                           # no streaming after the third
    assert a._first_scale == 1.0                                   # back to normal once it went through


def test_an_endpoint_that_answers_small_calls_but_not_this_one_is_reported(tmp_path, monkeypatch):
    a = Agent(str(tmp_path), mode="auto")
    monkeypatch.setattr(Agent, "_sleep", lambda self, s: None)

    def post(base, key, body, stream):
        raise RuntimeError("HTTP 0: connection problem (timed out)")
    a._post = post
    a._probe = lambda base, key, model: "ok"
    with pytest.raises(RuntimeError, match="answers small requests"):
        a._chat(_turn())
    core.GW.reset()
    a._probe = lambda base, key, model: "HTTP 0: refused"         # down, and recovery off (tests default)
    with pytest.raises(RuntimeError, match="HTTP 0") as e:
        a._chat(_turn())
    assert "answers small" not in str(e.value)


def test_chats_share_one_prober_and_one_slot_while_the_endpoint_recovers(monkeypatch):
    import threading
    import time
    from meshpool_harness.core import gateway as GW
    monkeypatch.setattr(GW, "BACKOFF_S", 0.1)
    h = GW.health("http://x/v1", "key-12345678")
    assert h is GW.health("http://x/v1/", "other-12345678") and h.limit == 4
    assert h.check(lambda: "HTTP 0: timed out") == "down" and h.limit == 1
    probes, results = [], []

    def probe():
        probes.append(1)
        time.sleep(0.05)
        return "ok" if len(probes) >= 2 else "HTTP 0"

    def waiter():
        results.append(h.wait_until_up(probe, lambda s: time.sleep(0.02), lambda e: None, 5, lambda: None))
    ts = [threading.Thread(target=waiter) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    assert results == [True, True, True] and len(probes) == 2      # three chats, one prober
    h.acquire(lambda: None)
    got = []
    t = threading.Thread(target=lambda: (h.acquire(lambda: None), got.append(1)))
    t.start()
    time.sleep(0.2)
    assert not got                                                 # recovering: one call at a time
    h.release(True)
    t.join(2)
    assert got
    for _ in range(3):
        h.release(True)
    assert h.limit == 4                                            # open again after calls go through


def test_a_known_outage_holds_new_calls_until_the_endpoint_is_back(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_RECOVER_MIN", "30")
    from meshpool_harness import providers as PV
    a = Agent(str(tmp_path), mode="auto")
    monkeypatch.setattr(Agent, "_sleep", lambda self, s: None)
    base, key = PV.endpoint("key")
    core.GW.health(base, key, _model(a)).check(lambda: "HTTP 0")              # another chat found it down
    order = []
    a._probe = lambda b, k, m: order.append("probe") or "ok"
    a._post = lambda b, k, body, stream: order.append("post") or _ok()
    a._chat(_turn())
    assert order == ["probe", "post"]


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


def test_with_recovery_off_a_past_outage_does_not_block_the_app_forever(tmp_path, monkeypatch):
    from meshpool_harness import providers as PV
    a = Agent(str(tmp_path), mode="auto")
    base, key = PV.endpoint("key")
    core.GW.health(base, key, _model(a)).check(lambda: "HTTP 0")              # marked down earlier; MH_RECOVER_MIN is 0 here
    a._probe = lambda b, k, m: "ok"                                # it is back now
    a._post = lambda b, k, body, stream: _ok()
    assert a._chat(_turn())[0]["content"] == "ok" and not core.GW.health(base, key, _model(a)).is_down()


def _silent_server(log):
    """Takes the request and never answers; records when the client closes the connection."""
    import socket
    import threading
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)

    def run():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return

            def hold(c=c):
                c.settimeout(20)
                try:
                    while True:
                        if not c.recv(65536):
                            break
                        if "request" not in log:
                            log.append("request")
                    log.append("closed")
                except OSError:
                    log.append("closed")
            threading.Thread(target=hold, daemon=True).start()
    threading.Thread(target=run, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.getsockname()[1]}"


def test_stop_closes_a_call_still_waiting_for_its_first_byte(tmp_path, monkeypatch):
    import threading
    import time
    monkeypatch.setenv("MH_AGENT_FIRST_BYTE_S", "60")
    a = Agent(str(tmp_path), mode="auto")
    log = []
    srv, base = _silent_server(log)
    threading.Timer(0.5, lambda: setattr(a, "_stop", True)).start()
    with pytest.raises(core.Interrupted):
        a._wait(a._post, base, "k", {"model": "m", "messages": []}, True)
    t0 = time.time()
    while "closed" not in log and time.time() - t0 < 3:
        time.sleep(0.05)
    assert log == ["request", "closed"]                            # the gateway sees the disconnect at once
    srv.close()


def test_a_chat_started_during_an_outage_waits_once_not_twice(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_RECOVER_MIN", "0.02")                  # about a second
    monkeypatch.setattr(core.GW, "BACKOFF_S", 0.2)
    from meshpool_harness import providers as PV
    a = Agent(str(tmp_path), mode="auto")
    base, key = PV.endpoint("key")
    core.GW.health(base, key, _model(a)).check(lambda: "HTTP 0")
    probes = []
    a._probe = lambda b, k, m: probes.append(1) or "HTTP 0: still down"
    a._post = lambda *x: pytest.fail("no request while the endpoint is down")
    import time
    t0 = time.time()
    with pytest.raises(RuntimeError, match="did not come back"):
        a._chat(_turn())
    assert time.time() - t0 < 2.5 and len(probes) <= 6


def test_a_non_streamed_reply_that_streams_anyway_is_read(tmp_path, monkeypatch):
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(200)
            self.end_headers()
            body = "/html/" in self.path and b"<html>oops</html>" or (
                b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    a = Agent(str(tmp_path), mode="auto")
    resp, _ = a._post(base, "k", {"model": "m", "messages": []}, False)
    assert resp["choices"][0]["message"]["content"] == "hi"
    with pytest.raises(RuntimeError, match="HTTP 0: the endpoint sent an unreadable reply"):
        a._post(base + "/html", "k", {"model": "m", "messages": []}, False)
    srv.shutdown()


def test_a_stale_down_probe_does_not_undo_a_recovery(monkeypatch):
    from meshpool_harness.core import gateway as GW
    h = GW.health("http://y/v1", "k-1")

    def slow_down():                                               # this probe started while it was down...
        with h.cond:
            h._mark_up()                                           # ...and it came back meanwhile
        return "HTTP 0"
    assert h.check(slow_down) == "up" and not h.is_down()
    h.last_probe = (0.0, "")
    h.check(lambda: "HTTP 0")                                      # down for real
    assert h.check(lambda: pytest.fail("no probe while marked down")) == "down"


def test_a_500_from_one_model_is_an_answer_not_an_outage(monkeypatch):
    import urllib.error

    def answer(code):
        def fake(req, timeout):
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)
        return fake
    for code, want in ((500, "ok"), (429, "ok"), (401, "auth"), (502, "HTTP 502"), (503, "HTTP 503")):
        monkeypatch.setattr(core.urllib.request, "urlopen", answer(code))
        assert core._probe_once("http://x", "k", "m") == want


def test_one_models_outage_does_not_hold_back_another_model(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_RECOVER_MIN", "30")
    from meshpool_harness import providers as PV
    a = Agent(str(tmp_path), mode="auto")
    base, key = PV.endpoint("key")
    core.GW.health(base, key, "other-model").check(lambda: "HTTP 503")   # another chat's model is down
    sent = []
    a._post = lambda b, k, body, stream: sent.append(body["model"]) or _ok()
    a._chat(_turn())
    assert sent == [_model(a)]                                     # this chat's model went straight through
    assert core.GW.health(base, key, _model(a)).gate is core.GW.health(base, key, "other-model").gate


def test_a_stream_of_only_keep_alives_ends(tmp_path, monkeypatch):
    import http.server
    import threading
    import time

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    time.sleep(0.2)
            except OSError:
                pass

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("MH_AGENT_FIRST_BYTE_S", "1")
    monkeypatch.setenv("MH_AGENT_IDLE_S", "1")
    a = Agent(str(tmp_path), mode="auto")
    t0 = time.time()
    with pytest.raises(RuntimeError, match="only keep-alives"):
        a._post(f"http://127.0.0.1:{srv.server_address[1]}", "k", {"model": "m", "messages": []}, True)
    assert time.time() - t0 < 4
    srv.shutdown()


def test_stop_while_connecting_sends_nothing(tmp_path, monkeypatch):
    import threading
    import time
    a = Agent(str(tmp_path), mode="auto")
    log = []
    srv, base = _silent_server(log)
    real = core.http.client.HTTPConnection.connect

    def slow_connect(self):
        time.sleep(0.6)                                            # Stop lands while this is connecting
        real(self)
    monkeypatch.setattr(core.http.client.HTTPConnection, "connect", slow_connect)
    threading.Timer(0.2, lambda: setattr(a, "_stop", True)).start()
    with pytest.raises(core.Interrupted):
        a._wait(a._post, base, "k", {"model": "m", "messages": []}, True)
    time.sleep(1.2)
    assert "request" not in log                                    # closed without a request on it
    srv.close()


def test_route_rotation_adds_no_cache_fields_when_they_are_off(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_CACHE_KEY", "0")
    monkeypatch.setattr(Agent, "_sleep", lambda self, s: None)
    a = Agent(str(tmp_path), mode="auto")
    a._probe = lambda b, k, m: "ok"
    keys = []

    def post(base, key, body, stream):
        keys.append(sorted(k for k in body if k in ("prompt_cache_key", "user")))
        if len(keys) < 3:
            raise RuntimeError("HTTP 0: connection problem (timed out)")
        return _ok()
    a._post = post
    a._chat(_turn())
    assert keys == [[], [], []]


def test_simultaneous_failures_share_one_probe(monkeypatch):
    import threading
    import time
    from meshpool_harness.core import gateway as GW
    h = GW.health("http://z/v1", "k-2", "m")
    probes = []

    def probe():
        probes.append(1)
        time.sleep(0.3)
        return "HTTP 0"
    out = []
    ts = [threading.Thread(target=lambda: out.append(h.check(probe))) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(3)
    assert out == ["down"] * 4 and len(probes) == 1 and h.fails == 1


def test_stop_works_while_waiting_for_another_chats_probe():
    import threading
    import time
    from meshpool_harness.core import gateway as GW
    h = GW.health("http://w/v1", "k-3", "m")
    threading.Thread(target=lambda: h.check(lambda: time.sleep(5) or "HTTP 0"), daemon=True).start()
    time.sleep(0.2)
    t0 = time.time()

    def stop():
        if time.time() - t0 > 0.3:
            raise core.Interrupted()
    with pytest.raises(core.Interrupted):
        h.check(lambda: "ok", check_stop=stop)
    assert time.time() - t0 < 1.5


def test_a_reply_cut_short_after_some_text_resets_the_stream(tmp_path, monkeypatch):
    import http.server
    import threading
    import time

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"Hello "},"finish_reason":null}]}\n\n')
                for _ in range(40):
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    time.sleep(0.1)
            except OSError:
                pass

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("MH_AGENT_FIRST_BYTE_S", "1")
    monkeypatch.setenv("MH_AGENT_IDLE_S", "1")
    a = Agent(str(tmp_path), mode="auto")
    events = []
    a.emit = events.append
    with pytest.raises(RuntimeError, match="only keep-alives"):
        a._post(f"http://127.0.0.1:{srv.server_address[1]}", "k", {"model": "m", "messages": []}, True)
    assert any(e["type"] == "text_delta" for e in events) and events[-1]["type"] == "stream_reset"
    srv.shutdown()


def test_the_call_limit_holds_across_processes(tmp_path, monkeypatch):
    import subprocess
    import sys
    import time
    from meshpool_harness.core import gateway as GW
    monkeypatch.setenv("MH_MAX_INFLIGHT", "1")
    name = "test-" + tmp_path.name
    holder = subprocess.Popen([sys.executable, "-c", (
        "import sys, time; sys.path.insert(0, %r)\n"
        "from meshpool_harness.core import gateway as GW\n"
        "f = GW._Slots(%r).take(1, lambda: None, lambda: None)\n"
        "print('held', flush=True); time.sleep(1.5)\n") % (str(core.C.APP), name)],
        stdout=subprocess.PIPE, text=True, env={**__import__("os").environ, "MH_MAX_INFLIGHT": "1"})
    assert holder.stdout.readline().strip() == "held"              # another process (an assistant server) holds it
    t0 = time.time()
    f = GW._Slots(name).take(1, lambda: None, lambda: None)
    waited = time.time() - t0
    GW._Slots.give(f)
    holder.wait(5)
    assert waited > 0.8                                            # it waited for the other process's call


def test_the_call_limit_follows_settings_without_a_restart(tmp_path, monkeypatch):
    from meshpool_harness import config as C
    from meshpool_harness.core import gateway as GW
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "meshpool.config"))
    C.write({"MH_MAX_INFLIGHT": "2"})
    GW._CFG["t"] = 0
    g = GW.health("http://v/v1", "k-4", "m").gate
    assert g.limit == 2
    C.write({"MH_MAX_INFLIGHT": "6"})                              # changed in Settings while the app runs
    GW._CFG["t"] = 0
    assert g.limit == 6
    g.degrade()
    assert g.limit == 1


def test_a_model_that_cannot_be_replaced_fails_at_once(tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(Agent, "_sleep", lambda self, s: slept.append(s))
    monkeypatch.setenv("MH_MODEL", "gone-model")                   # today's default is the same model
    a = Agent(str(tmp_path), mode="auto")
    a.model = "gone-model"
    calls = []

    def post(base, key, body, stream):
        calls.append(1)
        raise RuntimeError("HTTP 400: The 'gone-model' model is not supported")
    a._post = post
    with pytest.raises(RuntimeError, match="does not serve the model"):
        a._chat(_turn())
    assert len(calls) == 1 and not slept                           # no pointless retries


def test_stop_closes_the_probe_connection_too(tmp_path, monkeypatch):
    import threading
    import time
    monkeypatch.setattr(Agent, "_probe", _REAL_PROBE)
    a = Agent(str(tmp_path), mode="auto")
    log = []
    srv, base = _silent_server(log)
    threading.Timer(0.5, lambda: setattr(a, "_stop", True)).start()
    with pytest.raises(core.Interrupted):
        a._wait(a._probe, base, "k", "m")
    t0 = time.time()
    while "closed" not in log and time.time() - t0 < 3:
        time.sleep(0.05)
    assert "closed" in log
    srv.close()


def test_a_call_waiting_for_a_shared_slot_follows_a_raised_limit(tmp_path):
    import threading
    import time
    from meshpool_harness.core import gateway as GW
    name = "live-" + tmp_path.name
    held = GW._Slots(name).take(1, lambda: None, lambda: None)     # slot 0 is busy
    limit = {"n": 1}
    threading.Timer(0.5, lambda: limit.update(n=2)).start()        # raised in Settings meanwhile
    t0 = time.time()
    got = GW._Slots(name).take(lambda: limit["n"], lambda: None, lambda: None)
    assert got is not None and time.time() - t0 < 2
    GW._Slots.give(got)
    GW._Slots.give(held)


def test_a_cleared_call_limit_means_the_default(tmp_path, monkeypatch):
    from meshpool_harness import config as C
    from meshpool_harness.core import gateway as GW
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "meshpool.config"))
    monkeypatch.setenv("MH_MAX_INFLIGHT", "1")                     # an assistant server's copy from start-up
    C.write({"MH_MAX_INFLIGHT": ""})                               # the user cleared the field since
    GW._CFG["t"] = 0
    assert GW._max_inflight() == 4
