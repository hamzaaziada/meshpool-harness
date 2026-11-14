"""Forge validator regressions: no false failures (blank chart points, AlternateContent, number formats the renderer
records, whole-column refs, dynamic names) and no false passes (value checks without values, loose fact matching)."""
import sys
import warnings
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

from openpyxl import Workbook  # noqa: E402
from openpyxl.workbook.defined_name import DefinedName  # noqa: E402

from engines.forge import ooxml_check as OX  # noqa: E402
from engines.forge import validate_pptx as VP, validate_xlsx as VX  # noqa: E402
from engines.forge.common import load_rules  # noqa: E402


def _rewrite(src, dst, part, fn):
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for item in zi.infolist():
            data = zi.read(item.filename)
            zo.writestr(item, fn(data) if item.filename == part else data)


# ------------------------------------------------------------------ build.number_checks (pre-render P13)
def test_fact_numbers_match_within_rounding_not_across_scales():
    from engines.forge.build import number_checks
    rules = load_rules()
    facts = [{"id": "F1", "text": "Revenue $7.2m in 2025"}, {"id": "F2", "text": "Gross margin 34.6%"}]
    bad = lambda t, prov=("F1",): number_checks([{"title": "x", "bullets": [{"text": t, "prov": list(prov)}]}],  # noqa: E731
                                                facts, rules)
    assert not bad("Revenue reached $7.2m") and not bad("Revenue reached $7m") and not bad("about 7,200,000 dollars")
    assert bad("Revenue reached $45m") and bad("Revenue reached $7.2bn") and bad("Revenue reached $7.9m")
    assert not bad("Margin of 35%", ("F2",)) and bad("Margin of 37%", ("F2",))


# ------------------------------------------------------------------ pptx
def test_blank_chart_points_compare_by_index(tmp_path):
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Revenue grew 40% from 2020 to 2024 on pricing"
    cd = CategoryChartData()
    cd.categories = ["2020", "2021", "2022", "2023", "2024", "2025", "2026"]
    cd.add_series("Actual", [10, 11, 12, 13, 14, None, None])
    cd.add_series("Forecast", [None, None, None, None, 14, 15, 16])
    s.shapes.add_chart(XL_CHART_TYPE.LINE, Inches(0.5), Inches(1.5), Inches(8), Inches(5), cd)
    out = tmp_path / "gap.pptx"
    prs.save(out)
    r = VP.validate(str(out), render=False)
    assert not [f for f in r["failures"] if f["rule"] == "P2" and f["check"] == "series_cache_mismatch"]


def test_alternate_content_is_not_a_duplicate_id(tmp_path):
    from pptx import Presentation
    prs = Presentation()
    prs.slides.add_slide(prs.slide_layouts[6])
    src = tmp_path / "a.pptx"
    prs.save(src)
    sp = ('<p:sp><p:nvSpPr><p:cNvPr id="9" name="Box"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr/></p:sp>')
    ac = ('<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
          'xmlns:a14="http://schemas.microsoft.com/office/drawing/2010/main"><mc:Choice Requires="a14">' + sp +
          '</mc:Choice><mc:Fallback>' + sp + '</mc:Fallback></mc:AlternateContent>')
    out = tmp_path / "ac.pptx"
    _rewrite(src, out, "ppt/slides/slide1.xml", lambda d: d.replace(b"</p:spTree>", ac.encode() + b"</p:spTree>"))
    assert not [p for p in OX.check(str(out))["problems"] if p["check"] == "duplicate_shape_id"]
    dup = tmp_path / "dup.pptx"                            # a real duplicate is still caught
    _rewrite(src, dup, "ppt/slides/slide1.xml", lambda d: d.replace(b"</p:spTree>", (sp + sp).encode() + b"</p:spTree>"))
    assert [p for p in OX.check(str(dup))["problems"] if p["check"] == "duplicate_shape_id"]


