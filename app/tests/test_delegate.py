"""delegate_task: Hermes-style sub-agents for Meshpool's own agent (agent/delegate.py). No network, no model: a fake
model answers the chat and its sub-agents (told apart by the sub-agent block in their system prompt)."""
import json
import threading
import time

import pytest

from meshpool_harness.agent import core as K
from meshpool_harness.agent import delegate as D


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(K, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "meshpool.config"))
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    for k in ("MH_SUBAGENTS", "MH_SUBAGENT_MODEL", "MH_SUBAGENT_ACCESS", "MH_SUBAGENT_PARALLEL", "MH_SUBAGENT_STEPS",
              "MH_SUBAGENT_MINUTES", "MH_SUBAGENT_AUTO_APPROVE", "MH_SUBAGENT_BACKGROUND", "MH_SUBAGENT_DEPTH",
              "MH_SUBAGENT_SUMMARY_CHARS"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "proj").mkdir()
    return tmp_path / "proj"


def _msg(content="", calls=None):
    m = {"role": "assistant", "content": content}
    if calls:
        m["tool_calls"] = [{"id": f"c{i}-{time.time_ns()}", "type": "function",
                            "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": m, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10}}, False


def _is_child(body):
    return "You are a focused sub-agent" in body["messages"][0]["content"]


def _goal(body):
    return next(m["content"] for m in body["messages"][1:] if m["role"] == "user")


def fake(monkeypatch, child, parent=None, seen=None):
    """child(body) -> reply for sub-agents; parent defaults to: delegate once, then finish with what came back."""
    def post(self, base, key, body, stream):
        if seen is not None:
            seen.append({"child": _is_child(body), "tools": sorted(t["function"]["name"] for t in body["tools"]),
                         "model": body["model"], "system": body["messages"][0]["content"]})
        if _is_child(body):
            return child(body)
        return (parent or (lambda b: _msg("parent done")))(body)
    monkeypatch.setattr(K.Agent, "_post", post)


def _agent(proj, **kw):
    return K.Agent(str(proj), mode=kw.pop("mode", "auto"), **kw)


def test_a_batch_runs_in_parallel_reports_back_and_counts_toward_the_chat(monkeypatch, _env):
    seen, events = [], []
    fake(monkeypatch, lambda b: _msg(f"report for: {_goal(b)[:40]}"), seen=seen)
    a = _agent(_env, emit=events.append)
    out, ok = D.run_tool(a, {"tasks": [{"goal": "Research company Acme pricing", "context": "B2B only"},
                                       {"goal": "Research company Globex pricing"}]})
    assert ok and "2 sub-agent report(s)" in out and "report for: Research company Acme" in out
    assert "report for: Research company Globex" in out
    data = json.loads(out.rsplit("```json\n", 1)[1].rstrip("`\n"))
    assert [r["status"] for r in data["results"]] == ["completed", "completed"]
    assert a.usage["in"] == 200 and a.usage["calls"] == 2           # the chat pays for its sub-agents
    kinds = [(e["kind"], e["index"]) for e in events if e["type"] == "subagent"]
    assert ("start", 0) in kinds and ("done", 1) in kinds
    child_sys = [s["system"] for s in seen if s["child"]][0]
    assert "YOUR TASK:" in child_sys and str(_env) in child_sys     # fresh context: goal, context, workspace
    assert "B2B only" in [s["system"] for s in seen if s["child"] and "Acme" in s["system"]][0]


def test_children_get_the_chats_tools_minus_the_blocked_ones(monkeypatch, _env):
    seen = []
    fake(monkeypatch, lambda b: _msg("ok"), seen=seen)
    D.run_tool(_agent(_env), {"tasks": [{"goal": "Look at the project files"}]})
    tools = [s["tools"] for s in seen if s["child"]][0]
    assert "bash" in tools and "write_file" in tools                 # the chat's own tools
    assert "ask_user" not in tools and "delegate_task" not in tools  # needs a person; depth limit 1
    monkeypatch.setenv("MH_SUBAGENT_ACCESS", "read")
    seen.clear()
    D.run_tool(_agent(_env), {"tasks": [{"goal": "Look at the project files"}]})
    tools = [s["tools"] for s in seen if s["child"]][0]
    assert "bash" not in tools and "write_file" not in tools and "read_file" in tools


