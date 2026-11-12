"""Connectors: parsing, Jira ADF flattening, registry and custom plug-ins, HTTP calls mocked (no network)."""
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from meshpool_harness import connectors  # noqa: E402
from meshpool_harness.connectors import base  # noqa: E402


def test_github_ref_parsing():
    from meshpool_harness.connectors.github import GitHub
    assert GitHub.parse("acme/app#12") == ("acme", "app", 12)
    assert GitHub.parse("https://github.com/acme/app/issues/7") == ("acme", "app", 7)


def test_github_fetch_and_report(monkeypatch):
    calls = []

    def fake(method, url, headers, body=None, timeout=30):
        calls.append((method, url, body))
        return {"title": "Crash on empty input", "body": "Steps...", "html_url": "u", "labels": [{"name": "bug"}]}
    monkeypatch.setattr("meshpool_harness.connectors.github.http_json", fake)
    conn, ref = connectors.get("github:acme/app#3")
    t = conn.fetch(ref)
    assert t.title == "Crash on empty input" and t.labels == ["bug"]
    conn.report(t, {"ok": True, "answer": "fixed"})
    assert calls[-1][0] == "POST" and calls[-1][1].endswith("/issues/3/comments") and "Solved" in calls[-1][2]["body"]


def test_jira_adf_and_fetch(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://x.atlassian.net")
    adf = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Login fails"}]}]}
    monkeypatch.setattr("meshpool_harness.connectors.jira.http_json",
                        lambda *a, **k: {"fields": {"summary": "Bug", "description": adf, "labels": ["auth"]}})
    conn, ref = connectors.get("jira:PROJ-7")
    t = conn.fetch(ref)
    assert t.ref == "PROJ-7" and "Login fails" in t.body and t.labels == ["auth"]


def test_file_connector(tmp_path):
    p = tmp_path / "task.md"
    p.write_text("# Add a flag\nAdd --dry-run to the CLI.")
    conn, ref = connectors.get(f"file:{p}")
    t = conn.fetch(ref)
    assert t.title == "Add a flag" and "--dry-run" in t.body
    conn.report(t, {"ok": False})
    assert json.loads(Path(str(p) + ".result.json").read_text())["ok"] is False


def test_custom_plugin(monkeypatch):
    mod = types.ModuleType("my_connector")

    @connectors.register("linear")
    class Linear(base.Connector):
        def fetch(self, ref):
            return base.Task("linear", ref, "Title " + ref)
    mod.Linear = Linear
    sys.modules["my_connector"] = mod
    monkeypatch.setenv("MH_CONNECTORS", "my_connector")
    conn, ref = connectors.get("linear:ENG-1")
    assert conn.fetch(ref).title == "Title ENG-1"


def test_unknown_connector():
    try:
        connectors.get("nope:1")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "available" in str(e)
