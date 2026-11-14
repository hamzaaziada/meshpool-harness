"""Sheets engine at the turn cap: the model knows its budget, is told to wrap up near the end, and a run that hits the
cap still delivers its last saved workbook when it passes the finish checks (or names the file and the failures when
it does not). Build scripts and stray workbook copies leave the deliverable folder."""
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from meshpool_harness.core.client import MockClient, tool_call  # noqa: E402
from engines.sheets import engine as E  # noqa: E402

SAVE = ("python -c \"import os; os.makedirs('conv', exist_ok=True); "
        "open('Model.xlsx','wb').write(b'x'); open('conv/Model.xlsx','wb').write(b'x')\"")


def _refining(n):
    """A model that writes its build script, saves the workbook, then keeps 'refining' and never calls finish."""
    script = [tool_call("write_file", {"path": "build_workbook.py", "content": "print('built')\n"}),
              tool_call("bash", {"command": SAVE})]
    script += [tool_call("bash", {"command": f"echo refine {i}"}) for i in range(n - len(script))]
    return MockClient(script)


def _notes(client):
    return [m["content"] for m in client.requests[-1]["messages"] if m["role"] == "user"][1:]


def test_cap_with_passing_workbook_delivers_it(tmp_path, monkeypatch):
    (tmp_path / "brief.md").write_text("# brief\n")                      # an input copied in before the run
    seen = {}

    def passing(answer, artifacts, ws, report=None):
        seen["artifacts"] = artifacts
        if report is not None:
            report["rules"] = {"X1": "pass", "X2": "pass", "X11": "not_run"}
            report["warnings"] = ["X7 Inputs!A9: label has no unit"]
        return None
    monkeypatch.setattr(E, "finish_check", passing)
    c = _refining(10)
    r = E.solve("Build an IC model from brief.md", workdir=str(tmp_path), task_id="t", max_turns=10, client=c,
                model="mock", files=[str(tmp_path / "brief.md")])
    assert r["ok"] is True and r["turns"] == 10
    assert r["reason"].startswith("turn cap 10 reached") and "checks pass" in r["reason"]
    assert r["artifacts"] == [str(tmp_path / "Model.xlsx")] and Path(r["artifacts"][0]).exists()
    assert seen["artifacts"] == [str(tmp_path / "Model.xlsx")]          # the top-level file, not the conv/ copy
    a = r["answer"]
    assert a and "turn limit (10 turns)" in a and "last saved, checked version" in a and "Model.xlsx" in a
    assert "X1=pass" in a and "label has no unit" in a
    # the deliverable folder keeps only the workbook and the input; the script and the copy go to .run/
    left = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if ".run" not in p.parts)
    assert left == ["Model.xlsx", "brief.md"]
    assert (tmp_path / ".run" / "scripts" / "build_workbook.py").exists()
    assert (tmp_path / ".run" / "scratch" / "conv" / "Model.xlsx").exists()


def test_cap_with_failing_workbook_returns_file_and_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "finish_check", lambda answer, artifacts, ws, report=None:
                        "ENGINE CHECK: workbook QA failed:\n- checks_tab Checks!F3: shows ERROR\n"
                        "- X4 DCF!C9: hardcoded number in a formula")
    r = E.solve("Build a model", workdir=str(tmp_path), task_id="t", max_turns=6, client=_refining(6), model="mock")
    assert r["ok"] is False and r["artifacts"] == [str(tmp_path / "Model.xlsx")]
    assert r["checks"] == ["checks_tab Checks!F3: shows ERROR", "X4 DCF!C9: hardcoded number in a formula"]
    assert "fails 2 checks" in r["reason"] and "Checks!F3" in r["reason"]
    assert "does NOT pass" in r["answer"] and "hardcoded number" in r["answer"]


def test_cap_without_workbook_is_not_ok_with_reason(tmp_path):
    c = MockClient([tool_call("bash", {"command": f"echo explore {i}"}) for i in range(5)])
    r = E.solve("Build a model", workdir=str(tmp_path), task_id="t", max_turns=5, client=c, model="mock")
    assert r["ok"] is False and r["artifacts"] == []
    assert r["reason"] == "turn cap 5 reached; no workbook saved"
    assert r["answer"] and "before any workbook was saved" in r["answer"]


