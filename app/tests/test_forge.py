"""Forge validators: a template-built deck and a disciplined workbook pass; decks and workbooks with the known
AI-output defects fail on the right rule."""
import json
import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

from engines.forge.common import find_soffice, load_rules, positive_axis_ids  # noqa: E402
from engines.forge import ooxml_check as OX  # noqa: E402
from engines.forge import validate_pptx as VP, validate_xlsx as VX  # noqa: E402

HAS_LO = find_soffice() is not None
HAS_XSD = OX._schema_dir() is not None


# ------------------------------------------------------------------ fixtures
def _good_deck(path):
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches, Pt
    prs = Presentation()                                   # default template: 10in x 7.5in, Calibri theme fonts
    s1 = prs.slides.add_slide(prs.slide_layouts[0])        # Title Slide
    s1.shapes.title.text = "Pricing can fund the growth plan"
    s1.placeholders[1].text = "Board update"
    s1._element.find("{http://schemas.openxmlformats.org/presentationml/2006/main}cSld").set("name", "arch:cover node:0")
    s2 = prs.slides.add_slide(prs.slide_layouts[1])        # Title and Content
    s2.shapes.title.text = "Revenue grew 20% as pricing offset lower volume"
    s2.shapes.title.text_frame.paragraphs[0].runs[0].font.size = Pt(28)
    col = (9.0 - 0.25 * 11) / 12
    edge = lambda k: 0.5 + k * (col + 0.25)                # left edge of grid column k (0-based)
    body = s2.placeholders[1]
    body.left, body.width = Inches(edge(6)), Inches(9.5 - edge(6))
    body.top, body.height = Inches(1.6), Inches(4.6)
    tf = body.text_frame
    tf.text = "Price rose in every region"
    for t in ("Volume fell in two regions", "Margin held steady"):
        p = tf.add_paragraph()
        p.text = t
    for p in tf.paragraphs:
        p.runs[0].font.size = Pt(20)
    cd = CategoryChartData()
    cd.categories = ["2024", "2025"]
    cd.add_series("Revenue ($M)", (100, 120))
    gf = s2.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.5), Inches(1.6),
                             Inches(edge(5) + col - 0.5), Inches(4.6), cd)
    positive_axis_ids(gf.chart)
    gf._element.find(".//{http://schemas.openxmlformats.org/presentationml/2006/main}cNvPr").set(
        "descr", "Column chart: revenue rose from $100M in 2024 to $120M in 2025")
    src = s2.shapes.add_textbox(Inches(0.5), Inches(6.6), Inches(9.0), Inches(0.4))
    src.text_frame.text = "Source: company accounts 2024-2025 (F1, F2)"
    src.text_frame.paragraphs[0].runs[0].font.size = Pt(10)
    s2._element.find("{http://schemas.openxmlformats.org/presentationml/2006/main}cSld").set("name", "arch:bar_takeaway node:1")
    prs.save(path)
    return {"slide_count": 2}, {"slides": {"2": [{"text": "20%", "prov": "F3"}]}}, {"nodes": [{"id": "0"}, {"id": "1"}]}


def _bad_deck(path):
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[6])         # Blank: no title placeholder
    t = s.shapes.add_textbox(Inches(0.3), Inches(0.2), Inches(9), Inches(0.8))
    t.text_frame.text = "Revenue overview"
    t.text_frame.paragraphs[0].runs[0].font.size = Pt(28)
    t.text_frame.paragraphs[0].runs[0].font.color.rgb = RGBColor(0x12, 0x34, 0x56)
    b = s.shapes.add_textbox(Inches(0.5), Inches(1.2), Inches(4), Inches(1.0))
    b.text_frame.word_wrap = True
    from pptx.enum.text import MSO_AUTO_SIZE
    b.text_frame.auto_size = MSO_AUTO_SIZE.NONE
    b.text_frame.text = "Revenue reached 431 million in the year, driven by strong demand " * 3
    for k in range(7):
        p = b.text_frame.add_paragraph()
        p.text = f"Point {k} about the market with 3{k}% share \U0001F680"
        p.level = 2 if k == 3 else 0
        p.runs[0].font.name = "Comic Sans MS"
    s.shapes.add_picture(str(ROOT / "engines/slides/examples/sample.png"), Inches(5), Inches(1.2), Inches(4), Inches(3)).name = "Chart 1"
    prs.save(path)


