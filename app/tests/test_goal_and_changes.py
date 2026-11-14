"""/goal: when a chat keeps going on its own, and the branch change list in the Git panel."""
import subprocess
import threading
from pathlib import Path

from meshpool_harness.agent.core import Agent
from meshpool_harness.ui import agent_api as A
from meshpool_harness.ui import workbench as W


def session(goal="ship it"):
    s = A.Session.__new__(A.Session)
    s.events, s.lock, s.goal, s._goal_idle, s.id = [], threading.Lock(), goal, 0, "test-goal"
    s.emit = lambda ev: s.events.append(ev)
    return s


def turn(s, reply, acted=True, extra=()):
    s.events += [{"type": "user", "text": "go"}, *([{"type": "tool_end"}] if acted else []), *extra,
                 {"type": "assistant", "text": reply}, {"type": "turn_end"}]


def test_goal_continues_until_done(monkeypatch):
    monkeypatch.setattr(A, "write_meta", lambda *a, **k: {})
    s = session()
    turn(s, "Wrote part 1.\nGOAL: CONTINUE with part 2")
    assert s._goal_next(0) == (A.GOAL_NEXT, [])
    n = len(s.events)
    turn(s, "All done and tested.\nGOAL: DONE")
    assert s._goal_next(n) is None and s.goal == ""
    assert s.events[-1]["text"].startswith("Goal met")


def test_goal_pauses_for_the_user_errors_and_no_progress(monkeypatch):
    monkeypatch.setattr(A, "write_meta", lambda *a, **k: {})
    s = session()
    turn(s, "Which database should I use?")                       # a question: no GOAL line
    assert s._goal_next(0) is None and s.goal == "ship it"
    n = len(s.events)
    turn(s, "GOAL: CONTINUE", extra=[{"type": "error", "text": "API down"}])
    assert s._goal_next(n) is None and "error" in s.events[-1]["text"]
    n = len(s.events)
    turn(s, "Thinking.\nGOAL: CONTINUE", acted=False)
    assert s._goal_next(n) == (A.GOAL_NEXT, [])
    n = len(s.events)
    turn(s, "Still thinking.\nGOAL: CONTINUE", acted=False)       # two idle turns in a row: pause
    assert s._goal_next(n) is None and "without doing anything" in s.events[-1]["text"]


def test_runs_have_no_step_limit_by_default(tmp_path):
    assert Agent(str(tmp_path)).max_steps == 0
    assert Agent(str(tmp_path), max_steps=12).max_steps == 12


def test_branch_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "check_root", lambda r: Path(r))
    g = lambda *a: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=tmp_path,  # noqa: E731
                                  check=True, capture_output=True)
    g("init", "-q", "-b", "main")
    (tmp_path / "keep.py").write_text("a\nb\nc\n")
    (tmp_path / "old.txt").write_text("gone\n")
    (tmp_path / "move.md").write_text("same text\n" * 5)
    g("add", "-A"); g("commit", "-qm", "base")
    g("checkout", "-qb", "feature")
    (tmp_path / "keep.py").write_text("a\nB\nc\nd\n")
    (tmp_path / "new.py").write_text("x\ny\n")
    (tmp_path / "old.txt").unlink()
    (tmp_path / "move.md").rename(tmp_path / "moved.md")
    g("add", "-A"); g("commit", "-qm", "work")
    c = W.branch_changes(str(tmp_path), "main")
    by = {f["path"]: f for f in c["files"]}
    assert c["base"] == "main" and c["count"] == 4
    assert by["new.py"]["status"] == "added" and by["new.py"]["added"] == 2
    assert by["old.txt"]["status"] == "deleted" and by["old.txt"]["removed"] == 1
    assert by["keep.py"]["status"] == "changed" and (by["keep.py"]["added"], by["keep.py"]["removed"]) == (2, 1)
    assert by["moved.md"]["status"] == "renamed" and by["moved.md"]["from"] == "move.md"
    assert (c["added"], c["removed"]) == (4, 2)
