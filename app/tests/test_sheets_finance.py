"""Finance kit in the sheets engine: assumption book, comps, precedents, DCF and football field build live-formula
workbooks whose values match a Python recompute and pass the forge workbook rules; finish_check demands sources."""
import shutil
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

from engines.sheets import builder as B  # noqa: E402
from engines.sheets import qa  # noqa: E402

HAS_LO = shutil.which("soffice") is not None or shutil.which("libreoffice") is not None

INP = dict(shares=100.0, debt=500.0, cash=200.0, target_ebitda=180.0, revenue_base=1000.0, units_start=110.0,
           units_growth=0.08, price_start=10.0, price_growth=0.02, margin_start=-0.05, margin_end=0.25, da_pct=0.04,
           capex_pct=0.06, nwc_pct=0.10, tax_rate=0.25, risk_free=0.042, beta_unlevered=1.1,
           equity_risk_premium=0.05, cost_of_debt=0.065, debt_weight=0.2, terminal_growth=0.025, exit_multiple=10.0,
           mid_year=1, wacc_step=0.01, g_step=0.005)
PEERS = [dict(name="Alpha", price=40.0, shares=120.0, debt=800.0, cash=150.0, revenue=2400.0, ebitda=410.0),
         dict(name="Beta", price=22.5, shares=300.0, debt=1200.0, cash=400.0, revenue=5100.0, ebitda=730.0),
         dict(name="Gamma", price=75.0, shares=60.0, debt=300.0, cash=500.0, revenue=1500.0, ebitda=260.0),
         dict(name="Delta", price=12.0, shares=210.0, debt=100.0, cash=90.0, revenue=900.0, ebitda=-40.0),
         dict(name="Epsilon", price=55.0, shares=90.0, debt=950.0, cash=210.0, revenue=3300.0, ebitda=520.0)]
DEALS = [dict(date="2025-03-14", acquirer="BuyCo", name="Tgt1", consideration="Cash", ev=5200.0, revenue=1300.0,
              ebitda=260.0, offer=48.0, unaffected=36.5),
         dict(date="2024-09-02", acquirer="StratCo", name="Tgt2", consideration="Stock", ev=2100.0, revenue=700.0,
              ebitda=150.0, offer=21.0, unaffected=16.0),
         dict(date="2023-11-20", acquirer="PE Fund", name="Tgt3", consideration="Cash", ev=900.0, revenue=420.0,
              ebitda=70.0, offer=9.5, unaffected=7.9),
         dict(date="2022-06-30", acquirer="BigCo", name="Tgt4", consideration="Mixed", ev=7600.0, revenue=2100.0,
              ebitda=495.0, offer=63.0, unaffected=45.0)]


def build(path, drop_source=None):
    wb = B.banker_workbook("Target valuation", "Comps, precedents and DCF for a test target", "Test", "2026-11-13",
                           middle=("Inputs", "Comps", "Precedents", "DCF", "Outputs"))
    I = wb["Inputs"]
    B.title(I, "Inputs")
    book = B.assumption_book(I, 3)
    fmt = {"shares": "num", "mid_year": "int", "exit_multiple": "x", "beta_unlevered": "0.00;(0.00)",
           "units_start": "num", "price_start": "usd2"}
    usd = {"debt", "cash", "target_ebitda", "revenue_base"}
    for k, v in INP.items():
        f = fmt.get(k) or ("usd" if k in usd else "pct")
        name = {"shares": "diluted_shares", "debt": "total_debt"}.get(k, k)
        label = k.replace("_", " ").capitalize() + {"usd": " ($m)", "pct": " (%)"}.get(f, " (x)")
        book.add(label, v, f, name=name, source="" if k == drop_source else f"Test source for {k}",
                 rationale="Test rationale", low=v * 0.9, high=v * 1.1)
    for p in PEERS:
        p["source"] = "Test filing"
    for d in DEALS:
        d["source"] = "Test proxy"
    B.title(wb["Comps"], "Trading comparables")
    comps = B.comps_table(wb["Comps"], 3, PEERS)
    B.title(wb["Precedents"], "Precedent transactions")
    prec = B.precedents_table(wb["Precedents"], 3, DEALS)
    D = wb["DCF"]
    B.title(D, "DCF")
    dcf = B.dcf_block(D, 3, [2027, 2028, 2029, 2030, 2031],
                      [{"key": "units", "label": "Units sold (m)", "start": "units_start", "growth": "units_growth"},
                       {"key": "price", "label": "Price per unit ($)", "start": "price_start",
                        "growth": "price_growth", "fmt": "usd2"}],
                      margin=("margin_start", "margin_end"))
    O = wb["Outputs"]
    B.title(O, "Valuation summary")
    per_share = "({m}*target_ebitda-total_debt+cash)/diluted_shares"
    ff = B.football_field(O, 3, [
        {"label": "Trading comps, EV/EBITDA interquartile", "low": "=" + per_share.format(m="comps_ev_ebitda_p25"),
         "high": "=" + per_share.format(m="comps_ev_ebitda_p75")},
        {"label": "Precedents, EV/EBITDA interquartile", "low": "=" + per_share.format(m="prec_ev_ebitda_p25"),
         "high": "=" + per_share.format(m="prec_ev_ebitda_p75")},
        {"label": "DCF, Gordon vs exit multiple", "low": "=MIN(dcf_per_share,dcf_per_share_exit)",
         "high": "=MAX(dcf_per_share,dcf_per_share_exit)"}])
    B.add_check(wb, "EV bridge ties", "=dcf_equity-(dcf_ev-total_debt+cash)")
    wb.save(path)
    return comps, prec, dcf, ff


