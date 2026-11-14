"""Vertical composition on thin slides: the room goes to exhibits, then type, then a capped rhythm (never into empty
panels); table gaps are dropped honestly; the executive summary's decision band never repeats the title; a thin slide
goes back to the writer with the facts the deck has not used; the slide count holds; the review verdict never
disappears silently."""
import json
import re

import pytest
from pptx import Presentation

from engines.forge import parts as PT
from engines.forge import render as R
from engines.forge.common import find_soffice

HAS_LO = find_soffice() is not None
lo = pytest.mark.skipif(not HAS_LO, reason="LibreOffice needed")
SRC = {"text": "Company filings"}


def _shapes(path, i=0):
    return {sh.name: sh for sh in Presentation(str(path)).slides[i].shapes}


def _in(v):
    return v / 914400


# ------------------------------------------------------------------ grid: type, figures and rhythm fill a thin page
def test_thin_grid_steps_up_type_and_figures_then_spreads_its_rhythm():
    R.set_density("balanced")
    parts = [{"type": "text", "heading": "Recommendation", "lead": "Buy the remaining 93% at a 35% premium.",
              "bullets": ["Pays 50/50 cash and stock", "Backlog of $132.5B underpins growth"]},
             {"type": "stats", "items": [{"value": "35%", "label": "Premium"}, {"value": "$33.6B", "label": "Equity"},
                                         {"value": "$132.5B", "label": "Backlog"}]}]
    top, bottom = R.BODY_Y, R.BODY_B
    placed, over, empty = PT._compose_one(parts, "halves_v", "boxed", top, bottom)
    assert over <= 0.01
    by = {j: (box, p) for j, box, p in placed}
    (tx, ty, tw, th), tp = by[0]
    (sx, sy, sw, sh), sp = by[1]
    assert tp.get("_bump", 0) > 3 and sp.get("_bump", 0) >= 2          # text past the old cap, figures larger
    assert tp["_bump"] <= PT.MAX_BUMP["balanced"]
    gap = sy - (ty + th)
    assert PT.GAP < gap <= PT.RHYTHM_MAX + 1e-6                           # a larger, capped gap between the parts
    assert th <= PT.measure(tp, tw, "boxed", tp["_bump"])[1] + 1e-6      # the text card fits its text: no air in it
    items = sp["items"]
    assert PT.stats_plan(items, sw, True, up=3)["vsize"] > PT.STAT_VALUE_SIZES[0]   # figures may pass 40 pt
    assert PT.stats_plan(items, sw, True, up=sp["_bump"])["total"] > PT.stats_plan(items, sw, True)["total"]
    assert (sy + sh - top) / (bottom - top) >= 0.75                       # the page no longer ends at its middle


def test_read_density_keeps_its_type_limit():
    R.set_density("read")
    try:
        parts = [{"type": "text", "lead": "One short line.", "bullets": ["A", "B"]}]
        placed, _, _ = PT._compose_one(parts, "full", "editorial", R.BODY_Y, R.BODY_B)
        assert placed[0][2].get("_bump", 0) <= PT.MAX_BUMP["read"]
    finally:
        R.set_density("balanced")


