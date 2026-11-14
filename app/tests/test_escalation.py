import subprocess
from meshpool_harness.core.client import MockClient, tool_call
from engines.code.engine import restore, snapshot, solve

T = "python -m pytest -q tests"
BIN = bytes(range(256)) * 4                                   # not valid UTF-8, has NUL bytes


def _repo(tmp_path, monkeypatch):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "util.py").write_text("X = 1\n")
    (tmp_path / "logo.bin").write_bytes(b"\x89PNG\x00old")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    subprocess.run("git init -q && git add -A && git -c user.email=x@y -c user.name=x commit -qm init", shell=True, cwd=tmp_path)
    # the user's own work, not committed: an edit, a staged edit, an untracked module, binary files
    (tmp_path / "util.py").write_text("X = 2  # mine\n")
    (tmp_path / "logo.bin").write_bytes(BIN)
    subprocess.run(["git", "add", "logo.bin"], cwd=tmp_path)
    (tmp_path / "notes.py").write_text("TODO = 'keep me'\n")
    (tmp_path / "data.bin").write_bytes(BIN[::-1])
    monkeypatch.setenv("MH_TESTCMD", "0"); monkeypatch.setenv("MH_MAX_SECONDS", "3600")
    return tmp_path


def _user_state_kept(d):
    assert (d / "util.py").read_text() == "X = 2  # mine\n"
    assert (d / "logo.bin").read_bytes() == BIN and (d / "data.bin").read_bytes() == BIN[::-1]
    assert (d / "notes.py").read_text() == "TODO = 'keep me'\n"
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=d, capture_output=True, text=True).stdout
    assert staged.split() == ["logo.bin"]


# attempt 1 never finishes, and on the way damages the user's files (edit, delete, binary write, new file)
FIRST = [tool_call("read_file", {"path": "calc.py"}, cid="a1"),
         tool_call("edit", {"path": "calc.py", "old": "a - b", "new": "a * b"}, cid="a2"),
         tool_call("bash", {"command": "rm notes.py; printf 'x' >> logo.bin; echo 'X = 3' > util.py; "
                                       "printf 'junk' > data.bin; echo 'y = 1' > scratch.py"}, cid="a3"),
         tool_call("glob", {"pattern": "*"}, cid="a4")]


def test_escalation_resets_to_the_users_state_and_retries(tmp_path, monkeypatch):
    d = _repo(tmp_path, monkeypatch)
    second = [tool_call("read_file", {"path": "calc.py"}, cid="b1"),
              tool_call("edit", {"path": "calc.py", "old": "a - b", "new": "a + b"}, cid="b2"),
              tool_call("run_tests", {"command": T}, cid="b3"),
              tool_call("finish", {"answer": "fixed", "evidence": [T]}, cid="b4")]
    out = solve("fix add", workdir=str(d), task_id="t", max_turns=4, client=MockClient(FIRST + second))
    assert out["attempts"] == 2 and out["ok"]
    assert "a + b" in (d / "calc.py").read_text()             # second attempt started from the user's state
    assert not (d / "scratch.py").exists()                     # attempt 1's new file is gone
    _user_state_kept(d)
    assert (d / ".run" / "attempt1.patch").read_bytes().count(b"GIT binary patch") >= 1


def test_empty_second_attempt_restores_attempt_one(tmp_path, monkeypatch):
    d = _repo(tmp_path, monkeypatch)
    idle = [tool_call("glob", {"pattern": "*.py"}, cid=f"b{i}") for i in range(4)]
    out = solve("fix add", workdir=str(d), task_id="t", max_turns=4, client=MockClient(FIRST + idle))
    assert out["attempts"] == 2 and not out["ok"]
    assert "a * b" in (d / "calc.py").read_text() and (d / "scratch.py").exists()
    assert not (d / "notes.py").exists() and (d / "data.bin").read_bytes() == b"junk"


def test_snapshot_restore_round_trip(tmp_path, monkeypatch):
    d = _repo(tmp_path, monkeypatch)
    snap = snapshot(str(d))
    (d / "notes.py").unlink()
    (d / "logo.bin").write_bytes(b"changed")
    (d / "new_dir").mkdir()
    (d / "new_dir" / "n.py").write_text("n = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=d)
    assert restore(str(d), snap)
    assert not (d / "new_dir" / "n.py").exists()
    _user_state_kept(d)
