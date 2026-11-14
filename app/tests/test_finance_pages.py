"""Banker pages computed from a finished workbook: football field (native floating bars), comps, precedents, DCF bridge
and sensitivities, scenarios, method weighting and the three-statement summary. Numbers on the pages equal the
recalculated workbook; the pages pass the deck rules; build() inserts them untouched; the forge engine finds the
model workbook on its own."""
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

HAS_LO = shutil.which("soffice") is not None or shutil.which("libreoffice") is not None
lo = pytest.mark.skipif(not HAS_LO, reason="LibreOffice needed")


@pytest.fixture(scope="module")
def books(tmp_path_factory):
    import test_sheets_finance as TF
    import test_sheets_statements as TS
    d = tmp_path_factory.mktemp("pages")
    TS.build_model(d / "model.xlsx")
    TF.build(d / "val.xlsx")
    return d


def test_fmt_value_and_range_checks():
    from engines.forge.render import chart_problems, fmt_value
    assert [fmt_value(24.37, "$"), fmt_value(12.46, "x"), fmt_value(38, "%"), fmt_value(-4.2, "$m"),
            fmt_value(None, "x")] == ["$24.37", "12.5x", "38.0%", "($4.2m)", "NM"]
    ok = {"kind": "range", "categories": ["A", "B"], "series": [{"name": "Low", "values": [1, 2]},
                                                                  {"name": "High", "values": [3, 4]}]}
    assert chart_problems(ok) == []
    bad = dict(ok, series=[{"name": "Low", "values": [5, 2]}, {"name": "High", "values": [3, 4]}])
    assert "low above high for A" in chart_problems(bad)[0]


@lo
def test_pages_carry_the_workbook_numbers(books):
    from openpyxl import load_workbook
    from engines.forge.finance_pages import pages_from_workbook
    from engines.sheets import qa
    pages, facts = pages_from_workbook(str(books / "model.xlsx"))
    kinds = [p["archetype"] for p in pages]
    assert kinds[:2] == ["grid", "football_field"] and "bar_takeaway" in kinds
    assert {"waterfall_bridge", "grid", "table_focus"} <= set(kinds)                # different visuals, not one table
    wb = load_workbook(qa.recalc(str(books / "model.xlsx"), str(books / "rc")), data_only=True)

    def named(n):
        sh, _, c = str(wb.defined_names[n].attr_text).rpartition("!")
        return wb[sh.strip("'")][c.replace("$", "")].value
    ff = next(p for p in pages if p["archetype"] == "football_field")
    assert ff["chart"]["kind"] == "range" and ff["chart"]["marker"]["value"] == pytest.approx(36.0)
    assert f"${named('weighted_value_per_share'):,.2f}" in ff["title"]
    dcf = next(p for p in pages if p["title"].startswith("The DCF puts"))
    assert f"${named('dcf_per_share'):,.2f}" in dcf["title"]
    wf = dcf["chart"]["series"][0]["values"]                                        # waterfall to equity value
    assert dcf["archetype"] == "waterfall_bridge" and wf[-1] == pytest.approx(named("dcf_equity"))
    assert wf[0] + sum(wf[1:-1]) == pytest.approx(wf[-1])                             # the bridge adds up
    sens = next(p for p in pages if p["title"].startswith("Value per share runs"))
    assert sens["table"]["rows"][2][3] == f"${named('dcf_per_share'):,.2f}"          # grid centre = base value
    assert len(sens["table"]["rows"]) == 11                                         # both grids stacked
    hg = sens["table"]["heat_grid"]                                                  # heat map around each base
    assert hg[0]["center"] == pytest.approx(named("dcf_per_share")) and hg[0]["base"] == [2, 3]
    assert hg[1]["center"] == pytest.approx(named("dcf_per_share_exit"))
    wt = next(p for p in pages if p["title"].startswith("Weighting the methods"))
    assert [pt["type"] for pt in wt["parts"]] == ["harvey", "chart"]
    sc = next(p for p in pages if p["archetype"] == "bar_takeaway")
    assert sc["chart"]["series"][0]["values"] == pytest.approx(
        [named("bear_value_per_share"), named("base_value_per_share"), named("bull_value_per_share")])
    assert all(f["source"].startswith("Meshpool model") for f in facts) and len(facts) >= len(pages)
    p2, _ = pages_from_workbook(str(books / "val.xlsx"))
    comps = next(p for p in p2 if p["title"].startswith("Peers are valued"))
    assert comps["archetype"] == "benchmark"
    ev = next(pn for pn in comps["benchmark"]["panels"] if pn["title"].startswith("EV / EBITDA"))
    assert ev["median"] == pytest.approx(named_val(books / "val.xlsx", "comps_ev_ebitda_median"))
    assert ev["values"][3] is None and len(ev["values"]) == len(comps["benchmark"]["companies"])   # Delta: NM
    assert any(b["text"].startswith("NM") for b in comps["bullets"])
    assert any(p["title"].startswith("Precedent deals paid") for p in p2)