def _good_wb(path):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.workbook.defined_name import DefinedName
    blue, green = Font(color="0000FF"), Font(color="008000")
    wb = Workbook()
    cover = wb.active
    cover.title = "Cover"
    cover["A1"], cover["A2"], cover["B2"] = "Revenue model", "All checks pass", "=AND(Checks!B2:B3)"
    inp = wb.create_sheet("Inputs")
    inp.append(["Driver", "2025", "2026", "2027", "2028"])
    inp.append(["Units sold (count)", 100, 110, 120, 130])
    inp.append(["Price ($)", 10, 10.5, 11, 11.5])
    for r in (2, 3):
        for c in range(2, 6):
            inp.cell(r, c).font = blue
    inp.freeze_panes = "B2"
    calc = wb.create_sheet("Calc")
    calc.append(["Line", "2025", "2026", "2027", "2028"])
    calc.append(["Revenue ($)"] + [f"=Inputs!{c}2*Inputs!{c}3" for c in "BCDE"])
    calc.append(["Growth (%)", "=0"] + [f"={c}2/{p}2-1" for p, c in zip("BCD", "CDE")])
    for c in range(2, 6):
        calc.cell(2, c).font = green
        calc.cell(2, c).number_format = '#,##0;(#,##0)'
        calc.cell(3, c).number_format = '0.0%'
    calc.freeze_panes = "B2"
    out = wb.create_sheet("Outputs")
    out.append(["Summary", "Value"])
    out.append(["Total revenue ($)", "=SUM(Calc!B2:E2)"])
    out["B2"].font = green
    out["B2"].number_format = '#,##0;(#,##0)'
    chk = wb.create_sheet("Checks")
    chk.append(["Check", "OK"])
    chk.append(["Revenue positive in every period", "=MIN(Calc!B2:E2)>0"])
    chk.append(["Summary ties to calc", "=ABS(Outputs!B2-SUM(Calc!B2:E2))<1"])
    chk["B2"].font = green
    chk["B3"].font = green
    wb.defined_names["MasterCheck"] = DefinedName("MasterCheck", attr_text="Cover!$B$2")
    wb.save(path)
    return {"checks": [{"cell": "Outputs!B2", "expected": 100 * 10 + 110 * 10.5 + 120 * 11 + 130 * 11.5, "label": "total"}]}


def _bad_wb(path):
    from openpyxl import Workbook
    wb = Workbook()
    c = wb.active
    c.title = "Calc"
    c.append(["Line", "2025", "2026", "2027", "2028"])
    c.append(["Revenue", 100, "=B2*1.05", "=C2*1.05", "=D2+7"])
    c.append(["Margin", "=B2/0", "=missing_rate*C2", "=D3+E3", "=D3"])
    c.append(["Balancing plug", 5, 5, 5, 5])
    c.merge_cells("B4:C4")
    wb.save(path)


# ------------------------------------------------------------------ deck
@pytest.fixture(scope="module")
def decks(tmp_path_factory):
    d = tmp_path_factory.mktemp("decks")
    good, bad = d / "good.pptx", d / "bad.pptx"
    side = _good_deck(good)
    _bad_deck(bad)
    return good, bad, side


