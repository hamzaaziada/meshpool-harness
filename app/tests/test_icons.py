"""Icon library: index, search, set restriction, path lookup and the find_icon MCP tool."""
from engines import icons as IC


def test_both_sets_are_indexed_with_licences():
    info = {s["set"]: s for s in IC.sets()}
    assert info["lucide"]["count"] >= 1500 and info["lucide"]["license"] == "ISC"
    assert info["tabler"]["count"] >= 4000 and info["tabler"]["license"] == "MIT"
    for s in info.values():
        assert open(s["license_file"], encoding="utf-8").read().strip()
    assert not any(r["name"].startswith("brand-") for r in IC.index("tabler"))   # no trademark logos


def test_every_icon_has_set_and_existing_path():
    rows = IC.index()
    assert {r["set"] for r in rows} == {"lucide", "tabler"}
    assert all(r["path"].endswith(".svg") for r in rows[:50])
    assert sum(1 for r in IC.index("lucide") if r["tags"]) > 1400


def test_search_money_ranks_money_icons_first():
    top = [r["name"] for r in IC.search("money", limit=5)]
    assert "circle-dollar-sign" in top                       # the forge meaning for money
    assert any("cash" in n or "coin" in n or "dollar" in n for n in top[:3])


def test_search_uses_synonyms_and_plurals():
    assert IC.search("workforce", set="lucide", limit=3)[0]["name"] == "users"
    assert any("chart" in r["name"] for r in IC.search("charts", limit=5))
    assert IC.search("deal", set="lucide", limit=1)[0]["name"] == "handshake"


def test_search_can_be_restricted_to_one_set():
    rows = IC.search("growth", set="tabler", limit=10)
    assert rows and all(r["set"] == "tabler" for r in rows)
    assert IC.search("", limit=5) == []


def test_unknown_set_is_an_error():
    import pytest
    with pytest.raises(ValueError):
        IC.search("money", set="nope")


def test_path_lookup():
    assert IC.path("coin", "tabler").name == "coin.svg"
    assert IC.path("tabler:coin") == IC.path("coin", "tabler")
    assert IC.path("users").parent.name == "icons"            # Lucide first
    assert IC.path("no-such-icon") is None


def test_best_set_picks_one_set():
    assert IC.best_set(["money", "growth", "risk"]) in IC.SETS


def test_find_icon_mcp_tool_is_registered_and_safe():
    from meshpool_harness.agent import office_mcp_server as O
    from meshpool_harness.agent.core import SAFE_MCP
    assert "find_icon" in O.TOOLS and "mcp__meshpool_office__find_icon" in SAFE_MCP
    r = O.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "find_icon", "arguments": {"query": "money", "set": "tabler", "limit": 3}}})
    text = r["result"]["content"][0]["text"]
    assert not r["result"]["isError"] and "tabler:" in text and "lucide:" not in text
    bad = O.find_icon("money", set="nope")
    assert bad[1] is True
