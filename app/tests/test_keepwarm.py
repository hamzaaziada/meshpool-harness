"""MH_KEEPWARM_S: cache keep-warm pings during long tool runs (no model calls)."""
import subprocess
import threading

from meshpool_harness.core.client import MockClient, tool_call
from meshpool_harness.core.loop import Caps, run
from meshpool_harness.core.tools import Workspace

CTX = {"task_id": "t", "model": "mock", "profile": "deepseek"}
SLOW = "sleep 1.3 && python -m pytest -q tests"


class WarmMock(MockClient):
    """Answers keep-warm pings without using the script; records them apart."""

    def __init__(self, script):
        super().__init__(script)
        self.pings, self.lock = [], threading.Lock()

    def chat(self, *, model, messages, tools=None, **kw):
        if kw.get("role") == "keepwarm":
            with self.lock:
                self.pings.append({"messages": [dict(m) for m in messages], **kw})
            return {"choices": [{"message": {"role": "assistant", "content": "."}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 1, "prompt_cache_hit_tokens": 100}}
        return super().chat(model=model, messages=messages, tools=tools, **kw)


def _go(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    subprocess.run("git init -q && git add -A && git -c user.email=x@y -c user.name=x commit -qm init", shell=True,
                   cwd=tmp_path)
    ws = Workspace(str(tmp_path), str(tmp_path / ".run"))
    c = WarmMock([tool_call("read_file", {"path": "calc.py"}),
                  tool_call("edit", {"path": "calc.py", "old": "a - b", "new": "a + b"}),
                  tool_call("run_tests", {"command": SLOW}),
                  tool_call("finish", {"answer": "fixed", "evidence": [SLOW]})])
    r = run("fix add", client=c, model="mock", profile=None, ws=ws, ctx=CTX, system_text="sys", caps=Caps(max_turns=8))
    return r, c


def test_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("MH_KEEPWARM_S", raising=False)
    r, c = _go(tmp_path)
    assert r.ok and not c.pings and "keepwarm_calls" not in r.usage


def test_pings_are_valid_prefixes(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_KEEPWARM_S", "0.4")
    r, c = _go(tmp_path)
    assert r.ok
    assert 1 <= len(c.pings) <= 4 and r.usage["keepwarm_calls"] == len(c.pings)
    for p in c.pings:
        assert p["max_tokens"] == 1
        assert p["task_id"] == CTX["task_id"]                       # same cache key and session as the real call
        assert p["messages"][-1]["role"] != "assistant"            # no tool call left without its result
    nxt = c.requests[-1]["messages"]                                # the real request after the slow command
    ping = c.pings[0]["messages"]
    assert nxt[:len(ping)] == ping                                  # exact prefix -> cache hit on the real call
    assert r.usage["prompt_tokens"] >= 400 + 100 * len(c.pings) - 100


def test_ping_failure_never_breaks_run(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_KEEPWARM_S", "0.3")

    class Boom(WarmMock):
        def chat(self, **kw):
            if kw.get("role") == "keepwarm":
                raise RuntimeError("HTTP 500")
            return super().chat(**kw)
    monkeypatch.setattr(__import__(__name__), "WarmMock", Boom)
    r, c = _go(tmp_path)
    assert r.ok


def test_full_miss_counter(tmp_path, monkeypatch):
    monkeypatch.delenv("MH_KEEPWARM_S", raising=False)
    r, c = _go(tmp_path)                    # MockClient reports no cached tokens: every follow-up is a full miss
    assert r.usage.get("full_miss_calls") == 3
