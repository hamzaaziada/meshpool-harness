"""Regression tests for deterministic deck number hygiene (bugs found by the blind judge)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engines.slides.pipeline import _chart_ok, _fix_units, facts_from_files  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def test_money_followed_by_word_starting_with_b():
    assert _fix_units("the trough at $56,158.73, before the peak") == "the trough at $56.2K, before the peak"


def test_money_rounding_and_counts():
    assert _fix_units("$1,312.31 avg; $393,694.22 total") == "$1.31K avg; $394K total"
    assert _fix_units("$111,188.16K") == "$111.2K"
    assert _fix_units("8031 users, 669.25 users in 2026") == "8,031 users, 669 users in 2026"


@__import__("pytest").mark.skipif(not (__import__("pathlib").Path(__file__).resolve().parents[1] / "eval").exists(), reason="needs the benchmark suite (eval/)")
def test_change_facts_state_direction():
    f = facts_from_files([str(ROOT / "eval/tasks/office2/deck-01-sales/sales.csv")])
    texts = " ".join(x["text"] for x in f)
    assert "ROSE from 2026-01 to 2026-06" in texts and "FELL from its 2026-02 peak" in texts


def test_interpolated_chart_values_rejected():
    facts = [{"value": 12672}, {"value": 4.93}]
    assert _chart_ok({"chart": {"series": [{"values": [12672, 13250]}]}}, facts)
    assert not _chart_ok({"chart": {"series": [{"values": [12672, 4.9]}]}}, facts)
