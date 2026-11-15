"""Sub-agents (run_subtasks): the settings, the parent chat's model and permission mode, read or edit access, how
many run at once, and the time limit and Stop that end them. No network, no model."""
import json
import threading
import time

import pytest

from meshpool_harness import assistant as A
from meshpool_harness.agent import core as K


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(K, "SESSIONS", tmp_path / "_sessions")
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "meshpool.config"))
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.delenv("MESHPOOL_PROJECT", raising=False)          # other tests leave it set (agent/mcp.py)
    monkeypatch.setitem(A._TRUSTED, "token", "tok")              # what the app's initialize handed this server
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "projects.json").write_text(json.dumps([{"path": str(tmp_path / "proj")}]))
    for k in ("MH_SUBAGENTS", "MH_SUBAGENT_MODEL", "MH_SUBAGENT_ACCESS", "MH_SUBAGENT_PARALLEL", "MH_SUBAGENT_STEPS",
              "MH_SUBAGENT_MINUTES"):
        monkeypatch.delenv(k, raising=False)
    proj = tmp_path / "proj"
    proj.mkdir()
    return proj


def P(**kw):
    return {"token": "tok", **kw}


def _reply(text="report"):
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}], "usage": {}}, False


def _record(monkeypatch, seen, delay=0.0):
    lock = threading.Lock()
    state = {"now": 0, "most": 0}

    def post(self, base, key, body, stream):
        with lock:
            state["now"] += 1
            state["most"] = max(state["most"], state["now"])
        seen.append({"model": body["model"], "tools": sorted(t["function"]["name"] for t in body.get("tools") or [])})
        try:
            time.sleep(delay)
        finally:
            with lock:
                state["now"] -= 1
        return _reply()
    monkeypatch.setattr(K.Agent, "_post", post)
    return state


def test_settings_have_safe_defaults_and_bounds(monkeypatch):
    assert A.subagent_settings() == {"on": True, "model": "", "access": "read", "parallel": 4, "steps": 30,
                                     "minutes": 20}
    monkeypatch.setenv("MH_SUBAGENT_PARALLEL", "50")
    monkeypatch.setenv("MH_SUBAGENT_MINUTES", "90")
    monkeypatch.setenv("MH_SUBAGENT_STEPS", "x")
    s = A.subagent_settings()
    assert s["parallel"] == 6 and s["minutes"] == 25 and s["steps"] == 30   # under the tool's 30 min timeout


def test_settings_file_wins_over_a_stale_environment(monkeypatch):
    from meshpool_harness import config as C
    monkeypatch.setenv("MH_SUBAGENT_PARALLEL", "2")                  # the server process's copy from start-up
    C.write({"MH_SUBAGENT_PARALLEL": "5"})                           # what the user saved since
    assert A.subagent_settings()["parallel"] == 5


def test_off_in_settings(monkeypatch, _env):
    monkeypatch.setenv("MH_SUBAGENTS", "0")
    with pytest.raises(ValueError, match="off in Settings"):
        A.run_subtasks([{"prompt": "x"}], str(_env))


def test_parent_model_by_default_and_the_setting_overrides(monkeypatch, _env):
    seen = []
    _record(monkeypatch, seen)
    out = A.run_subtasks([{"title": "a", "prompt": "x"}], str(_env), parent=P(model="parent-model"))
    assert "1 parts finished" in out and "report" in out and seen[0]["model"] == "parent-model"
    monkeypatch.setenv("MH_SUBAGENT_MODEL", "cheap-model")
    seen.clear()
    A.run_subtasks([{"prompt": "x"}], str(_env), parent=P(model="parent-model"))
    assert seen[0]["model"] == "cheap-model"


def test_read_only_unless_settings_and_the_parent_chat_allow_edits(monkeypatch, _env):
    seen = []
    _record(monkeypatch, seen)
    A.run_subtasks([{"prompt": "x"}], str(_env), parent=P(mode="auto"))
    assert "write_file" not in seen[-1]["tools"] and "read_file" in seen[-1]["tools"]
    monkeypatch.setenv("MH_SUBAGENT_ACCESS", "edit")
    A.run_subtasks([{"prompt": "x"}], str(_env), parent=P(mode="ask"))            # Supervised: they cannot ask
    assert "write_file" not in seen[-1]["tools"]
    A.run_subtasks([{"prompt": "x"}], "", parent=P(mode="auto"))                 # no project: nothing to edit
    assert "write_file" not in seen[-1]["tools"]
    A.run_subtasks([{"prompt": "x"}], str(_env), parent=P(mode="edits"))
    assert {"write_file", "edit"} <= set(seen[-1]["tools"]) and "bash" not in seen[-1]["tools"]


def test_no_more_at_once_than_the_setting(monkeypatch, _env):
    monkeypatch.setenv("MH_SUBAGENT_PARALLEL", "2")
    seen = []
    state = _record(monkeypatch, seen, delay=0.3)
    out = A.run_subtasks([{"prompt": f"p{i}"} for i in range(5)], str(_env), max_parallel=6)
    assert "5 parts finished" in out and state["most"] == 2


