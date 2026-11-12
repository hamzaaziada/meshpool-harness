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
