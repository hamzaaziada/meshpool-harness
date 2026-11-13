"""Undo turn only reverses the agent's own changes (QA blocker B1: it used to reset the whole folder)."""
from meshpool_harness.checkpoints import Checkpoints


def _cp(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "calc.py").write_text("v1\n")
    (root / "keep.md").write_text("user\n")
    return root, Checkpoints(str(root), tmp_path / "store")


def test_undo_reverts_agent_changes_and_spares_user_files(tmp_path):
    root, cp = _cp(tmp_path)
    cp.begin_turn("agent edits")
    (root / "calc.py").write_text("v2\n")              # agent modifies
    (root / "new.py").write_text("agent\n")            # agent creates
    cp.end_turn()
    (root / "notes.md").write_text("mine\n")           # user creates after the turn
    (root / "keep.md").write_text("user edit\n")       # user edits a file the agent never touched
    r = cp.revert_to_before(1)
    assert (root / "calc.py").read_text() == "v1\n" and not (root / "new.py").exists()
    assert (root / "notes.md").read_text() == "mine\n" and (root / "keep.md").read_text() == "user edit\n"
    assert r["restored"] == ["calc.py"] and r["deleted"] == ["new.py"] and r["kept"] == []


def test_undo_keeps_a_file_the_user_changed_after_the_agent(tmp_path):
    root, cp = _cp(tmp_path)
    cp.begin_turn("agent edits")
    (root / "calc.py").write_text("v2\n")
    cp.end_turn()
    (root / "calc.py").write_text("v3 by user\n")
    r = cp.revert_to_before(1)
    assert (root / "calc.py").read_text() == "v3 by user\n" and r["kept"] == ["calc.py"]


def test_undo_of_a_turn_with_no_file_changes_touches_nothing(tmp_path):
    root, cp = _cp(tmp_path)
    cp.begin_turn("chat only")
    cp.end_turn()
    (root / "util.py").write_text("x = 1\n")
    r = cp.revert_to_before(1)
    assert (root / "util.py").exists() and r == {"restored": [], "deleted": [], "kept": [],
                                                 "message_index": None}


def test_undo_over_several_turns(tmp_path):
    root, cp = _cp(tmp_path)
    cp.begin_turn("one")
    (root / "a.py").write_text("1\n")
    cp.end_turn()
    cp.begin_turn("two")
    (root / "a.py").write_text("2\n")
    (root / "calc.py").unlink()                        # agent deletes
    cp.end_turn()
    r = cp.revert_to_before(1)
    assert not (root / "a.py").exists() and (root / "calc.py").read_text() == "v1\n"
    assert sorted(r["restored"]) == ["calc.py"] and r["deleted"] == ["a.py"]
