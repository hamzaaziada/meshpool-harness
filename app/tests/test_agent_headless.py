"""run_task(engine="agent"): the app's chat agent, headless, with a scripted model (no model calls)."""
import json

from meshpool_harness import run_task
from meshpool_harness.agent import core as A


def _script(monkeypatch, msgs, usage=None):
    it = iter(msgs)

    def fake_chat(self, turn):
        u = usage or {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 900, "completion_tokens": 50}
        for k, v in {"in": u["prompt_tokens"], "cached": u.get("prompt_cache_hit_tokens", 0),
                     "out": u["completion_tokens"], "calls": 1}.items():
            turn[k] += v
            self.usage[k] += v
        return next(it), False
    monkeypatch.setattr(A.Agent, "_chat", fake_chat)


def _call(name, args, cid="c1"):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def test_agent_engine_runs_and_stays_in_workdir(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_RUNS_DIR", str(tmp_path / "elsewhere"))
    _script(monkeypatch, [_call("write_file", {"path": "hello.txt", "content": "hi\n"}),
                          {"role": "assistant", "content": "Wrote hello.txt."}])
    r = run_task("write hello.txt", workdir=str(tmp_path), engine="agent")
    assert r["ok"] and r["answer"] == "Wrote hello.txt." and r["error"] is None
    assert (tmp_path / "hello.txt").read_text() == "hi\n"
    assert r["usage"]["model_calls"] == 2 and r["turns"] == 2
    assert (tmp_path / ".run" / "agent").is_dir() and not (tmp_path / "elsewhere").exists()


def test_agent_engine_error_comes_back_not_raised(tmp_path, monkeypatch):
    def boom(self, turn):
        raise RuntimeError("HTTP 500 upstream")
    monkeypatch.setattr(A.Agent, "_chat", boom)
    r = run_task("anything", workdir=str(tmp_path), engine="agent")
    assert r["ok"] is False and "HTTP 500" in r["error"]


def test_agent_scope_check_once(tmp_path, monkeypatch):
    import subprocess
    (tmp_path / "a.py").write_text("A = 1\n")
    (tmp_path / "b.py").write_text("B = 1\n")
    subprocess.run("git init -q && git add -A && git -c user.email=x@y -c user.name=x commit -qm i", shell=True,
                   cwd=tmp_path)
    monkeypatch.setenv("MH_SCOPE_CHECK", "1")
    seen = []
    msgs = [_call("write_file", {"path": "a.py", "content": "A = 2\n"}, "c1"),
            _call("write_file", {"path": "b.py", "content": "B = 2\n"}, "c2"),
            {"role": "assistant", "content": "Done."}, {"role": "assistant", "content": "Both changes are needed."}]
    it = iter(msgs)

    def fake_chat(self, turn):
        seen.append(self.messages[-1].get("content") or "")
        turn["calls"] += 1
        self.usage["calls"] += 1
        return next(it), False
    monkeypatch.setattr(A.Agent, "_chat", fake_chat)
    r = run_task("change a and b", workdir=str(tmp_path), engine="agent")
    assert r["ok"] and r["answer"] == "Both changes are needed."
    assert sum("Scope check" in s for s in seen) == 1 and "reply is accepted" in seen[-1]
