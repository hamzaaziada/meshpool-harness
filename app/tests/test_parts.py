"""Parts library: one deck that uses every part type across the grid layouts and every look opens without repair,
renders without overflow or overlap (P1, P8) and keeps text in its own shapes (P17)."""
import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

from engines.forge import ooxml_check as OX  # noqa: E402
from engines.forge import parts as PT  # noqa: E402
from engines.forge import validate_pptx as VP  # noqa: E402
from engines.forge.common import find_soffice  # noqa: E402

HAS_LO = find_soffice() is not None
SRC = {"text": "Company accounts 2021-2025; customer survey (n=412)"}

REVENUE = {"kind": "column", "categories": ["2021", "2022", "2023", "2024", "2025"],
           "series": [{"name": "Revenue ($M)", "values": [212, 238, 251, 274, 301]}]}
MARGIN = {"kind": "line", "categories": ["Q1 24", "Q2 24", "Q3 24", "Q4 24", "Q1 25", "Q2 25"],
          "series": [{"name": "Gross margin (%)", "values": [31, 32, 31, 33, 34, 35]}], "unit": "%"}
CHANNEL = {"kind": "bar", "categories": ["Direct sales", "Distributors", "Online store", "Marketplaces", "Partners",
                                         "Retail"],
           "series": [{"name": "Share of growth (%)", "values": [38, 22, 17, 11, 8, 4]}], "unit": "%"}
STATS3 = [{"value": "$301M", "label": "Revenue 2025", "delta": "+10% vs 2024"},
          {"value": "35%", "label": "Gross margin", "delta": "+3 pp in two years"},
          {"value": "1.9x", "label": "Net revenue retention", "delta": "Top quartile"}]
STATS4 = STATS3 + [{"value": "412", "label": "Customers surveyed", "delta": "68% would buy again"}]
TABLE = {"columns": ["Region", "Revenue", "Growth", "Margin"],
         "rows": [["North", "$112M", "+12%", "37%"], ["South", "$84M", "+9%", "33%"], ["East", "$61M", "+8%", "34%"],
                  ["West", "$44M", "+6%", "31%"]],
         "highlight_rows": [0]}
STEPS = [{"title": "Diagnose", "text": "Map margin by product and region"},
         {"title": "Design", "text": "Set price corridors and discount rules"},
         {"title": "Pilot", "text": "Test in two regions for one quarter"},
         {"title": "Scale", "text": "Roll out with sales incentives"}]
MILESTONES = [{"date": "Jan 2026", "label": "Pricing diagnostic complete"},
              {"date": "Mar 2026", "label": "Corridors approved"},
              {"date": "Jun 2026", "label": "Pilot in North and East"},
              {"date": "Sep 2026", "label": "Pilot readout"},
              {"date": "Jan 2027", "label": "National roll-out"}]
MATRIX = {"type": "matrix2x2", "heading": "Initiatives by impact and ease",
          "x_axis": {"label": "Ease of delivery", "low": "Hard", "high": "Easy"},
          "y_axis": {"label": "Margin impact", "low": "Low", "high": "High"},
          "quadrants": [{"title": "Plan carefully", "items": ["Contract renegotiation"]},
                        {"title": "Do now", "items": ["Discount governance", "Price corridors"], "highlight": True},
                        {"title": "Deprioritise", "items": ["New tiering model"]},
                        {"title": "Quick wins", "items": ["Invoice clean-up"]}]}
HARVEY = {"type": "harvey", "heading": "Capability today",
          "criteria": ["Data", "Process", "Tools", "Skills"],
          "rows": [{"label": "Price setting", "scores": [75, 50, 25, 50]},
                   {"label": "Discount control", "scores": [25, 0, 25, 50]},
                   {"label": "Deal review", "scores": [100, 75, 50, 75]}]}
QUOTE = {"type": "quote", "text": "We were giving away margin in every deal without knowing it.",
         "attribution": "Regional sales director"}
