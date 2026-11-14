"""Deck density modes (engines/forge/density.py): present / balanced / read.

The same slide content rendered for presenting carries fewer words on the page and larger type than the dense
send-ahead version; what present cuts lands in the speaker notes. Every mode opens without repair and renders without
overflow or overlap (P1, P8)."""
import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

from engines.forge import ooxml_check as OX  # noqa: E402
from engines.forge import validate_pptx as VP  # noqa: E402
from engines.forge.common import find_soffice  # noqa: E402
from engines.forge.density import condense, detect, headline  # noqa: E402

HAS_LO = find_soffice() is not None
SRC = {"text": "Company accounts 2021-2025; customer survey (n=412); pricing diagnostic"}

CHANNEL = {"kind": "bar", "categories": ["Direct sales", "Distributors", "Online store", "Marketplaces", "Partners"],
           "series": [{"name": "Share of growth (%)", "values": [38, 22, 17, 11, 8]}], "unit": "%",
           "highlight_categories": ["Direct sales"]}
MARGIN = {"kind": "column", "categories": ["2021", "2022", "2023", "2024", "2025"],
          "series": [{"name": "Gross margin (%)", "values": [31, 32, 31, 33, 35]}], "unit": "%"}


def sample() -> list[dict]:
    """Five slides of send-ahead content (today's dense targets): chart + takeaway, challenge matrix, exhibit +
    argument, table, next steps."""
    return [
        {"archetype": "bar_takeaway", "node": "1.1", "source": SRC, "footnotes": ["Growth 2023-2025, constant currency"],
         "title": "Direct sales drove 38% of growth, twice the next channel",
         "subtitle": "Share of revenue growth by channel, %, 2023-2025",
         "chart": CHANNEL,
         "takeaway": {"text": "Growth came from the channel we control"},
         "bullets": [
             {"text": "Direct sales added 38% of growth, led by the north at 9% a year, the third rise in three years"},
             {"text": "Distributors added 22%; their share fell for the second year as two large partners consolidated"},
             {"text": "Online and marketplaces together added 28%, but at margins 4 points below direct sales"},
             {"text": "Partners and retail are flat; neither channel has had new investment since 2022"}]},
        {"archetype": "challenge_matrix", "node": "1.2", "source": SRC,
         "title": "Four issues hold margin back, and each is getting worse without action",
         "columns": ["Issue", "A - Today", "B - Outlook"],
         "rows": [
             {"icon": "money", "label": "**Discounts** outpace list price",
              "cells": [{"lead": "Discounts grew 6% a year since 2021",
                         "bullets": ["6% average discount growth against 3% list-price growth",
                                     "Top 50 accounts get 70% of all discount value"]},
                        {"lead": "Gap widens by 1 point a year",
                         "bullets": ["3 competitors cut list prices in 2025",
                                     "Sales incentives still reward volume, not margin"]}]},
             {"icon": "people", "label": "**Sales** sets prices alone",
              "cells": [{"lead": "No deal review below $1M",
                         "bullets": ["80% of deals close without a pricing check",
                                     "Regional managers approve their own exceptions"]},
                        {"lead": "Headcount grows 12% next year",
                         "bullets": ["New reps join without pricing training",
                                     "Approval load doubles by 2027"]}]},
             {"icon": "risk", "label": "**Data** is late and partial",
              "cells": [{"lead": "Margin reports arrive 6 weeks late",
                         "bullets": ["Product margin is known for 60% of revenue",
                                     "No view of margin by customer"]},
                        {"lead": "ERP upgrade slips to 2027",
                         "bullets": ["Manual extracts remain the only source",
                                     "Analysts spend 40% of time on reconciliation"]}]},
             {"icon": "growth", "label": "**Mix** shifts to low margin",
              "cells": [{"lead": "Online grew 3x faster than direct",
                         "bullets": ["Online margin is 4 points below direct",
                                     "Marketplace fees rose 2 points in 2025"]},
                        {"lead": "Online reaches 30% of revenue by 2027",
                         "bullets": ["Mix alone costs 1.2 points of margin",
                                     "Fee pressure continues as platforms consolidate"]}]}]},
        {"archetype": "exhibit_argument", "node": "2.1", "source": SRC,
         "title": "Margin rose 4 points in four years…", "title_right": "…but discounts took half the gain",
         "exhibit": {"title": "Gross margin", "unit": "%, 2021-2025"},
         "chart": MARGIN,
         "blocks": [
             {"text": "Price rises lifted **gross margin from 31% to 35%** over four years",
              "bullets": [{"text": "List prices rose 3% a year, ahead of input costs at 2%"},
                          {"text": "Premium lines grew to 24% of revenue, up from 18%"}]},
             {"text": "Discounts gave back **half of the gain** in the same period",
              "bullets": [{"text": "Average discount grew from 9% to 12% of list price"},
                          {"text": "The top 50 accounts took most of the discount value"}]},
             {"text": "Peers kept **2 points more** of their price rises",
              "bullets": [{"text": "Peer discount growth averaged 1% a year against our 6%"}]}],
         "callout": {"bullets": [{"text": "**Discount discipline** is worth 2 points of margin"},
                                 {"text": "**Price corridors** stop the leakage at the source"}]}},
        {"archetype": "table_focus", "node": "2.2", "source": SRC,
         "title": "North leads on growth and margin; the south needs a pricing reset",
         "subtitle": "Results by region, 2025",
         "table": {"columns": ["Region", "Growth", "Margin", "Discount"],
                   "rows": [["North", "+12%", "37%", "9%"], ["South", "+9%", "29%", "14%"],
                            ["East", "+8%", "34%", "11%"], ["West", "+6%", "31%", "12%"],
                            ["Central", "+5%", "33%", "10%"], ["Islands", "+4%", "30%", "13%"],
                            ["Export", "+3%", "28%", "15%"], ["Total", "+8%", "33%", "11%"]],
                   "highlight_rows": [1], "total_row": True},
         "takeaway": {"text": "The south carries the deepest discounts and the lowest margin"},
         "bullets": [{"text": "South discounts average 14%, five points above the north, on similar deal sizes"},
                     {"text": "Export and the islands follow the same pattern at a smaller scale"},
                     {"text": "A reset in the south alone would add about 1 point to group margin"}]},
        {"archetype": "closing", "node": "3.1", "source": SRC,
         "title": "Start with discount governance now and roll out corridors within a year",
         "steps": [
             {"action": "Publish discount rules", "owner": "CFO", "timing": "Q1 2026",
              "detail": "Set approval levels by deal size so every discount above 10% needs a second signature",
              "milestones": ["Rules approved by the executive committee", "Training for 120 sales staff"]},
             {"action": "Set price corridors", "owner": "Pricing lead", "timing": "Q2 2026",
              "detail": "Define floor and target prices for the top 50 products, which carry 70% of revenue",
              "milestones": ["Corridors for the top 50 products", "Exceptions logged weekly"]},
             {"action": "Pilot in the south", "owner": "Head of south", "timing": "Q3 2026",
              "detail": "Run the new rules for one quarter in the region with the deepest discounts",
              "milestones": ["Pilot readout on margin and win rate"]},
             {"action": "Scale nationally", "owner": "COO", "timing": "Q1 2027",
              "detail": "Roll out to all regions with sales incentives tied to margin, not volume",
              "milestones": ["All regions live", "Incentives changed"]}]},
    ]