def recompute():
    x = INP
    n = 5
    t = np.arange(1, n + 1)
    rev = x["units_start"] * (1 + x["units_growth"]) ** (t - 1) * x["price_start"] * (1 + x["price_growth"]) ** (t - 1)
    margin = x["margin_start"] + (x["margin_end"] - x["margin_start"]) * (t - 1) / (n - 1)
    ebitda = rev * margin
    da = -rev * x["da_pct"]
    tax = -np.maximum(0, ebitda + da) * x["tax_rate"]
    capex = -rev * x["capex_pct"]
    nwc = -(rev - np.concatenate([[x["revenue_base"]], rev[:-1]])) * x["nwc_pct"]
    fcf = ebitda + tax + capex + nwc
    dw = x["debt_weight"]
    beta = x["beta_unlevered"] * (1 + (1 - x["tax_rate"]) * dw / (1 - dw))
    ke = x["risk_free"] + beta * x["equity_risk_premium"]
    wacc = (1 - dw) * ke + dw * x["cost_of_debt"] * (1 - x["tax_rate"])

    def per_share(w, g):
        pv = (fcf / (1 + w) ** (t - x["mid_year"] / 2)).sum()
        tv = fcf[-1] * (1 + g) / (w - g) / (1 + w) ** n
        return pv, tv, (pv + tv - x["debt"] + x["cash"]) / x["shares"]
    pv, pvtv, ps = per_share(wacc, x["terminal_growth"])
    ev = pv + pvtv
    ev_exit = pv + ebitda[-1] * x["exit_multiple"] / (1 + wacc) ** n
    return {"fcf": fcf, "wacc": wacc, "ev": ev, "per_share": ps, "ev_exit": ev_exit, "tv_share": pvtv / ev,
            "grid_corner": per_share(wacc - 2 * x["wacc_step"], x["terminal_growth"] - 2 * x["g_step"])[2]}


def _peer_stats():
    ev = [p["price"] * p["shares"] + p["debt"] - p["cash"] for p in PEERS]
    m = [e / p["ebitda"] for e, p in zip(ev, PEERS) if p["ebitda"] > 0]
    prem = [d["offer"] / d["unaffected"] - 1 for d in DEALS]
    pm = [d["ev"] / d["ebitda"] for d in DEALS]
    return {"comps_ev_ebitda_median": np.median(m), "comps_ev_ebitda_p25": np.percentile(m, 25),
            "comps_ev_ebitda_p75": np.percentile(m, 75), "comps_ev_ebitda_mean": np.mean(m),
            "prec_premium_median": np.median(prem), "prec_ev_ebitda_p75": np.percentile(pm, 75),
            "prec_ev_ebitda_p25": np.percentile(pm, 25)}


def test_builder_blocks_are_live_and_sourced(tmp_path):
    p = tmp_path / "val.xlsx"
    comps, prec, dcf, ff = build(p)
    wb = load_workbook(p)
    for sh in ("Comps", "Precedents", "DCF", "Outputs"):          # calculations: no typed numbers except blue inputs
        for row in wb[sh].iter_rows():
            for c in row:
                if isinstance(c.value, (int, float)) and not isinstance(c.value, bool):
                    assert qa._blue(c), f"{sh}!{c.coordinate} typed number not styled as input"
    assert wb["Comps"]["C5"].font.color.rgb.endswith("0000FF")    # inputs blue
    assert str(wb["Comps"][f"{comps['cols']['ev']}{comps['first_row']}"].value).startswith("=")
    assert wb["Inputs"]["C3"].value == "Source" and wb["Inputs"]["E3"].value == "Low"
    assert "total_debt_low" in wb.defined_names and "dcf_per_share" in wb.defined_names
    assert qa.missing_sources(str(p)) == []
    build(tmp_path / "gap.xlsx", drop_source="risk_free")
    gaps = qa.missing_sources(str(tmp_path / "gap.xlsx"))
    assert len(gaps) == 1 and "Risk free" in gaps[0] and gaps[0].startswith("Inputs!C")


