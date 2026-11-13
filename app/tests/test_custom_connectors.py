"""Connectors and custom MCP servers, end to end against a local mock server (tests/mock_mcp_server.py): no sign-in,
a bearer token, a custom header, OAuth with dynamic client registration (public and with a secret), the user's own
client for apps that do not register others, the localhost redirect spelling, the legacy SSE transport, and refresh."""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mock_mcp_server as M  # noqa: E402
from meshpool_harness import mcp_apps as CN  # noqa: E402
from meshpool_harness.agent.mcp import Manager, Server  # noqa: E402

REDIRECT = "http://127.0.0.1:8765/oauth/callback"


@pytest.fixture(scope="module")
def mock():
    httpd, base = M.start()
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _approve(url: str) -> dict:
    """Open the sign-in URL like a browser that is already signed in: the mock approves and redirects back."""
    op = urllib.request.build_opener(_NoRedirect)
    try:
        op.open(url, timeout=10)
    except urllib.error.HTTPError as e:
        assert e.code == 302, e.read()
        loc = e.headers["Location"]
        return {"redirect": loc.split("?")[0], **dict(urllib.parse.parse_qsl(urllib.parse.urlparse(loc).query))}
    raise AssertionError("no redirect")


def _sign_in(cid: str) -> dict:
    back = _approve(CN.begin(cid, REDIRECT))
    assert CN.finish(back["state"], back["code"]) == CN.get(cid)["name"]
    return back


def _call_echo(spec_name: str) -> str:
    m = Manager(roots=["."]).start(timeout=20, use_health=False)
    try:
        st = {s["name"]: s for s in m.status()}
        assert st[spec_name]["status"] == "connected", st[spec_name]
        out, err = m.call(f"mcp__{spec_name}__echo", {"text": "hi"})
        assert not err
        return out
    finally:
        m.close()


def test_open_server_needs_nothing(home, mock):
    c = CN.add_custom("Open", mock + "/open/mcp")
    assert c["auth"] == "none" and c["tools"] == ["echo"]
    assert _call_echo(c["id"]) == "echo: hi"
    row = next(r for r in CN.listing() if r["id"] == c["id"])
    assert row["connected"] and row["enabled"]


def test_bearer_token_checked_before_saving(home, mock):
    with pytest.raises(ValueError, match="refused that token"):
        CN.add_custom("Tok", mock + "/token/mcp", "wrong")
    assert not [r for r in CN.listing() if r["custom"]]          # nothing half-saved
    c = CN.add_custom("Tok", mock + "/token/mcp", "good-token")
    assert c["auth"] == "token"
    assert CN.servers()[c["id"]]["headers"] == {"Authorization": "Bearer good-token"}
    assert _call_echo(c["id"]) == "echo: hi"


def test_pasted_bearer_prefix_and_custom_header(home, mock):
    c = CN.add_custom("Tok", mock + "/token/mcp", "Bearer good-token")
    assert CN.servers()[c["id"]]["headers"] == {"Authorization": "Bearer good-token"}
    k = CN.add_custom("Key", mock + "/key/mcp", "X-Api-Key: k1")
    assert CN.servers()[k["id"]]["headers"] == {"X-Api-Key": "k1"}
    assert _call_echo(k["id"]) == "echo: hi"


def test_token_server_without_token_says_what_to_do(home, mock):
    with pytest.raises(ValueError, match="needs a token or API key"):
        CN.add_custom("Tok", mock + "/token/mcp")


def test_addresses(home, mock):
    with pytest.raises(ValueError, match="https://"):
        CN.add_custom("x", "http://example.com/mcp")             # plain http only for this computer
    with pytest.raises(ValueError, match="Could not connect"):
        CN.add_custom("x", mock + "/nothing-here")


def test_oauth_public_client_end_to_end_and_refresh(home, mock):
    c = CN.add_custom("OAuth", mock + "/oauth/mcp")
    assert c["auth"] == "oauth"
    _sign_in(c["id"])
    assert CN.check(c["id"])["tools"] == ["echo"]
    assert _call_echo(c["id"]) == "echo: hi"
    # the access token runs out: the next start refreshes it on its own
    store = json.loads((home / "mcp-auth.json").read_text())
    old = store[c["id"]]["access_token"]
    store[c["id"]]["expires_at"] = time.time() - 5
    (home / "mcp-auth.json").write_text(json.dumps(store))
    assert CN.check(c["id"])["ok"]
    assert json.loads((home / "mcp-auth.json").read_text())[c["id"]]["access_token"] != old


def test_oauth_client_with_secret_uses_basic_auth(home, mock):
    c = CN.add_custom("Secret", mock + "/secret/mcp")
    _sign_in(c["id"])
    call = [t for t in M.S.token_calls if t["kind"] == "secret"][-1]
    assert call["basic"].startswith("Basic ") and "client_secret" not in call
    assert CN.check(c["id"])["ok"]


