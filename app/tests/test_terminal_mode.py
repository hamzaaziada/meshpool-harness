"""Terminal light mode: no exploration nudges or lock (shell-made changes are not counted as edits), longer timeouts."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from meshpool_harness.core.client import MockClient, tool_call  # noqa: E402
from meshpool_harness.core.guards import Guards  # noqa: E402
from meshpool_harness.core.loop import Caps, run  # noqa: E402
from meshpool_harness.core.tools import Workspace  # noqa: E402


def _ws():
    d = tempfile.mkdtemp()
    return Workspace(d, os.path.join(d, ".run"))


def test_progress_nudge_off_in_terminal_mode(monkeypatch):
    g = Guards({"task_id": "t"}, _ws())
    monkeypatch.delenv("MH_MODE", raising=False)
    assert g.progress(12, 60, 0, True)
    g2 = Guards({"task_id": "t"}, _ws())
    monkeypatch.setenv("MH_MODE", "terminal")
    assert g2.progress(12, 60, 0, True) is None


def test_exploration_lock_off_in_terminal_mode(monkeypatch):
    monkeypatch.setenv("MH_MODE", "terminal")
    script = [tool_call("bash", {"command": f"echo step{i}"}) for i in range(28)]
    script += [tool_call("finish", {"answer": "done", "evidence": ["echo step27"]})] * 3
    ws = _ws()
    run("do it", client=MockClient(script), model="m", profile=None, ws=ws, ctx={"task_id": "t"}, system_text="s",
        caps=Caps(max_turns=40), require_check=True)
    assert not any("exploration budget exhausted" in c.get("tail", "") for c in ws.commands)
    assert len([c for c in ws.commands if c["cmd"].startswith("echo step")]) == 28


def test_timeout_cap_terminal(monkeypatch):
    monkeypatch.setenv("MH_MODE", "terminal")
    ws = _ws()
    out = ws.bash("echo ok", timeout_s=1700)
    assert "ok" in out


def test_light_mode_turns_off_nudges(monkeypatch):
    monkeypatch.setenv("MH_MODE", "light")
    g = Guards({"task_id": "t"}, _ws())
    assert g.progress(12, 60, 0, True) is None


# ---- terminal front end: approvals, Ctrl+C, -p exit code, key step (review fixes) ----------------------------------
import builtins  # noqa: E402

from meshpool_harness.agent import tui as T  # noqa: E402


def _term(monkeypatch, answers):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    it = iter(answers)
    monkeypatch.setattr(builtins, "input", lambda *a, **k: next(it))
    return T.Terminal()


def test_enter_alone_does_not_approve(monkeypatch):
    assert _term(monkeypatch, [""]).approve("bash", "Run rm -rf build?", "rm -rf build") == ("no", "")
    assert _term(monkeypatch, ["y"]).approve("bash", "Run?", "ls")[0] == "yes"


def test_always_maps_to_the_acp_allow_always_option(monkeypatch):
    opts = [{"id": "ok1", "label": "Allow", "kind": "allow_once"},
            {"id": "ok-all", "label": "Always", "kind": "allow_always"},
            {"id": "no1", "label": "Reject", "kind": "reject_once"}]
    assert _term(monkeypatch, ["a"]).approve("edit", "Edit?", "+x", opts)[0] == "ok-all"
    assert _term(monkeypatch, ["a"]).approve("edit", "Edit?", "+x", opts[:1])[0] == "yes"
    assert _term(monkeypatch, ["a"]).approve("edit", "Edit?", "+x")[0] == "always"     # the built-in agent


def test_ctrl_c_stops_the_agent_and_later_questions_are_refused(monkeypatch):
    class FakeAgent:
        stopped = False

        def stop(self):
            self.stopped = True
    term, agent = _term(monkeypatch, []), FakeAgent()
    T._stop_turn(term, agent)
    assert agent.stopped and term.approve("bash", "Run?", "ls") == ("no", "")


def test_print_mode_exits_non_zero_when_the_turn_failed(monkeypatch, tmp_path):
    import meshpool_harness.harnesses as HR
    import meshpool_harness.agent.core as core

    class FakeAgent:
        def __init__(self, wd, emit=None, **kw):
            self.emit, self.root, self.messages, self.mode = emit, wd, [], "auto"

        def send(self, text):
            self.emit({"type": "error", "text": "Couldn't reach the model."})
    monkeypatch.setattr(HR, "ready", lambda: True)
    monkeypatch.setattr(HR, "default_for", lambda kind: "meshpool")
    monkeypatch.setattr(HR, "get", lambda hid: {"id": "meshpool", "type": "builtin", "ready": True})
    monkeypatch.setattr(core, "Agent", FakeAgent)
    monkeypatch.setattr(T, "_context", lambda: "")
    assert T.main(str(tmp_path), prompt="hi", no_mcp=True) == 1


def test_context_is_the_apps_context_block(monkeypatch):
    from meshpool_harness import assistant as AS
    monkeypatch.setattr(AS, "context_block", lambda: "Today is X.\n\nNotes from Meshpool: ...")
    assert T._context() == "Today is X.\n\nNotes from Meshpool: ..."


def test_key_step_puts_the_old_endpoint_back_when_cancelled(monkeypatch):
    from meshpool_harness.agent import onboard
    import getpass
    monkeypatch.setenv("MESHPOOL_BASE_URL", "https://llm.meshpool.ai/v1")
    monkeypatch.setenv("MH_MODEL", "deepseek-v4.1-flash")
    monkeypatch.setenv("MESHPOOL_API_KEY", "old-key")
    answers = iter(["https://api.example.com/v1", "gpt-x"])
    monkeypatch.setattr(builtins, "input", lambda *a, **k: next(answers))

    def cancel(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(getpass, "getpass", cancel)
    assert onboard.key_step(T.Style(0), custom=True) is False
    assert os.environ["MESHPOOL_BASE_URL"] == "https://llm.meshpool.ai/v1"
    assert os.environ["MH_MODEL"] == "deepseek-v4.1-flash" and os.environ["MESHPOOL_API_KEY"] == "old-key"


def test_key_check_never_uses_a_subscription_model(monkeypatch):
    from meshpool_harness.agent import onboard
    monkeypatch.setenv("MH_MODEL", "chatgpt:gpt-5")
    assert onboard._check_model(False) == "deepseek-v4.1-flash"
    assert onboard._check_model(True) == "gpt-5"
    monkeypatch.setenv("MH_MODEL", "gpt-4.1-mini")
    assert onboard._check_model(True) == "gpt-4.1-mini"


def test_check_accepts_a_subscription_without_a_key(monkeypatch, capsys):
    import meshpool_harness.cli as CLI
    import meshpool_harness.harnesses as HR
    from meshpool_harness import config as Cfg
    from meshpool_harness import providers as PV
    monkeypatch.delenv("MESHPOOL_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(PV, "ready", lambda p: p == "claude")
    monkeypatch.setattr(HR, "ready", lambda: True)
    monkeypatch.setattr(Cfg, "system_check", lambda: [])
    assert CLI._check(False) == 0
    out = capsys.readouterr().out
    assert "MISSING" not in out and "Claude subscription" in out
    monkeypatch.setattr(HR, "ready", lambda: False)
    monkeypatch.setattr(PV, "ready", lambda p: False)
    assert CLI._check(False) == 1