# ------------------------------------------------------------------ table_focus and benchmark use the body
def test_short_full_width_table_grows_its_type_and_rhythm(tmp_path):
    s = {"archetype": "table_focus", "title": "Precedents cluster at $4.9-9.0B, far below a full takeout",
         "subtitle": "Selected AI and data-centre deals, 2020-2025",
         "table": {"columns": ["Target", "Acquirer", "Value", "Structure", "Status"], "size": 12,
                   "rows": [["Core Scientific", "CoreWeave", "~$9B", "All-stock", "Rejected"],
                            ["ZT Systems", "AMD", "$4.9B", "Cash and stock", "Completed"],
                            ["Mellanox", "NVIDIA", "$6.9B", "Cash", "Completed"]]},
         "takeaway": {"text": "Full takeouts sold for $4.9-9.0B"},
         "bullets": [{"text": "AMD paid $4.9B for ZT Systems"}, {"text": "NVIDIA paid $6.9B for Mellanox"}],
         "source": SRC}
    out = tmp_path / "t.pptx"
    R.render([s], str(out), fix_labels=False)
    sh = _shapes(out)
    tbl = sh["Table"]
    size = tbl.table.cell(1, 0).text_frame.paragraphs[0].runs[0].font.size.pt
    assert size > R.T["cell"]                               # the writer's 12 pt is where it starts, not a cap
    panel = next(v for k, v in sh.items() if k.startswith("ov_Panel"))
    gap = _in(panel.top) - _in(tbl.top + tbl.height)
    assert 0.19 <= gap <= R.RHYTHM_MAX + 0.01
    assert _in(panel.top + panel.height) > R.BODY_Y + 0.7 * (R.BODY_B - R.BODY_Y)   # thin: the rest is substance


def test_short_table_goes_full_width_when_beside_its_panel_it_would_end_high(tmp_path):
    s = {"archetype": "table_focus", "title": "NVIDIA offers a 35% premium and assumes ~$47B of debt",
         "table": {"columns": ["Term", "Value", "Basis"], "highlight_rows": [0],
                   "rows": [["Offer price", "$65.37", "35% premium"], ["Mix", "50/50 cash and stock", "At $180.40"],
                            ["Debt", "~$47B", "Consolidated"]]},
         "takeaway": {"text": "The premium sets the price"}, "bullets": [{"text": "$65.37 is 35% above $48.42"}],
         "source": SRC}
    out = tmp_path / "s.pptx"
    R.render([s], str(out), fix_labels=False)
    sh = _shapes(out)
    assert _in(sh["Table"].width) > 12                       # full width, not a short table in the left two thirds
    panel = next(v for k, v in sh.items() if k.startswith("ov_Panel"))
    assert _in(panel.top) > _in(sh["Table"].top + sh["Table"].height)


def test_thin_benchmark_gives_the_room_to_the_bars_first(tmp_path):
    s = {"archetype": "benchmark", "title": "CoreWeave trades at ~6x forward revenue, below Nebius and IREN",
         "benchmark": {"companies": ["CoreWeave", "Nebius", "IREN"], "panels": [
             {"title": "EV / NTM revenue (x)", "unit": "x", "values": [6.0, 8.9, 9.0], "median": 8.9}],
             "highlight": [0]},
         "takeaway": {"text": "CoreWeave's multiple is ~3 points below its peers"},
         "bullets": [{"text": "Nebius trades at 8.9x"}], "source": SRC}
    out = tmp_path / "b.pptx"
    R.render([s], str(out), fix_labels=False)
    sh = _shapes(out)
    bars = sh["ov_Bench1"]
    assert _in(bars.height) > 4 * 0.42 + 0.01                # rows grew past the old 0.42in cap
    assert _in(bars.height) <= 4 * R.BENCH_ROW_MAX + 0.01    # ... but stay bars, not slabs
    panel = next(v for k, v in sh.items() if k.startswith("ov_Panel"))
    gap = _in(panel.top) - _in(bars.top + bars.height)
    assert 0.24 <= gap <= R.RHYTHM_MAX + 0.01


# ------------------------------------------------------------------ table gaps
def test_deal_terms_never_show_value_na():
    s = {"archetype": "table_focus", "title": "Terms",
         "table": {"columns": ["Term", "Value", "Basis"], "highlight_rows": [3],
                   "rows": [["Offer price", "$65.37", "35% premium"],
                            ["Shares acquired", "[GAP: shares]", "Only shares not owned"],
                            ["Equity value", "[GAP: equity value]", "Shares at $65.37"],
                            ["Debt assumed", "~$47B", "Consolidated"]]}}
    n = R.normalize(s)
    rows = n["table"]["rows"]
    assert [r[0] for r in rows] == ["Offer price", "Debt assumed"]
    assert not any("N/A" in c for r in rows for c in r)
    assert n["table"]["highlight_rows"] == [1]                       # the focus row follows its row
    assert "Shares acquired" in n["notes"] and "Equity value" in n["notes"]
    assert not any("N/A" in f for f in n.get("footnotes") or [])      # nothing left to footnote


