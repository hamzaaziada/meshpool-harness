"""Composition: football-field gating and drawing, price bridges, grid parts stacked (never dropped) and
shrink-wrapped, argument slides with a visual per block, single footnote markers, deck-wide stamps."""
from pptx import Presentation

from engines.forge import parts as PT
from engines.forge import render as R

SRC = {"text": "Company filings"}


def _ff(methods):
    cats = [m[0] for m in methods]
    return {"kind": "range", "categories": cats, "unit": "$",
            "series": [{"name": "Low", "values": [m[1] for m in methods]},
                       {"name": "High", "values": [m[2] for m in methods]}],
            "markers": [{"label": "Offer", "value": 65.37}, {"label": "Current", "value": 48.42}]}


def test_football_field_needs_three_methods_in_one_unit():
    one = _ff([("Precedent transactions", 4.9, 20.0)])
    probs = R.chart_problems(one)
    assert any(">= 3 methods" in p for p in probs) and any("unit" in p for p in probs)
    ok = _ff([("Comps", 40, 60), ("Precedents", 50, 75), ("DCF", 45, 90)])
    assert R.chart_problems(ok) == []
    s = {"archetype": "football_field", "title": "The offer sits above the comps range", "chart": one,
         "takeaway": {"text": "The offer is above the only range"}, "bullets": ["Past deals ran $4.9B to $20B"],
         "source": SRC}
    fb = R.fallback(R.normalize(s))
    assert fb["archetype"] == "grid" and [p["type"] for p in fb["parts"]] == ["stats", "text"]
    assert any(it["value"] == "$65.37" for it in fb["parts"][0]["items"])


def test_football_field_draws_sorted_methods_and_labelled_markers(tmp_path):
    out = tmp_path / "ff.pptx"
    s = {"archetype": "football_field", "title": "The offer sits inside every method's range",
         "chart": _ff([("Comps", 40, 60), ("DCF", 45, 90), ("Precedents", 50, 75)]),
         "takeaway": {"text": "The offer is inside every range"}, "bullets": ["DCF is widest"], "source": SRC}
    R.render([s], str(out), fix_labels=False)
    sl = Presentation(str(out)).slides[0]
    ch = next(sh for sh in sl.shapes if sh.has_chart).chart
    assert list(ch.plots[0].categories) == ["DCF", "Precedents", "Comps"]      # by midpoint, highest first
    labels = [sh.text_frame.text for sh in sl.shapes if sh.name.startswith("ov_MarkerLabel")]
    assert any("Offer" in t for t in labels) and any("Current" in t for t in labels)
    va = ch.value_axis
    assert va.minimum_scale <= 40 and va.maximum_scale >= 90


def test_price_bridge_and_nested_exhibit_chart():
    s = {"archetype": "exhibit_argument", "title": "x", "exhibit": {"chart": {
        "kind": "bar", "categories": ["Unaffected price (5 Nov 2026)", "Premium (35%)", "Offer price"],
        "series": [{"name": "$ per share", "values": [48.42, 16.95, 65.37]}]}}}
    n = R.normalize(s)
    assert n["chart"]["kind"] == "waterfall" and R.chart_problems(n["chart"]) == []


def _grid(tmp_path, s):
    out = tmp_path / "g.pptx"
    R.render([s], str(out), fix_labels=False)
    return Presentation(str(out)).slides[0]


TEXT = {"type": "text", "heading": "Why it matters", "lead": "The stake is reported, not filed.",
        "bullets": ["The $2B was agreed on 26 Jan 2026", "The backstop runs to April 2032"]}
STATS = {"type": "stats", "heading": "Position", "items": [{"value": "~6.6%", "label": "Reported stake"},
                                                          {"value": "$2B", "label": "Agreed investment"}]}
CALLOUT = {"type": "callout", "bullets": ["Confirm the stake in a primary filing before sizing the offer"]}


def test_grid_stacks_extra_parts_instead_of_dropping_them(tmp_path):
    s = {"archetype": "grid", "layout": "halves", "look": "boxed", "title": "NVIDIA buys only the remaining shares",
         "parts": [STATS, TEXT, CALLOUT], "source": SRC}
    names = [sh.name for sh in _grid(tmp_path, s).shapes]
    assert "Callout3" in names and any(n.startswith("ov_role_numeral_Stat1_") for n in names)


def test_grid_cards_shrink_to_their_content():
    placed = PT.compose([TEXT, TEXT], "halves", "boxed", R.BODY_Y, R.BODY_B)
    for _, (x, y, w, h), p in placed:
        assert h <= PT.measure(p, w, "boxed", p.get("_bump") or 0)[1] + 0.01
        assert h < R.BODY_B - R.BODY_Y - 0.5                   # a short card is not stretched to the body


def test_argument_slides_carry_a_visual_per_block(tmp_path):
    s = {"archetype": "argument_only", "title": "Year 1 dilutes EPS; synergies make year 2 accretive",
         "intro": "**Synergy timing drives EPS.**",
         "blocks": [{"text": "The deal starts from **$2.46 of standalone EPS**.", "bullets": [{"text": "Interest drags",
                                                                                              "prov": ["F1"]}]},
                    {"text": "The model takes **$1.0B of synergies** a year.", "bullets": [{"text": "50% in year 1",
                                                                                           "prov": ["F2"]}]}],
         "callout": {"bullets": ["Accretion is a year-2 story"]}, "source": SRC}
    names = [sh.name for sh in _grid(tmp_path, s).shapes]
    assert sum(n.startswith("role_numeral_Figure") for n in names) == 2      # sourced headline figures
    s2 = dict(s, blocks=[{"text": "Antitrust review could block the deal", "bullets": ["Regulators look closely"]},
                         {"text": "Debt of $47B weighs on the balance sheet", "bullets": ["Interest is $846M"]}])
    sl = _grid(tmp_path, s2)
    icons = [sh for sh in sl.shapes if sh.shape_type == 13]
    assert len(icons) == 2 and len({sh.name for sh in icons}) == 2           # one distinct icon per block
    assert any(sh.name == "Intro" for sh in sl.shapes)


def test_footnote_markers_are_written_once():
    assert R.footnote_text("1. Unaffected price on 5 Nov") == "Unaffected price on 5 Nov"
    assert R.footnote_text("^1 Applied Digital EV not verified¹") == "Applied Digital EV not verified"


def test_stamp_sits_on_every_content_slide(tmp_path):
    out = tmp_path / "st.pptx"
    a = {"archetype": "argument_only", "title": "Risks could break the case", "tags": ["Preliminary"],
         "blocks": [{"text": "Antitrust review", "bullets": ["x"]}, {"text": "Debt load", "bullets": ["y"]}],
         "source": SRC}
    b = dict(a, title="Debt could break the case", subtitle="Debt, $B")
    R.render([a, b], str(out), fix_labels=False)
    for sl in Presentation(str(out)).slides:
        assert any(sh.name.startswith("ov_Tag") and sh.text_frame.text == "Preliminary" for sh in sl.shapes)
