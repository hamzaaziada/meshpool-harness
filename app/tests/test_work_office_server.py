"""Meshpool Work gets the Office MCP server (make_document, make_pdf, conversions) wherever it runs: in the app and
over ACP (`meshpool acp --profile work`). Both use harnesses/acp.py office_extra, so they cannot drift."""
import sys

import pytest

from meshpool_harness.harnesses.acp import office_extra


def test_office_extra_spec(tmp_path):
    ex = office_extra(str(tmp_path))
    spec = ex["meshpool-office"]
    assert spec["command"] == sys.executable
    assert spec["args"] == ["-m", "meshpool_harness.agent.office_mcp_server"]
    assert spec["timeout"] == 330 and spec["env"]["OFFICE_WAIT_S"] == "280"
    assert spec["env"]["MESHPOOL_PROJECT"] == str(tmp_path.resolve())


class _FakeManager:
    seen: list = []

    def __init__(self, roots=None, extra=None, **kw):
        _FakeManager.seen.append(dict(extra or {}))

    def start(self, timeout=0):
        return self


@pytest.fixture
def server(monkeypatch):
    from meshpool_harness.agent import acp_server as A
    from meshpool_harness.agent import mcp as M
    _FakeManager.seen = []
    monkeypatch.setattr(M, "Manager", _FakeManager)

    def make(profile):
        s = A.Server(profile=profile)
        s.on_event = lambda *a: None
        return s
    return make


def test_acp_work_session_gets_the_office_server(server, tmp_path):
    server("work").new_session({"cwd": str(tmp_path)})
    assert "meshpool-office" in _FakeManager.seen[-1]


def test_acp_code_session_does_not(server, tmp_path):
    server("code").new_session({"cwd": str(tmp_path)})
    assert "meshpool-office" not in _FakeManager.seen[-1]


def test_client_server_with_the_same_name_wins(server, tmp_path):
    server("work").new_session({"cwd": str(tmp_path), "mcpServers": [
        {"name": "meshpool-office", "command": "my-office", "args": [], "env": []}]})
    assert _FakeManager.seen[-1]["meshpool-office"]["command"] == "my-office"