def test_good_deck_passes_content_rules(decks):
    good, _, (brief, prov, story) = decks
    r = VP.validate(str(good), brief=brief, provenance=prov, storyline=story, render=HAS_LO)
    fails = {k: v for k, v in r["rules"].items() if v == "fail"}
    detail = [f["message"] for f in r["failures"] if f["status"] == "fail"]
    # the default python-pptx template hardcodes nothing on slides; P6 must pass with charts included
    assert not fails, (fails, detail)
    assert r["stats"]["native_charts"] == 1 and r["stats"]["charts_with_embedded_workbook"] == 1


def test_bad_deck_fails_each_defect(decks):
    _, bad, _ = decks
    r = VP.validate(str(bad), brief={"slide_count": 3}, provenance={"slides": {}}, render=HAS_LO)
    st = r["rules"]
    if HAS_LO:
        assert st["P1"] == "fail"                                # rendered text spills outside its box
    for rule in ("P2", "P4", "P5", "P6", "P7", "P9", "P10", "P11", "P13", "P15", "P16", "P18"):
        assert st[rule] == "fail", (rule, st[rule], [f["message"] for f in r["failures"] if f["rule"] == rule])
    msgs = " ".join(f["message"] for f in r["failures"])
    assert "overflows by" in msgs and "cut to" in msgs           # repair message names the cut
    assert "topic label" in msgs


def test_failures_are_machine_readable(decks):
    _, bad, _ = decks
    r = VP.validate(str(bad), render=False)
    for f in r["failures"]:
        assert {"rule", "check", "status", "severity", "fix_scope", "message"} <= f.keys()
        assert f["fix_scope"] in ("content", "renderer", "template")
    json.dumps({k: v for k, v in r.items() if k != "measurement"}, default=str)


def test_verb_heuristic_knows_chart_verbs():
    for t in ("Deal value jumped 36% to $4.8T in 2025E", "Margins slipped as costs surged", "Asia overtook Europe"):
        assert VP._has_verb(t), t
    assert not VP._has_verb("Deal value by region, 2020-2025")


def test_verb_heuristic():
    assert VP._has_verb("Revenue grew 20% as pricing offset lower volume")
    assert VP._has_verb("Margins are under pressure in Europe")
    assert not VP._has_verb("Revenue overview")
    assert not VP._has_verb("Market landscape 2025")


def test_rules_override_tightens():
    r = load_rules({"pptx": {"density": {"max_bullets": 2}}})
    assert r["pptx"]["density"]["max_bullets"] == 2 and r["pptx"]["density"]["max_body_words"] == 80


# ------------------------------------------------------------------ workbook
@pytest.mark.skipif(not HAS_LO, reason="LibreOffice needed for recalculation")
def test_good_workbook_passes(tmp_path):
    p = tmp_path / "good.xlsx"
    rc = _good_wb(p)
    r = VX.validate(str(p), recompute=rc)
    fails = {k: v for k, v in r["rules"].items() if v == "fail"}
    assert not fails, (fails, [f["message"] for f in r["failures"] if f["status"] == "fail"])
    assert r["rules"]["X7"] == "pass" and r["rules"]["X12"] == "pass"


@pytest.mark.skipif(not HAS_LO, reason="LibreOffice needed for recalculation")
def test_bad_workbook_fails_each_defect(tmp_path):
    p = tmp_path / "bad.xlsx"
    _bad_wb(p)
    r = VX.validate(str(p))
    st = r["rules"]
    for rule in ("X1", "X2", "X3", "X5", "X6", "X8", "X10", "X12"):
        assert st[rule] == "fail", (rule, st[rule], [f["message"] for f in r["failures"] if f["rule"] == rule])
    assert st["X7"] == "not_run"                                 # never reported as a pass


def test_r1c1_and_cycles():
    assert VX._r1c1("=B2*Inputs!$C$3", 2, 3) == VX._r1c1("=C2*Inputs!$C$3", 2, 4)
    assert VX._tarjan({"a": {"b"}, "b": {"a"}, "c": {"a"}}) == [["b", "a"]] or len(VX._tarjan({"a": {"b"}, "b": {"a"}})) == 1


