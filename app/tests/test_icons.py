"""Icon library: index, search, set restriction, path lookup and the find_icon MCP tool."""
import pytest

from engines import icons as IC

NEW_SETS = {"tabler-filled", "phosphor", "phosphor-light", "phosphor-thin", "phosphor-bold", "phosphor-fill",
            "phosphor-duotone", "iconoir", "iconoir-solid", "heroicons", "heroicons-solid", "bootstrap",
            "bootstrap-fill", "material", "material-fill", "material-rounded", "material-rounded-fill",
            "material-sharp", "material-sharp-fill", "carbon"}


def test_both_sets_are_indexed_with_licences():
    info = {s["set"]: s for s in IC.sets()}
    assert info["lucide"]["count"] >= 1500 and info["lucide"]["license"] == "ISC"
    assert info["tabler"]["count"] >= 4000 and info["tabler"]["license"] == "MIT"
    for s in info.values():
        assert open(s["license_file"], encoding="utf-8").read().strip()
    assert not any(r["name"].startswith("brand-") for r in IC.index("tabler"))   # no trademark logos


def test_every_icon_has_set_and_existing_path():
    rows = IC.index()
    assert {r["set"] for r in rows} == {"lucide", "tabler"} | NEW_SETS
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


# ------------------------------------------------------------------ the bundled sets
def test_new_sets_have_counts_licences_and_styles():
    info = {s["set"]: s for s in IC.sets()}
    assert NEW_SETS <= set(info)
    want = {"phosphor": (1400, "MIT"), "phosphor-fill": (1400, "MIT"), "iconoir": (1200, "MIT"),
            "heroicons": (300, "MIT"), "heroicons-solid": (300, "MIT"), "bootstrap": (1300, "MIT"),
            "material": (3800, "Apache-2.0"), "material-fill": (3800, "Apache-2.0"), "carbon": (2300, "Apache-2.0"),
            "tabler-filled": (1000, "MIT")}
    for s, (n, lic) in want.items():
        assert info[s]["count"] >= n and info[s]["license"] == lic, s
        assert open(info[s]["license_file"], encoding="utf-8").read().strip()
    assert {info[s]["kind"] for s in NEW_SETS} == {"outline", "filled", "duotone"}
    assert info["phosphor-duotone"]["kind"] == "duotone" and info["material-fill"]["kind"] == "filled"
    assert [s["set"] for s in IC.sets(style="filled")] and all(s["kind"] == "filled" for s in IC.sets(style="solid"))


def test_bundles_are_compact_files():
    files = list(IC.BUNDLES.glob("*.json.gz"))
    assert len(files) == 7 and sum(f.stat().st_size for f in files) < 12_000_000


@pytest.mark.parametrize("set_, logos", [
    ("phosphor", ["github-logo", "apple-logo", "google-logo"]), ("bootstrap", ["github", "microsoft", "youtube"]),
    ("iconoir", ["github", "apple", "google"]), ("carbon", ["logo--github", "ibm-cloud", "watson"]),
    ("material", ["android", "flutter"]), ("tabler-filled", ["brand-apple", "brand-github"])])
def test_no_company_logos(set_, logos):
    names = {r["name"] for r in IC.index(set_)}
    assert names and not names & set(logos)


def test_search_across_new_sets_and_styles():
    for s in ("phosphor", "material", "carbon", "heroicons", "iconoir", "bootstrap"):
        rows = IC.search("bank", set=s, limit=5)
        assert rows and all(r["set"] == s for r in rows), s
    rows = IC.search("growth", limit=40)
    assert {r["set"] for r in rows} <= set(IC.search_sets())           # main styles only by default
    assert "phosphor-fill" not in IC.search_sets() and "phosphor" in IC.search_sets()
    filled = IC.search("money", style="filled", limit=20)
    assert filled and all(r["style"] == "filled" for r in filled)
    assert IC.search("money", set="phosphor-duotone", limit=1)[0]["style"] == "duotone"
    top = IC.search("bank", set="phosphor", limit=1)[0]
    assert top["name"] == "bank" and top["license"] == "MIT" and top["family"] == "phosphor"
    with pytest.raises(ValueError):
        IC.search("money", style="sparkly")


