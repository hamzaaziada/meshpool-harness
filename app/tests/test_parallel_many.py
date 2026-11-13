"""MH_PARALLEL_MANY: read-only bash_many batches run at the same time (no model calls)."""
import time

import pytest

from meshpool_harness.core.tools import Workspace, read_only


@pytest.mark.parametrize("cmd", ["cat a.txt", "grep -rn foo src | head -20", "ls -la && wc -l *.py",
                                 "git log --oneline -5", "sed -n 1,40p x.py", "find . -name '*.py' 2>/dev/null",
                                 "python3 --version", "pip show requests", "LC_ALL=C sort f | uniq -c"])
def test_read_only_yes(cmd):
    assert read_only(cmd)


@pytest.mark.parametrize("cmd", ["echo x > f", "rm -rf build", "sed -i s/a/b/ f", "pip install x", "python3 t.py",
                                 "git checkout -- .", "make", "find . -delete", "cat $(ls)", "tee f", "cd d && ls",
                                 "npm install", "cat <<EOF\nx\nEOF", "find . -exec rm {} \\;", "ls | xargs rm",
                                 "awk 'BEGIN{system(\"rm f\")}'", "curl -o f http://x", "sleep 2", "'unclosed"])
def test_read_only_no(cmd):
    assert not read_only(cmd)


def _ws(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\n")
    return Workspace(str(tmp_path), str(tmp_path / ".run"))


def test_off_keeps_order_and_is_sequential(tmp_path, monkeypatch):
    monkeypatch.delenv("MH_PARALLEL_MANY", raising=False)
    import meshpool_harness.core.tools as T
    monkeypatch.setattr(T, "_RO_PROGS", T._RO_PROGS | {"sleep"})
    ws = _ws(tmp_path)
    t0 = time.time()
    out = ws.bash_many(["sleep 0.6; cat a.txt", "sleep 0.6; echo two", "sleep 0.6; echo three"])
    assert time.time() - t0 >= 1.7
    assert out.index("alpha") < out.index("two") < out.index("three")


def test_on_runs_together_same_output(tmp_path, monkeypatch):
    import meshpool_harness.core.tools as T
    monkeypatch.setattr(T, "_RO_PROGS", T._RO_PROGS | {"sleep"})
    cmds = ["sleep 0.6; cat a.txt", "sleep 0.6; echo two", "sleep 0.6; echo three"]
    ws = _ws(tmp_path)
    monkeypatch.delenv("MH_PARALLEL_MANY", raising=False)
    seq = ws.bash_many(cmds)
    monkeypatch.setenv("MH_PARALLEL_MANY", "1")
    t0 = time.time()
    par = ws.bash_many(cmds)
    assert time.time() - t0 < 1.3
    assert par == seq


def test_on_but_a_write_runs_in_order(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_PARALLEL_MANY", "1")
    ws = _ws(tmp_path)
    out = ws.bash_many(["echo new > b.txt", "cat b.txt"])
    assert "new" in out.split("$ cat b.txt", 1)[1]


def test_parallel_capped_outputs_get_unique_files(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_PARALLEL_MANY", "1")
    ws = _ws(tmp_path)
    (tmp_path / "big.txt").write_text("x" * 20000)
    ws.bash_many(["cat big.txt"] * 4)
    assert len(list((tmp_path / ".run").glob("out_*_bash.txt"))) == 4