def test_what_would_need_a_yes_is_refused_unless_self_approval_is_on(monkeypatch, _env):
    def child(b):
        if b["messages"][-1]["role"] == "tool":
            return _msg("result: " + b["messages"][-1]["content"][:80])
        return _msg("", [("bash", {"command": "echo hi"})])
    fake(monkeypatch, child)
    out, _ = D.run_tool(_agent(_env, mode="ask"), {"tasks": [{"goal": "Run the echo command"}]})
    assert "sub-agents cannot ask for approval" in out
    monkeypatch.setenv("MH_SUBAGENT_AUTO_APPROVE", "1")
    out, _ = D.run_tool(_agent(_env, mode="ask"), {"tasks": [{"goal": "Run the echo command"}]})
    assert "[exit 0]" in out and "cannot ask" not in out


def test_depth_two_lets_children_delegate_once_more(monkeypatch, _env):
    seen = []
    fake(monkeypatch, lambda b: _msg("ok"), seen=seen)
    monkeypatch.setenv("MH_SUBAGENT_DEPTH", "2")
    D.run_tool(_agent(_env), {"tasks": [{"goal": "Plan and split the review"}]})
    assert "delegate_task" in [s["tools"] for s in seen if s["child"]][0]
    a = _agent(_env)
    a._delegate_depth = 2
    out, ok = D.run_tool(a, {"tasks": [{"goal": "Go one level deeper"}]})
    assert not ok and "depth limit" in out


def test_goals_are_checked_and_batches_capped(_env):
    a = _agent(_env)
    assert "placeholder" in D.run_tool(a, {"tasks": [{"goal": "TODO"}]})[0]
    assert "too short" in D.run_tool(a, {"tasks": [{"goal": "do it"}, {"goal": "Review the parser module"}]})[0]
    out, ok = D.run_tool(a, {"tasks": [{"goal": f"Review file number {i} carefully"} for i in range(13)]})
    assert not ok and "Too many tasks" in out


def test_output_schema_is_checked_with_one_correction(monkeypatch, _env):
    replies = iter(['{"name": "Acme"}', '{"name": "Acme", "price": 20}'])
    fake(monkeypatch, lambda b: _msg(next(replies)))
    sch = {"type": "object", "required": ["name", "price"], "properties": {"price": {"type": "number"}}}
    out, ok = D.run_tool(_agent(_env), {"tasks": [{"goal": "Find Acme's price", "output_schema": sch}]})
    r = json.loads(out.rsplit("```json\n", 1)[1].rstrip("`\n"))["results"][0]
    assert ok and r["schema_valid"] and r["schema_retries"] == 1 and '"price": 20' in out


def test_a_long_report_is_capped_and_kept_in_full_in_a_file(monkeypatch, _env):
    monkeypatch.setenv("MH_SUBAGENT_SUMMARY_CHARS", "2000")
    fake(monkeypatch, lambda b: _msg("A" * 1500 + "MIDDLE" + "Z" * 1500))
    out, _ = D.run_tool(_agent(_env), {"tasks": [{"goal": "Write a long report"}]})
    assert "MIDDLE" not in out.split("```json")[0] and "characters left out" in out
    path = out.split("The full report is in ")[1].split(" (read it")[0]
    assert "MIDDLE" in open(path).read()


def test_plan_mode_and_settings_off_leave_no_delegate_task(monkeypatch, _env):
    a = _agent(_env)
    assert "delegate_task" in [t["function"]["name"] for t in a.tool_schemas()]
    a.plan = True
    assert "delegate_task" not in [t["function"]["name"] for t in a.tool_schemas()]
    a.plan = False
    monkeypatch.setenv("MH_SUBAGENTS", "0")
    assert "delegate_task" not in [t["function"]["name"] for t in a.tool_schemas()]
    assert "off in Settings" in D.run_tool(a, {"tasks": [{"goal": "Research Acme"}]})[0]


