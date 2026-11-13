"""ask_user (questions with options) and the deck brief (engines/forge/brief.py): no network, no model calls."""
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness.agent import ask as ASK  # noqa: E402
from meshpool_harness.agent import core  # noqa: E402
from meshpool_harness.agent.core import Agent, WORK_SYSTEM  # noqa: E402
from meshpool_harness.core.tools import ToolError  # noqa: E402
from engines.forge import brief as BR  # noqa: E402

Q = {"questions": [
    {"question": "Who is the deck for?", "header": "Audience", "options": [
        {"label": "The board", "description": "Decision deck", "recommended": True},
        {"label": "My team"}]},
    {"question": "How dense?", "header": "Density", "options": [
        {"label": "present"}, {"label": "balanced", "recommended": True}, {"label": "read"}]}]}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.delenv("MH_ASK_AUTO", raising=False)
    monkeypatch.delenv("MH_ASK_TIMEOUT_S", raising=False)


def _script(agent, replies):
    seq = list(replies)

    def fake(base, key, body, stream):
        return {"choices": [{"message": seq.pop(0), "finish_reason": "stop"}], "usage": {}}, False
    agent._post = fake


def _call(name, args, cid="c1"):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def _tool_out(a):
    return [m["content"] for m in a.messages if m["role"] == "tool"]


def _run(a, text):
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("r", a.send(text)), daemon=True)
    t.start()
    return t, box


def _wait_question(a, events, limit=5):
    end = time.time() + limit
    while time.time() < end:
        q = a.pending_question()
        if q:
            return q
        time.sleep(0.02)
    raise AssertionError("no question came")


def test_blocks_and_resumes_with_ui_reply(tmp_path):
    events = []
    a = Agent(str(tmp_path), emit=events.append, mode="ask", profile="work")
    a.interactive = True
    _script(a, [_call("ask_user", Q), {"role": "assistant", "content": "Building."}])
    t, box = _run(a, "make a deck")
    q = _wait_question(a, events)
    time.sleep(0.3)
    assert t.is_alive()                                   # it waits for the answer
    ev = next(e for e in events if e["type"] == "question")
    assert ev["id"] == q["id"] and len(ev["questions"]) == 2 and ev["timeout_s"] > 0
    a.reply(q["id"], [{"selected": ["My team"], "other": ""}, {"selected": [], "other": "very visual please"}])
    t.join(5)
    assert box["r"] == "Building."
    out = _tool_out(a)[0]
    assert out.startswith("The user answered") and "My team" in out and '"very visual please"' in out
    done = next(e for e in events if e["type"] == "question_done")
    assert done["source"] == "user" and done["answers"][0]["selected"] == ["My team"]
    with pytest.raises(ValueError):
        a.reply(q["id"], [])                              # no longer waiting


def test_typed_reply_counts_as_answer(tmp_path):
    a = Agent(str(tmp_path), mode="ask")
    a.interactive = lambda: True
    _script(a, [_call("ask_user", Q), {"role": "assistant", "content": "ok"}])
    t, _ = _run(a, "go")
    q = _wait_question(a, [])
    a.reply(q["id"], "for investors, keep it short")
    t.join(5)
    assert "in their own words" in _tool_out(a)[0] and "for investors" in _tool_out(a)[0]


def test_headless_returns_recommended(tmp_path):
    events = []
    a = Agent(str(tmp_path), emit=events.append, mode="ask")      # no interactive client
    _script(a, [_call("ask_user", Q), {"role": "assistant", "content": "ok"}])
    a.send("go")
    out = _tool_out(a)[0]
    assert out.startswith("No answer") and "The board" in out and "balanced" in out and "assumptions" in out
    assert [e["source"] for e in events if e["type"] == "question_done"] == ["headless"]


