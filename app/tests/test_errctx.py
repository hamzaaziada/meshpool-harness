"""MH_ERROR_CONTEXT: a failing command's output gets excerpts of the project code it points to. Real processes where
the tool is on this machine, captured compiler output otherwise; no model."""
import shutil

import pytest

from meshpool_harness.core import errctx
from meshpool_harness.core.tools import Workspace


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("MH_ERROR_CONTEXT", "1")


def _repo(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "calc.py").write_text(
        "def scale(values, factor):\n    total = sum(values)\n    return [v / total * factor for v in values]\n")
    (tmp_path / "run.py").write_text("import json\nfrom pkg.calc import scale\n\njson.dumps({})\n"
                                     "print(scale([0, 0], 1))\n")
    return Workspace(str(tmp_path), str(tmp_path / ".run"))


def test_python_traceback_shows_deepest_project_frame_first(tmp_path):
    ws = _repo(tmp_path)
    out = ws.bash("python run.py")
    assert out.startswith("[exit 1]")
    block = out.split("--- where it failed", 1)[1]
    assert block.index("calc.py line 3") < block.index("run.py line 5")       # cause first, caller second
    assert "ZeroDivisionError" in block
    assert ">   3      return [v / total * factor for v in values]" in block
    assert "json/" not in block                                                 # standard library skipped


def test_excerpt_counts_as_a_read_so_the_file_can_be_edited(tmp_path):
    ws = _repo(tmp_path)
    ws.bash("python run.py")
    out = ws.edit("pkg/calc.py", "v / total * factor", "(v / total * factor if total else 0)")
    assert out == "edited pkg/calc.py"


def test_off_by_default_and_silent_on_success(tmp_path, monkeypatch):
    ws = _repo(tmp_path)
    assert "where it failed" not in ws.bash("python -c 'print(1)'")
    monkeypatch.delenv("MH_ERROR_CONTEXT")
    assert "where it failed" not in ws.bash("python run.py")


def test_pytest_failure(tmp_path):
    ws = _repo(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text(
        "from pkg.calc import scale\n\ndef test_empty():\n    assert scale([0, 0], 2) == [0, 0]\n")
    out = ws.bash("python -m pytest -q -p no:cacheprovider tests")
    assert "--- where it failed" in out and "pkg/calc.py line 3" in out


def test_cd_prefix_resolves_relative_paths(tmp_path):
    ws = _repo(tmp_path)
    out = ws.bash("cd pkg && python -c 'from calc import scale; scale([0], 1)'")
    assert "calc.py line 3" in out.split("--- where it failed", 1)[1]


def test_bash_many_keeps_the_block_after_trimming(tmp_path):
    ws = _repo(tmp_path)
    out = ws.bash_many(["python -c 'print(\"x\" * 5000)'", "python run.py"])
    assert out.count("--- where it failed") == 1 and "calc.py line 3" in out


def test_paths_outside_the_project_are_ignored(tmp_path):
    ws = _repo(tmp_path)
    assert errctx.context('Traceback (most recent call last):\n  File "/etc/hosts", line 1, in <module>\n'
                          "ValueError: x\n", ws) == ""


@pytest.mark.parametrize("line,path,n,col", [
    ("src/a.ts(2,7): error TS2322: Type 'string' is not assignable to type 'number'.", "src/a.ts", 2, 7),
    ("src/a.c:2:5: error: use of undeclared identifier 'y'", "src/a.c", 2, 5),
    ("src/a.py:2:1: F821 Undefined name `y`", "src/a.py", 2, 1),
    ("src/a.py:2: error: Name \"y\" is not defined  [name-defined]", "src/a.py", 2, 0),
    ("  /abs/src/a.py:2:1 - error: \"y\" is not defined (reportUndefinedVariable)", "/abs/src/a.py", 2, 1),
    ("    at scale (/abs/src/a.js:2:9)", "/abs/src/a.js", 2, 9),
])
def test_compiler_and_linter_formats(line, path, n, col):
    loc = errctx.locations(line)
    assert loc and loc[0][:3] == (path, n, col)


def test_rustc_takes_the_message_from_the_line_above():
    out = "error[E0425]: cannot find value `y` in this scope\n --> src/main.rs:2:5\n  |\n"
    assert errctx.locations(out)[0] == ("src/main.rs", 2, 5, "error[E0425]: cannot find value `y` in this scope")


@pytest.mark.skipif(not shutil.which("tsc"), reason="tsc not installed")
def test_real_tsc(tmp_path):
    ws = Workspace(str(tmp_path), str(tmp_path / ".run"))
    (tmp_path / "a.ts").write_text("const n: number = 1;\nconst s: number = 'x';\nconsole.log(n, s);\n")
    out = ws.bash("tsc --noEmit --pretty false a.ts")
    assert "a.ts line 2, column 7: error TS2322" in out and ">   2  const s: number = 'x';" in out


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_real_node_stack(tmp_path):
    ws = Workspace(str(tmp_path), str(tmp_path / ".run"))
    (tmp_path / "a.js").write_text("function f(o) {\n  return o.x.y;\n}\nf({});\n")
    out = ws.bash("node a.js")
    assert "a.js line 2" in out.split("--- where it failed", 1)[1]