# ------------------------------------------------------------------ detection
def test_density_detected_from_request():
    from engines.forge.engine import density_from
    assert density_from("Make a 10-slide deck on regional transport") == "balanced"
    assert density_from("A 6-slide deck for presenting to the board") == "present"
    assert density_from("Build a keynote on our strategy") == "present"
    assert density_from("Make it visual, not just full of writing") == "present"
    assert density_from("Make a presentation on pricing") == "present"
    assert density_from("A detailed send-ahead pre-read for the committee") == "read"
    assert density_from("A dense deck to be read on its own") == "read"
    assert density_from("Read the attached file and make a deck") == "balanced"   # 'read the file' is not a mode
    assert density_from("too busy, make it less dense") == "present"
    assert density_from('slides on pricing, "density": "read"') == "read"
    assert density_from("keynote deck", {"density": "read"}) == "read"            # the explicit option wins
    assert density_from("a pre-read", density="present") == "present"
    assert detect("anything", "balanced") == "balanced"


def test_headline_keeps_the_lead_clause():
    t = "Price rose 6% on average, led by the north at 9%, the third rise in three years"
    assert headline(t, 12) == "Price rose 6% on average"
    assert headline("Short line", 12) == "Short line"
    assert headline("**Discounts** grew faster than list prices in every region since 2021 and the gap widens each "
                    "year as competitors cut", 8).count("**") % 2 == 0


