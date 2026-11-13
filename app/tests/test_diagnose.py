"""MH_LINT_EDIT (new problems after an edit) and MH_NAV (symbol: definition and uses). Real Ruff and Jedi, no model."""
import pytest

from meshpool_harness.core.diagnose import new_problems, problems
from meshpool_harness.core.tools import Workspace


def _ws(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "geo.py").write_text(
        "import os\n\n\ndef area(w, h):\n    \"\"\"Rectangle area.\"\"\"\n    return w * h\n\n\n"
        "def total(shapes):\n    return sum(area(w, h) for w, h in shapes)\n")
    (tmp_path / "pkg" / "use.py").write_text("from pkg.geo import area\n\nprint(area(2, 3))\n")
    ws = Workspace(str(tmp_path), str(tmp_path / ".run"))
    ws.read_file("pkg/geo.py")
    return ws


def test_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("MH_LINT_EDIT", raising=False)
    ws = _ws(tmp_path)
    assert ws.edit("pkg/geo.py", "return w * h", "return w * hh") == "edited pkg/geo.py"


def test_new_undefined_name_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_LINT_EDIT", "1")
    ws = _ws(tmp_path)
    out = ws.edit("pkg/geo.py", "return w * h", "return w * hh")
    assert "F821" in out and "hh" in out and "line 6" in out
    assert "Files changed so far: pkg/geo.py" in out


def test_clean_edit_and_old_warnings_stay_quiet(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_LINT_EDIT", "1")
    ws = _ws(tmp_path)
    (tmp_path / "pkg" / "old.py").write_text("x = undefined_before\n")
    ws.read_file("pkg/old.py")
    out = ws.edit("pkg/old.py", "x = undefined_before", "x = undefined_before\ny = 1")
    assert "No new errors" in out and "F821" not in out           # the existing problem is not repeated
    out = ws.write_file("pkg/new.py", "def f():\n    return g()\n")
    assert "F821" in out and "`g`" in out                          # a new file: everything in it is new


def test_problems_fallback_without_ruff(monkeypatch, tmp_path):
    import meshpool_harness.core.diagnose as D
    monkeypatch.setattr(D.shutil, "which", lambda *_: None)
    assert problems(tmp_path / "a.py", "def f(:\n")[0][1] == "E999"
    assert problems(tmp_path / "a.txt", "def f(:\n") == []
    assert new_problems(tmp_path / "a.py", "x = 1\n", "x = 1\n") == ""


def test_symbol_definition_and_uses(tmp_path, monkeypatch):
    pytest.importorskip("jedi")
    ws = _ws(tmp_path)
    out = ws.symbol("area")
    assert "definition: pkg/geo.py:4" in out and "area(w, h)" in out
    assert "pkg/use.py:3" in out and "pkg/geo.py:10" in out


def test_symbol_unknown_name(tmp_path):
    pytest.importorskip("jedi")
    ws = _ws(tmp_path)
    assert "no definition" in ws.symbol("nope_not_here")
