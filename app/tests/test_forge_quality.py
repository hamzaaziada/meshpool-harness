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


def test_variety_sends_back_look_alike_pages_and_text_led_neighbours_alternate(tmp_path):
    """'All the slides look the exact same': neighbours with one layout, or a deck mostly of words, go back to the
    writer; two text-led slides in a row are drawn in different forms (columns, then rows)."""
    from pptx import Presentation
    from engines.forge.build import variety_checks
    from engines.forge.render import render
    arg = lambda t: {"archetype": "argument_only", "title": t, "blocks": [  # noqa: E731
        {"text": "**Debt** is rising", "bullets": [{"text": "Debt nears $47B"}]},
        {"text": "**Revenue** is growing", "bullets": [{"text": "Revenue grew 168%"}]},
        {"text": "**Risk** is regulatory", "bullets": [{"text": "Antitrust review is likely"}]}]}
    deck = [{"archetype": "cover", "title": "Deck"}, arg("Debt rises as revenue grows"), arg("Risks are regulatory"),
            arg("The board should act now"), {"archetype": "closing", "title": "Next steps", "steps": []}]
    v = variety_checks(deck)
    assert 3 in v and 4 in v and 2 not in v and any("same layout" in m for m in v[3])
    out = tmp_path / "v.pptx"
    render(deck[1:3], str(out), carry=False, density="balanced")
    tops = []
    for sl in Presentation(str(out)).slides:
        ys = sorted({round(sh.top / 914400, 1) for sh in sl.shapes if sh.name.startswith("ov_IconRing")})
        tops.append(len(ys))
    assert tops[0] == 1 and tops[1] > 1           # columns (icons on one line), then rows (icons stacked)


def test_libreoffice_draws_segoe_ui_with_the_bundled_selawik(tmp_path):
    """Without Segoe UI installed, LibreOffice fell back to a wider face and the render check flagged overflows
    PowerPoint never shows; the throwaway profile now carries Selawik and a substitution for Segoe UI."""
    import re as _re
    import pytest as _pt
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from engines.forge.common import find_soffice, soffice_convert
    if not find_soffice():
        _pt.skip("LibreOffice not installed")
    p = Presentation()
    tb = p.slides.add_slide(p.slide_layouts[6]).shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1))
    r = tb.text_frame.paragraphs[0].add_run()
    r.text, r.font.name, r.font.size = "Segoe UI text", "Segoe UI", Pt(28)
    p.save(tmp_path / "t.pptx")
    pdf = soffice_convert(str(tmp_path / "t.pptx"), "pdf", str(tmp_path))
    fonts = set(_re.findall(rb"/BaseFont\s*/([A-Za-z+\-]+)", pdf.read_bytes()))
    assert any(b"Selawik" in f for f in fonts), fonts


def test_options_let_a_recommendation_be_sized_without_inventing_facts():
    """Reviewers kept failing decks that 'never size anything': a buyback size is a choice, not a fact. Options are
    declared, labelled, and usable in calcs with the facts."""
    from engines.forge.build import number_checks, verify_calcs
    facts = [{"id": "F5", "text": "Cash and cash equivalents $20.21B at 2026-08-30"}]
    rules = load_rules()
    s = {"archetype": "argument_only", "title": "A $5B buyback leaves $15.21B of cash", "title_prov": ["O1", "C1"],
         "options": [{"id": "O1", "value": "$5B", "label": "buyback size proposed"}],
         "calcs": [{"id": "C1", "value": "$15.21B", "formula": "20.21 - 5", "from": ["F5", "O1"]}],
         "bullets": [{"text": "Option: a **$5B** buyback leaves **$15.21B** of cash.", "prov": ["O1", "C1"]}]}
    ok, bad = verify_calcs([s], facts, rules)
    assert not bad and [f["id"] for f in ok[1]] == ["S1O1", "S1C1"] and ok[1][0]["option"]
    assert number_checks([s], facts + ok[1], rules) == {}
    s2 = {"archetype": "argument_only", "title": "t", "calcs": [{"id": "C1", "value": "$15.21B",
          "formula": "20.21 - 5", "from": ["F5"]}]}
    assert verify_calcs([s2], facts, rules)[1]          # without the declared option, 5 is an invented number