def test_condense_moves_detail_to_notes():
    slides = sample()
    read = [condense(s, "read") for s in slides]
    assert read == slides                                  # read mode changes nothing
    pres = [condense(s, "present") for s in slides]
    assert pres[1]["archetype"] == "grid" and len(pres[1]["parts"]) == 4      # the matrix becomes short parts
    assert len(pres[0]["bullets"]) <= 3
    assert "the third rise in three years" in pres[0]["notes"]               # the cut detail is in the notes
    assert len(pres[3]["table"]["rows"]) <= 6 and pres[3]["table"]["highlight_rows"] == [1]
    assert pres[3]["table"]["rows"][-1][0] == "Total"                        # the total row stays last
    assert all(not st["milestones"] for st in pres[4]["steps"])
    assert "Training for 120 sales staff" in pres[4]["notes"]
    bal = [condense(s, "balanced") for s in slides]
    assert all(len(c["bullets"]) <= 2 for r in bal[1]["rows"] for c in r["cells"])


def test_fill_checks_do_not_push_present_slides_for_more():
    from engines.forge.build import fill_checks
    thin = {"archetype": "bar_takeaway", "chart": CHANNEL, "takeaway": {"text": "Direct sales led growth"},
            "bullets": [{"text": "Direct sales added 38% of growth"}]}
    assert 1 in fill_checks([thin], "read")                # read wants 3 full bullets
    assert fill_checks([thin], "present") == {}            # present: an exhibit and a takeaway are enough
    busy = sample()[0]
    assert any("too busy" in m for m in fill_checks([busy], "present").get(1, []))
    assert 1 not in fill_checks([busy], "read")
    assert fill_checks([dict(thin, takeaway={}, bullets=[])], "present")      # but a bare chart is sent back


def test_p10_caps_follow_the_density_mode():
    from engines.forge.common import load_rules
    R = load_rules()["pptx"]
    caps = {m: VP.density_caps(R, m) for m in ("present", "balanced", "read")}
    for arch in ("challenge_matrix", "exhibit_argument", "grid"):
        assert caps["present"]["by_archetype"][arch] < caps["balanced"]["by_archetype"][arch] \
            < caps["read"]["by_archetype"][arch]
    assert caps["present"]["max_bullets"] <= 3 and caps["read"]["max_bullets"] == 5
    assert VP.slide_density({"name": "arch:grid node:1 density:present"}, R) == "present"
    assert VP.slide_density({"name": "arch:grid node:1"}, R) == "read"              # untagged: today's caps
    assert VP.slide_density({"name": "arch:grid density:present"}, R, "read") == "read"


