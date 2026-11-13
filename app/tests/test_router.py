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
            {"id": "claude-subscription", "name": "Claude (your subscription)", "available": True,
             "subscription": True, "signed_in": True}]
    monkeypatch.setattr(HR, "load_all", lambda: rows)
    monkeypatch.setattr(R, "table", lambda: {"categories": {"code": {"keywords": ["bug"], "scores": {
        "claude-subscription": 92, "meshpool-code": 90}}}})
    monkeypatch.setattr(C, "read", lambda *a, **k: {})
    monkeypatch.delenv("MH_AUTO_SUBSCRIPTIONS", raising=False)
    assert R.route("fix the bug", "code")["harness"] == "meshpool-code"
    monkeypatch.setattr(C, "read", lambda *a, **k: {"MH_AUTO_SUBSCRIPTIONS": "1"})
    assert R.route("fix the bug", "code")["harness"] == "claude-subscription"
