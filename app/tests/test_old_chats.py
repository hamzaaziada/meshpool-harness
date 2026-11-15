"""Picking up a chat from weeks ago: a model the endpoint dropped, a history full of screenshots, and harnesses
(Hermes, Codex, Pi) whose own session for the chat is gone. No network, no model."""
import json
import sys
import textwrap
import time
from pathlib import Path

import pytest

from meshpool_harness import config as C
from meshpool_harness.agent import core as K
from meshpool_harness.core.client import classify_error
from meshpool_harness.harnesses.acp import ACPAgent, earlier_context

ECHO = textwrap.dedent('''
    import json, sys
    def send(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
    for line in sys.stdin:
        m = json.loads(line)
        meth, mid, p = m.get("method"), m.get("id"), m.get("params", {})
        if meth == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": 1, "agentCapabilities": {"loadSession": False}}})
        elif meth == "session/new":
            send({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": "fresh"}})
        elif meth == "session/set_model":
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found"}})
        elif meth == "session/prompt":
            text = p["prompt"][0]["text"]
            send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "fresh", "update": {
                "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "SAW:" + text}}}})
            send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
''')


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "none.json"))
    C.ensure_home()
    return tmp_path


def test_a_harness_that_lost_its_session_still_knows_the_chat(home):
    script = home / "echo_acp.py"
    script.write_text(ECHO)
    proj = home / "proj"
    proj.mkdir()
    manifest = {"id": "fake", "name": "Fake", "mode": "cowork", "type": "acp", "command": sys.executable,
                "args": [str(script)], "share_mcp": False, "office": False}
    old = {"id": "old-1", "workdir": str(proj), "title": "Pricing study", "created": time.time() - 31 * 86400,
           "acp_session": "gone-weeks-ago", "events": [
               {"type": "user", "text": "Compare the pricing of Acme and Globex"},
               {"type": "tool_start", "name": "web_search", "title": "acme pricing"},
               {"type": "assistant", "text": "Acme is $20 a seat; Globex is $35."}]}
    events = []
    a = ACPAgent.resume(old, manifest, emit=events.append, approve=lambda *x, **k: ("allow_once", ""), mode="auto")
    try:
        reply = a.send("and what about Initech?")
    finally:
        a.close()
    assert "Compare the pricing of Acme and Globex" in reply and "Acme is $20 a seat" in reply
    assert reply.rstrip().endswith("and what about Initech?")
    assert any("could not reopen its own session" in (e.get("text") or "") for e in events)
    events.clear()
    a2 = ACPAgent(manifest, str(proj), emit=events.append, approve=lambda *x, **k: ("allow_once", ""), mode="auto")
    try:
        assert a2.send("hello").endswith("SAW:hello")             # a new chat gets nothing extra
    finally:
        a2.close()


def test_earlier_context_keeps_the_latest_turns_within_the_limit():
    evs = [{"type": "user", "text": f"question {i} " + "x" * 300} for i in range(400)]
    out = earlier_context(evs, limit=5000)
    assert "question 399" in out and "question 0 " not in out and "[earlier messages left out]" in out
    assert len(out) < 6500 and earlier_context([]) == ""


def test_model_gone_and_request_too_large_are_read_right():
    assert classify_error("HTTP 400: Invalid model name passed in model=deepseek-v3.2-old. Call /v1/models") == \
        "model_missing"
    assert classify_error('HTTP 404: {"error": "The model `gpt-old` does not exist"}') == "model_missing"
    assert classify_error("HTTP 413: Request Entity Too Large") == "context"
    assert classify_error("HTTP 400: bad json") == "bad_request"


@pytest.fixture()
def agent(tmp_path, monkeypatch):
    monkeypatch.setattr(K, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "meshpool.config"))
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setenv("MH_MODEL", "today-model")
    return K.Agent(str(tmp_path), mode="auto")


def _ok():
    return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}, False


def test_an_old_chat_on_a_dropped_model_continues_on_todays(agent, monkeypatch):
    monkeypatch.setattr(K.Agent, "_sleep", lambda self, s: None)
    agent.model = "deepseek-v3.2-old"
    sent, events = [], []
    agent.emit = events.append

    def post(base, key, body, stream):
        sent.append(body["model"])
        if body["model"] != "today-model":
            raise RuntimeError(f"HTTP 400: Invalid model name passed in model={body['model']}.")
        return _ok()
    agent._post = post
    agent._chat({"in": 0, "cached": 0, "out": 0, "calls": 0})
    assert sent == ["deepseek-v3.2-old", "today-model"] and agent.model == "today-model"
    assert any("no longer available" in (e.get("text") or "") for e in events)


def test_old_screenshots_stay_in_the_chat_but_not_in_each_request(agent):
    img = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 1000}}
    for i in range(10):
        agent.messages.append({"role": "user", "content": [{"type": "text", "text": f"shot {i}"}, img]})
        agent.messages.append({"role": "assistant", "content": "seen"})
    body = agent._body()
    sent = sum(1 for m in body["messages"] if isinstance(m.get("content"), list)
               for p in m["content"] if p.get("type") == "image_url")
    kept = sum(1 for m in agent.messages if isinstance(m.get("content"), list)
               for p in m["content"] if p.get("type") == "image_url")
    assert sent == 4 and kept == 10