@pytest.mark.skipif(not HAS_LO, reason="LibreOffice needed for recalculation")
def test_values_match_python_recompute_and_forge_rules_pass(tmp_path):
    from engines.forge import validate_xlsx as VX
    p = tmp_path / "val.xlsx"
    comps, prec, dcf, ff = build(p)
    wb = load_workbook(qa.recalc(str(p), str(tmp_path / "rc")), data_only=True)

    def val(name):
        dest = str(wb.defined_names[name].attr_text)
        sh, _, cell = dest.rpartition("!")
        return wb[sh.strip("'")][cell.replace("$", "")].value
    for k, v in _peer_stats().items():
        assert val(k) == pytest.approx(v, rel=1e-9), k
    assert wb["Comps"][f"{comps['cols']['ev_ebitda']}{comps['first_row'] + 3}"].value == "NM"   # negative EBITDA peer is not meaningful
    rc = recompute()
    D = wb["DCF"]
    fcf = [D[f"{c}{dcf['rows']['fcf']}"].value for c in dcf["cols"]]
    assert fcf == pytest.approx(list(rc["fcf"]), rel=1e-9)
    assert fcf[0] < 0                                                # loss year stays negative
    for k in ("wacc", "ev", "per_share", "ev_exit", "tv_share"):
        assert val(f"dcf_{k}") == pytest.approx(rc[k], rel=1e-9), k
    assert D[dcf["grid"].split("!")[1].split(":")[0]].value == pytest.approx(rc["grid_corner"], rel=1e-9)
    assert wb["Checks"]["F3"].value == "OK"
    lo = wb["Outputs"][f"B{ff['first_row']}"].value
    assert lo == pytest.approx((_peer_stats()["comps_ev_ebitda_p25"] * 180 - 500 + 200) / 100, rel=1e-9)

    recheck = {"checks": [{"cell": "DCF!B" + str(dcf["rows"]["per_share"]), "expected": rc["per_share"],
                           "label": "DCF value per share"}]}
    r = VX.validate(str(p), recompute=recheck)
    from engines.forge.ooxml_check import _schema_dir             # X0 cannot run without the ECMA-376 schemas
    allowed = {"X11": "not_run"} | ({} if _schema_dir() else {"X0": "not_run"})
    bad = {k: v for k, v in r["rules"].items() if v != "pass" and allowed.get(k) != v}
    assert not bad, (bad, [f["message"] for f in r["failures"] if f["status"] != "pass"])
    assert qa.check(str(p))["issues"] == []


@pytest.mark.skipif(not HAS_LO, reason="LibreOffice needed for recalculation")
def test_finish_check_flags_missing_source(tmp_path):
    from engines.sheets.engine import finish_check
    build(tmp_path / "gap.xlsx", drop_source="equity_risk_premium")
    msg = finish_check("done", ["gap.xlsx"], SimpleNamespace(root=tmp_path))
    assert msg and "source" in msg.lower() and "Equity risk premium" in msg


# ------------------------------------------------------------------ build first: no reading the engine's own source
def test_engine_refuses_source_reads_and_pushes_a_first_build(tmp_path):
    from meshpool_harness.core.client import MockClient, tool_call
    from engines.sheets import engine as E
    (tmp_path / "input.csv").write_text("year,revenue\n2025,100\n")
    src = str(E.ROOT / "engines" / "sheets" / "builder.py")
    c = MockClient([tool_call("read_file", {"path": src}),
                    tool_call("bash", {"command": f"cat {E.ROOT}/engines/sheets/engine.py"}),
                    tool_call("grep", {"pattern": "def ", "path": str(E.ROOT / "engines")}),
                    tool_call("read_file", {"path": "input.csv"}),
                    tool_call("bash", {"command": 'python -c "import inspect, engines.sheets.builder as B; '
                                                  'print(inspect.getsource(B))"'})])
    r = E.solve("Build a revenue model", workdir=str(tmp_path), task_id="t", max_turns=5, client=c, model="mock",
                files=[str(tmp_path / "input.csv")])
    assert not r["ok"]
    last = c.requests[-1]["messages"]
    tools = [m["content"] for m in last if m["role"] == "tool"]
    assert len(tools) == 4 and sum("REFUSED" in t for t in tools) == 3
    assert "revenue" in tools[3] and "REFUSED" not in tools[3]          # the given input file stays readable
    nudges = [m["content"] for m in last if m["role"] == "user" and "BUILD NOW" in m["content"]]
    assert len(nudges) == 1 and "3 of 5 turns" in nudges[0]              # injected once, at 60% of the budget
    assert "do NOT read" in E.SYSTEM and "B.dcf_block" in E.SYSTEM and "B.assumption_book" in E.SYSTEM
    ws = E.SheetsWorkspace(str(tmp_path), str(tmp_path / ".run"))
    for cmd in (f"sed -n 1,50p {src}", 'python -c "import inspect, engines.sheets.builder as B; '
                                       'print(inspect.getsource(B))"'):
        with pytest.raises(Exception, match="REFUSED"):
            ws.bash(cmd)
    assert "[exit 0]" in ws.bash("python -c \"from engines.sheets import builder as B; print(B.FMT['pct'])\"")
    (tmp_path / "model.xlsx").write_bytes(b"x")
    assert E.build_first(4, 5, ws) is None                                 # a saved workbook: no nudge