def test_mostly_gap_column_goes_with_a_footnote_that_keeps_its_values():
    s = {"archetype": "table_focus", "title": "Precedents",
         "table": {"columns": ["Target", "Value", "Premium", "Status"], "col_widths": [3, 2, 2, 2],
                   "rows": [["Core Scientific", "~$9B", "66%", "Rejected"], ["ZT Systems", "$4.9B", "[GAP]", "Done"],
                            ["Mellanox", "$6.9B", "[GAP: premium]", "Done"], ["Groq", "[GAP]", "[GAP]", "[GAP]"]]}}
    n = R.normalize(s)
    t = n["table"]
    assert t["columns"] == ["Target", "Value", "Status"] and t["col_widths"] == [3, 2, 2]
    assert [r[0] for r in t["rows"]] == ["Core Scientific", "ZT Systems", "Mellanox"]   # an all-gap row goes
    assert any(f.startswith("Premium: 66% for Core Scientific; not available") for f in n["footnotes"])
    assert "Groq" in n["notes"] and "Premium" in n["notes"]


def test_explicit_dashes_and_grid_tables():
    s = {"archetype": "table_focus", "title": "Options",
         "table": {"columns": ["Option", "NPV", "IRR"], "rows": [["A", "140", "14%"], ["B", "0", "–"], ["C", "60", "n/a"]]}}
    n = R.normalize(s)
    assert len(n["table"]["rows"]) == 3 and not n.get("footnotes")     # a dash is a nil, not a gap
    g = {"archetype": "grid", "title": "Deal", "parts": [{"type": "table", "heading": "Terms", "table": {
        "columns": ["Term", "Value"], "rows": [["Offer", "$65.37"], ["Equity value", "[GAP: equity]"]]}}]}
    n = R.normalize(g)
    assert n["parts"][0]["table"]["rows"] == [["Offer", "$65.37"]]


# ------------------------------------------------------------------ executive summary
def test_decision_band_is_dropped_when_it_repeats_the_title(tmp_path):
    base = {"archetype": "executive_summary", "title": "Buy CoreWeave's remaining shares at $65.37 per share",
            "points": [{"lead": "The backlog is the prize", "bullets": ["$132.5B at 30 Sep"]},
                       {"lead": "The premium is defensible", "bullets": ["35% above $48.42"]}], "source": SRC}
    out = tmp_path / "e.pptx"
    R.render([dict(base, decision="Decision: buy CoreWeave's remaining shares at $65.37")], str(out), fix_labels=False)
    assert "ov_Decision" not in _shapes(out)
    assert "repeats the title" in Presentation(str(out)).slides[0].notes_slide.notes_text_frame.text
    R.render([dict(base, decision="Decision today: approve a $33.6B offer and antitrust pre-filing by 1 Dec")],
             str(out), fix_labels=False)
    assert "ov_Decision" in _shapes(out)
    assert R.repeats("Approve the deal now", "approve THE deal, now!") and not R.repeats("", "x")


def test_executive_summary_prompt_asks_for_the_decision_today():
    from engines.forge.build import arch_help
    for mode in ("present", "balanced", "read"):
        assert "Decision today:" in arch_help(mode) and "must NOT restate the title" in arch_help(mode)