def test_the_chat_calls_it_as_a_tool_end_to_end(monkeypatch, _env):
    def parent(b):
        if b["messages"][-1]["role"] == "tool":
            return _msg("summary: " + b["messages"][-1]["content"][:60])
        return _msg("", [("delegate_task", {"tasks": [{"goal": "Research company Acme"}]})])
    fake(monkeypatch, lambda b: _msg("acme sells widgets"), parent=parent)
    a = _agent(_env, mode="ask", approve=lambda *x: pytest.fail("delegating must not ask"))
    assert "1 sub-agent report" in a.send("research acme")


def test_children_use_the_chats_mcp_tools_minus_memory_scheduling_and_sub_agents():
    class Mcp:
        last_images = ["img"]

        def schemas(self):
            return [{"function": {"name": n}} for n in ("mcp__meshpool-assistant__memory_save",
                                                         "mcp__meshpool-assistant__schedule_task",
                                                         "mcp__meshpool-assistant__run_subtasks",
                                                         "mcp__meshpool-assistant__search_chats",
                                                         "mcp__meshpool-office__make_document")]

        def has(self, name):
            return True

        def call(self, name, args):
            return "made", False
    c = D.ChildMcp(Mcp())
    assert [s["function"]["name"] for s in c.schemas()] == ["mcp__meshpool-assistant__search_chats",
                                                             "mcp__meshpool-office__make_document"]
    assert not c.has("mcp__meshpool-assistant__memory_save") and c.has("mcp__meshpool-office__make_document")
    assert c.call("mcp__meshpool-office__make_document", {}) == ("made", False) and c.last_images == ["img"]