def test_prose_checks_flag_internal_ids_in_visible_text():
    from engines.forge.build import prose_checks
    s = {"archetype": "executive_summary", "title": "Return $8B to owners",
         "items": [{"lead": "The size is a board choice (options S201, S2O1)", "text": "Cash covers it (F5)"}]}
    got = " ".join(prose_checks([s]).get(1, []))
    assert "S2O1" in got and "F5" in got and "internal reference" in got
    ok = {"archetype": "executive_summary", "title": "Return $8B to owners", "items": [{"lead": "FY2026 cash of $20B"}]}
    assert not prose_checks([ok])


def test_tie_checks_flag_one_rate_with_two_values_across_slides():
    from engines.forge.build import tie_checks
    s2 = {"archetype": "executive_summary", "title": "Growth wins: 22.2% return on capital beats a 1.9% buyback yield",
          "items": [{"lead": "Reinvestment earns 22.2% after-tax return on capital"}]}
    s3 = {"archetype": "grid", "title": "Reinvestment returns 21.8% return on capital against 1.9% buyback yield"}
    got = tie_checks([{"archetype": "cover", "title": "x"}, s2, s3])
    assert list(got) == [3] and "21.8%" in got[3][0] and "slide 2" in got[3][0]
    same = {"archetype": "grid", "title": "Reinvestment earns 22% return on capital"}       # coarser, same value
    assert tie_checks([s2, same]) == {}
    other = {"archetype": "grid", "title": "Cash yield of 5.15% beats the 1.9% buyback yield"}
    assert tie_checks([s2, other]) == {}


def test_p21_language_lint_flags_machine_written_words(tmp_path):
    from pptx import Presentation
    from pptx.util import Inches
    from engines.forge.common import load_rules
    from engines.forge.validate_pptx import validate
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "We leverage synergies to unlock growth — a game-changer!"
    tb = s.shapes.add_textbox(Inches(1), Inches(2), Inches(6), Inches(1))
    tb.text_frame.text = "REVENUE GROWTH ACCELERATES SHARPLY in 2025; EBITDA margin holds at 21%."
    prs.save(tmp_path / "x.pptx")
    got = {(f["rule"], f["check"]) for f in validate(str(tmp_path / "x.pptx"), load_rules(), render=False)["failures"]}
    assert {("P21", "banned_phrase"), ("P21", "em_dash"), ("P21", "exclamation"), ("P21", "all_caps")} <= got


def test_arabic_slides_read_right_to_left_and_mirror(tmp_path):
    from pptx import Presentation
    from engines.forge.common import is_rtl, text_width_in
    from engines.forge.render import render
    assert is_rtl("ارتفعت الإيرادات بنسبة 31% في 2025") and not is_rtl("Revenue grew 31% in 2025")
    assert 1.5 < text_width_in("ارتفعت الإيرادات بنسبة 31% في 2025", 14, None) < 4.5       # shaped, measured
    sl = [{"id": "S1", "node": "1.1", "archetype": "grid", "title": "ثلاث أولويات للإدارة في 2026 لدعم النمو",
           "title_prov": ["F1"], "parts": [{"type": "process", "heading": "الخطوات", "steps": [
               {"title": "توسيع الطاقة", "text": "إضافة 200 سرير"}, {"title": "خفض النفقات", "text": "400 مليون جنيه"},
               {"title": "رفع الهامش", "text": "تحسين الهامش"}]}], "source": {"text": "عرض المستثمرين 2025", "prov": ["F1"]}},
          {"id": "S2", "node": "1.2", "archetype": "grid", "title": "Three priorities for 2026 support growth",
           "title_prov": ["F1"], "parts": [{"type": "process", "heading": "Steps", "steps": [
               {"title": "Add capacity", "text": "200 beds"}, {"title": "Cut capex", "text": "EGP 400m"},
               {"title": "Lift margin", "text": "EBITDA margin"}]}], "source": {"text": "Investor deck 2025", "prov": ["F1"]}}]
    render(sl, str(tmp_path / "ar.pptx"), carry=False, density="balanced")
    prs = Presentation(str(tmp_path / "ar.pptx"))
    ar, en = prs.slides[0], prs.slides[1]
    assert 'rtl="1"' in ar._element.xml and 'lang="ar-SA"' in ar._element.xml and "المصدر: " in ar._element.xml
    assert 'rtl="1"' not in en._element.xml

    def step_x(slide, word):
        return next(sh.left for sh in slide.shapes if sh.has_text_frame and word in sh.text_frame.text)
    assert step_x(ar, "توسيع الطاقة") > step_x(ar, "رفع الهامش")          # step 1 on the right
    assert step_x(en, "Add capacity") < step_x(en, "Lift margin")
    assert 'flipH="1"' in ar._element.xml
