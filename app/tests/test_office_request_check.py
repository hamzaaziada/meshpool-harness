"""office_task and the Office MCP server refuse a request with no content, or a Word-type document sent to an engine
that does not make one, before any job folder exists (agent/office_jobs.py check_request). No engine runs."""
import pytest

from meshpool_harness.agent import office_jobs as OJ


@pytest.mark.parametrize("engine,request_text", [
    ("sheets", "placeholder"),
    ("slides", ""),
    ("data", "   "),
    ("sheets", "x"),
    ("slides", "TODO"),
    ("sheets", "test test"),
    ("data", "..."),
])
def test_empty_requests_are_refused(engine, request_text):
    why = OJ.check_request(engine, request_text)
    assert why and "does not describe a deliverable" in why


@pytest.mark.parametrize("engine,request_text", [
    ("sheets", "This is a Word memo task."),
    ("slides", "A one-page memo to the finance team about expense deadlines"),
    ("data", "Write a cover letter for the analyst role"),
    ("sheets", "Make memo.docx announcing the new expense rules"),
])
def test_word_documents_go_to_make_document(engine, request_text):
    why = OJ.check_request(engine, request_text)
    assert why and "make_document" in why and engine in why


@pytest.mark.parametrize("engine,request_text", [
    ("slides", "a deck"),
    ("sheets", "a model"),
    ("sheets", "A 12-month cafe budget with live formulas"),
    ("slides", "A deck summarising the attached memo for the board"),       # names its own deliverable too
    ("sheets", "An Excel tracker for letters of credit"),
    ("data", "Analyse the survey CSV and chart the trend by month"),
    ("data", "Analysis of the customer letters in letters.csv"),
])
def test_real_requests_pass(engine, request_text):
    assert OJ.check_request(engine, request_text) is None


def test_mcp_server_refuses_before_making_a_job(tmp_path, monkeypatch):
    from meshpool_harness.agent import office_mcp_server as O
    monkeypatch.setattr(O, "_project", lambda: tmp_path)
    started = []
    monkeypatch.setattr(OJ, "new_job", lambda *a, **k: started.append(a) or "never")
    text, is_error = O.make_spreadsheet("placeholder")
    assert is_error and text.startswith("Refused, nothing was built") and not started
    assert not (tmp_path / "Meshpool outputs").exists()


def test_office_task_refuses_before_making_a_job(tmp_path, monkeypatch):
    from meshpool_harness.agent.core import Agent
    started = []
    monkeypatch.setattr(OJ, "new_job", lambda *a, **k: started.append(a) or "never")
    a = Agent(str(tmp_path), mode="auto", profile="work")
    out, ok = a._office({"engine": "sheets", "request": "placeholder"})
    assert not ok and out.startswith("ERROR: office_task refused, nothing was built") and not started
    out, ok = a._office({"engine": "sheets", "request": "A one-page memo to the finance team"})
    assert not ok and "make_document" in out and not started
    assert not (tmp_path / "Meshpool outputs").exists()
