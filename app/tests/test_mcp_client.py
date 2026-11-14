"""MCP client robustness: unique tool names, late-starting servers, stray stdout lines, the legacy event stream's
silence and reconnects, expired sessions, refreshed connector tokens, secrets in errors, and the one mcp.json writer."""
import json
import os
import stat
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mock_mcp_server as MOCK  # noqa: E402
from meshpool_harness.agent import mcp as M  # noqa: E402


@pytest.fixture(scope="module")
def mock():
    httpd, base = MOCK.start()
    yield base
    httpd.shutdown()


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "meshpool.config"))
    monkeypatch.setenv("MESHPOOL_MCP", str(tmp_path / "mcp.json"))
    monkeypatch.setenv("MH_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("INSTANTLY_API_KEY", raising=False)
    return tmp_path


# ---------- tool names ----------

def test_tool_names_are_unique_and_map_back_exactly():
    long_a, long_b = "x" * 70 + "_a", "x" * 70 + "_b"
    pairs = [("srv", "a.b"), ("srv", "a_b"), ("srv", long_a), ("srv", long_b), ("srv", "plain")]
    names = M.unique_tool_names(pairs)
    assert len(set(names.values())) == len(pairs)
    assert all(len(n) <= 64 and n.replace("_", "").replace("-", "").isalnum() for n in names.values())
    assert names[("srv", "plain")] == "mcp__srv__plain"            # short, distinct names stay as they were
    assert names[("srv", "a.b")] == "mcp__srv__a_b"                 # the first keeps the plain name
    assert names[("srv", "a_b")] != "mcp__srv__a_b"


class _FakeServer:
    def __init__(self, name, tools, status="connected"):
        self.name, self.status, self.error = name, status, ""
        self.tools = [{"name": t, "inputSchema": {"type": "object"}} for t in tools]

    def close(self):
        pass


def test_schemas_and_map_agree():
    m = M.Manager(roots=["."])
    m.servers = {"s": _FakeServer("s", ["a.b", "a_b", "y" * 80]), "down": _FakeServer("down", ["t"], "error")}
    m._rebuild()
    offered = [x["function"]["name"] for x in m.schemas()]
    assert sorted(offered) == sorted(m.map) and len(set(offered)) == 3
    assert {m.map[n] for n in offered} == {("s", "a.b"), ("s", "a_b"), ("s", "y" * 80)}


# ---------- a server that finishes starting after the manager gave up ----------

class _SlowTransport:
    headers: dict = {}

    def __init__(self, *a, **k):
        pass

    def request(self, rid, method, params, timeout):
        time.sleep(0.4)
        if method == "initialize":
            return {"result": {"serverInfo": {"name": "slow"}}}
        return {"result": {"tools": [{"name": "t"}]}}

    def notify(self, *a, **k):
        pass

    def close(self):
        pass                                # like a stuck process: closing does not stop the start thread


def test_late_start_stays_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(M, "_Stdio", _SlowTransport)
    s = M.Server("slow", {"command": "x"}, tmp_path, ["."])
    th = threading.Thread(target=s.start, args=(5,))
    th.start()
    time.sleep(0.1)
    s.cancel("did not start within 5 s")
    th.join(5)
    assert s.status == "error" and s.error == "did not start within 5 s" and s.tools == []
    m = M.Manager(roots=["."])
    m.servers = {"slow": s}
    m._rebuild()
    assert m.schemas() == [] and m.map == {}


# ---------- stdio ----------

def _script(tmp_path, body: str) -> dict:
    p = tmp_path / "srv.py"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return {"command": sys.executable, "args": [str(p)]}


def test_stdio_ignores_non_object_json_lines(tmp_path):
    spec = _script(tmp_path, """
        import json, sys
        for line in sys.stdin:
            msg = json.loads(line)
            if "id" not in msg:
                continue
            print("42"); print("true"); print("[1, 2]"); print('{"id": [1]}')
            if msg["method"] == "initialize":
                res = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "noisy"}}
            elif msg["method"] == "tools/list":
                res = {"tools": [{"name": "hi", "inputSchema": {"type": "object"}}]}
            else:
                res = {"content": [{"type": "text", "text": "hello"}]}
            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
    """)
    s = M.Server("noisy", spec, tmp_path / "logs", ["."])
    s.start(20)
    try:
        assert s.status == "connected", s.error
        assert s.call("hi", {}) == ("hello", False)
    finally:
        s.close()


def test_builtin_server_drops_made_up_arguments(tmp_path):
    s = M.Server("demo", {"command": "python", "args": ["-m", "meshpool_harness.agent.demo_mcp_server"]},
                 tmp_path / "logs", ["."])
    s.start(30)
    try:
        assert s.status == "connected", s.error
        out, err = s.call("calculate", {"expression": "2*21", "engine": "fast"})
        assert not err and "Ignored unknown argument(s): engine" in out and "= 42" in out
    finally:
        s.close()


# ---------- legacy HTTP+SSE ----------

def test_sse_survives_silence_and_reconnects(home, mock):
    s = M.Server("legacy", {"url": mock + "/legacy/sse"}, home / "logs", ["."])
    s.start(1)                               # the connect timeout is 1 s
    try:
        assert s.status == "connected", s.error
        time.sleep(1.6)                      # longer than that timeout with nothing on the stream
        assert s.t.alive
        assert s.call("echo", {"text": "a"}) == ("echo: a", False)
        old = s.t
        for q in list(MOCK.S.sse.values()):  # the server closes the stream
            q.put(None)
        deadline = time.time() + 5
        while old.alive and time.time() < deadline:
            time.sleep(0.05)
        assert not old.alive
        assert s.call("echo", {"text": "b"}) == ("echo: b", False)
        assert s.t is not old and s.t.alive
    finally:
        s.close()


# ---------- streamable HTTP ----------

def test_expired_http_session_starts_a_new_one(home, mock):
    s = M.Server("sess", {"url": mock + "/session/mcp"}, home / "logs", ["."])
    s.start(10)
    try:
        assert s.status == "connected", s.error
        first = s.t.session
        MOCK.S.sessions.clear()             # the server forgets every session
        assert s.call("echo", {"text": "x"}) == ("echo: x", False)
        assert s.t.session and s.t.session != first
    finally:
        s.close()


def test_connector_token_refreshed_on_401(home, mock):
    from test_custom_connectors import _sign_in
    from meshpool_harness import mcp_apps as CN
    c = CN.add_custom("OAuth", mock + "/oauth/mcp")
    _sign_in(c["id"])
    m = M.Manager(roots=["."]).start(timeout=20, use_health=False)
    try:
        name = M.tool_name(c["id"], "echo")
        assert m.has(name)
        for tok in list(MOCK.S.tokens):     # every access token is revoked, as after an hour
            MOCK.S.tokens[tok] = 0
        assert m.call(name, {"text": "hi"}) == ("echo: hi", False)
    finally:
        m.close()


def test_query_secrets_never_reach_errors_or_health(home):
    url = "http://127.0.0.1:1/mcp?apikey=SUPERSECRET123&x=1"
    (home / "mcp.json").write_text(json.dumps({"mcpServers": {"av": {"url": url}}}), encoding="utf-8")
    m = M.Manager(roots=["."]).start(timeout=5, use_health=False)
    m.close()
    st = {r["name"]: r for r in m.status()}["av"]
    assert st["status"] == "error" and "SUPERSECRET123" not in st["error"]
    assert "SUPERSECRET123" not in (home / "mcp-health.json").read_text(encoding="utf-8")
    assert M.redact("https://x/mcp?apikey=abc&access_token=def&q=1") == "https://x/mcp?apikey=***&access_token=***&q=1"


# ---------- mcp.json ----------

def test_edit_config_keeps_vscode_key(home):
    p = home / "mcp.json"
    p.write_text(json.dumps({"servers": {"a": {"command": "x"}}}), encoding="utf-8")
    M.edit_config(lambda s: s.__setitem__("b", {"command": "y"}))
    d = json.loads(p.read_text(encoding="utf-8"))
    assert set(d) == {"servers"} and set(d["servers"]) == {"a", "b"}
    assert set(M.load_config()) == {"a", "b"}


def test_edit_config_merges_a_file_with_both_keys(home):
    p = home / "mcp.json"
    p.write_text(json.dumps({"servers": {"a": {"command": "x"}}, "mcpServers": {"b": {"command": "y"}}}),
                 encoding="utf-8")
    assert set(M.load_config()) == {"a", "b"}
    M.edit_config(lambda s: None)
    d = json.loads(p.read_text(encoding="utf-8"))
    assert set(d) == {"mcpServers"} and set(d["mcpServers"]) == {"a", "b"}


def test_edit_config_writes_nothing_when_unchanged_and_leaves_no_temp_files(home):
    p = home / "mcp.json"
    p.write_text('{"mcpServers": {"a": {"command": "x"}}}', encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    M.edit_config(lambda s: None)
    assert p.read_text(encoding="utf-8") == before            # untouched, original formatting kept
    M.edit_config(lambda s: s["a"].__setitem__("disabled", True))
    assert json.loads(p.read_text(encoding="utf-8"))["mcpServers"]["a"]["disabled"] is True
    assert sorted(x.name for x in home.iterdir()) == ["mcp.json"]
    assert M.edit_config(lambda s: 1, path=home / "missing.json", create=False) is None
    assert not (home / "missing.json").exists()


def test_library_toggle_and_remove_on_vscode_file(home, monkeypatch):
    from meshpool_harness import mcp_library as L
    e = L.get("time")
    p = home / "mcp.json"
    p.write_text(json.dumps({"servers": {"time": L.spec(e), "keep": {"command": "x"}}}), encoding="utf-8")
    L._set_disabled("time", False)
    d = json.loads(p.read_text(encoding="utf-8"))
    assert set(d) == {"servers"} and "disabled" not in d["servers"]["time"]
    L.remove("time")
    assert set(json.loads(p.read_text(encoding="utf-8"))["servers"]) == {"keep"}


def test_auth_file_is_owner_only(home):
    from meshpool_harness import mcp_apps as CN
    CN._write(CN._auth_file(), {"x": {"access_token": "t"}})
    assert json.loads(CN._auth_file().read_text(encoding="utf-8")) == {"x": {"access_token": "t"}}
    assert [x.name for x in home.iterdir()] == ["mcp-auth.json"]
    if os.name != "nt":
        assert stat.S_IMODE(CN._auth_file().stat().st_mode) == 0o600


# ---------- computer use ----------

def test_open_app_refuses_shell_metacharacters():
    from meshpool_harness.agent import computer_mcp_server as CU
    for bad in ("x&calc", "a|b", "a>b", "%COMSPEC%", 'x" & calc', "a^b"):
        with pytest.raises(CU.ToolFailed):
            CU._check_app_name(bad)
    assert CU._check_app_name(" Calculator ") == "Calculator"
    assert CU._check_app_name("https://example.com/?a=1&b=2") == "https://example.com/?a=1&b=2"