# ------------------------------------------------------------------ rendering
def _on_slide(path):
    """(body words, largest body size in pt, notes text) per slide, from the file."""
    from pptx import Presentation
    out = []
    for sl in Presentation(str(path)).slides:
        sizes, words = [], 0
        for sh in sl.shapes:
            if not sh.has_text_frame or sh.is_placeholder or sh.name in ("Source", "Firm", "Footnotes"):
                continue
            for p in sh.text_frame.paragraphs:
                for r in p.runs:
                    if r.font.size:
                        sizes.append(r.font.size.pt)
            words += len(sh.text_frame.text.split())
        if any(sh.has_table for sh in sl.shapes):
            for sh in sl.shapes:
                if sh.has_table:
                    words += sum(len(c.text.split()) for row in sh.table.rows for c in row.cells)
        notes = sl.notes_slide.notes_text_frame.text if sl.has_notes_slide else ""
        out.append((words, sorted(sizes), notes))
    return out


@pytest.mark.skipif(not HAS_LO, reason="LibreOffice needed to render")
def test_present_vs_read_same_content(tmp_path):
    from statistics import median
    from engines.forge.render import render
    got = {}
    for mode in ("present", "balanced", "read"):
        out = tmp_path / f"{mode}.pptx"
        res = render(sample(), str(out), carry=False, density=mode)
        assert res["slides"] == 5, res["errors"]
        ox = OX.check(str(out))
        assert ox["ok"], (mode, ox["problems"][:5])
        r = VP.validate(str(out), render=True)
        for rule in ("P1", "P8"):
            assert r["rules"][rule] == "pass", (mode, [f["message"] for f in r["failures"] if f["rule"] == rule][:6])
        assert not [f for f in r["failures"] if f["rule"] == "P20" and f["check"] in ("stretched_rows", "oversized_bars")]
        got[mode] = _on_slide(out)
    pw = [w for w, _, _ in got["present"]]
    rw = [w for w, _, _ in got["read"]]
    bw = [w for w, _, _ in got["balanced"]]
    assert sum(pw) < 0.6 * sum(rw), (pw, rw)              # far fewer words on the page when presenting
    assert sum(pw) < sum(bw) <= sum(rw), (pw, bw, rw)
    for (p_w, p_s, p_n), (r_w, r_s, r_n) in zip(got["present"], got["read"]):
        assert p_w <= r_w
        assert median(p_s) > median(r_s), (p_s, r_s)        # larger type
    assert "the third rise in three years" in got["present"][0][2]      # the cut detail is in the speaker notes
    assert "the third rise in three years" not in got["read"][0][2]
    assert "Training for 120 sales staff" in got["present"][4][2]


# ------------------------------------------------------------------ P4 per density mode
def test_p4_font_sizes_counted_per_density_mode(tmp_path):
    """Each density mode's slides are held to that mode's size cap; the renderer's own decks pass in every mode,
    a forced cross-mode check and an untagged deck still fail."""
    from engines.forge.render import render
    from pptx import Presentation
    from pptx.oxml.ns import qn
    for mode in ("present", "balanced", "read"):
        out = tmp_path / f"{mode}.pptx"
        render(sample(), str(out), carry=False, density=mode)
        r = VP.validate(str(out), render=False)
        assert r["rules"]["P4"] == "pass", (mode, [f["message"] for f in r["failures"] if f["rule"] == "P4"])
    r = VP.validate(str(tmp_path / "present.pptx"), render=False, density="read")   # present type, read caps
    assert r["rules"]["P4"] == "fail"
    prs = Presentation(str(tmp_path / "balanced.pptx"))     # untagged: the base cap (3) applies, not a mode's
    for sl in prs.slides:
        c = sl._element.find(qn("p:cSld"))
        c.set("name", c.get("name").replace("density:balanced", ""))
    prs.save(str(tmp_path / "untagged.pptx"))
    r = VP.validate(str(tmp_path / "untagged.pptx"), render=False)
    assert any(f["check"] == "font_sizes" and f["limit"] == 3 for f in r["failures"])


