import json
from pathlib import Path
from meshpool_harness.router.rules import decide

def test_routing_stress_accuracy(tmp_path):
    (tmp_path / ".git").mkdir()
    rows = [json.loads(l) for l in (Path(__file__).parent / "stress" / "routing_stress.jsonl").read_text().splitlines()]
    hits = sum(decide(x["r"], x["files"], None, str(tmp_path) if x.get("wd") else None)[0] == x["gold"] for x in rows)
    assert hits / len(rows) >= 0.95
