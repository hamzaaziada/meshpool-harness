"""Sub-agents (run_subtasks): the settings, the parent chat's model and permission mode, read or edit access, how
many run at once, and the time limit and Stop that end them. No network, no model."""
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
    for k in ("MH_SUBAGENTS", "MH_SUBAGENT_MODEL", "MH_SUBAGENT_ACCESS", "MH_SUBAGENT_PARALLEL", "MH_SUBAGENT_STEPS",
              "MH_SUBAGENT_MINUTES"):
        monkeypatch.delenv(k, raising=False)
    proj = tmp_path / "proj"
    proj.mkdir()
    return proj


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
    out = A.run_subtasks([{"title": "a", "prompt": "x"}], str(_env), parent={"model": "parent-model"})
    assert "1 parts finished" in out and "report" in out and seen[0]["model"] == "parent-model"
    monkeypatch.setenv("MH_SUBAGENT_MODEL", "cheap-model")
    seen.clear()
    A.run_subtasks([{"prompt": "x"}], str(_env), parent={"model": "parent-model"})
    assert seen[0]["model"] == "cheap-model"


def test_read_only_unless_settings_and_the_parent_chat_allow_edits(monkeypatch, _env):
    seen = []
    _record(monkeypatch, seen)
    A.run_subtasks([{"prompt": "x"}], str(_env), parent={"mode": "auto"})
    assert "write_file" not in seen[-1]["tools"] and "read_file" in seen[-1]["tools"]
    monkeypatch.setenv("MH_SUBAGENT_ACCESS", "edit")
    A.run_subtasks([{"prompt": "x"}], str(_env), parent={"mode": "ask"})            # Supervised: they cannot ask
    assert "write_file" not in seen[-1]["tools"]
    A.run_subtasks([{"prompt": "x"}], "", parent={"mode": "auto"})                 # no project: nothing to edit
    assert "write_file" not in seen[-1]["tools"]
    A.run_subtasks([{"prompt": "x"}], str(_env), parent={"mode": "edits"})
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
    out = A.run_subtasks([{"prompt": "a"}, {"prompt": "b"}], str(_env), parent={"stop_file": str(flag)})
    assert time.time() - t0 < 5 and "stopped by Stop" in out