# ------------------------------------------------------------------ wide tables keep their bullets
WIDE = {"archetype": "table_focus", "node": "2.3", "source": SRC, "title": "The south trails on every measure",
        "table": {"columns": ["Region", "Lead channel", "Growth", "Margin", "Discount", "Win rate"],
                  "rows": [["North", "Direct", "+12%", "37%", "9%", "41%"], ["South", "Distributors", "+9%", "29%", "14%", "33%"],
                           ["East", "Online", "+8%", "34%", "11%", "38%"], ["West", "Direct", "+6%", "31%", "12%", "36%"],
                           ["Central", "Partners", "+5%", "33%", "10%", "39%"], ["Islands", "Direct", "+4%", "30%", "13%", "34%"],
                           ["Export", "Distributors", "+3%", "28%", "15%", "31%"]]},   # 7 rows: stays a table
        "takeaway": {"text": "The south carries the deepest discounts"},
        "bullets": [{"text": "South discounts average 14%, five points above the north"},
                    {"text": "Win rates in the south trail by eight points despite the discounts"},
                    {"text": "A reset in the south alone would add about 1 point to group margin"}]}


def _shapes_text(sl):
    return " ".join(sh.text_frame.text for sh in sl.shapes if sh.has_text_frame)


@pytest.mark.parametrize("mode", ["present", "balanced", "read"])
def test_wide_table_keeps_bullets_in_a_band(tmp_path, mode):
    from pptx import Presentation
    from engines.forge.render import render
    out = tmp_path / "wide.pptx"
    render([WIDE], str(out), carry=False, density=mode)
    sl = Presentation(str(out)).slides[0]
    table = next(sh for sh in sl.shapes if sh.has_table)
    assert table.width.inches > 11                                   # full width
    band = [sh for sh in sl.shapes if sh.name.startswith("ov_Panel")]     # the so-what panel under the table
    assert band and all(sh.top >= table.top + table.height for sh in band)   # under the table
    assert all((sh.top + sh.height) / 914400 <= 7.02 for sh in band)  # above the footer rule
    on, notes = _shapes_text(sl), sl.notes_slide.notes_text_frame.text if sl.has_notes_slide else ""
    for b in WIDE["bullets"]:                                        # on the slide, else in the notes: never lost
        assert b["text"] in on or b["text"] in notes, (mode, b["text"])
    if mode == "read":
        assert all(b["text"] in on for b in WIDE["bullets"])


def test_wide_table_band_overflow_goes_to_notes(tmp_path):
    from pptx import Presentation
    from engines.forge.render import render
    long = [{"text": f"Point {i}: " + " ".join(["the south region trails the group on discount depth and win rate"] * 3)}
            for i in range(8)]
    s = dict(WIDE, table=dict(WIDE["table"], rows=WIDE["table"]["rows"] * 2), bullets=long)
    out = tmp_path / "wide_long.pptx"
    render([s], str(out), carry=False, density="read")
    sl = Presentation(str(out)).slides[0]
    on, notes = _shapes_text(sl), sl.notes_slide.notes_text_frame.text
    assert "Did not fit" in notes
    for b in long:
        assert b["text"] in on or b["text"] in notes
    if HAS_LO:
        r = VP.validate(str(out), render=True)
        assert r["rules"]["P1"] == "pass" and r["rules"]["P8"] == "pass", \
            [f["message"] for f in r["failures"] if f["rule"] in ("P1", "P8")]


def test_condense_keeps_computed_tables_whole():
    rows = [[f"{w}%"] + ["$1.00"] * 5 for w in range(8, 13)] + [["WACC \\ exit"] + ["10x"] * 5] + \
           [[f"{w}%"] + ["$2.00"] * 5 for w in range(8, 13)]
    s = {"archetype": "table_focus", "fixed": True, "title": "Value per share runs $1-$2 across WACC and growth",
         "table": {"columns": ["WACC"] + ["g"] * 5, "rows": rows, "keep_table": True, "italic_rows": [5],
                   "heat_grid": [{"rows": list(range(5)), "center": 1.0, "base": [2, 3]},
                                 {"rows": list(range(6, 11)), "center": 2.0, "base": [8, 3]}]}}
    for mode in ("present", "balanced"):
        out = condense(s, mode)
        assert out["table"]["rows"] == rows and out["table"]["heat_grid"] == s["table"]["heat_grid"]
