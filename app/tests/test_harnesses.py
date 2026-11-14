"""Harness registry and the ACP client, against a tiny fake ACP agent (no network, no model)."""
import json
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from meshpool_harness import config as C  # noqa: E402
from meshpool_harness import harnesses as HR  # noqa: E402
from meshpool_harness.harnesses.acp import ACPAgent  # noqa: E402

FAKE = textwrap.dedent('''
    import json, sys
    def send(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
    def upd(sid, u): send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": u}})
    pending = {}
    for line in sys.stdin:
        m = json.loads(line)
        if "method" not in m:                       # reply to our permission request
            perm = m.get("result", {}).get("outcome", {})
            sid, rid = pending.pop("p")
            upd(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed",
                      "content": [{"type": "content", "content": {"type": "text", "text": "chose " + perm.get("optionId", "?")}}]})
            upd(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "done"}})
            send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
            continue
        meth, mid, p = m["method"], m.get("id"), m.get("params", {})
        if meth == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": 1, "agentInfo": {"name": "fake"},
                  "agentCapabilities": {"loadSession": False}}})
        elif meth == "session/new":
            assert p["cwd"]
            names = [s["name"] for s in p.get("mcpServers", [])]
            send({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": "s1", "_names": names}})
        elif meth == "session/prompt":
            sid = p["sessionId"]
            upd(sid, {"sessionUpdate": "plan", "entries": [{"content": "step one", "status": "in_progress", "priority": "high"}]})
            upd(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Working. "}})
            upd(sid, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "mcp__office__make_deck", "kind": "other", "status": "pending"})
            pending["p"] = (sid, mid)
            send({"jsonrpc": "2.0", "id": 99, "method": "session/request_permission", "params": {"sessionId": sid,
                  "toolCall": {"toolCallId": "t1", "title": "Run it?", "kind": "execute"},
                  "options": [{"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"},
                              {"optionId": "deny", "name": "Deny", "kind": "reject_once"}]}})
''')


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "none.json"))
    C.ensure_home()
    return tmp_path


def test_acp_client_round_trip(home):
    script = home / "fake_acp.py"
    script.write_text(FAKE)
    proj = home / "proj"
    proj.mkdir()
    manifest = {"id": "fake", "name": "Fake", "mode": "cowork", "type": "acp", "command": sys.executable,
                "args": [str(script)]}
    events, asked = [], []

    def approve(kind, title, detail, options=None):
        asked.append((kind, [o["id"] for o in options]))
        return "allow_once", ""
    a = ACPAgent(manifest, str(proj), emit=events.append, approve=approve, mode="ask")
    try:
        reply = a.send("make me a deck")
    finally:
        a.close()
    kinds = [e["type"] for e in events]
    assert reply.endswith("done")
    assert asked == [("bash", ["allow_once", "deny"])]
    assert "plan" in kinds and "tool_start" in kinds and "tool_end" in kinds and kinds[-1] == "turn_end"
    start = next(e for e in events if e["type"] == "tool_start")
    assert start["name"] == "make_deck" and start["summary"] == "office"
    end = next(e for e in events if e["type"] == "tool_end")
    assert "chose allow_once" in end["output"]
    saved = json.loads((a.dir / "session.json").read_text())
    assert saved["harness"] == "fake" and saved["events"]


def test_registry_add_remove_and_defaults(home):
    assert HR.default_for("code") == "meshpool-code"
    assert HR.default_for("cowork") in ("hermes", "meshpool-work", "hermes-local")
    d = HR.add(json.dumps({"id": "my-agent", "name": "My agent", "mode": "code", "command": "definitely-missing-cmd"}))
    assert d["available"] is False
    assert any(h["id"] == "my-agent" for h in HR.load_all())
    with pytest.raises(ValueError):
        HR.add(json.dumps({"id": "meshpool-code", "name": "x", "command": "x"}))
    with pytest.raises(ValueError):
        HR.add("{not json")
    HR.remove("my-agent")
    assert not any(h["id"] == "my-agent" for h in HR.load_all())
    assert {e["id"] for e in HR.examples()} >= {"goose"}
    assert any(h["id"] == "gemini-cli" for h in HR.load_all())          # built in now, no longer an example


def test_a_bad_harness_file_does_not_break_the_registry(home):
    HR.folder().mkdir(parents=True, exist_ok=True)
    (HR.folder() / "numeric.json").write_text(json.dumps({"id": 5, "name": "x", "command": "y"}))
    (HR.folder() / "listcmd.json").write_text(json.dumps({"id": "listcmd", "name": "x", "command": ["y"]}))
    rows = {h["id"]: h for h in HR.load_all()}
    assert rows["numeric"]["error"] and rows["listcmd"]["error"] and "meshpool-code" in rows


@pytest.mark.parametrize("bad", ["../meshpool.config", "..", "a/b", "x\\y", ""])
def test_remove_and_add_example_refuse_paths(home, bad):
    with pytest.raises(KeyError):
        HR.remove(bad)
    with pytest.raises(KeyError):
        HR.add_example(bad)


def test_windows_sign_in_command_line_is_quoted_for_cmd():
    npx = r"C:\Program Files\nodejs\npx.cmd"
    line = f'"{npx}" -y @anthropic-ai/claude-code@latest'
    s = HR.win_terminal_cmdline("Claude sign-in", line)
    assert s == f'cmd /k "title Claude sign-in & "{npx}" -y @anthropic-ai/claude-code@latest"'
    assert '\\"' not in s                                     # no list2cmdline escaping, which cmd.exe misreads
    # cmd /k with more than two quotes drops the first and the last one; what runs keeps the path quoted.
    rest = s[len("cmd /k "):]
    assert rest[0] == '"' and rest.count('"') > 2
    runs = rest[1:][::-1].replace('"', "", 1)[::-1]
    assert runs == f'title Claude sign-in & "{npx}" -y @anthropic-ai/claude-code@latest'


def test_windows_sign_in_opens_one_console_with_a_string(monkeypatch):
    import types
    seen = []
    monkeypatch.setattr(HR, "npx", lambda: r"C:\Program Files\nodejs\npx.cmd")
    monkeypatch.setattr(HR, "os", types.SimpleNamespace(name="nt", environ={}))
    monkeypatch.setattr(HR.sys, "platform", "win32")
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: seen.append((cmd, kw)))
    assert HR.login("gemini")["ok"]
    cmd, kw = seen[0]
    assert isinstance(cmd, str) and cmd.startswith('cmd /k "title Gemini sign-in & "C:\\Program Files')
    assert kw.get("creationflags") == 0x10


def test_relay_status_follows_the_stream_error():
    from meshpool_harness.core.chatgpt_relay import _status
    assert _status(RuntimeError('chatgpt HTTP 500: {"code": "rate_limit_exceeded"}')) == 429
    assert _status(RuntimeError('chatgpt HTTP 500: {"code": "usage_limit_reached"}')) == 429
    assert _status(RuntimeError('chatgpt HTTP 500: {"code": "context_length_exceeded"}')) == 400
    assert _status(RuntimeError('chatgpt HTTP 500: {"code": "server_error"}')) == 500
    assert _status(RuntimeError("chatgpt HTTP 0: timed out")) == 502