CALLOUT = {"type": "callout", "lead": "So what", "text": "Price discipline is worth 2-3 points of margin by 2027."}
TEXT = {"type": "text", "heading": "Why it matters", "lead": "Growth is real, but margin lags peers",
        "bullets": ["Revenue rose every year since 2021", "Discounts grew faster than list prices",
                    "Two regions carry most of the gap"]}


def gallery() -> list[dict]:
    return [
        {"archetype": "grid", "layout": "full", "look": "editorial", "source": SRC,
         "title": "The pricing programme runs for one year, from diagnostic to national roll-out",
         "parts": [{"type": "timeline", "heading": "Programme milestones", "highlight": 2, "milestones": [
             dict(m, text=d) for m, d in zip(MILESTONES, [
                 "Margin mapped for every product and region", "Executive committee signs off the corridors",
                 "Two regions trade on the new rules for a quarter", "Margin, volume and win-rate effects measured",
                 "All regions, with sales incentives aligned"])]}]},
        {"archetype": "grid", "layout": "halves", "look": "boxed", "source": SRC,
         "title": "Revenue grew every year, but margin lags peers",
         "parts": [{"type": "chart", "heading": "Revenue, $M", "chart": REVENUE}, dict(TEXT)]},
        {"archetype": "grid", "layout": "thirds", "look": "minimal", "source": SRC,
         "title": "Three numbers frame the case and four steps deliver it",
         "parts": [{"type": "stats", "heading": "Where we stand", "items": STATS3},
                   {"type": "process", "heading": "How we get there", "steps": STEPS},
                   dict(QUOTE, heading="What we heard")]},
        {"archetype": "grid", "layout": "quarters", "look": "bold", "source": SRC,
         "title": "North leads on growth while discount control is the weakest capability",
         "parts": [{"type": "table", "heading": "Results by region, 2025", "table": TABLE},
                   HARVEY,
                   dict(CALLOUT, heading="Implication"),
                   {"type": "stats", "heading": "Headline figures", "items": STATS4[:3]}]},
        {"archetype": "grid", "layout": "main_side", "look": "editorial", "source": SRC,
         "title": "Discount governance and price corridors are the moves to make now",
         "parts": [MATRIX, {"type": "text", "heading": "Next 90 days",
                            "bullets": ["Publish discount rules", "Set corridors for the top 50 products",
                                        "Train regional managers"]}]},
        {"archetype": "grid", "layout": "quarters", "look": "boxed", "source": SRC,
         "title": "Direct sales drove most growth and the plan fits one year",
         "parts": [{"type": "chart", "heading": "Share of growth by channel", "chart": CHANNEL},
                   {"type": "timeline", "heading": "Plan", "milestones": MILESTONES[:4]},
                   {"type": "process", "heading": "Approach", "steps": STEPS},
                   dict(QUOTE)]},
        {"archetype": "grid", "layout": "main_stack", "look": "minimal", "source": SRC,
         "title": "Margin has risen four points in six quarters",
         "parts": [{"type": "chart", "heading": "Gross margin by quarter", "chart": MARGIN},
                   {"type": "stats", "items": STATS3[:2]}, dict(CALLOUT)]},
        {"archetype": "grid", "layout": "side_main", "look": "bold", "source": SRC,
         "title": "Every capability needs work before the roll-out",
         "parts": [{"type": "timeline", "heading": "Milestones", "milestones": MILESTONES[:4]}, dict(HARVEY, heading="Capability by area")]},
        {"archetype": "grid", "layout": "full", "look": "boxed", "source": SRC,
         "title": "Four figures summarise the year",
         "parts": [{"type": "stats", "heading": "2025 in figures", "items": STATS4}]},
        {"archetype": "grid", "layout": "halves_v", "look": "editorial", "source": SRC,
         "title": "The roll-out follows four steps across four regions",
         "parts": [{"type": "process", "heading": "Approach", "steps": STEPS},
                   {"type": "table", "heading": "Regions", "table": TABLE}]},
        {"archetype": "grid", "layout": "stack_main", "look": "boxed", "source": SRC,
         "title": "Discount control is the first fix",
         "parts": [dict(QUOTE, heading="Voice of sales"), dict(CALLOUT, heading="So what"), MATRIX]},
    ]


