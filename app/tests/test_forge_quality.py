"""Deck defects seen on a real IC run: sentences with holes, mixed-unit and mixed-period charts, invented chart
values, empty bars, titles that repeat themselves, a marker label that says its number twice."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engines.forge.build import chart_checks, chart_fact_checks, prose_checks  # noqa: E402
from engines.forge.common import load_rules  # noqa: E402
from engines.forge.render import drop_gap_sentences, normalize, tidy_prose  # noqa: E402


def test_a_sentence_with_a_gap_goes_whole():
    gaps = []
    t = drop_gap_sentences("Applied Digital is excluded: market value of **~$5.5B** (Oct 2026) but [GAP: EV]. "
                           "Nebius trades at 8.9x.", gaps)
    assert t == "Nebius trades at 8.9x." and gaps == ["EV"]
    assert tidy_prose("The exact ownership gap is unconfirmed:.") == "The exact ownership gap is unconfirmed."
    assert tidy_prose("can achieve the **** scenario") == "can achieve the scenario"


def test_normalize_drops_bullets_that_were_only_a_gap_and_names_gaps_in_footnotes():
    s = normalize({"archetype": "argument_only", "title": "T", "bullets": [{"text": "[GAP: EPS year 1]"},
                                                                          {"text": "Kept."}],
                   "footnotes": ["DCF value is a [GAP: DCF] pending the model."]})
    assert [b["text"] for b in s["bullets"]] == ["Kept."]
    assert "[GAP" not in s["footnotes"][0] and "not available in the sources" in s["footnotes"][0]


def _bar(cats, vals, unit="$", kind="bar"):
    return {"archetype": "bar_takeaway", "title": "t",
            "chart": {"kind": kind, "categories": cats, "series": [{"name": "v", "values": vals}], "unit": unit}}


def test_chart_checks_catch_percent_with_dollars_quarter_with_year_and_empty_bars():
    msgs = chart_checks([_bar(["Offer price", "Unaffected price", "Premium (%)"], [65.37, 48.42, 35])])[1]
    assert any("percentage" in m for m in msgs)
    msgs = chart_checks([_bar(["Q2 2026", "Q3 2026", "FY2026E"], [2.58, 3.66, 13.15], "$B", "column")])[1]
    assert any("quarterly and annual" in m for m in msgs)
    msgs = chart_checks([_bar(["Standalone", "Year 1", "Year 2"], [2.46, None, None], "$", "column")])[1]
    assert any("no value for Year 1" in m for m in msgs)
    assert chart_checks([_bar(["Q1 2026", "Q2 2026", "Q3 2026"], [1.9, 2.58, 3.66], "$B", "column")]) == {}


def test_chart_values_must_be_in_the_facts():
    facts = [{"id": "F1", "text": "Revenue Q2 2026 $2.58B, Q3 2026 $3.66B; FY2026 guidance $13.0-13.3B"},
             {"id": "F2", "text": "Unaffected price $48.42; offer $65.37"}]
    rules = load_rules()
    ok = _bar(["Q2 2026", "Q3 2026", "FY2026 (mid)"], [2.58, 3.66, 13.15], "$B", "column")
    assert chart_fact_checks([ok], facts, rules) == {}
    ff = {"archetype": "football_field", "title": "t", "chart": {"kind": "range", "categories": ["Comps", "DCF"],
          "series": [{"name": "Low", "values": [50, 52]}, {"name": "High", "values": [65.37, 75]}], "unit": "$"}}
    msgs = chart_fact_checks([ff], facts, rules)[1]
    assert "50" in msgs[0] and "52" in msgs[0] and "75" in msgs[0] and "65.37" not in msgs[0]


def test_prose_checks_flag_a_title_that_repeats_itself():
    s = {"archetype": "argument_only", "title": "A DCF frames the offer, but value hinges on conversion and the "
         "value hinges on GW conversion", "bullets": [{"text": "Fine sentence."}]}
    msgs = prose_checks([s])[1]
    assert any("repeats itself" in m for m in msgs)
    assert prose_checks([{"archetype": "argument_only", "title": "Clean title with a 35% premium",
                          "bullets": [{"text": "**Bold** and done."}]}]) == {}


def test_calcs_are_recomputed_from_the_cited_facts():
    from engines.forge.build import number_checks, verify_calcs
    facts = [{"id": "F33", "text": "Offer premium 35% to the unaffected $48.42"},
             {"id": "F11", "text": "Backlog ~$132.5B at 30 Sep 2026 (from ~$104.2B at 30 Jun)"}]
    rules = load_rules()
    s = {"archetype": "argument_only", "title": "Offer at $65.37 and backlog up $28.3B", "title_prov": ["C1", "C2"],
         "calcs": [{"id": "C1", "value": "$65.37", "formula": "48.42 * (1 + 0.35)", "from": ["F33"]},
                   {"id": "C2", "value": "$28.3B", "formula": "132.5 - 104.2", "from": ["F11"]},
                   {"id": "C3", "value": "$70", "formula": "48.42 * 1.45", "from": ["F33"]},
                   {"id": "C4", "value": "$60", "formula": "48.42 * (1 + 0.35)", "from": ["F33"]}],
         "bullets": [{"text": "Offer **$65.37**.", "prov": ["C1"]}]}
    ok, bad = verify_calcs([s], facts, rules)
    assert [f["id"] for f in ok[1]] == ["S1C1", "S1C2"]
    assert s["title_prov"] == ["S1C1", "S1C2"] and s["bullets"][0]["prov"] == ["S1C1"]
    assert any("1.45 not in the cited facts" in m for m in bad[1]) and any("not $60" in m for m in bad[1])
    assert number_checks([s], facts + ok[1], rules) == {}
    assert number_checks([s], facts, rules)                      # without the ledger the derived numbers fail


def test_football_field_needs_methods_and_per_share_units():
    facts = [{"id": "F1", "text": "Precedent deal values $4.9B (AMD/ZT) to ~$20B (NVIDIA/Groq)"}]
    ff = {"archetype": "football_field", "title": "t", "subtitle": "Implied equity value per share, $ per share",
          "chart": {"categories": ["Precedent transactions"], "series": [{"name": "Low", "values": [4.9]},
                                                                         {"name": "High", "values": [20.0]}], "unit": "$"}}
    assert any("football field with 1 method" in m for m in chart_checks([ff])[1])
    assert any("per share" in m for m in chart_fact_checks([ff], facts, load_rules())[1])


def test_density_never_cuts_a_bullet_to_a_lead_in_phrase():
    from engines.forge.density import headline
    h = headline("On EV per live GW, CoreWeave and IREN both sit at ~$90B per GW, so financing, not asset quality, "
                 "explains the lower revenue multiple", 14)
    assert h != "On EV per live GW"
    assert headline("Backlog rose 27% to $132.5B in one quarter, driven by two hyperscaler contracts and a sovereign "
                    "deal in Europe", 12) == "Backlog rose 27% to $132.5B in one quarter"


def test_fallback_keeps_the_title_whole_and_moves_the_right_title_into_the_text():
    from engines.forge.render import fallback
    s = fallback({"archetype": "exhibit_argument", "title": "Year 1 dilutes EPS", "title_right":
                  "synergy timing drives EPS", "chart": {"kind": "column", "categories": ["A"],
                                                         "series": [{"name": "x", "values": [2.46]}]},
                  "blocks": [{"text": "Body."}]})
    assert s["title"] == "Year 1 dilutes EPS" and s["intro"].startswith("**Synergy timing drives EPS**")
