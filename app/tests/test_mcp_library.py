"""MCP library: entries are complete, adding writes a disabled server with placeholders, settings stay out of
mcp.json, OAuth entries go through connectors."""
import json
import re

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.setenv("MESHPOOL_CONFIG", str(tmp_path / "meshpool.config"))
    monkeypatch.delenv("MESHPOOL_MCP", raising=False)
    for k in ("FRED_API_KEY", "SEC_EDGAR_USER_AGENT", "TAVILY_API_KEY"):
        monkeypatch.setenv(k, "")              # empty counts as missing; teardown restores the real value
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {"keep": {"command": "x"}}}), encoding="utf-8")
    return tmp_path


def _mcp(home):
    return json.loads((home / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]


def test_every_entry_is_complete_and_verified():
    from meshpool_harness import mcp_apps as CN, mcp_library as L
    ids = [e["id"] for e in L.LIBRARY]
    assert len(ids) == len(set(ids)) and len(ids) >= 30
    connectors = {c["id"] for c in CN.CATALOG}
    for e in L.LIBRARY:
        assert e["category"] in L.CATEGORIES and e["name"] and e["description"] and e["license"]
        assert e["docs"].startswith("https://")
        assert e["transport"] in ("stdio", "http", "connector", "oauth")
        if e["transport"] == "stdio":
            assert e["command"] in ("npx", "uvx") and e["args"]
        if e["transport"] in ("http", "oauth"):
            assert e["url"].startswith("https://")
        if e["transport"] == "connector":
            assert e["connector"] in connectors
        if e["transport"] in ("stdio", "http", "oauth"):
            assert L.VERIFIED.get(e["id"]), f"{e['id']} has no verification note"
        for s in e["settings"]:
            assert re.fullmatch(r"[A-Z][A-Z0-9_]*", s["name"]) and s["hint"]
    assert {"sec-edgar", "fred", "alpha-vantage"} <= set(ids)          # buy-side data sources
    # names the 2.4 config upgrade deletes when disabled must not be used
    assert not {"filesystem", "fetch", "memory", "sequential-thinking"} & set(ids)


def test_settings_are_names_never_values():
    from meshpool_harness import mcp_library as L
    for e in L.LIBRARY:
        blob = json.dumps({k: e.get(k) for k in ("args", "env", "url", "headers")})
        for s in e["settings"]:
            assert "${" + s["name"] + "}" in blob or e["transport"] == "stdio", e["id"]
        assert not re.search(r"(sk|xox[pbc]|fc|ntn)-[A-Za-z0-9]{6,}", blob)


def test_add_writes_disabled_server_with_placeholders(home):
    from meshpool_harness import mcp_library as L
    r = L.add("fred")
    assert r == {"server": "fred"}
    s = _mcp(home)["fred"]
    assert s["disabled"] is True and s["command"] == "npx" and s["args"] == ["-y", "fred-mcp-server"]
    assert s["env"] == {"FRED_API_KEY": "${FRED_API_KEY}"} and s["description"].startswith(L.MARK)
    assert L.add("fred")["existing"] is True and list(_mcp(home)).count("fred") == 1
    row = next(x for x in L.listing() if x["id"] == "fred")
    assert row["added"] and not row["enabled"] and row["missing"] == ["FRED_API_KEY"]
    assert row["target"] == "npx -y fred-mcp-server"


def test_remote_entry_keeps_key_in_url_placeholder(home):
    from meshpool_harness import mcp_library as L
    L.add("alpha-vantage")
    assert _mcp(home)["alpha-vantage"]["url"].endswith("apikey=${ALPHAVANTAGE_API_KEY}")


def test_configure_saves_settings_outside_mcp_json_and_enables(home):
    from meshpool_harness import config as C, mcp_library as L
    with pytest.raises(ValueError):
        L.configure("fred", {})                                        # nothing filled in yet
    L.configure("fred", {"FRED_API_KEY": "abc123", "OTHER": "ignored"})
    assert C.read()["FRED_API_KEY"] == "abc123" and "OTHER" not in C.read()
    s = _mcp(home)["fred"]
    assert "disabled" not in s and "abc123" not in (home / "mcp.json").read_text()
    row = next(x for x in L.listing() if x["id"] == "fred")
    assert row["enabled"] and row["missing"] == []


def test_no_settings_entry_enables_and_remove_only_takes_library_servers(home):
    from meshpool_harness import mcp_library as L
    L.configure("duckdb", {})
    assert "disabled" not in _mcp(home)["duckdb"]
    L.remove("duckdb")
    assert "duckdb" not in _mcp(home)
    data = json.loads((home / "mcp.json").read_text())
    data["mcpServers"]["git"] = {"command": "uvx", "args": ["mcp-server-git"], "description": "mine"}
    (home / "mcp.json").write_text(json.dumps(data))
    L.remove("git")
    assert "git" in _mcp(home)                                          # not added by the library: kept


def test_connector_and_oauth_entries(home, monkeypatch):
    from meshpool_harness import mcp_apps as CN, mcp_library as L
    assert L.add("notion") == {"connector": "notion"}
    monkeypatch.setattr(CN, "_discover", lambda url: {"as": {}, "resource": url, "scopes": []})
    cid = L.add("financial-datasets")["connector"]
    assert cid.startswith("x-") and CN.get(cid)["url"] == "https://mcp.financialdatasets.ai/"
    assert L.add("financial-datasets")["connector"] == cid               # not added twice
    with pytest.raises(ValueError):
        L.spec(L.get("notion"))


def test_api_actions(home):
    from meshpool_harness import mcp_library as L
    v = L.api_action({"action": "add", "id": "sec-edgar"})
    assert v["result"]["server"] == "sec-edgar" and v["categories"] == L.CATEGORIES
    with pytest.raises(ValueError):
        L.api_action({"action": "nope"})
    with pytest.raises(ValueError):
        L.api_action({"action": "add", "id": "no-such"})
