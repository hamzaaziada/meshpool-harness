"""Rate limits never leave a task half done: the agent waits them out on their own clock, a turn that still ends on
one is picked up where it stopped by the next message, and the app picks it up by itself after a pause. No network."""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import RESUME_NOTE, Agent  # noqa: E402
from meshpool_harness.core.client import rate_limit_wait  # noqa: E402
from meshpool_harness.ui import agent_api as A  # noqa: E402

TURN = {"in": 0, "cached": 0, "out": 0, "calls": 0}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.delenv("MH_RATE_LIMIT_WAIT_S", raising=False)


def _ok(text="done"):
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}], "usage": {}}, False


def _limited(n, msg="HTTP 429: too many requests"):
    """A _post that is rate limited n times, then answers."""
    calls = []

    def post(base, key, body, stream):
        calls.append(1)
        if len(calls) <= n:
            raise RuntimeError(msg)
        return _ok()
    return post, calls


def test_many_rate_limits_in_a_row_are_waited_out_not_counted_as_failures(tmp_path):
    a = Agent(str(tmp_path), mode="auto")
    waits = []
    a._sleep = waits.append
    a._post, calls = _limited(15)                                # well past the 8 attempts other errors get
    resp, _ = a._chat(dict(TURN))
    assert resp["content"] == "done" and len(calls) == 16 and len(waits) == 15
    assert a._n429 == 0                                          # a success resets the backoff


def test_a_long_retry_after_is_honoured_up_to_the_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_RATE_LIMIT_WAIT_S", "900")
    a = Agent(str(tmp_path), mode="auto")
    waits = []
    a._sleep = waits.append
    a._post, _ = _limited(1, "HTTP 429: token window used up [retry-after: 600]")
    a._chat(dict(TURN))
    assert waits == [600.0]                                      # not cut to 300 s and retried too early
    assert rate_limit_wait("x [retry-after: 600]", 1) == 300.0     # callers without a budget keep the old cap