def test_the_time_limit_stops_them_and_keeps_the_rest(monkeypatch, _env):
    real = A.subagent_settings
    monkeypatch.setattr(A, "subagent_settings", lambda: {**real(), "minutes": 0.02})   # about a second
    _record(monkeypatch, [], delay=30)
    t0 = time.time()
    out = A.run_subtasks([{"title": "slow", "prompt": "x"}], str(_env))
    assert time.time() - t0 < 5 and "stopped by the time limit" in out and "## slow" in out


def test_stop_in_the_parent_chat_stops_them(monkeypatch, _env, tmp_path):
    _record(monkeypatch, [], delay=30)
    flag = tmp_path / "stop-subtasks"
    threading.Timer(0.5, flag.write_text, args=("stop",)).start()
    t0 = time.time()
    out = A.run_subtasks([{"prompt": "a"}, {"prompt": "b"}], str(_env), parent=P(stop_file=str(flag)))
    assert time.time() - t0 < 5 and "stopped by Stop" in out


def test_parent_details_from_a_model_are_ignored(monkeypatch, _env):
    monkeypatch.setenv("MH_SUBAGENT_ACCESS", "edit")
    seen = []
    _record(monkeypatch, seen)
    A.run_subtasks([{"prompt": "x"}], str(_env), parent={"mode": "auto", "model": "spoof", "token": "guess"})
    assert "write_file" not in seen[-1]["tools"] and seen[-1]["model"] != "spoof"


def test_the_chat_passes_its_model_mode_and_a_stop_file(tmp_path, monkeypatch):
    import os
    a = K.Agent(str(tmp_path), mode="edits")
    got = {}

    class Mcp:
        last_images = []

        def has(self, n):
            return n == "mcp__meshpool_assistant__run_subtasks"

        def call(self, name, args):
            got.update(args)
            a.stop()                                               # the user presses Stop while they run
            return "done", False
    a.mcp = Mcp()
    with pytest.raises(K.Interrupted):
        a._run_subtasks("mcp__meshpool_assistant__run_subtasks", {"tasks": [], "parent": {"mode": "auto"}})
    p = got["parent"]
    assert p["mode"] == "edits" and p["token"] == K.C.PARENT_TOKEN and p["model"] == a.current_model()
    assert open(p["stop_file"]).read() == "stop"                   # Stop reached the sub-agents


def test_sub_agents_work_in_the_parent_chats_project_not_the_servers(monkeypatch, _env, tmp_path):
    other = tmp_path / "other-chat"
    other.mkdir()
    monkeypatch.setenv("MESHPOOL_PROJECT", str(other))             # another chat's servers started at the same time
    roots = []

    def post(self, base, key, body, stream):
        roots.append(str(self.root))
        return _reply()
    monkeypatch.setattr(K.Agent, "_post", post)
    A.run_subtasks([{"prompt": "x"}], str(other), parent=P(root=str(_env)))
    assert roots == [str(_env.resolve())]


def test_a_cleared_setting_takes_effect(monkeypatch):
    from meshpool_harness import config as C
    monkeypatch.setenv("MH_SUBAGENT_MODEL", "old-model")            # the server's copy from start-up
    C.write({"MH_SUBAGENT_MODEL": ""})                               # the user cleared it since
    assert A.subagent_settings()["model"] == ""


def test_a_sub_agent_still_starting_when_stopped_stops_too(monkeypatch, _env):
    real = A.subagent_settings
    monkeypatch.setattr(A, "subagent_settings", lambda: {**real(), "minutes": 0.01})   # under a second
    real_send = K.Agent.send

    def slow_send(self, *a, **k):
        time.sleep(1.0)                                            # the limit passes while it starts
        return real_send(self, *a, **k)                            # send() resets the stop flag
    monkeypatch.setattr(K.Agent, "send", slow_send)
    calls = []

    def post(self, base, key, body, stream):
        calls.append(1)
        time.sleep(0.2)
        return {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{len(calls)}",
                "type": "function", "function": {"name": "glob", "arguments": '{"pattern": "*"}'}}]},
                "finish_reason": "tool_calls"}], "usage": {}}, False
    monkeypatch.setattr(K.Agent, "_post", post)
    t0 = time.time()
    out = A.run_subtasks([{"prompt": "x"}], str(_env))
    assert time.time() - t0 < 4 and len(calls) <= 3 and "time limit" in out


def test_stop_just_before_the_call_still_reaches_the_sub_agents(tmp_path):
    a = K.Agent(str(tmp_path), mode="auto")
    files = []

    class Mcp:
        last_images = []

        def call(self, name, args):
            files.append(args["parent"]["stop_file"])
            time.sleep(0.3)
            return "x", False
    a.mcp = Mcp()
    a._stop = True                                                 # Stop pressed a moment before
    with pytest.raises(K.Interrupted):
        a._run_subtasks("mcp__meshpool-assistant__run_subtasks", {"tasks": []})
    stop_files = list(a.dir.glob("stop-subtasks-*"))
    assert stop_files and stop_files[0].read_text() == "stop"