def test_budget_is_stated_and_wrap_up_and_finish_notes_come_near_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "finish_check", lambda *a, **k: None)
    c = _refining(20)
    E.solve("Build a model", workdir=str(tmp_path), task_id="t", max_turns=20, client=c, model="mock")
    task = c.requests[0]["messages"][1]["content"]
    assert "Turn budget: 20 turns" in task and "stop adding features by turn 16" in task
    assert "call finish before turn 20" in task and "fixed turn budget" in E.SYSTEM

    def notes_at(k):                                   # the note injected after turn k is first seen by request k+1
        return " | ".join([m["content"] for m in c.requests[k]["messages"] if m["role"] == "user"][1:])
    assert "WRAP UP" not in notes_at(15) and "FINISH NOW" not in notes_at(15)       # nothing before 80%
    assert "WRAP UP: 16 of 20 turns used; 4 left. Stop adding features" in notes_at(16)
    assert "FINISH NOW" not in notes_at(17)
    assert "FINISH NOW: 18 of 20 turns used; 2 turns left" in notes_at(18)
    assert "FINISH NOW: 19 of 20 turns used; 1 turn left" in notes_at(19)
    allnotes = _notes(c)
    assert sum("WRAP UP" in n for n in allnotes) == 1 and sum("FINISH NOW" in n for n in allnotes) == 2


def test_turn_budget_hook_thresholds(tmp_path):
    ws = E.SheetsWorkspace(str(tmp_path), str(tmp_path / ".run"))
    assert E.turn_budget(29, 50, ws) is None
    assert "BUILD NOW: 30 of 50" in E.turn_budget(30, 50, ws)                # no workbook at 60%: build now, once
    assert E.turn_budget(39, 50, ws) is None
    assert "still no .xlsx" in E.turn_budget(40, 50, ws)
    assert E.turn_budget(41, 50, ws) is None            # wrap-up once
    assert "FINISH NOW: 48 of 50 turns used; 2 turns left" in E.turn_budget(48, 50, ws)
    assert "1 turn left" in E.turn_budget(49, 50, ws)
    assert E.turn_budget(50, 50, ws) is None
    ws2 = E.SheetsWorkspace(str(tmp_path), str(tmp_path / ".run"))
    ws2.nudged = True
    (tmp_path / "m.xlsx").write_bytes(b"x")
    assert "Stop adding features" in E.turn_budget(40, 50, ws2)


def test_finished_run_moves_scripts_but_keeps_preexisting_files(tmp_path, monkeypatch):
    (tmp_path / "mine.py").write_text("# the user's own script\n")
    (tmp_path / "old.xlsx").write_bytes(b"x")
    monkeypatch.setattr(E, "finish_check", lambda *a, **k: None)
    c = MockClient([tool_call("write_file", {"path": "build_workbook.py", "content": "x = 1\n"}),
                    tool_call("bash", {"command": SAVE}),
                    tool_call("finish", {"answer": "Model.xlsx built", "evidence": ["python -m engines.sheets.check "
                                                                                    "Model.xlsx"],
                                         "artifacts": ["Model.xlsx"]})])
    r = E.solve("Build a model", workdir=str(tmp_path), task_id="t", max_turns=10, client=c, model="mock")
    assert r["ok"] and r["reason"] == "finished" and r["artifacts"] == ["Model.xlsx"]
    left = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if ".run" not in p.parts)
    assert left == ["Model.xlsx", "mine.py", "old.xlsx"]


HAS_LO = shutil.which("soffice") is not None or shutil.which("libreoffice") is not None


@pytest.mark.skipif(not HAS_LO, reason="LibreOffice needed for the real finish checks")
def test_cap_runs_the_real_finish_check_on_the_saved_model(tmp_path):
    """No stubbed checker: the full integrated model from test_sheets_statements, saved mid-run, passes the same gate
    finish runs (QA, Checks tab, forge rules, sources, model gaps) and is delivered at the cap."""
    build = (f"python -c \"import sys; sys.path.insert(0, r'{ROOT / 'tests'}'); "
             "from test_sheets_statements import build_model; build_model('Synthetic_Model.xlsx')\"")
    c = MockClient([tool_call("bash", {"command": build})] + [tool_call("bash", {"command": f"echo refine {i}"})
                                                              for i in range(3)])
    r = E.solve("Build a model", workdir=str(tmp_path), task_id="t", max_turns=4, client=c, model="mock")
    assert "[exit 0]" in [m["content"] for m in c.requests[1]["messages"] if m["role"] == "tool"][0]
    assert r["ok"] is True, r["answer"]
    assert r["artifacts"] == [str(tmp_path / "Synthetic_Model.xlsx")]
    assert "last saved, checked version" in r["answer"] and "X1=pass" in r["answer"]
    assert r["rules"] and all(v in ("pass", "not_run") for v in r["rules"].values())
