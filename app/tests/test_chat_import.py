"""Old chats and sessions become Meshpool threads: ChatGPT and Claude exports, Claude Code and Codex sessions."""
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mp"))
    monkeypatch.setenv("MH_RUNS_DIR", str(tmp_path / "mp" / "runs"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "user" / ".claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "user" / ".codex"))
    (tmp_path / "user").mkdir()
    (tmp_path / "mp").mkdir()
    import meshpool_harness.agent.core as core
    import meshpool_harness.ui.agent_api as A
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "mp" / "runs" / "sessions")
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "mp" / "runs" / "sessions")
    monkeypatch.setattr(A, "_ROWS", {})
    return tmp_path


def _gpt_node(nid, parent, role, text, recipient="all", hidden=False):
    return {"id": nid, "parent": parent, "children": [], "message": {
        "author": {"role": role}, "recipient": recipient, "content": {"content_type": "text", "parts": [text]},
        "metadata": {"is_visually_hidden_from_conversation": hidden}}}


def chatgpt_zip(path: Path) -> Path:
    conv = {"title": "Pricing the Q3 launch", "create_time": 1700000000.0, "update_time": 1700000500.5,
            "conversation_id": "c-1", "current_node": "a2", "mapping": {
                "root": {"id": "root", "parent": None, "children": ["sys"], "message": None},
                "sys": _gpt_node("sys", "root", "system", "You are ChatGPT", hidden=True),
                "u1": _gpt_node("u1", "sys", "user", "How should we price the Q3 launch?"),
                "t1": _gpt_node("t1", "u1", "assistant", "search('saas pricing')", recipient="browser"),
                "a1": _gpt_node("a1", "t1", "assistant", "Start with three tiers."),
                "u2": _gpt_node("u2", "a1", "user", "And the enterprise tier?"),
                "a2": _gpt_node("a2", "u2", "assistant", "Price it per seat, with a floor."),
                "old": _gpt_node("old", "u2", "assistant", "An abandoned branch reply.")}}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("conversations.json", json.dumps([conv, {"title": "empty", "mapping": {}}]))
        z.writestr("user.json", "{}")
        z.writestr("chat.html", "<html></html>")
    return path


def claude_zip(path: Path) -> Path:
    conv = {"uuid": "u-9", "name": "Board memo", "created_at": "2026-03-01T10:00:00Z",
            "updated_at": "2026-03-02T11:00:00.123Z", "chat_messages": [
                {"sender": "human", "text": "Draft a board memo on hiring.", "content": [],
                 "attachments": [{"file_name": "plan.txt", "extracted_content": "Hire 4 engineers"}]},
                {"sender": "assistant", "text": "", "content": [{"type": "text", "text": "Here is a draft."},
                                                                 {"type": "tool_use", "name": "web_search"}]}]}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("data/conversations.json", json.dumps([conv]))
        z.writestr("data/projects.json", json.dumps([]))
        z.writestr("data/users.json", "[]")
    return path


def test_chatgpt_export_becomes_threads_and_follows_the_shown_branch(home):
    from meshpool_harness import chat_import as CI
    r = CI.import_export(str(chatgpt_zip(home / "gpt.zip")), context=False)
    assert r["kind"] == "chatgpt" and r["imported"] == 1 and r["conversations_seen"] == 2
    d = json.loads(next((home / "mp" / "runs" / "sessions").glob("imp-chatgpt-*/session.json")).read_text())
    assert d["title"] == "Pricing the Q3 launch" and d["kind"] == "cowork" and d["harness"] == "meshpool-work"
    assert [m["role"] for m in d["messages"]] == ["user", "assistant", "user", "assistant"]
    text = json.dumps(d["messages"])
    assert "per seat" in text and "abandoned" not in text and "You are ChatGPT" not in text and "search(" not in text
    assert d["updated"] == 1700000500.5 and Path(d["workdir"]).is_dir()
    # importing again adds nothing
    assert CI.import_export(str(home / "gpt.zip"), context=False)["skipped"] == 1


def test_claude_export_keeps_attachments_and_tool_notes(home):
    from meshpool_harness import chat_import as CI
    r = CI.import_export(str(claude_zip(home / "claude.zip")), context=False)
    assert r["kind"] == "claude" and r["imported"] == 1
    d = json.loads(next((home / "mp" / "runs" / "sessions").glob("imp-claude-*/session.json")).read_text())
    assert d["title"] == "Board memo"
    assert "Hire 4 engineers" in d["messages"][0]["content"] and "[Used web_search]" in d["messages"][1]["content"]