def _register():
    from engines.forge import render as R
    assert "grid" in R.ARCHETYPES and R.REQUIRED.get("grid") == ["parts"]


def test_regions_tile_the_body():
    from engines.forge import render as R
    for lay, k in PT.LAYOUTS.items():
        rs = PT.regions(lay)
        assert len(rs) == k
        for x, y, w, h in rs:
            assert x >= R.LEFT - 1e-6 and x + w <= R.RIGHT + 1e-6 and y >= R.BODY_Y - 1e-6 and y + h <= R.BODY_B + 1e-6
        area = sum(w * h for _, _, w, h in rs)
        assert area > 0.85 * (R.RIGHT - R.LEFT) * (R.BODY_B - R.BODY_Y)
    assert PT._fewer("quarters", 3) == "main_stack" and PT._fewer("thirds", 2) == "halves" and PT._fewer("halves", 1) == "full"


def test_schema_gate_accepts_grid_and_flags_bad_parts():
    from engines.forge.render import schema_errors, fallback
    _register()
    for s in gallery():
        assert schema_errors(s) == [], (s["title"], schema_errors(s))
    bad = {"archetype": "grid", "title": "x", "parts": [{"type": "sparkle"}, {"type": "chart", "chart": {"kind": "column",
           "categories": ["A"], "series": [{"name": "v", "values": [3]}]}}]}
    errs = schema_errors(bad)
    assert any("sparkle" in e for e in errs) and any("one bar" in e for e in errs)
    fb = fallback(bad)
    assert fb["archetype"] == "grid" and [p["type"] for p in fb["parts"]] == ["stats"]
    assert schema_errors({"archetype": "grid", "title": "x"})


def test_fewer_parts_never_leave_a_region_empty(tmp_path):
    from pptx import Presentation
    from engines.forge.render import render
    _register()
    out = tmp_path / "few.pptx"
    s = {"archetype": "grid", "layout": "quarters", "title": "Margin rose four points in two years", "source": SRC,
         "parts": [dict(TEXT), dict(CALLOUT), {"type": "stats", "items": STATS3}]}
    render([s], str(out), fix_labels=False)
    names = [sh.name for sh in Presentation(str(out)).slides[0].shapes]
    assert "Text1" in names and "Callout2" in names and any(n.startswith("ov_role_numeral_Stat3_") for n in names)
    assert "main_stack" in Presentation(str(out)).slides[0].notes_slide.notes_text_frame.text


@pytest.mark.skipif(not HAS_LO, reason="LibreOffice not installed")
def test_parts_gallery_renders_clean(tmp_path):
    from engines.forge.render import render
    _register()
    slides = gallery()
    types = {p["type"] for s in slides for p in s["parts"]}
    assert types == set(PT.PART_TYPES)
    assert {s["layout"] for s in slides} >= {"full", "halves", "thirds", "quarters", "main_side"}
    assert {s["look"] for s in slides} == set(PT.LOOKS)
    out = tmp_path / "parts.pptx"
    res = render(slides, str(out))
    assert res["slides"] == len(slides)
    assert not [e for e in res["errors"] if "could not be drawn" in e], res["errors"]
    ox = OX.check(str(out))
    assert ox["ok"], ox["problems"][:5]
    r = VP.validate(str(out), render=True)
    for rule in ("P1", "P8", "P17"):
        assert r["rules"][rule] == "pass", [f["message"] for f in r["failures"] if f["rule"] == rule][:6]