def test_children_never_read_outside_the_project_even_in_full_access(monkeypatch, _env, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("KEY")

    def child(b):
        if b["messages"][-1]["role"] == "tool":
            return _msg("got: " + b["messages"][-1]["content"][:120])
        return _msg("", [("read_file", {"path": str(secret)})])
    fake(monkeypatch, child)
    out, _ = D.run_tool(_agent(_env, mode="auto"), {"tasks": [{"goal": "Read the secret file"}]})
    assert "only read inside the project" in out and "KEY" not in out.split("```json")[0]


def test_children_follow_the_chats_mode_and_plan_mode_as_they_change(monkeypatch, _env):
    a = _agent(_env, mode="auto")

    def child(b):
        if b["messages"][-1]["role"] == "tool":
            return _msg("result: " + b["messages"][-1]["content"][:100])
        a.mode = "ask"                                              # the user tightens it while the sub-agent works
        return _msg("", [("bash", {"command": "echo hi > made.txt"})])
    fake(monkeypatch, child)
    out, _ = D.run_tool(a, {"tasks": [{"goal": "Make the file"}]})
    assert "cannot ask for approval" in out and not (_env / "made.txt").exists()
    a.mode = "auto"

    def child2(b):
        if b["messages"][-1]["role"] == "tool":
            return _msg("result: " + b["messages"][-1]["content"][:100])
        a.plan = True
        return _msg("", [("write_file", {"path": "x.txt", "content": "x"})])
    fake(monkeypatch, child2)
    out, _ = D.run_tool(a, {"tasks": [{"goal": "Write the file"}]})
    assert "plan mode" in out and not (_env / "x.txt").exists()


def test_read_and_edit_access_means_no_commands(monkeypatch, _env):
    seen = []
    fake(monkeypatch, lambda b: _msg("ok"), seen=seen)
    monkeypatch.setenv("MH_SUBAGENT_ACCESS", "edit")                # saved by 0.0.5 as "Read and edit files"
    D.run_tool(_agent(_env, mode="auto"), {"tasks": [{"goal": "Tidy the docs folder"}]})
    tools = [x["tools"] for x in seen if x["child"]][0]
    assert "write_file" in tools and "edit" in tools and "bash" not in tools and "bash_many" not in tools


def test_nested_sub_agents_tokens_reach_the_chat(monkeypatch, _env):
    monkeypatch.setenv("MH_SUBAGENT_DEPTH", "2")

    def child(b):
        names = [t["function"]["name"] for t in b["tools"]]
        if "delegate_task" in names and b["messages"][-1]["role"] == "user":
            return _msg("", [("delegate_task", {"tasks": [{"goal": "Check the second file"}]})])
        return _msg("ok")
    fake(monkeypatch, child)
    a = _agent(_env)
    D.run_tool(a, {"tasks": [{"goal": "Check both files"}]})
    assert a.usage["calls"] == 3                                     # child twice, grandchild once


def test_only_goals_that_are_nothing_but_a_placeholder_are_refused(_env):
    assert D.check_goals([{"goal": "Add a <title> tag to every page"}, {"goal": 'Fix format("{user_name}")'}])
    with pytest.raises(ValueError, match="placeholder"):
        D.check_goals([{"goal": "<goal>"}])
    with pytest.raises(ValueError, match="placeholder"):
        D.check_goals([{"goal": "{task}"}])


def test_stop_during_delegation_returns_the_finished_reports(monkeypatch, _env):
    def child(b):
        if "slow" in _goal(b):
            time.sleep(0.2)
            return _msg("", [("glob", {"pattern": "*"})])            # never finishes on its own
        return _msg("fast report: acme sells widgets")

    def parent(b):
        if b["messages"][-1]["role"] == "tool":
            return _msg("unreachable")
        return _msg("", [("delegate_task", {"tasks": [{"goal": "Research fast Acme"}, {"goal": "Research slow Globex"}]})])
    fake(monkeypatch, child, parent=parent)
    a = _agent(_env)
    threading.Timer(1.0, a.stop).start()
    t0 = time.time()
    a.send("research both")
    assert time.time() - t0 < 4
    tool = [m["content"] for m in a.messages if m.get("role") == "tool"][-1]
    assert "fast report: acme sells widgets" in tool and "stopped by Stop" in tool    # not "Not run"


def test_nested_sub_agents_follow_the_chats_mode_too(monkeypatch, _env):
    monkeypatch.setenv("MH_SUBAGENT_DEPTH", "2")
    a = _agent(_env, mode="auto")

    def child(b):
        names = [t["function"]["name"] for t in b["tools"]]
        if "delegate_task" in names and b["messages"][-1]["role"] == "user":
            return _msg("", [("delegate_task", {"tasks": [{"goal": "Make the file as the grandchild"}]})])
        if "delegate_task" in names:
            return _msg("child done: " + b["messages"][-1]["content"][:400])
        if b["messages"][-1]["role"] == "tool":
            return _msg("grandchild result: " + b["messages"][-1]["content"][:100])
        a.mode = "ask"                                              # the user tightens the chat meanwhile
        return _msg("", [("bash", {"command": "echo x > nested.txt"})])
    fake(monkeypatch, child)
    out, _ = D.run_tool(a, {"tasks": [{"goal": "Delegate the file work"}]})
    assert "cannot ask for approval" in out and not (_env / "nested.txt").exists()


def test_progress_steps_are_live_only_and_do_not_crowd_the_saved_transcript(tmp_path, monkeypatch):
    from meshpool_harness.ui import agent_api as API
    s = API.Session.__new__(API.Session)
    s.lock, s.id, s.agent, s.events = threading.RLock(), "keep-test", None, []
    monkeypatch.setattr(API, "SESSIONS", tmp_path)
    s.events = [{"type": "user", "text": "hi", "i": 0}] + [
        {"type": "subagent", "kind": "tool", "text": "step", "i": n} for n in range(1, 4000)] + [
        {"type": "subagent", "kind": "done", "text": "completed", "i": 4000}]
    s._persist()
    saved = json.loads((tmp_path / "keep-test" / "transcript.json").read_text())["events"]
    assert saved[0]["text"] == "hi" and len(saved) == 2 and saved[-1]["kind"] == "done"