def test_p13_reads_numbers_the_way_the_renderer_records_them(tmp_path):
    from engines.forge.render import render
    nums = ["450 stores", "12.5x", "3.2pp", "10-15%", "(12.5%)", "2.4bn"]
    s = {"archetype": "argument_only", "title": "The network is large and the returns hold up", "density": "read",
         "blocks": [{"text": "Scale", "bullets": [{"text": f"Metric {k}: {t}", "prov": ["F1"]}
                                                  for k, t in enumerate(nums)]}],
         "source": {"text": "Company accounts", "prov": ["F1"]}}
    out = tmp_path / "p13.pptx"
    r = render([s], str(out), fix_labels=False)
    import json
    prov = json.loads(Path(r["provenance"]).read_text())
    res = VP.validate(str(out), provenance=prov, render=False)
    bad = [f["message"] for f in res["failures"] if f["check"] == "unsourced_number"]
    assert not bad, bad


# ------------------------------------------------------------------ xlsx
def _wb_with_checks(path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Calc"
    ws.append(["Line ($)", "2025", "2026"])
    ws.append(["Revenue ($)", 100, "=B2*1.1"])
    ws.append(["Costs ($)", 40, 44])
    ws.append(["Profit ($)", "=B2+B3", "=C2+C3"])
    ws.append(["Tie-out vs reported", "=B4-60", "=C4-66"])
    chk = wb.create_sheet("Checks")
    chk.append(["Check", "OK"])
    chk.append(["Profit positive", "=Calc!B4>0"])
    chk.append(["All", "=AND(B2)"])
    wb.defined_names["MasterCheck"] = DefinedName("MasterCheck", attr_text="Checks!$B$3")
    wb.save(path)


def test_value_rules_not_run_without_values(tmp_path):
    p = tmp_path / "nv.xlsx"
    _wb_with_checks(p)                                     # openpyxl writes no cached values
    r = VX.validate(str(p), do_recalc=False)
    for rule in ("X4", "X12", "X14"):
        assert r["rules"][rule] == "not_run", (rule, [f["message"] for f in r["failures"] if f["rule"] == rule])


def test_r1c1_whole_columns_and_rows_are_relative():
    assert VX._r1c1("=SUM(C:C)", 2, 3) == VX._r1c1("=SUM(D:D)", 2, 4)
    assert VX._r1c1("=SUM(2:2)", 2, 3) == VX._r1c1("=SUM(3:3)", 3, 3)


def test_names_and_sheet_qualified_operands(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Model"
    ws.append(["Line ($)", "2025"])
    ws.append(["Revenue ($)", 100])
    ws.append(["Loss ($)", 5])
    ws.append(["Total ($)", "=Model!Rev+B3"])
    wb.create_sheet("Q's")
    dn = {"Rev": "Model!$B$2", "Dyn": "OFFSET(Model!$B$1,0,0,COUNTA(Model!$B:$B),1)",
          "Multi": "Model!$B$2,Model!$B$3", "Quoted": "'Q''s'!$A$1", "Gone": "Nowhere!$A$1"}
    for k, v in dn.items():
        wb.defined_names[k] = DefinedName(k, attr_text=v)
    p = tmp_path / "names.xlsx"
    wb.save(p)
    r = VX.validate(str(p))                                 # X14 resolves Model!Rev instead of crashing
    bad = [f["message"] for f in r["failures"] if f["check"] == "bad_name"]
    assert len(bad) == 1 and "Gone" in bad[0], bad


def test_corrupt_workbook_is_x0_not_a_crash(tmp_path):
    p = tmp_path / "bad.xlsx"
    p.write_bytes(b"PK\x03\x04 not really a zip")
    r = VX.validate(str(p), do_recalc=False)
    assert r["rules"]["X0"] == "fail" and r["rules"]["X3"] == "not_run"


def test_r1c1_leaves_names_alone():
    assert VX._r1c1("=C19*dso/365", 37, 3) == VX._r1c1("=D19*dso/365", 37, 4)
