"""Engine job reports: the headline never says "passed" above failing rules, and repeated builds are told to stop.
Seen live: a 5-slide deck was rebuilt 8 times in 20 minutes because each report said "quality checks passed" while
listing failing rules, and the renderer-scope failures could never be cleared by a rebuild. No model calls."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meshpool_harness.agent import office_mcp_server as O  # noqa: E402


def _job(tmp_path, monkeypatch, notes, qa, ok=True, build_no=1):
    (tmp_path / "deck.pptx").write_bytes(b"x")
    monkeypatch.setattr("engines.forge.tools.qa_summary", lambda files: qa)
    O.JOBS["t1"] = {"engine": "slides", "out": tmp_path, "started": time.time(), "done": time.time(),
                    "build_no": build_no, "result": {"ok": ok, "steps": [{"answer": notes}]}}
    try:
        return O._report("t1")[0]
    finally:
        O.JOBS.pop("t1", None)


def test_headline_reflects_failing_rules(tmp_path, monkeypatch):
    text = _job(tmp_path, monkeypatch, "Built deck.pptx: 5 slides. rules failing: P1, P4.",
                "- deck.pptx: forge rules failing: P4 (run check_deck/check_workbook for the repair list)")
    assert "checks passed" not in text and "flagged" in text
    text = _job(tmp_path, monkeypatch, "Built deck.pptx: 5 slides.",
                "- deck.pptx: forge rules failing: none (run check_deck/check_workbook for the repair list)")
    assert "quality checks passed" in text and "stop here" not in text
    text = _job(tmp_path, monkeypatch, "", "", ok=False)
    assert "did not all pass" in text


def test_repeated_builds_are_told_to_stop(tmp_path, monkeypatch):
    assert "stop here" not in _job(tmp_path, monkeypatch, "", "", build_no=O.REBUILD_LIMIT - 1)
    assert "stop here" in _job(tmp_path, monkeypatch, "", "", build_no=O.REBUILD_LIMIT)


def test_build_numbers_count_per_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(O, "_project", lambda: tmp_path)
    monkeypatch.setattr(O, "WAIT", 0)
    monkeypatch.setattr(O, "_run_job", lambda jid: None)
    saved = dict(O.JOBS)
    O.JOBS.clear()
    try:
        for _ in range(3):
            O._engine("slides", "a deck", None)
        O._engine("sheets", "a model", None)
        nos = sorted((j["engine"], j["build_no"]) for j in O.JOBS.values())
        assert nos == [("sheets", 1), ("slides", 1), ("slides", 2), ("slides", 3)]
    finally:
        O.JOBS.clear()
        O.JOBS.update(saved)


def test_check_notes_say_what_a_rebuild_can_fix():
    from engines.forge.tools import _scope_note
    assert _scope_note({"failures": []}) == ""
    content = {"failures": [{"status": "fail", "fix_scope": "content"}]}
    assert "at most twice" in _scope_note(content) and "/renderer" not in _scope_note(content)
    mixed = {"failures": [{"status": "fail", "fix_scope": "content"}, {"status": "fail", "fix_scope": "renderer"}]}
    assert "does not fix them" in _scope_note(mixed)
    assert _scope_note({"failures": [{"status": "pass", "fix_scope": "renderer"}]}) == ""


def test_unknown_arguments_are_dropped_not_fatal(monkeypatch):
    got = {}
    monkeypatch.setitem(O.TOOLS, "probe", (lambda path: (got.setdefault("path", path) or "ok", False),
                                           "probe", {"path": {"type": "string"}}, ["path"]))
    r = O.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "probe", "arguments": {"path": "a.pptx", "engine": "slides"}}})
    text = r["result"]["content"][0]["text"]
    assert not r["result"]["isError"] and got["path"] == "a.pptx" and "Ignored unknown argument(s): engine" in text