def test_long_subtitle_keeps_one_line_under_a_two_line_title(tmp_path):
    s = {"archetype": "column_trend", "title": "The deal lowers EPS by ~1-2% in year 1, then raises it by year 2 with "
         "$1.0B of synergies", "subtitle": "Pro forma diluted EPS ($ per share): NVIDIA standalone, year 1 (50% "
         "synergies), year 2 (100% synergies) and the run-rate case", "chart": {"kind": "column",
         "categories": ["Standalone", "Year 1", "Year 2"], "series": [{"name": "EPS", "values": [2.46, 2.42, 2.55]}]},
         "takeaway": {"text": "Year 2 is accretive"}, "bullets": ["Synergies reach 100% in year 2"], "source": SRC}
    out = tmp_path / "s.pptx"
    R.render([s], str(out), fix_labels=False)
    sub = _shapes(out)["role_subtitle_Subtitle"].text_frame.text
    assert sub == "Pro forma diluted EPS ($ per share)"
    assert "subtitle shortened" in Presentation(str(out)).slides[0].notes_slide.notes_text_frame.text


# ------------------------------------------------------------------ build: substance, count, verdict
def test_thin_slide_hint_lists_the_facts_the_deck_has_not_used():
    from engines.forge.build import substance_hint, used_fact_ids
    slides = [{"title": "x", "title_prov": ["F1"], "bullets": [{"text": "a", "prov": ["F2"]}],
               "parts": [{"type": "stats", "items": [{"value": "1", "prov": ["S1C1"]}]}],
               "calcs": [{"id": "S1C1", "from": ["F3"]}]}]
    assert used_fact_ids(slides) == {"F1", "F2", "F3", "S1C1"}
    facts = [{"id": f"F{i}", "text": t} for i, t in enumerate(["a 1", "b 2", "c 3", "Capex was $37-39B", "Plan text"], 1)]
    hint = substance_hint(slides, facts)
    assert "F4: Capex was $37-39B" in hint and "F5: Plan text" in hint and "F1:" not in hint
    assert hint.index("F4:") < hint.index("F5:")                      # numbers first: they make charts and stats
    assert "stats" in hint and "chart" in hint and "callout" in hint
    assert "already used" in substance_hint(slides, facts[:3])


def test_fit_count_keeps_the_bookends_and_drops_thin_slides_first():
    from engines.forge.build import fit_count
    plan = ([{"node": "0", "archetype": "cover", "title": "Cover"},
             {"node": "1", "archetype": "executive_summary", "title": "Answer"}]
            + [{"node": f"2.{i}", "archetype": "bar_takeaway", "title": f"S{i}", "evidence": ["F1"] * (i + 1)}
               for i in range(9)]
            + [{"node": "3.0", "archetype": "section_divider", "title": "Div"},
               {"node": "9", "archetype": "closing", "title": "Next"}])
    out, cut = fit_count(plan, 11)
    assert len(out) == 11 and cut == ["Div", "S0"]
    assert [x["archetype"] for x in (out[0], out[1], out[-1])] == ["cover", "executive_summary", "closing"]
    assert fit_count(plan[:3], 11) == (plan[:3], [])


def test_final_review_not_run_falls_back_to_the_first_verdict():
    from engines.forge.build import review_verdict
    first = {"status": "ok", "slides": [{"slide": 2, "verdict": "ship", "score": 8, "complaints": []},
                                        {"slide": 3, "verdict": "fix", "score": 6, "complaints": []}]}
    final = {"status": "not_run", "slides": [], "not_run": [2, 3],
             "errors": ["slides 2-3 m: RuntimeError: meshpool HTTP 401: Invalid or missing API key"]}
    v = review_verdict([first, final])
    assert v["score"] == 7.0 and v["final_status"] == "not_run"
    assert v["note"].startswith("final partner review not run: gateway refused the key")
    v = review_verdict([final])
    assert v["score"] is None and v["note"] == "partner review not run: gateway refused the key"
    assert review_verdict([first])["score"] == 7.0 and "note" not in review_verdict([first])
    assert review_verdict([]) is None