def test_the_turn_ends_once_the_wait_budget_is_spent(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_RATE_LIMIT_WAIT_S", "20")
    a = Agent(str(tmp_path), mode="auto")
    waits = []
    a._sleep = waits.append
    a._post, _ = _limited(10 ** 6)
    with pytest.raises(RuntimeError, match="HTTP 429"):
        a._chat(dict(TURN))
    assert sum(waits) == pytest.approx(20)


def test_a_turn_cut_off_by_a_rate_limit_is_picked_up_where_it_stopped(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_RATE_LIMIT_WAIT_S", "0")
    ev = []
    a = Agent(str(tmp_path), mode="auto", emit=ev.append)
    a._sleep = lambda s: None
    a._post, _ = _limited(10 ** 6)
    a.send("build the report")
    err = [e["text"] for e in ev if e["type"] == "error"][-1]
    assert "continue" in err and "kept" in err and "HTTP 429" in err   # says how to carry on, and the detail
    assert a.unfinished

    a2 = Agent.resume(str(tmp_path), a.id, mode="auto", emit=ev.append)   # survives an app restart
    assert a2.unfinished
    seen = []

    def post(base, key, body, stream):
        seen.append(body["messages"][-1]["content"])
        return _ok("picked up")
    a2._post = post
    assert a2.send("continue") == "picked up"
    assert seen[0].startswith("continue") and seen[0].endswith(RESUME_NOTE)
    assert [e["text"] for e in ev if e["type"] == "user"][-1] == "continue"   # the note is hidden in the chat
    assert not a2.unfinished
    a2.send("next thing")
    assert RESUME_NOTE not in seen[-1]                           # only the first message after the cut-off


def test_a_plan_limit_that_resets_soon_is_waited_for(tmp_path, monkeypatch):
    from meshpool_harness import providers as PV
    from meshpool_harness.core import chatgpt as CG
    from meshpool_harness.core import wire as W
    monkeypatch.setattr(PV, "endpoint", lambda prov: ("http://relay", "k"))
    monkeypatch.setattr(W, "from_responses", lambda r: r)
    soon, later = time.time() + 120, time.time() + 11 * 86400
    raised = [CG.UsageLimit("Usage limit reached.", soon)]
    monkeypatch.setattr(CG, "chat", lambda body, **k: (_ for _ in ()).throw(raised.pop()) if raised
                        else _ok("back")[0])
    a = Agent(str(tmp_path), mode="auto")
    a.model = "chatgpt:gpt-x"
    waits = []
    a._sleep = waits.append
    resp, _ = a._chat(dict(TURN))
    assert resp["content"] == "back" and 120 <= waits[0] <= 126
    raised.append(CG.UsageLimit("Usage limit reached.", later))   # days away: end the turn at once
    with pytest.raises(CG.UsageLimit):
        a._chat(dict(TURN))


# ---------- the app picks the task up by itself ----------------------------------------------------------------------

def _session():
    s = A.Session.__new__(A.Session)
    s.events, s.lock, s.goal, s.id, s.queue, s._stop_req = [], threading.Lock(), "", "test-resume", [], False
    s.emit = lambda ev: s.events.append(ev)
    s.agent = type("Ag", (), {"_stop": False})()
    return s


def _turn(s, *extra):
    n = len(s.events)
    s.events += [{"type": "user", "text": "go"}, *extra, {"type": "turn_end"}]
    return n


BUSY = {"type": "error", "text": "The model is busy right now (rate limited). (HTTP 429: limit reached)"}


def test_the_app_picks_a_rate_limited_task_up_by_itself(monkeypatch):
    monkeypatch.setattr(A, "RESUME_WAIT_S", 0.01)
    s = _session()
    for i in range(1, A.RESUME_TRIES + 1):
        assert s._resume_next(_turn(s, BUSY)) == (A.RESUME_NEXT, [])
        assert f"try {i} of {A.RESUME_TRIES}" in s.events[-1]["text"]
    assert s._resume_next(_turn(s, BUSY)) is None                  # gives up after a few tries, says how to go on
    assert "continue" in s.events[-1]["text"]
    assert s._resume_next(_turn(s, BUSY)) == (A.RESUME_NEXT, [])   # the count starts again


def test_other_harnesses_rate_limit_errors_count_too(monkeypatch):
    monkeypatch.setattr(A, "RESUME_WAIT_S", 0.01)
    s = _session()
    for text in ("Internal error: Error code: 429 - {'error': {'type': 'rate_limit_error'}}",
                 "429 Too Many Requests", "Provider overloaded, try again later"):
        assert s._resume_next(_turn(s, {"type": "error", "text": text})) == (A.RESUME_NEXT, []), text
    s._resume_n = 0
    assert s._resume_next(_turn(s, {"type": "error", "text": "Usage limit reached on ChatGPT Go. It resets in "
                                                             "11 days."})) is None


def test_no_resume_after_stop_a_good_turn_or_another_error(monkeypatch):
    monkeypatch.setattr(A, "RESUME_WAIT_S", 0.01)
    s = _session()
    assert s._resume_next(_turn(s, BUSY, {"type": "interrupted", "text": "Stopped."})) is None
    assert s._resume_next(_turn(s, {"type": "assistant", "text": "done"})) is None
    assert s._resume_next(_turn(s, {"type": "error", "text": "The endpoint rejected the API key."})) is None
    monkeypatch.setenv("MH_AUTO_RESUME", "0")
    assert s._resume_next(_turn(s, BUSY)) is None


def test_stop_or_a_message_during_the_wait_takes_over(monkeypatch):
    monkeypatch.setattr(A, "RESUME_WAIT_S", 5)
    s = _session()
    threading.Timer(0.3, lambda: s.queue.append(("do this instead", []))).start()
    t0 = time.time()
    assert s._resume_next(_turn(s, BUSY)) == ("do this instead", []) and time.time() - t0 < 2
    threading.Timer(0.3, lambda: setattr(s, "_stop_req", True)).start()
    t0 = time.time()
    assert s._resume_next(_turn(s, BUSY)) is None and time.time() - t0 < 2
