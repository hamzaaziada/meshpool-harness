"""Bring your context: the paste-back prompt, ChatGPT and Claude exports, instructions as skills, bundled skills."""
import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class _Stub:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def chat(self, **kw):
        self.calls += 1
        return {"choices": [{"message": {"content": self.answer}}]}


def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))


def test_pasted_answer_becomes_instructions_and_memory(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    from meshpool_harness import assistant as AS, importer as IM
    assert "## Instructions" in IM.context_prompt() and "No secrets" in IM.context_prompt()
    r = IM.import_context_text("""## Instructions
- Answer in British English, short paragraphs
## About me
- I am a corporate development lead at a semiconductor company
- My API key is sk-123 (please remember)
## Recurring work
- Monthly board update deck with a one-page summary""")
    assert r["instructions"] == 1 and r["memory"] == 2 and len(r["skipped"]) == 1      # the secret is refused
    assert "British English" in AS.instructions()
    assert "corporate development" in AS.memory() and "sk-123" not in AS.memory()
    again = IM.import_context_text("## Instructions\n- Answer in British English, short paragraphs")
    assert again["instructions"] == 0                                                  # no duplicates


def _zip(files: dict) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, obj in files.items():
            z.writestr(name, json.dumps(obj))
    p = Path(__import__("tempfile").mkdtemp()) / "export.zip"
    p.write_bytes(buf.getvalue())
    return str(p)


def test_claude_export_projects_become_skills(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    from meshpool_harness import importer as IM, skills as SK
    path = _zip({"users.json": [{"full_name": "x"}],
                 "projects.json": [{"name": "Board memos", "description": "How we write board memos",
                                    "prompt_template": "Write memos with the answer first, then three reasons.",
                                    "docs": [{"filename": "style.md", "content": "Use plain English."}]}],
                 "conversations.json": [{"name": "Q3 board memo", "updated_at": "2026-10-01",
                                         "chat_messages": [{"sender": "human", "text": "Draft the Q3 board memo"}]}]})
    stub = _Stub("## Instructions\n- Lead with the answer\n## About me\n- Writes quarterly board memos")
    r = IM.import_export(path, client=stub)
    assert r["kind"] == "claude" and r["skills"] == ["project-board-memos"] and stub.calls == 1
    assert (SK.folder() / "project-board-memos" / "style.md").read_text() == "Use plain English."
    assert r["memory"] == 1 and r["instructions"] == 1


def test_chatgpt_export_reads_openers_not_chats(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    from meshpool_harness import importer as IM
    conv = {"title": "DCF for a data-centre company", "create_time": 1,
            "mapping": {"a": {"message": {"author": {"role": "user"}, "content": {"parts": ["Build a DCF for X"]}}}}}
    path = _zip({"conversations.json": [conv], "user.json": {"id": "u"}})
    stub = _Stub("## About me\n- Builds valuation models for infrastructure companies")
    r = IM.import_export(path, client=stub)
    assert r["kind"] == "chatgpt" and r["memory"] == 1 and r["skills"] == [] and r["conversations_seen"] == 1


def test_instructions_become_a_skill(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    from meshpool_harness import importer as IM, skills as SK
    sid = IM.import_instructions_as_skill("deal-memo-writer", "You write two-page deal memos: thesis, numbers, risks.")
    assert sid == "deal-memo-writer" and (SK.folder() / sid / "SKILL.md").exists()


def test_bundled_professional_skills_ship_with_their_licence():
    d = ROOT / "defaults" / "skills"
    for name in ("finance-financial-statements", "legal-review-contract", "sales-account-research",
                 "customer-support-ticket-triage"):
        assert (d / name / "SKILL.md").exists() and (d / name / "LICENSE").exists() and (d / name / "NOTICE.md").exists()
        assert f"name: {name}" in (d / name / "SKILL.md").read_text()
    for name in ("finance-buy-side-deal-analysis", "finance-trading-comps", "finance-precedent-transactions",
                 "finance-dcf-valuation", "finance-assumption-book", "finance-three-statement-model",
                 "finance-valuation-method-selection", "finance-research-method"):
        assert (d / name / "SKILL.md").exists()
