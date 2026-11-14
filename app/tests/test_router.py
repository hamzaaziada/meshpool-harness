import json
from pathlib import Path
from meshpool_harness.router.rules import decide

def test_routing_stress_accuracy(tmp_path):
    (tmp_path / ".git").mkdir()
    rows = [json.loads(l) for l in (Path(__file__).parent / "stress" / "routing_stress.jsonl").read_text().splitlines()]
    hits = sum(decide(x["r"], x["files"], None, str(tmp_path) if x.get("wd") else None)[0] == x["gold"] for x in rows)
    assert hits / len(rows) >= 0.95


def test_auto_skips_signed_in_subscriptions_unless_allowed(monkeypatch, tmp_path):
    """QA blocker B2: Auto used to send work to the user's paid Claude plan without asking."""
    from meshpool_harness import harness_router as R
    from meshpool_harness import harnesses as HR
    from meshpool_harness import config as C
    rows = [{"id": "meshpool-code", "name": "Meshpool Code", "available": True},
            {"id": "claude-code", "name": "Claude Code", "available": True, "ready": True, "plan": "claude",
             "signed_in": True}]
    monkeypatch.setattr(HR, "load_all", lambda: rows)
    monkeypatch.setattr(R, "table", lambda: {"categories": {"code": {"keywords": ["bug"], "scores": {
        "claude-code": 92, "meshpool-code": 90}}}})
    monkeypatch.setattr(C, "read", lambda *a, **k: {})
    monkeypatch.delenv("MH_AUTO_SUBSCRIPTIONS", raising=False)
    monkeypatch.setattr(HR, "provider", lambda: "meshpool")
    assert R.route("fix the bug", "code")["harness"] == "meshpool-code"
    monkeypatch.setattr(C, "read", lambda *a, **k: {"MH_AUTO_SUBSCRIPTIONS": "1"})
    assert R.route("fix the bug", "code")["harness"] == "claude-code"


def _auto(monkeypatch, table, ids):
    from meshpool_harness import harness_router as R
    from meshpool_harness import harnesses as HR
    from meshpool_harness import config as C
    monkeypatch.setattr(HR, "load_all", lambda: [{"id": h, "name": h, "available": True} for h in ids])
    monkeypatch.setattr(R, "table", lambda: table)
    monkeypatch.setattr(C, "read", lambda *a, **k: {})
    monkeypatch.setattr(HR, "provider", lambda: "meshpool")
    return R


def test_auto_takes_the_cheapest_of_the_equally_good_and_never_a_worse_one(monkeypatch):
    t = {"quality_band": 5, "categories": {"code": {"keywords": ["bug"],
         "scores": {"a": 100, "b": 96, "c": 80}, "cost_usd": {"a": 0.04, "b": 0.007, "c": 0.001},
         "seconds": {"a": 300, "b": 90, "c": 10}}}}
    R = _auto(monkeypatch, t, ["a", "b", "c"])
    r = R.route("fix the bug", "code")
    assert r["harness"] == "b"                       # within 5 points of the best, and cheaper
    assert "lowest cost" in r["reason"]
    assert [x["harness"] for x in r["ranking"]][:2] == ["b", "a"]
    t["categories"]["code"]["scores"]["b"] = 94     # now measurably worse: quality wins over price
    assert R.route("fix the bug", "code")["harness"] == "a"


def test_auto_without_cost_data_picks_by_quality_and_ignores_unscored_harnesses(monkeypatch):
    t = {"categories": {"documents": {"keywords": ["deck"], "scores": {"w": 85, "h": 83}}},
         "mode_bonus": {"cowork": {"x": 50, "h": 3}}}
    R = _auto(monkeypatch, t, ["w", "h", "x"])
    assert R.route("make a deck", "cowork")["harness"] == "h"        # 86 vs 85, no cost: best quality
    assert R.route("make a deck", "code")["harness"] == "w"


def test_classify_prefers_the_deliverable_over_generic_words():
    from meshpool_harness import harness_router as R
    assert R.classify("build me a deck about Q3 sales")[0] == "documents"
    assert R.classify("make a financial model in excel")[0] == "data"
    assert R.classify("hello")[0] == "general"


def test_an_artifact_word_naming_code_routes_to_code():
    # "slide renderer", "pptx exporter", "survey parser": the artifact word modifies a code noun, so it is code work
    for t in ("fix the slide renderer test", "add a unit test for the pptx exporter",
              "debug why the survey parser crashes", "fix the csv parser bug", "read an excel file in python with openpyxl"):
        assert decide(t)[0] == ["code"], t
    assert decide("make a deck about our data pipeline")[0] == ["slides"]
    assert decide("analyze this csv in pandas")[0] == ["data"]