def named_val(path, name):
    from openpyxl import load_workbook
    from engines.sheets import qa
    wb = load_workbook(qa.recalc(str(path)), data_only=True)
    sh, _, c = str(wb.defined_names[name].attr_text).rpartition("!")
    return wb[sh.strip("'")][c.replace("$", "")].value


@lo
def test_pages_render_and_pass_the_deck_rules(books, tmp_path):
    from pptx import Presentation
    from engines.forge.finance_pages import pages_from_workbook
    from engines.forge.render import render
    from engines.forge.validate_pptx import validate
    from engines.forge.validate_xlsx import load_rules
    pages, _ = pages_from_workbook(str(books / "model.xlsx"))
    p2, _ = pages_from_workbook(str(books / "val.xlsx"))
    pages += [p for p in p2 if p["title"].startswith(("Peers", "Precedent"))]
    out = tmp_path / "pages.pptx"
    r = render(pages, str(out))
    assert r["errors"] == []
    res = validate(str(out), load_rules(), render=True)
    hard = [f for f in res["failures"] if f["status"] == "fail" and f["rule"] != "P20"]
    assert not hard, [f["message"] for f in hard]
    assert sum(f["rule"] == "P20" for f in res["failures"] if f["status"] == "fail") <= 1
    prs = Presentation(str(out))
    ff = prs.slides[1]
    chart = next(s for s in ff.shapes if s.has_chart).chart
    assert [s.name for s in chart.plots[0].series] == ["Low", "Range"]                # native, editable bars
    assert any(s.name == "ov_Marker" for s in ff.shapes)


def test_build_inserts_fixed_pages_before_the_closing(monkeypatch, tmp_path):
    from engines.forge import build as BLD, clarity
    seen = {}

    def fake_call(client, model, system, user, task_id, max_tokens=12000):
        if task_id.endswith("-story"):
            seen["story"] = system
            return {"slides": [{"node": "1", "title": "Demand drives the growth story", "archetype": "argument_only"},
                               {"node": "2", "title": "Act now to secure the deal", "archetype": "closing"}]}
        item = json.loads(user.split("This slide: ", 1)[1].split("\n\nFacts", 1)[0])
        return {"archetype": item["archetype"], "node": item["node"], "title": item["title"]}
    monkeypatch.setattr(BLD, "_call", fake_call)
    monkeypatch.setattr(clarity, "review", lambda *a, **k: {})
    rendered = {}

    def fake_render(slides, path, **kw):
        rendered["slides"] = [dict(s) for s in slides]
        Path(path).write_bytes(b"")
        prov = Path(path).with_suffix(".provenance.json")
        prov.write_text("{}")
        return {"provenance": str(prov), "errors": []}
    monkeypatch.setattr(BLD, "render", fake_render)
    monkeypatch.setattr(BLD, "validate", lambda *a, **k: {"rules": {}, "failures": []})
    monkeypatch.setattr(BLD, "number_checks", lambda *a, **k: {})
    monkeypatch.setattr(BLD, "fill_checks", lambda *a, **k: {})
    monkeypatch.setattr(BLD, "chart_checks", lambda *a, **k: {})
    page = {"archetype": "table_focus", "title": "Peers trade at a median 12.1x EV/EBITDA", "node": "W1",
            "table": {"columns": ["a", "b"], "rows": [["x", "1"], ["y", "2"], ["z", "3"]]}}
    BLD.build("Brief", str(tmp_path), 3, client=object(), fixed_pages=[page],
              extra_facts=[{"id": "W1", "text": "Peers median 12.1x", "source": "Meshpool model"}])
    titles = [s["title"] for s in rendered["slides"]]
    assert titles == ["Demand drives the growth story", page["title"], "Act now to secure the deal"]
    assert rendered["slides"][1]["fixed"]
    assert "do NOT plan slides that repeat them" in seen["story"] and "Exactly 3 slides" in seen["story"]
    assert json.loads((tmp_path / "facts.json").read_text())[-1]["id"] == "W1"


@lo
def test_forge_engine_finds_the_model_workbook(books, monkeypatch, tmp_path):
    from engines.forge import build as BLD
    from engines.forge import engine as FE
    got = {}

    def fake_build(brief, out_dir, slides_wanted, **kw):
        got.update(kw, slides_wanted=slides_wanted)
        p = Path(out_dir) / "deck.pptx"
        p.write_bytes(b"x")
        return {"pptx": str(p), "rules": {}, "unresolved": {}, "calls": 1}
    monkeypatch.setattr(BLD, "build", fake_build)
    shutil.copy(books / "model.xlsx", tmp_path / "model.xlsx")
    out = FE.solve("Build an 8-slide board deck on the valuation", workdir=str(tmp_path), task_id="t",
                   client=object())
    assert got["fixed_pages"] and got["slides_wanted"] == 8 + len(got["fixed_pages"])
    assert "computed from model.xlsx" in out["answer"]
