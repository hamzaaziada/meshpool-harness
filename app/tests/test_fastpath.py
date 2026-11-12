"""Fast path: candidate files from hints, exact edits applied only when unique, draft note via a mock client."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engines.code import fastpath  # noqa: E402


def test_candidate_files_skip_tests():
    h = "Definitions:\n  pkg/a.py:3: def f():\nSource files that mention them most:\n  pkg/b.py (4 mentions)\n  tests/test_a.py (2 mentions)"
    assert fastpath.candidate_files(h) == ["pkg/a.py", "pkg/b.py"]


def test_apply_unique_only():
    d = tempfile.mkdtemp()
    Path(d, "a.py").write_text("x = 1\ny = 1\n")
    assert fastpath.apply_edits(d, [{"path": "a.py", "search": "x = 1\n", "replace": "x = 2\n"}]) == ["a.py"]
    assert fastpath.apply_edits(d, [{"path": "a.py", "search": "= ", "replace": "=="}]) == []   # not unique
    assert Path(d, "a.py").read_text() == "x = 2\ny = 1\n"


def test_draft_with_mock_client():
    d = tempfile.mkdtemp()
    Path(d, "pkg").mkdir()
    Path(d, "pkg/a.py").write_text("def add(a, b):\n    return a - b\n")

    class C:
        def chat(self, **k):
            return {"choices": [{"message": {"content": json.dumps({"edits": [
                {"path": "pkg/a.py", "search": "    return a - b\n", "replace": "    return a + b\n"}], "why": "sign"})}}]}
    note = fastpath.draft("add is wrong", "  pkg/a.py:1: def add(a, b):", d, C(), "m", "t")
    assert "ALREADY APPLIED" in note and "a + b" in Path(d, "pkg/a.py").read_text()