def test_app_without_registration_takes_the_users_own_client(home, mock):
    c = CN.add_custom("NoReg", mock + "/noreg/mcp")
    with pytest.raises(CN.NeedsClient) as e:
        CN.begin(c["id"], REDIRECT)
    assert REDIRECT in str(e.value)
    # the user creates a client in that app with our redirect URL and pastes it
    M.S.clients["mine"] = {"redirects": [REDIRECT], "secret": "pw", "kind": "noreg"}
    CN.set_client(c["id"], "mine", "pw")
    _sign_in(c["id"])
    call = [t for t in M.S.token_calls if t["kind"] == "noreg"][-1]
    assert call.get("client_secret") == "pw"                      # own clients send the secret in the form
    assert CN.listing()[-1]["own_client"] and CN.check(c["id"])["ok"]


def test_catalog_entry_that_needs_a_client_fails_fast(home):
    with pytest.raises(CN.NeedsClient):
        CN.begin("slack", REDIRECT)


def test_localhost_redirect_spelling(home, mock):
    c = CN.add_custom("Lhost", mock + "/lhost/mcp")
    back = _sign_in(c["id"])
    assert back["redirect"].startswith("http://localhost:8765/")
    assert CN.check(c["id"])["ok"]


def test_legacy_sse_transport(home, mock):
    s = Server("legacy", {"url": mock + "/legacy/sse"}, home / "logs", ["."])
    s.start(20)
    assert s.status == "connected", s.error
    assert s.call("echo", {"text": "sse"}) == ("echo: sse", False)
    s.close()
    c = CN.add_custom("Legacy", mock + "/legacy/sse")
    assert c["auth"] == "none" and _call_echo(c["id"]) == "echo: hi"


def test_sse_fallback_when_post_is_refused(home, mock):
    """A server at an address without /sse that refuses POST is retried as HTTP+SSE."""
    s = Server("legacy", {"url": mock + "/legacy/sse?x=1"}, home / "logs", ["."])
    s.start(20)
    assert s.status == "connected", s.error
    s.close()


def test_open_catalog_servers_stay_off_until_turned_on(home):
    assert "context7" not in CN.servers()
    row = next(r for r in CN.listing() if r["id"] == "context7")
    assert not row["connected"]
    CN.set_enabled("context7", True)
    assert CN.servers()["context7"]["url"] == "https://mcp.context7.com/mcp"
    CN.disconnect("context7")
    assert "context7" not in CN.servers()


def test_catalog_is_complete():
    ids = [c["id"] for c in CN.CATALOG]
    assert len(ids) == len(set(ids)) and len(ids) >= 50
    for c in CN.CATALOG:
        assert c["auth"] in ("oauth", "token", "none") and c["category"] and c["description"].endswith(".")
        assert c["url"].startswith("https://")
        if c.get("needs_client"):
            assert c["auth"] == "oauth" and "client ID" in c["client_help"]
        if c["auth"] == "token":
            assert c["token_key"] and c["token_help"]


# ---------- the terminal: meshpool connect ----------

def _fake_browser(monkeypatch):
    """webbrowser.open plays a signed-in browser: it follows the sign-in to the redirect, i.e. the CLI's local page."""
    import threading
    import webbrowser

    def open_(url, *a, **k):
        def go():
            back = _approve(url)
            q = urllib.parse.urlencode({"state": back["state"], "code": back["code"]})
            urllib.request.urlopen(back["redirect"] + "?" + q, timeout=10).read()
        threading.Thread(target=go, daemon=True).start()
        return True
    monkeypatch.setattr(webbrowser, "open", open_)


def test_cli_connect_url_with_browser_sign_in(home, mock, monkeypatch, capsys):
    from meshpool_harness.agent import connect_cli
    _fake_browser(monkeypatch)
    ok, cid = connect_cli.connect(mock + "/oauth/mcp", name="CLI OAuth")
    assert ok and "1 tools" in capsys.readouterr().out
    assert CN.check(cid)["ok"]


def test_cli_connect_asks_for_own_client(home, mock, monkeypatch):
    from meshpool_harness.agent import connect_cli
    _fake_browser(monkeypatch)
    c = CN.add_custom("NoReg CLI", mock + "/noreg/mcp")
    answers = iter(["mine-cli"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    monkeypatch.setattr(connect_cli.getpass, "getpass", lambda *a: "pw2")
    port = 8765
    M.S.clients["mine-cli"] = {"redirects": [f"http://127.0.0.1:{p}/oauth/callback" for p in range(port, port + 20)],
                               "secret": "pw2", "kind": "noreg"}
    ok, cid = connect_cli.connect(c["id"])
    assert ok and cid == c["id"]


def test_cli_connect_open_catalog_server_and_list(home, monkeypatch, capsys):
    from meshpool_harness.agent import connect_cli
    monkeypatch.setattr(CN, "check", lambda cid: {"ok": True, "tools": ["a", "b"], "error": ""})
    ok, cid = connect_cli.connect("deepwiki")
    assert ok and cid == "deepwiki" and "deepwiki" in CN.servers()
    connect_cli.listing()
    out = capsys.readouterr().out
    assert "Connected" in out and "deepwiki" in out and "your own client ID" in out


def test_manager_add_makes_tools_live(home, mock):
    m = Manager(roots=["."]).start(timeout=10, use_health=False)
    m.add("late", {"url": mock + "/open/mcp"})
    assert m.has("mcp__late__echo") and m.call("mcp__late__echo", {"text": "x"}) == ("echo: x", False)
    m.close()