def test_a_cancelled_call_stops_the_sub_agents(monkeypatch, _env):
    _record(monkeypatch, [], delay=30)
    t0 = time.time()
    out = A.run_subtasks([{"prompt": "a"}], str(_env), cancelled=lambda: time.time() - t0 > 0.5)
    assert time.time() - t0 < 4 and "stopped by Stop" in out


def test_the_mcp_server_hands_a_clients_cancel_to_the_tool(tmp_path):
    import json
    import subprocess
    import sys
    tool = tmp_path / "slow_server.py"
    tool.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {str(K.C.APP)!r})\n"
        "from meshpool_harness.agent.mcp_stdio import Tool, serve, cancel_check\n"
        "def slow():\n"
        "    stop = cancel_check()\n"
        "    t0 = time.time()\n"
        "    while time.time() - t0 < 20:\n"
        "        if stop():\n"
        "            return 'cancelled after %.1f s' % (time.time() - t0)\n"
        "        time.sleep(0.05)\n"
        "    return 'ran to the end'\n"
        "serve('t', {'slow': Tool(slow, 'x')})\n")
    p = subprocess.Popen([sys.executable, str(tool)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                  "params": {"name": "slow", "arguments": {}}}) + "\n")
        p.stdin.flush()
        time.sleep(0.5)
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                  "params": {"requestId": 7, "reason": "Stop"}}) + "\n")
        p.stdin.flush()
        reply = json.loads(p.stdout.readline())
        assert reply["id"] == 7 and "cancelled after" in reply["result"]["content"][0]["text"]
    finally:
        p.kill()


def test_the_parent_token_is_in_no_environment_and_only_the_apps_own_server_gets_it(tmp_path, monkeypatch):
    """Review rounds 6 and 7: in os.environ any harness model could read it, and in a server's environment any
    process of the same user can (/proc/<pid>/environ). It now goes only in the MCP initialize the app sends to
    the assistant server it starts itself."""
    import os
    from meshpool_harness.agent import mcp as M
    monkeypatch.delenv("MH_PARENT_TOKEN", raising=False)
    assert "MH_PARENT_TOKEN" not in os.environ and len(K.C.PARENT_TOKEN) >= 32
    seen = {}

    class P:
        def __init__(self, args, env=None, **kw):
            seen[args[-1]] = env
    monkeypatch.setattr(M.subprocess, "Popen", P)
    monkeypatch.setattr(M.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    monkeypatch.setenv("MH_PARENT_TOKEN", "leaked")               # even if something put one there
    own = {"command": "python", "args": ["-m", "meshpool_harness.agent.assistant_mcp_server"]}
    web = {"command": "python", "args": ["-m", "meshpool_harness.agent.web_mcp_server"]}
    for spec in (own, web):
        M._Stdio("x", spec, tmp_path, [str(tmp_path)])
    assert all("MH_PARENT_TOKEN" not in env for env in seen.values())
    srv = M.Server.__new__(M.Server)
    srv.spec = own
    assert srv._init_params()["_meta"]["meshpool/parentToken"] == K.C.PARENT_TOKEN
    srv.spec = web
    assert "_meta" not in srv._init_params()
    src = open(os.path.join(os.path.dirname(M.__file__), "..", "harnesses", "acp.py")).read() + \
        open(os.path.join(os.path.dirname(M.__file__), "..", "harnesses", "pi_rpc.py")).read()
    assert src.count('env.pop("MH_PARENT_TOKEN", None)') == 2      # harness processes never inherit one


def test_a_forged_token_from_the_config_file_or_a_harness_server_is_ignored(monkeypatch, _env):
    from meshpool_harness import config as C
    from meshpool_harness.agent import assistant_mcp_server as S
    monkeypatch.setitem(A._TRUSTED, "token", "")                   # started by a harness: no initialize token
    monkeypatch.setenv("MH_SUBAGENT_ACCESS", "edit")
    C.write({"MH_PARENT_TOKEN": "forged"})                         # a model wrote one into the settings file
    seen = []
    _record(monkeypatch, seen)
    A.run_subtasks([{"prompt": "x"}], str(_env), parent={"token": "forged", "mode": "auto"})
    assert "write_file" not in seen[-1]["tools"]
    S._initialize({"_meta": {"meshpool/parentToken": "real"}})     # the app's own initialize sets it once
    S._initialize({"_meta": {"meshpool/parentToken": "other"}})    # and nothing replaces it later
    assert A._TRUSTED["token"] == "real"


def test_a_root_that_is_not_a_known_project_is_ignored(monkeypatch, _env, tmp_path):
    roots = []

    def post(self, base, key, body, stream):
        roots.append(str(self.root))
        return _reply()
    monkeypatch.setattr(K.Agent, "_post", post)
    A.run_subtasks([{"prompt": "x"}], str(_env), parent=P(root="/", mode="auto"))
    assert roots == [str(_env.resolve())]                          # not "/": the chat's folder instead