def test_p19_catches_inconsistent_titles(tmp_path):
    from pptx import Presentation
    from pptx.util import Inches, Pt
    prs = Presentation()
    for k, size in enumerate((28, 28, 22, 28)):
        s = prs.slides.add_slide(prs.slide_layouts[5])        # Title Only
        s.shapes.title.text = f"Costs fell {k + 2}% after the plant closed"
        s.shapes.title.text_frame.paragraphs[0].runs[0].font.size = Pt(size)
        if k == 3:
            s.shapes.title.left = Inches(1.2)
    p = tmp_path / "c.pptx"
    prs.save(p)
    r = VP.validate(str(p), render=False)
    msgs = [f["message"] for f in r["failures"] if f["rule"] == "P19"]
    assert r["rules"]["P19"] == "fail"
    assert any("Titles use 2 sizes" in m for m in msgs) and any("Title left edge" in m for m in msgs)


# ------------------------------------------------------------------ P0: opens in Office without repair
def _rewrite(src, dst, part, fn):
    import zipfile
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for item in zi.infolist():
            data = zi.read(item.filename)
            zo.writestr(item, fn(data) if item.filename == part else data)


def test_p0_catches_broken_relationship_and_duplicate_ids(decks, tmp_path):
    good, _, _ = decks
    assert OX.check(str(good))["problems"] == [] or HAS_XSD
    bad = tmp_path / "broken.pptx"
    _rewrite(good, bad, "ppt/slides/_rels/slide2.xml.rels",
             lambda b: b.replace(b"../charts/chart", b"../charts/missing"))
    r = VP.validate(str(bad), render=False)
    assert r["rules"]["P0"] == "fail"
    assert any(f["check"] == "broken_relationship" for f in r["failures"] if f["rule"] == "P0")
    dup = tmp_path / "dup.pptx"
    _rewrite(good, dup, "ppt/slides/slide2.xml", lambda b: b.replace(b'id="3"', b'id="2"'))
    assert any(p["check"] == "duplicate_shape_id" for p in OX.check(str(dup))["problems"])


@pytest.mark.skipif(not HAS_XSD, reason="ECMA-376 schemas not installed (ooxml_check --install-schemas)")
def test_p0_schema_catches_what_powerpoint_repairs(decks, tmp_path):
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches
    good, _, _ = decks
    assert OX.check(str(good))["ok"], OX.check(str(good))["problems"]
    prs = Presentation()                                   # a raw python-pptx chart: negative axis ids
    s = prs.slides.add_slide(prs.slide_layouts[6])
    cd = CategoryChartData()
    cd.categories = ["a", "b"]
    cd.add_series("s", (1, 2))
    s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(1), Inches(4), Inches(3), cd)
    raw = tmp_path / "raw.pptx"
    prs.save(raw)
    assert any("axId" in p["detail"] for p in OX.check(str(raw))["problems"])
    order = tmp_path / "order.pptx"                        # child elements out of schema order
    _rewrite(good, order, "ppt/slides/slide2.xml", lambda b: b.replace(b"<p:spTree>", b"<p:spTree><p:extLst/>", 1))
    assert VP.validate(str(order), render=False)["rules"]["P0"] == "fail"


def test_forge_render_opens_cleanly(tmp_path):
    from engines.forge.render import render
    slides = [
        {"archetype": "cover", "title": "Regional growth plan", "subtitle": "Board review"},
        {"archetype": "chart_takeaway", "title": "Revenue grew 20% as pricing offset lower volume",
         "chart": {"kind": "column", "categories": ["2023", "2024", "2025"],
                   "series": [{"name": "Revenue ($M)", "values": [100, 110, 120]}]},
         "takeaways": ["Price rose in every region", "Volume fell 4%"], "source": "Company accounts"},
    ]
    out = tmp_path / "d.pptx"
    render(slides, str(out))
    r = OX.check(str(out))
    assert r["ok"], r["problems"]