def test_claude_code_and_codex_sessions_open_in_their_folders(home):
    from meshpool_harness import chat_import as CI
    proj = home / "user" / "code" / "app"
    proj.mkdir(parents=True)
    cc = home / "user" / ".claude" / "projects" / "-code-app"
    cc.mkdir(parents=True)
    rows = [{"type": "summary", "summary": "Fix the login bug"},
            {"type": "user", "cwd": str(proj), "timestamp": "2026-05-01T09:00:00Z",
             "message": {"role": "user", "content": "The login button does nothing"}},
            {"type": "assistant", "cwd": str(proj), "timestamp": "2026-05-01T09:00:05Z",
             "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "secret"},
                                                           {"type": "text", "text": "Let me look."},
                                                           {"type": "tool_use", "name": "Bash",
                                                            "input": {"command": "npm test"}}]}},
            {"type": "user", "cwd": str(proj), "message": {"role": "user", "content": [
                {"type": "tool_result", "content": "1 failing"}]}},
            {"type": "user", "cwd": str(proj), "isSidechain": True, "message": {"role": "user", "content": "sub"}},
            {"type": "assistant", "cwd": str(proj), "timestamp": "2026-05-01T09:01:00Z",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "Fixed the handler."}]}}]
    (cc / "s1.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    cx = home / "user" / ".codex" / "sessions" / "2026" / "05" / "02"
    cx.mkdir(parents=True)
    crow = [{"timestamp": "2026-05-02T10:00:00Z", "type": "session_meta", "payload": {"id": "cx-1", "cwd": str(proj)}},
            {"timestamp": "2026-05-02T10:00:01Z", "type": "response_item", "payload": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>x"}]}},
            {"timestamp": "2026-05-02T10:00:02Z", "type": "response_item", "payload": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": "Add a dark mode"}]}},
            {"timestamp": "2026-05-02T10:00:03Z", "type": "response_item", "payload": {
                "type": "function_call", "name": "shell", "arguments": json.dumps({"command": ["rg", "theme"]})}},
            {"timestamp": "2026-05-02T10:00:09Z", "type": "response_item", "payload": {
                "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Done."}]}}]
    (cx / "rollout-1.jsonl").write_text("\n".join(json.dumps(r) for r in crow))
    scan = CI.scan_local()
    assert scan["claude-code"]["found"] == 1 and scan["codex"]["found"] == 1
    r = CI.import_local()
    assert r["imported"] == 2
    ses = {p.parent.name.split("-")[1]: json.loads(p.read_text())
           for p in (home / "mp" / "runs" / "sessions").glob("imp-*/session.json")}
    c = ses["claude"]                                # imp-claude-code-...
    assert c["title"] == "Fix the login bug" and c["workdir"] == str(proj) and c["kind"] == "code"
    assert c["messages"][0]["content"] == "The login button does nothing"
    assert "Let me look." in c["messages"][1]["content"] and "[Bash: npm test]" in c["messages"][1]["content"]
    assert "Fixed the handler." in c["messages"][1]["content"]            # one assistant turn, merged
    assert "secret" not in json.dumps(c) and "1 failing" not in json.dumps(c) and "sub" != c["messages"][-1]["content"]
    x = ses["codex"]
    assert x["title"] == "Add a dark mode" and [m["role"] for m in x["messages"]] == ["user", "assistant"]
    assert "rg theme" in x["messages"][1]["content"] or "rg" in x["messages"][1]["content"]
    assert CI.scan_local()["codex"]["imported"] == 1 and CI.import_local()["imported"] == 0


def test_imported_threads_are_listed_searchable_and_resumable(home):
    from meshpool_harness import assistant as AS
    from meshpool_harness import chat_import as CI
    from meshpool_harness.agent.core import Agent
    from meshpool_harness.ui import agent_api as A
    CI.import_export(str(chatgpt_zip(home / "gpt.zip")), context=False)
    rows = A.listing()
    row = next(r for r in rows if r["id"].startswith("imp-chatgpt-"))
    assert row["title"] == "Pricing the Q3 launch" and row["kind"] == "cowork" and row["imported"] == "chatgpt"
    assert "Pricing the Q3 launch" in AS.search_chats("enterprise tier")
    d = json.loads((home / "mp" / "runs" / "sessions" / row["id"] / "session.json").read_text())
    a = Agent.resume(d["workdir"], row["id"])
    assert a is not None and a.title == "Pricing the Q3 launch" and len(a.messages) == 5   # system + 4
    assert A.listing()[0]["id"] == row["id"]                                              # served from the cache


def test_bad_files_say_what_to_do(home):
    from meshpool_harness import chat_import as CI
    (home / "x.zip").write_text("not a zip")
    with pytest.raises(ValueError, match="not a .zip"):
        CI.import_export(str(home / "x.zip"))
    with zipfile.ZipFile(home / "y.zip", "w") as z:
        z.writestr("readme.txt", "hi")
    with pytest.raises(ValueError, match="does not look like"):
        CI.import_export(str(home / "y.zip"))