def test_the_real_gateways_answers_are_read_right():
    """Bodies measured on llm.meshpool.ai (2026-11-15)."""
    gone = ('HTTP 404: {"error":{"message":"The model `foo` does not exist or you do not have access to it. '
            '(reference: 383be86b2fb1)","type":"key_model_access_denied","param":"model","code":"404"}}')
    assert classify_error(gone) == "model_missing"
    too_long = ('HTTP 400: {"error":{"message":"prompt is too long: 1001984 tokens > 1000000 maximum. This model\'s '
                'maximum context length is 1000000 tokens. Reduce the length of the messages."}}')
    assert classify_error(too_long) == "context"                  # trimmed and retried, not a plain bad request
    assert classify_error("HTTP 502: upstream request failed") == "transient"   # plain-text 502: retried


def test_a_new_chat_whose_model_the_harness_refuses_is_not_treated_as_old(home):
    script = home / "echo_acp.py"
    script.write_text(ECHO)
    proj = home / "proj"
    proj.mkdir()
    manifest = {"id": "fake", "name": "Fake", "mode": "cowork", "type": "acp", "command": sys.executable,
                "args": [str(script)], "share_mcp": False, "office": False}
    a = ACPAgent(manifest, str(proj), emit=lambda e: None, approve=lambda *x, **k: ("allow_once", ""), mode="auto")
    a.model = "some-model"                                         # set_model fails: a status line is stored
    try:
        assert a.send("hello").endswith("SAW:hello")               # no "started earlier" preamble, no repeat
    finally:
        a.close()


def test_a_request_too_large_keeps_the_screenshot_just_attached(agent, monkeypatch):
    monkeypatch.setattr(K.Agent, "_sleep", lambda self, s: None)
    img = lambda tag: {"type": "image_url", "image_url": {"url": "data:image/png;base64," + tag * 1000}}  # noqa: E731
    for i in range(6):
        agent.messages.append({"role": "user", "content": [{"type": "text", "text": f"old {i}"}, img("A")]})
        agent.messages.append({"role": "assistant", "content": "seen"})
    agent.messages.append({"role": "user", "content": [{"type": "text", "text": "what is wrong here?"}, img("Z")]})
    sent = []

    def post(base, key, body, stream):
        imgs = [p["image_url"]["url"][-3:] for m in body["messages"] if isinstance(m.get("content"), list)
                for p in m["content"] if p.get("type") == "image_url"]
        sent.append(imgs)
        if len(sent) == 1:
            raise RuntimeError("HTTP 413: Request Entity Too Large")
        return _ok()
    agent._post = post
    agent._chat({"in": 0, "cached": 0, "out": 0, "calls": 0})
    assert len(sent[0]) == 4 and len(sent[1]) == 2 and sent[1][-1] == "ZZZ"   # the new screenshot still goes


def test_a_request_too_big_for_the_gateway_answered_with_502_is_trimmed(agent, monkeypatch):
    """The real gateway answers an oversized body with a plain 502 'upstream request failed', not a 413."""
    monkeypatch.setattr(K.Agent, "_sleep", lambda self, s: None)
    lim = len(__import__("json").dumps(agent._body())) + 45000    # room for two 20 KB images over the base
    monkeypatch.setenv("MH_BIG_REQUEST", str(lim))
    agent._probe = lambda b, k, m: "ok"                            # small requests get through
    big = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "B" * 20000}}
    for i in range(6):
        agent.messages.append({"role": "user", "content": [{"type": "text", "text": f"shot {i}"}, big]})
        agent.messages.append({"role": "assistant", "content": "seen"})
    sizes = []

    def post(base, key, body, stream):
        n = len(__import__("json").dumps(body))
        sizes.append(n)
        if n > lim:
            raise RuntimeError("HTTP 502: upstream request failed")
        return _ok()
    agent._post = post
    agent._chat({"in": 0, "cached": 0, "out": 0, "calls": 0})
    assert len(sizes) == 2 and sizes[0] > lim >= sizes[1]         # trimmed once, then it went through


def test_one_image_too_big_alone_is_kept_and_the_user_told(agent, monkeypatch):
    monkeypatch.setattr(K.Agent, "_sleep", lambda self, s: None)
    monkeypatch.setenv("MH_BIG_REQUEST", str(len(__import__("json").dumps(agent._body())) + 40000))
    agent._probe = lambda b, k, m: "ok"
    huge = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "H" * 80000}}
    agent.messages.append({"role": "user", "content": [{"type": "text", "text": "what is this?"}, huge]})
    agent._post = lambda b, k, body, stream: (_ for _ in ()).throw(RuntimeError("HTTP 502: upstream request failed"))
    with pytest.raises(RuntimeError, match="attached image is too large"):
        agent._chat({"in": 0, "cached": 0, "out": 0, "calls": 0})
    assert any(p.get("type") == "image_url" for p in agent.messages[-1]["content"])   # never silently removed


def test_a_too_big_text_only_request_says_so_without_mentioning_an_image(agent, monkeypatch):
    monkeypatch.setattr(K.Agent, "_sleep", lambda self, s: None)
    agent.messages.append({"role": "user", "content": "log:\n" + "x" * 90000})
    monkeypatch.setenv("MH_BIG_REQUEST", str(len(__import__("json").dumps(agent._body())) - 1000))
    agent._probe = lambda b, k, m: "ok"
    agent._post = lambda b, k, body, stream: (_ for _ in ()).throw(RuntimeError("HTTP 502: upstream request failed"))
    with pytest.raises(RuntimeError, match="message is too large") as e:
        agent._chat({"in": 0, "cached": 0, "out": 0, "calls": 0})
    assert "image" not in str(e.value).split("(")[0]