def test_phosphor_tags_come_from_the_package():
    row = next(r for r in IC.index("phosphor") if r["name"] == "acorn")
    assert "savings" in row["tags"] and "finance" in row["category"]
    carbon = next(r for r in IC.index("carbon") if r["name"] == "api--1")
    assert "application programming interface" in carbon["tags"]


@pytest.mark.parametrize("query, expect", [
    ("datacentre", ("server", "database", "rack")), ("data center", ("server", "database", "rack")),
    ("GPU", ("gpu", "cpu", "chip")), ("semiconductor", ("cpu", "chip", "circuit")), ("M&A", ("handshake",)),
    ("supply chain", ("container", "truck", "warehouse", "package", "inventory", "supply", "blockchain", "product")),
    ("power grid", ("bolt", "utility", "pole", "tower")), ("KPI", ("gauge", "dashboard", "meter")),
    ("OKR", ("okr", "target", "flag", "goal")), ("ESG", ("leaf", "plant", "recycle")),
    ("cybersecurity", ("shield", "firewall", "lock")), ("antitrust", ("gavel", "scale")),
    ("IPO", ("rocket", "chart", "bell", "candle")), ("insurance", ("umbrella", "shield")),
    ("bank", ("bank", "landmark", "vault")), ("roadmap", ("roadmap", "timeline", "map", "route", "milestone")),
    ("governance", ("landmark", "gavel", "scale", "building", "rule")), ("dividend", ("coin", "divide", "pie", "money")),
    ("regulation", ("gavel", "scale", "law", "landmark", "file", "rule"))])
def test_business_synonyms(query, expect):
    top = [r["name"] for r in IC.search(query, limit=5)]
    assert any(e in n for n in top for e in expect), (query, top)


def test_svg_and_path_for_bundled_sets(tmp_path, monkeypatch):
    monkeypatch.setattr(IC, "CACHE", tmp_path)
    txt = IC.svg("phosphor-fill:bank")
    assert txt.startswith("<svg") and "viewBox" in txt and "currentColor" in txt and "width=" not in txt[:120]
    p = IC.path("bank", "phosphor-fill")
    assert p.is_file() and p.read_text(encoding="utf-8") == txt and tmp_path in p.parents
    assert IC.path("material:attach_money").name == "attach-money.svg"         # Material's own spelling works
    assert IC.svg("carbon:bank--vault") and IC.svg("heroicons-solid:banknotes")
    assert IC.svg("users") == IC.path("users").read_text(encoding="utf-8")     # Lucide first, as before
    assert IC.path("../../etc/passwd", "phosphor") is None and IC.svg("no-such-icon") is None
    hit = IC.search("money", set="material", limit=1)[0]
    assert hit["path"] and open(hit["path"], encoding="utf-8").read().startswith("<svg")


def test_every_bundled_svg_is_clean():
    import gzip
    import json
    for f in IC.BUNDLES.glob("*.json.gz"):
        data = json.loads(gzip.open(f).read())
        for variant, icons in data["variants"].items():
            for name, svg in icons.items():
                low = svg.lower()
                assert svg.startswith("<svg") and "viewbox" in low, (f.name, name)
                assert "<script" not in low and "href" not in low and " on" not in low.split(">")[0], (f.name, name)
                assert not __import__("re").search(r'(fill|stroke)="#', svg), (f.name, variant, name)


def test_find_icon_lists_sets_and_filters_by_style():
    from meshpool_harness.agent import office_mcp_server as O
    text, err = O.find_icon(list_sets=True)
    assert not err and "phosphor-duotone" in text and "Apache-2.0" in text and "carbon" in text
    text, err = O.find_icon("bank", style="filled", limit=6)
    assert not err and "filled" in text and "outline," not in text
    text, err = O.find_icon("bank", set="material", limit=2)
    assert not err and "material:" in text and "Apache-2.0" in text
    assert O.find_icon("bank", style="sparkly")[1] is True


def test_forge_engine_reads_icon_set():
    from engines.forge.engine import icon_set_from
    assert icon_set_from("Board deck, use phosphor icons") == "phosphor"
    assert icon_set_from("Icon set: material-rounded") == "material-rounded"
    assert icon_set_from("a deck about money") is None
    assert icon_set_from("x", {"icon_set": "Carbon"}) == "carbon" and icon_set_from("x", {"icon_set": "no"}) is None
