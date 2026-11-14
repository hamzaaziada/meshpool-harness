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


def test_thread_diff_lists_only_this_threads_changes(tmp_path):
    root, cp = _cp(tmp_path)
    cp.begin_turn("agent edits")
    (root / "calc.py").write_text("v2\n")
    cp.end_turn()
    (root / "other.docx").write_bytes(b"made by another thread")   # someone else, later, same folder
    (root / "keep.md").write_text("user edit\n")
    d = cp.diff(None)
    assert [f["path"] for f in d["files"]] == ["calc.py"], d["files"]
    cp.begin_turn("second reply")
    (root / "new.py").write_text("x\n")
    cp.end_turn()
    assert sorted(f["path"] for f in cp.diff(None)["files"]) == ["calc.py", "new.py"]


# ---- review fixes: visibility, links, modes, file<->folder swaps, build-named files ---------------------------------
import os

from meshpool_harness import checkpoints as _ckp


def test_undo_never_deletes_a_file_that_only_became_visible(tmp_path):
    root, cp = _cp(tmp_path)
    (root / ".gitignore").write_text(".env\nsecrets/\n")
    (root / ".env").write_text("KEY=user\n")
    (root / "secrets").mkdir()
    (root / "secrets" / "k.pem").write_text("pem\n")
    cp.begin_turn("agent rewrites .gitignore")
    (root / ".gitignore").write_text("")
    cp.end_turn()
    changed = {c["path"]: c["status"] for c in cp.turns()[-1]["changed"]}
    assert changed[".env"] == "modified" and changed["secrets/k.pem"] == "modified"
    r = cp.revert_to_before(1)
    assert (root / ".env").read_text() == "KEY=user\n" and (root / "secrets" / "k.pem").exists()
    assert (root / ".gitignore").read_text() == ".env\nsecrets/\n"
    assert ".env" in r["kept"] and "secrets/k.pem" in r["kept"] and r["notes"]


def test_undo_never_deletes_a_big_file_that_shrank(tmp_path, monkeypatch):
    monkeypatch.setattr(_ckp, "MAX_FILE", 10)
    root, cp = _cp(tmp_path)
    (root / "big.bin").write_bytes(b"x" * 50)
    cp.begin_turn("agent shrinks it")
    (root / "big.bin").write_bytes(b"y")
    cp.end_turn()
    r = cp.revert_to_before(1)
    assert (root / "big.bin").read_bytes() == b"y" and r["kept"] == ["big.bin"]


def test_undo_never_overwrites_a_file_that_became_ignored(tmp_path):
    root, cp = _cp(tmp_path)
    cp.begin_turn("agent ignores calc.py")
    (root / ".gitignore").write_text("calc.py\n")
    cp.end_turn()
    (root / "calc.py").write_text("user edit\n")
    r = cp.revert_to_before(1)
    assert (root / "calc.py").read_text() == "user edit\n" and "calc.py" in r["kept"]
    assert not (root / ".gitignore").exists()


def test_undo_does_not_write_through_links(tmp_path):
    root, cp = _cp(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "f.txt").write_text("outside\n")
    (root / "sub").mkdir()
    (root / "sub" / "f.txt").write_text("inside\n")
    cp.begin_turn("agent swaps in links")
    (root / "calc.py").unlink()
    (root / "calc.py").symlink_to(outside / "f.txt")
    (root / "sub" / "f.txt").unlink()
    (root / "sub").rmdir()
    (root / "sub").symlink_to(outside)
    cp.end_turn()
    r = cp.revert_to_before(1)
    assert (outside / "f.txt").read_text() == "outside\n"
    assert sorted(r["kept"]) == ["calc.py", "sub/f.txt"]


def test_undo_restores_permissions_and_a_file_replaced_by_a_folder(tmp_path):
    root, cp = _cp(tmp_path)
    (root / "run.sh").write_text("echo hi\n")
    os.chmod(root / "run.sh", 0o755)
    (root / "x").write_text("file\n")
    cp.begin_turn("agent")
    (root / "run.sh").unlink()
    (root / "x").unlink()
    (root / "x").mkdir()
    (root / "x" / "a.txt").write_text("a\n")
    cp.end_turn()
    r = cp.revert_to_before(1)
    assert (root / "run.sh").read_text() == "echo hi\n" and os.stat(root / "run.sh").st_mode & 0o777 == 0o755
    assert (root / "x").read_text() == "file\n"
    assert sorted(r["restored"]) == ["run.sh", "x"] and r["deleted"] == ["x/a.txt"]


def test_a_folder_with_user_files_in_place_of_a_file_is_left_alone(tmp_path):
    root, cp = _cp(tmp_path)
    cp.begin_turn("agent")
    (root / "calc.py").unlink()
    (root / "calc.py").mkdir()
    (root / "calc.py" / "agent.txt").write_text("a\n")
    cp.end_turn()
    (root / "calc.py" / "user.txt").write_text("mine\n")
    r = cp.revert_to_before(1)
    assert (root / "calc.py" / "user.txt").read_text() == "mine\n"
    assert "calc.py" in r["kept"] and r["deleted"] == ["calc.py/agent.txt"]


def test_files_named_like_build_folders_are_tracked(tmp_path):
    root, cp = _cp(tmp_path)
    (root / "build").write_text("#!/bin/sh\nmake\n")
    cp.begin_turn("agent edits the build script")
    (root / "build").write_text("broken\n")
    cp.end_turn()
    r = cp.revert_to_before(1)
    assert (root / "build").read_text() == "#!/bin/sh\nmake\n" and r["restored"] == ["build"]