def test_auto_mode_with_ask_auto(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_ASK_AUTO", "1")
    a = Agent(str(tmp_path), mode="auto")
    a.interactive = True
    _script(a, [_call("ask_user", Q), {"role": "assistant", "content": "ok"}])
    a.send("go")
    assert "auto mode" in _tool_out(a)[0]


def test_timeout_returns_recommended(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_ASK_TIMEOUT_S", "1")
    events = []
    a = Agent(str(tmp_path), emit=events.append, mode="ask")
    a.interactive = True
    _script(a, [_call("ask_user", Q), {"role": "assistant", "content": "ok"}])
    t0 = time.time()
    a.send("go")
    assert time.time() - t0 < 5
    out = _tool_out(a)[0]
    assert "did not answer" in out and "The board" in out
    assert [e["source"] for e in events if e["type"] == "question_done"] == ["timeout"]


def test_stop_interrupts_the_question(tmp_path):
    events = []
    a = Agent(str(tmp_path), emit=events.append, mode="ask")
    a.interactive = True
    _script(a, [_call("ask_user", Q)])
    t, _ = _run(a, "go")
    _wait_question(a, events)
    a.stop()
    t.join(5)
    assert not t.is_alive()
    assert any(e["type"] == "interrupted" for e in events)
    assert any(e["type"] == "question_done" and e["source"] == "stopped" for e in events)
    assert _tool_out(a)[0].startswith("Not run")


def test_frontend_ask_callback(tmp_path):
    a = Agent(str(tmp_path), mode="ask")
    a.ask = lambda qid, qs: [{"selected": [qs[0]["options"][1]["label"]]}, {"selected": ["read"]}]
    _script(a, [_call("ask_user", Q), {"role": "assistant", "content": "ok"}])
    a.send("go")
    assert "My team" in _tool_out(a)[0] and "read" in _tool_out(a)[0]


def test_schema_validation():
    assert "ask_user" in core.BUILTIN and "ask_user" in core.WORK_TOOLS and "ask_user" in core.PLAN_TOOLS
    with pytest.raises(ToolError):
        ASK.normalize({"questions": []})
    with pytest.raises(ToolError):
        ASK.normalize({"questions": [Q["questions"][0]] * 5})                 # more than 4 questions
    with pytest.raises(ToolError):
        ASK.normalize({"questions": [{"question": "x?", "options": [{"label": "only"}]}]})   # 1 option
    with pytest.raises(ToolError):
        ASK.normalize({"questions": [{"question": "x?", "options": [{"label": str(i)} for i in range(6)]}]})
    with pytest.raises(ToolError):
        ASK.normalize({"questions": [{"question": "", "options": [{"label": "a"}, {"label": "b"}]}]})
    with pytest.raises(ToolError):                     # "Other" is the app's own; it does not count
        ASK.normalize({"questions": [{"question": "x?", "options": [{"label": "a"}, {"label": "Other"}]}]})
    qs = ASK.normalize({"questions": [{"question": "x?", "options": [
        {"label": "a", "recommended": True}, {"label": "b", "recommended": True}, "c"]}]})
    assert [o["recommended"] for o in qs[0]["options"]] == [True, False, False]   # one recommendation
    assert qs[0]["header"] == "Question 1" and qs[0]["multi"] is False
    assert ASK.recommended(qs)[0]["selected"] == ["a"]
    # a malformed call is a tool error the model sees, not a crash
    a = Agent(str(Path(__file__).parent), mode="ask")
    _script(a, [_call("ask_user", {"questions": [{"question": "x?", "options": ["a"]}]}),
                {"role": "assistant", "content": "ok"}])
    a.send("go")
    assert _tool_out(a)[0].startswith("ERROR: ask_user")


def test_coerce_answers():
    qs = ASK.normalize(Q)
    got = ASK.coerce(qs, [{"selected": ["the board", "My team"]}, {}])
    assert got[0]["selected"] == ["The board"]                   # single choice keeps one, case-insensitive
    assert got[1]["default"] and got[1]["selected"] == ["balanced"]   # blank: the recommendation stands


def test_prompts_mention_ask_user(tmp_path):
    assert "ask_user" in WORK_SYSTEM and "density" in WORK_SYSTEM and "BRIEF NEEDED" in WORK_SYSTEM
    assert "ask_user" in core.SYSTEM
    assert "ask_user" in Agent(str(tmp_path), profile="work").system_prompt()


def test_events_replay_as_answered_card():
    from meshpool_harness.ui.agent_api import events_from_messages
    msgs = [{"role": "user", "content": "go"}, _call("ask_user", Q, "c9"),
            {"role": "tool", "tool_call_id": "c9", "content": "The user answered:\n1. Audience: Who? -> My team\nFollow"}]
    evs = events_from_messages(msgs)
    types = [e["type"] for e in evs]
    assert types == ["user", "question", "question_done"]
    assert evs[2]["text"] == "1. Audience: Who? -> My team"


# ---------------------------------------------------------------- the deck brief
def test_brief_needed_when_ambiguous():
    qs = BR.questions("Make a deck about our Q3 results")
    assert [q["header"] for q in qs] == ["Audience", "Density", "Length", "Look"]
    for q in qs:
        assert 2 <= len(q["options"]) <= 5 and sum(o["recommended"] for o in q["options"]) == 1
    ASK.normalize({"questions": qs})                            # the same shape as ask_user's
    txt = BR.needed_text(qs)
    assert txt.startswith("BRIEF NEEDED") and "brief_ok=true" in txt and "(recommended)" in txt


def test_brief_recommendations_follow_the_request():
    rec = BR.recommend("A deck for the board meeting on the budget")
    assert rec["audience"] == "Leadership or board" and rec["density"] == "present"
    assert BR.recommend("A pre-read for the steering committee")["density"] == "read"
    d = next(q for q in BR.questions("A deck for the board") if q["header"] == "Density")
    assert next(o["label"] for o in d["options"] if o["recommended"]) == "present"
    assert "Audience" not in [q["header"] for q in BR.questions("A deck for the board")]


def test_brief_settled_request_builds():
    req = "8-slide deck for the board, density: present, ocean theme, on Q3 results"
    assert BR.questions(req) == []
    assert BR.questions("Q3 deck", fields={"audience": "board", "density": "read", "slides": 6, "theme": "forest"}) == []
    text = BR.apply("Q3 deck", {"audience": "the board", "density": "present", "slides": "8 slides", "theme": "ocean"})
    assert "density: present" in text and "8 slides" in text and "ocean theme" in text and "audience: the board" in text
    from engines.forge.engine import theme_from
    assert theme_from(text) == {"palette": "ocean"}


def test_make_deck_tool(monkeypatch):
    from meshpool_harness.agent import office_mcp_server as O
    calls = []
    monkeypatch.setattr(O, "_engine", lambda engine, request, files: (calls.append(request) or ("built", False)))
    text, err = O.make_deck("Make a deck about our Q3 results")
    assert not err and text.startswith("BRIEF NEEDED") and not calls          # ambiguous: does not build
    text, err = O.make_deck("Q3 results deck", audience="the board", density="present", slides=8, theme="ocean")
    assert text == "built" and "density: present" in calls[-1] and "8 slides" in calls[-1]
    text, _ = O.make_deck("Make a deck about our Q3 results", brief_ok=True)  # skips the questions
    assert text == "built" and "density:" in calls[-1] and "10 slides" in calls[-1] and "navy theme" in calls[-1]
    monkeypatch.setenv("MH_ASK_AUTO", "1")                                   # headless: recommended answers
    text, _ = O.make_deck("Make a deck about our Q3 results")
    assert text == "built" and len(calls) == 3
    assert "brief_ok" in O.TOOLS["make_deck"][2] and "density" in O.TOOLS["make_deck"][2]


def test_engine_headless_completes_brief():
    out = BR.complete("A deck for the board on Q3")
    assert "density: present" in out and "8 slides" in out and "navy theme" in out
    assert BR.complete("8-slide deck for the board, density: read, ocean theme") == \
        "8-slide deck for the board, density: read, ocean theme"


def test_office_task_asks_the_brief(tmp_path, monkeypatch):
    seen = {}
    a = Agent(str(tmp_path), mode="ask", profile="work")
    a.ask = lambda qid, qs: (seen.setdefault("qs", qs), [{"selected": ["My team"]}, {"selected": ["read"]},
                                                         {"selected": ["5 slides"]}, {"selected": ["forest"]}])[1]
    req = a._deck_brief({"engine": "slides", "request": "Deck on the Q3 results"})
    assert [q["header"] for q in seen["qs"]] == ["Audience", "Density", "Length", "Look"]
    assert "density: read" in req and "5 slides" in req and "forest theme" in req and "audience: My team" in req
    b = Agent(str(tmp_path), mode="ask", profile="work")                      # headless: recommendations
    assert "navy theme" in b._deck_brief({"engine": "slides", "request": "Deck on the Q3 results"})