class _Stub:
    def __init__(self, fn):
        self.fn = fn

    def chat(self, *, model, messages, **kw):
        return {"choices": [{"message": {"content": json.dumps(self.fn(messages))}}]}


def test_build_holds_the_slide_count_and_sends_thin_slides_the_unused_facts(tmp_path):
    from engines.forge.build import build
    seen = {"fix": []}

    def model(messages):
        sys_, user = messages[0]["content"], messages[1]["content"]
        if "engagement manager" in sys_:
            n = int(re.search(r"Exactly (\d+) slides", sys_).group(1))
            body = [{"node": f"{i + 2}.1", "section": f"Part {i % 3}", "title": f"North revenue grew 18% in part {i}",
                     "archetype": "bar_takeaway", "evidence": []} for i in range(n + 1)]       # two more than asked
            return {"slides": [{"node": "0", "title": "Plan", "archetype": "cover"},
                               {"node": "1", "title": "Grow the North", "archetype": "executive_summary"}] + body
                    + [{"node": "9", "title": "Next steps to grow", "archetype": "closing"}]}
        if "ONE consulting slide" in sys_:
            x = json.loads(user.split("This slide: ", 1)[1].split("\n\nFacts", 1)[0])
            if x["archetype"] == "cover":
                return dict(x, subtitle="Board")
            if x["archetype"] == "executive_summary":
                return dict(x, points=[{"lead": "North leads", "bullets": ["$420m in 2025"]}])
            if x["archetype"] == "closing":
                return dict(x, steps=[{"action": "Open stores", "detail": "Ten stores in the North"}])
            return dict(x, chart={"kind": "bar", "categories": ["North", "South", "East"],
                                  "series": [{"name": "Revenue", "values": [420, 310, 220]}], "unit": "$m"},
                        takeaway={"text": "North carries the growth"}, bullets=[{"text": "North grew 18%"}],
                        source={"text": "Company accounts"})
        if "Fix this one consulting slide" in sys_:
            seen["fix"].append(user)
            return json.loads(user.split("Slide:\n", 1)[1].split("\n\nFacts", 1)[0])
        return {"edits": []}
    import engines.forge.build as B
    old = (B.MODEL, B.STORY_MODEL)
    B.MODEL = B.STORY_MODEL = "main"
    try:
        res = build("Northwind revenue was $420m in the North, $310m in the South and $220m in the East in 2025; "
                    "North grew 18%. Capex was $90m in 2025.", str(tmp_path), 10, client=_Stub(model), task_id="t")
    finally:
        B.MODEL, B.STORY_MODEL = old
    assert len(Presentation(res["pptx"]).slides) == 10                   # agenda and summary inside the count
    story = json.loads((tmp_path / "storyline.json").read_text())
    assert len(story["cut_to_fit_count"]) == 4                           # 13 planned -> 9, + agenda = 10
    thin = [u for u in seen["fix"] if "[fill]" in u]
    assert thin and all("Unused facts:" in u for u in thin)


# ------------------------------------------------------------------ the thin check itself
@lo
def test_p20_thin_flags_a_page_that_ends_high(tmp_path):
    from engines.forge.validate_pptx import validate
    thin = {"archetype": "argument_only", "title": "Two figures and nothing else",
            "blocks": [{"text": "**$2.46** standalone EPS", "prov": ["F1"]}, {"text": "**$1.0B** synergies",
                                                                              "prov": ["F2"]}], "source": SRC}
    out = tmp_path / "thin.pptx"
    R.render([thin], str(out), fix_labels=False)
    res = validate(str(out), render=True, density="balanced")
    msgs = [f for f in res["failures"] if f["rule"] == "P20" and "thin" in f["message"] and f["status"] == "fail"]
    assert msgs and msgs[0]["fix_scope"] == "content" and res["stats"]["bottom_blank_by_slide"][0] > 0.15
