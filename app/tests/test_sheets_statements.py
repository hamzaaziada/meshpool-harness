"""Spreading (SEC EDGAR company facts -> standard statements), the integrated three-statement model with scenarios,
the DCF taken from the statements, and the valuation-method selector / weighting. Workbooks are recalculated in
LibreOffice and compared with an independent Python recompute; the forge workbook rules must pass."""
import copy
import json
import shutil
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

from engines.sheets import builder as B  # noqa: E402
from engines.sheets import qa  # noqa: E402
from engines.sheets import spreading as S  # noqa: E402

HAS_LO = shutil.which("soffice") is not None or shutil.which("libreoffice") is not None
lo = pytest.mark.skipif(not HAS_LO, reason="LibreOffice needed for recalculation")
AAPL = ROOT / "tests" / "fixtures" / "edgar" / "CIK0000320193_trimmed.json"   # real SEC data, trimmed to used tags


# ------------------------------------------------------------------------------------------------ spreading
def test_spread_real_filing_maps_sources_and_ties():
    sp = S.spread(S.load_companyfacts(AAPL), years=3)
    assert sp.entity == "Apple Inc." and sp.years == ["FY2024", "FY2025", "FY2026"]
    assert sp.ends["FY2024"] == "2024-09-28"
    assert sp.value("revenue", "FY2024") == 391035.0                     # $m, from the FY2024 10-K
    assert sp.value("cogs", "FY2024") == -210352.0                       # costs shown negative
    assert sp.value("capex", "FY2024") == -9447.0
    c = sp.cells["revenue"]["FY2024"]
    assert c.tag == "RevenueFromContractWithCustomerExcludingAssessedTax"   # first tag in the list not reported
    assert c.form == "10-K" and c.accn and c.filed and c.url.startswith("https://www.sec.gov/Archives/edgar/data/320193/")
    assert "accession" in c.source() and c.tag in c.source()
    # checks: balance sheet balances and both cash flow ties pass; NI vs CF is the same tag, so it says not_run
    st = {(k["check"].split(":")[0], k["year"], k["check"]): k["status"] for k in sp.checks}
    assert all(v == "pass" for (fam, y, name), v in st.items() if fam in ("Balance sheet balances", "Cash flow ties"))
    ni = [k for k in sp.checks if k["check"].startswith("Net income ties: income statement")]
    assert all(k["status"] == "not_run" and "same tag" in k["detail"] for k in ni)
    assert all(k["status"] == "pass" for k in sp.checks if "pre-tax" in k["check"])
    # gaps are reported, not filled: Apple reports no interest expense line or minority interest
    gaps = {(g["line"], g["year"]) for g in sp.gaps}
    assert ("interest_expense", "FY2026") in gaps and sp.value("interest_expense", "FY2026") is None
    # derived lines make the condensed balance sheet whole
    ob = S.opening_balances(sp)
    assets = sum(ob[k] for k in ("cash_open", "ar_open", "inv_open", "other_ca_open", "ppe_open", "other_nca_open"))
    liab = sum(ob[k] for k in ("ap_open", "other_cl_open", "term_debt_open", "other_ncl_open"))
    assert assets == pytest.approx(sp.value("total_assets", "FY2026"))
    assert assets == pytest.approx(liab + ob["equity_open"])
    hm = S.history_metrics(sp)["FY2026"]
    assert hm["dso_days"] == pytest.approx(sp.value("receivables", "FY2026") / sp.value("revenue", "FY2026") * 365)
    assert 0 < hm["gross_margin"] < 1 and 0 < hm["tax_rate"] < 0.3


def _fact(val, end, start=None, form="10-K", filed="2025-02-01", accn="0000000001-25-000001"):
    f = {"end": end, "val": val, "form": form, "filed": filed, "accn": accn, "fp": "FY", "fy": int(end[:4])}
    if start:
        f["start"] = start
    return f


def _facts(**tags):
    return {"cik": 1234, "entityName": "Synthetic Co", "facts": {"us-gaap": {
        t: {"units": {("shares" if "Shares" in t else "USD"): fs}} for t, fs in tags.items()}}}


def test_spread_fallbacks_restatements_and_gaps():
    y1, y2 = ("2023-01-01", "2023-12-31"), ("2024-01-01", "2024-12-31")
    facts = _facts(
        SalesRevenueNet=[_fact(100e6, y1[1], y1[0]), _fact(120e6, y2[1], y2[0]),
                         _fact(30e6, "2024-03-31", "2024-01-01", form="10-Q"),          # quarterly: ignored
                         _fact(31e6, "2024-12-31", "2024-10-01")],                     # quarter inside a 10-K
        NetIncomeLoss=[_fact(10e6, y1[1], y1[0]), _fact(12e6, y2[1], y2[0]),
                       _fact(12.5e6, y2[1], y2[0], filed="2026-02-01", accn="0000000001-26-000001")],  # restated
        Assets=[_fact(500e6, y1[1]), _fact(560e6, y2[1])],
        LiabilitiesAndStockholdersEquity=[_fact(500e6, y1[1]), _fact(561e6, y2[1])],   # FY2024 off by 1
        StockholdersEquity=[_fact(200e6, y1[1]), _fact(212e6, y2[1])],
    )
    sp = S.spread(facts, years=2)
    assert sp.years == ["FY2023", "FY2024"]
    assert sp.value("revenue", "FY2024") == 120.0 and sp.cells["revenue"]["FY2024"].tag == "SalesRevenueNet"
    ni = sp.cells["net_income"]["FY2024"]
    assert ni.value == 12.5 and ni.restated and ni.accn == "0000000001-26-000001" and "restated" in ni.source()
    assert sp.value("total_liabilities", "FY2024") is None                 # not reported: a gap, not a plug
    assert any(g["line"] == "total_liabilities" and g["kind"] == "missing" and "Liabilities" in g["tags_tried"]
               for g in sp.gaps)
    assert sp.value("other_noncurrent_liabilities", "FY2024") is None      # derived from a gap: also a gap
    chk = {(k["check"], k["year"]): k for k in sp.checks}
    bal = chk[("Balance sheet balances: total assets = total liabilities and equity", "FY2024")]
    assert bal["status"] == "fail" and bal["diff"] == pytest.approx(-1.0)
    assert chk[("Balance sheet balances: total assets = total liabilities and equity", "FY2023")]["status"] == "pass"
    assert chk[("Balance sheet balances: total assets = liabilities + equity", "FY2024")]["status"] == "not_run"
    assert chk[("Cash flow ties: operating + investing + financing + FX = change in cash", "FY2024")]["status"] == \
        "not_run"
    with pytest.raises(ValueError, match="gaps"):
        S.opening_balances(sp)
    with pytest.raises(ValueError, match="User-Agent"):
        S._get("https://data.sec.gov/x", "no contact")


@lo
def test_write_spread_is_sourced_and_checks_recalculate(tmp_path):
    sp = S.spread(S.load_companyfacts(AAPL), 3)
    wb = B.banker_workbook("Apple history", "Spread", "T", "2026-11-13", middle=("Historicals", "Inputs"))
    t = S.write_spread(wb["Historicals"], 4, sp)
    p = tmp_path / "hist.xlsx"
    wb.save(p)
    assert qa.missing_sources(str(p)) == []
    rc = load_workbook(qa.recalc(str(p), str(tmp_path / "rc")), data_only=True)
    H = rc["Historicals"]
    col = t["cols"]["FY2026"]
    assert H[f"{col}{t['rows']['revenue']}"].value == pytest.approx(sp.value("revenue", "FY2026"))
    assert H[f"{col}{t['rows']['other_current_assets']}"].value == pytest.approx(
        sp.value("other_current_assets", "FY2026"))
    for r in t["check_rows"]:
        for c in t["cols"].values():
            v = H[f"{c}{r}"].value
            assert v is None or abs(v) < 1e-6
    assert rc["Checks"]["F3"].value == "OK"
    from engines.forge import validate_xlsx as VX
    res = VX.validate(str(p))
    assert res["rules"]["X4"] == "pass" and res["rules"]["X5"] == "pass"   # historicals sourced, no errors


# ------------------------------------------------------------------------------------------------ the model
CASES = {  # name: (base, bull, bear, fmt)
    "units_start": (100.0, 100.0, 100.0, "num"), "units_growth": (0.06, 0.10, 0.02, "pct"),
    "price_start": (10.0, 10.0, 10.0, "usd2"), "price_growth": (0.02, 0.03, 0.0, "pct"),
    "unit_cost_start": (6.0, 6.0, 6.0, "usd2"), "unit_cost_growth": (0.01, 0.0, 0.03, "pct"),
    "subs_start": (2.0, 2.0, 2.0, "num"), "subs_g_start": (0.30, 0.40, 0.15, "pct"),
    "subs_g_end": (0.08, 0.12, 0.03, "pct"), "arpu_start": (120.0, 120.0, 120.0, "usd2"),
    "arpu_step": (5.0, 8.0, 0.0, "usd2"), "subs_gross_margin": (0.75, 0.78, 0.70, "pct"),
    "sga_pct": (0.12, 0.11, 0.14, "pct"), "rnd_pct": (0.05, 0.05, 0.06, "pct"), "tax_rate": (0.25, 0.25, 0.25, "pct"),
    "dso": (45.0, 40.0, 55.0, "num"), "dio": (60.0, 55.0, 70.0, "num"), "dpo": (50.0, 55.0, 45.0, "num"),
    "capex_pct": (0.04, 0.035, 0.05, "pct"), "capex_per_unit": (2.0, 2.0, 2.5, "usd2"),
    "useful_life_new": (8.0, 8.0, 8.0, "num"), "remaining_life_existing": (4.5, 4.5, 4.5, "num"),
    "term_rate": (0.06, 0.055, 0.07, "pct"), "revolver_rate": (0.07, 0.065, 0.08, "pct"),
    "cash_rate": (0.03, 0.03, 0.02, "pct"), "min_cash": (50.0, 50.0, 50.0, "usd"),
    "revolver_capacity": (150.0, 150.0, 150.0, "usd"), "term_draw": (0.0, 25.0, 0.0, "usd"),
    "term_amort": (40.0, 40.0, 60.0, "usd"), "payout_ratio": (0.3, 0.3, 0.2, "pct"),
    "buyback": (20.0, 40.0, 260.0, "usd"),                   # the bear case leans on the revolver and runs short
}
OPEN = {"cash_open": 80.0, "ar_open": 120.0, "inv_open": 90.0, "other_ca_open": 30.0, "ppe_open": 400.0,
        "other_nca_open": 150.0, "ap_open": 70.0, "other_cl_open": 40.0, "revolver_open": 10.0,
        "term_debt_open": 300.0, "other_ncl_open": 60.0}
OPEN["equity_open"] = (sum(OPEN[k] for k in ("cash_open", "ar_open", "inv_open", "other_ca_open", "ppe_open",
                                              "other_nca_open"))
                       - sum(OPEN[k] for k in ("ap_open", "other_cl_open", "revolver_open", "term_debt_open",
                                               "other_ncl_open")))
VAL = {"risk_free": 0.042, "beta_unlevered": 1.0, "equity_risk_premium": 0.05, "cost_of_debt": 0.06,
       "debt_weight": 0.25, "terminal_growth": 0.025, "exit_multiple": 9.0, "mid_year": 1, "diluted_shares": 50.0,
       "wacc_step": 0.01, "g_step": 0.005, "exit_step": 1.0, "comps_low_ps": 30.0, "comps_high_ps": 45.0,
       "prec_low_ps": 38.0, "prec_high_ps": 52.0, "lbo_low_ps": 25.0, "lbo_high_ps": 33.0, "share_price": 36.0}
YEARS = [2026, 2027, 2028, 2029, 2030]
SEGMENTS = [
    {"name": "Devices", "drivers": [
        {"key": "units", "label": "Units sold (m)", "start": "units_start", "growth": "units_growth"},
        {"key": "price", "label": "Price per unit ($)", "start": "price_start", "growth": "price_growth", "fmt": "usd2"},
        {"key": "ucost", "label": "Unit cost ($)", "start": "unit_cost_start", "growth": "unit_cost_growth",
         "fmt": "usd2"}],
     "revenue": "{units}*{price}", "cogs": "{units}*{ucost}"},
    {"name": "Subscriptions", "drivers": [
        {"key": "subs", "label": "Subscribers (m)", "start": "subs_start", "growth": ("subs_g_start", "subs_g_end")},
        {"key": "arpu", "label": "Revenue per subscriber ($)", "start": "arpu_start", "step": "arpu_step",
         "fmt": "usd2"}],
     "revenue": "{subs}*{arpu}", "gross_margin": "subs_gross_margin"}]
OPEX = [{"key": "sga", "label": "Selling, general and administrative ($m, negative)", "pct": "sga_pct"},
        {"key": "rnd", "label": "Research and development ($m, negative)", "pct": "rnd_pct"}]
MODEL_ARGS = dict(years=YEARS, segments=SEGMENTS, opex=OPEX, capex_growth="MAX(0,{units}-{units@prev})*capex_per_unit")
PROFILE = {"sector": "general", "stage": "mature", "context": "control", "drivers_traceable": True, "n_peers": 7,
           "peer_comparability": "medium", "n_precedents": 5, "precedents_recent": True, "sponsor_relevant": True,
           "tv_share": 0.7}


def build_model(path, scenario=1):
    wb = B.banker_workbook("Synthetic Co", "Integrated model and valuation", "Test", "2026-11-13",
                           middle=("Inputs", "Model", "DCF", "Outputs", "Valuation inputs"))
    I = wb["Inputs"]
    B.title(I, "Inputs")
    sb = B.scenario_book(I, 3, default=scenario)
    sb.section("Operating drivers and balance sheet assumptions")
    for k, (b, u, d, f) in CASES.items():
        sb.add(k.replace("_", " ").capitalize(), b, u, d, f, k, source=f"Test source for {k}", rationale="Test")
    sb.section("Opening balance sheet")
    for k, v in OPEN.items():
        sb.add(k.replace("_", " ").capitalize() + " ($m)", v, fmt="usd", name=k, source="Test 10-K", rationale="Test")
    sb.section("Valuation")
    for k, v in VAL.items():
        sb.add(k.replace("_", " ").capitalize(), v, fmt="pct" if v < 1 and k not in ("beta_unlevered",) else "num",
               name=k, source="Test source", rationale="Test")
    m = B.three_statement(wb["Model"], 3, **MODEL_ARGS)
    d = B.dcf_from_statements(wb["DCF"], 3, m)
    O = wb["Outputs"]
    B.title(O, "Outputs")
    ss = B.scenario_summary(O, 3, MODEL_ARGS)
    V = wb["Valuation inputs"]
    B.title(V, "Valuation summary")
    v = B.valuation_summary(V, 3, [
        {"key": "dcf", "low": "=MIN(dcf_per_share,dcf_per_share_exit)", "high": "=MAX(dcf_per_share,dcf_per_share_exit)"},
        {"key": "trading_comps", "low": "=comps_low_ps", "high": "=comps_high_ps"},
        {"key": "precedents", "low": "=prec_low_ps", "high": "=prec_high_ps"},
        {"key": "lbo", "low": "=lbo_low_ps", "high": "=lbo_high_ps"}], PROFILE,
        marker={"label": "Current price ($/share)", "value": "=share_price"})
    wb.save(path)
    return m, d, ss, v


def recompute(case: str) -> dict:
    """Independent Python implementation of the model and DCF for one case."""
    i = {"base": 0, "bull": 1, "bear": 2}[case]
    x = {k: v[i] for k, v in CASES.items()} | OPEN | VAL
    n = len(YEARS)
    units, price, ucost, subs, arpu = [x["units_start"]], [x["price_start"]], [x["unit_cost_start"]], \
        [x["subs_start"]], [x["arpu_start"]]
    for t in range(1, n + 1):
        units.append(units[-1] * (1 + x["units_growth"]))
        price.append(price[-1] * (1 + x["price_growth"]))
        ucost.append(ucost[-1] * (1 + x["unit_cost_growth"]))
        g = x["subs_g_start"] + (x["subs_g_end"] - x["subs_g_start"]) * (t - 1) / (n - 1)
        subs.append(subs[-1] * (1 + g))
        arpu.append(arpu[-1] + x["arpu_step"])
    o = {k: [] for k in ("revenue", "cogs", "ebitda", "dep", "ebit", "int_exp", "int_inc", "ebt", "tax", "ni", "ar",
                         "inv", "ap", "additions", "ppe", "term", "rev", "cash", "equity", "div", "cf_nwc", "cfo",
                         "rev_flow", "shortfall", "assets", "liab", "fcf")}
    ar, inv, ap = x["ar_open"], x["inv_open"], x["ap_open"]
    ppe, term, rev, cash, eq = x["ppe_open"], x["term_debt_open"], x["revolver_open"], x["cash_open"], x["equity_open"]
    adds = []
    for t in range(1, n + 1):
        r_dev = units[t] * price[t]
        r_sub = subs[t] * arpu[t]
        revenue = r_dev + r_sub
        cogs = -units[t] * ucost[t] - r_sub * (1 - x["subs_gross_margin"])
        ebitda = revenue + cogs - revenue * (x["sga_pct"] + x["rnd_pct"])
        add = revenue * x["capex_pct"] + max(0, units[t] - units[t - 1]) * x["capex_per_unit"]
        adds.append(add)
        po, le, ln = x["ppe_open"], x["remaining_life_existing"], x["useful_life_new"]
        dep = min(po / le, max(0, po - po / le * (t - 1)))
        for j, cj in enumerate(adds, start=1):
            dep += 0 if t < j else min(cj / ln, max(0, cj - cj / ln * (t - j)))
        ebit = ebitda - dep
        draw = x["term_draw"]
        repay = -min(term + draw, x["term_amort"])
        int_exp = -term * x["term_rate"] - rev * x["revolver_rate"]
        int_inc = cash * x["cash_rate"]
        ebt = ebit + int_exp + int_inc
        tax = -max(0, ebt) * x["tax_rate"]
        ni = ebt + tax
        div = -max(0, ni) * x["payout_ratio"]
        ar1 = revenue * x["dso"] / 365
        inv1 = -cogs * x["dio"] / 365
        ap1 = -cogs * x["dpo"] / 365
        cf_nwc = (ar - ar1) + (inv - inv1) + (ap1 - ap)
        cfo = ni + dep + cf_nwc
        cash_pre = cash + cfo - add + draw + repay + div - x["buyback"]
        flow = max(-rev, min(x["revolver_capacity"] - rev, x["min_cash"] - cash_pre))
        short = max(0, x["min_cash"] - cash_pre - flow)
        rev += flow
        term = term + draw + repay
        cash = cash_pre + flow
        ppe = ppe + add - dep
        eq = eq + ni + div - x["buyback"]
        ar, inv, ap = ar1, inv1, ap1
        assets = cash + ar + inv + x["other_ca_open"] + ppe + x["other_nca_open"]
        liab = ap + x["other_cl_open"] + rev + term + x["other_ncl_open"]
        fcf = ebitda - max(0, ebit) * x["tax_rate"] - add + cf_nwc
        for k, v in (("revenue", revenue), ("cogs", cogs), ("ebitda", ebitda), ("dep", dep), ("ebit", ebit),
                     ("int_exp", int_exp), ("int_inc", int_inc), ("ebt", ebt), ("tax", tax), ("ni", ni), ("ar", ar),
                     ("inv", inv), ("ap", ap), ("additions", add), ("ppe", ppe), ("term", term), ("rev", rev),
                     ("cash", cash), ("equity", eq), ("div", div), ("cf_nwc", cf_nwc), ("cfo", cfo),
                     ("rev_flow", flow), ("shortfall", short), ("assets", assets), ("liab", liab), ("fcf", fcf)):
            o[k].append(v)
    dw, tx = x["debt_weight"], x["tax_rate"]
    beta = x["beta_unlevered"] * (1 + (1 - tx) * dw / (1 - dw))
    wacc = (1 - dw) * (x["risk_free"] + beta * x["equity_risk_premium"]) + dw * x["cost_of_debt"] * (1 - tx)
    debt, cash0, sh = x["term_debt_open"] + x["revolver_open"], x["cash_open"], x["diluted_shares"]

    def value(w, g):
        pv = sum(f / (1 + w) ** (t - x["mid_year"] / 2) for t, f in enumerate(o["fcf"], start=1))
        tv = o["fcf"][-1] * (1 + g) / (w - g) / (1 + w) ** (n - x["mid_year"] / 2)   # Gordon: final-year timing
        return pv, tv
    pv, pvtv = value(wacc, x["terminal_growth"])
    ev = pv + pvtv
    ev_exit = pv + o["ebitda"][-1] * x["exit_multiple"] / (1 + wacc) ** n
    pv_c, tv_c = value(wacc - 2 * x["wacc_step"], x["terminal_growth"] - 2 * x["g_step"])
    o.update(wacc=wacc, ev=ev, ev_exit=ev_exit, tv_share=pvtv / ev, per_share=(ev - debt + cash0) / sh,
             per_share_exit=(ev_exit - debt + cash0) / sh, grid_corner=(pv_c + tv_c - debt + cash0) / sh)
    return o


MAP = {"revenue": "revenue", "cogs": "cogs", "ebitda": "ebitda", "ebit": "ebit", "int_exp": "int_exp",
       "int_inc": "int_inc", "ebt": "ebt", "tax": "tax", "ni": "net_income", "ar": "ar", "inv": "inv", "ap": "ap",
       "additions": "additions", "ppe": "ppe_end", "term": "term_end", "rev": "rev_end", "cash": "bs_cash",
       "equity": "eq_end", "div": "dividends", "cf_nwc": "cf_nwc", "cfo": "cfo", "rev_flow": "rev_flow",
       "shortfall": "shortfall", "assets": "total_assets", "liab": "total_liabilities"}


def _rows(ws, m, key):
    return [ws[f"{c}{m['rows'][key]}"].value for c in m["cols"]]


def _named(wb, name):
    sh, _, cell = str(wb.defined_names[name].attr_text).rpartition("!")
    return wb[sh.strip("'")][cell.replace("$", "")].value


def _compare(ws, m, rc):
    for k, row in MAP.items():
        assert _rows(ws, m, row) == pytest.approx(rc[k], rel=1e-9, abs=1e-9), k
    assert _rows(ws, m, "bal_check") == pytest.approx([0] * len(YEARS), abs=1e-7)
    assert _rows(ws, m, "cash_check") == pytest.approx([0] * len(YEARS), abs=1e-7)
    assert ws[f"B{m['rows']['bal_check']}"].value == pytest.approx(0, abs=1e-9)        # opening balance sheet


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    d = tmp_path_factory.mktemp("model")
    p = d / "model.xlsx"
    parts = build_model(p)
    rc = load_workbook(qa.recalc(str(p), str(d / "rc")), data_only=True)
    return SimpleNamespace(path=p, dir=d, m=parts[0], dcf=parts[1], ss=parts[2], v=parts[3], wb=rc)


@lo
def test_statements_match_recompute_in_every_case(model):
    wb, m = model.wb, model.m
    rec = {c: recompute(c) for c in B.SCENARIOS}
    assert any(v > 0 for v in rec["bear"]["rev_flow"]) and max(rec["bear"]["shortfall"]) > 0   # revolver and cap hit
    assert any(v < 0 for v in rec["bull"]["rev_flow"])                                        # opening revolver repaid
    _compare(wb["Model"], m, rec["base"])                                                     # switch = 1
    for c in B.SCENARIOS:
        pm = model.ss["models"][c][0]
        _compare(wb[pm["sheet"]], pm, rec[c])
    for k in ("ar", "ppe", "cash"):
        assert [wb["Model"][f"B{m['rows'][MAP[k]]}"].value] == pytest.approx([OPEN[{"ar": "ar_open", "ppe": "ppe_open",
                                                                                      "cash": "cash_open"}[k]]])
    assert wb["Checks"]["F3"].value == "OK"
    labels = [wb["Checks"].cell(row=r, column=1).value for r in range(4, wb["Checks"].max_row + 1)]
    assert any("balance sheet balances" in str(x) for x in labels)
    assert any("closing cash equals balance sheet cash" in str(x) for x in labels)


@lo
def test_dcf_from_statements_and_scenario_summary(model):
    wb, d = model.wb, model.dcf
    rec = {c: recompute(c) for c in B.SCENARIOS}
    D = wb["DCF"]
    assert [D[f"{c}{d['rows']['fcf']}"].value for c in d["cols"]] == pytest.approx(rec["base"]["fcf"], rel=1e-9)
    for k in ("wacc", "ev", "ev_exit", "tv_share", "per_share", "per_share_exit"):
        assert _named(wb, f"dcf_{k}") == pytest.approx(rec["base"][k], rel=1e-9), k
        for c in B.SCENARIOS:
            assert _named(wb, f"{c}_dcf_{k}") == pytest.approx(rec[c][k], rel=1e-9), (c, k)
    sh, rng = d["grid"].split("!")
    assert wb[sh.strip("'")][rng.split(":")[0]].value == pytest.approx(rec["base"]["grid_corner"], rel=1e-9)
    assert "grid_exit" in d
    vals = [_named(wb, f"{c}_value_per_share") for c in B.SCENARIOS]
    assert vals == pytest.approx([rec[c]["per_share"] for c in B.SCENARIOS], rel=1e-9)
    assert vals[1] > vals[0] > vals[2]                                     # bull > base > bear


@lo
def test_switch_drives_the_live_model(model):
    wb = load_workbook(model.path)
    wb["Inputs"]["B4"].value = 3                                          # the switch row of the scenario book
    p = model.dir / "bear.xlsx"
    wb.save(p)
    rc = load_workbook(qa.recalc(str(p), str(model.dir / "rc_bear")), data_only=True)
    _compare(rc["Model"], model.m, recompute("bear"))
    assert _named(rc, "dcf_per_share") == pytest.approx(recompute("bear")["per_share"], rel=1e-9)
    assert _named(rc, "scenario_name") == "Bear case" and rc["Checks"]["F3"].value == "OK"


@lo
def test_forge_rules_and_engine_gates_pass_on_the_full_model(model):
    from engines.forge import validate_xlsx as VX
    from engines.forge.ooxml_check import _schema_dir
    from engines.sheets.engine import finish_check
    rec = recompute("base")
    chk = {"checks": [{"cell": f"DCF!B{model.dcf['rows']['per_share']}", "expected": rec["per_share"],
                       "label": "DCF value per share"},
                      {"cell": f"Model!{model.m['cols'][-1]}{model.m['rows']['net_income']}",
                       "expected": rec["ni"][-1], "label": "final-year net income"}]}
    r = VX.validate(str(model.path), recompute=chk)
    allowed = {"X11": "not_run"} | ({} if _schema_dir() else {"X0": "not_run"})
    bad = {k: v for k, v in r["rules"].items() if v != "pass" and allowed.get(k) != v}
    assert not bad, (bad, [f["message"] for f in r["failures"] if f["status"] == "fail"][:10])
    assert r["stats"]["balance_sheet_found"]
    assert qa.check(str(model.path))["issues"] == []
    assert qa.missing_sources(str(model.path)) == []
    assert qa.model_gaps(str(model.path)) == []
    assert finish_check("done", [str(model.path)], SimpleNamespace(root=model.dir)) is None


# ------------------------------------------------------------------------------------------------ method selection
def test_selector_rules():
    sel = B.select_methods({"sector": "bank", "pays_dividends": True, "n_peers": 8, "n_precedents": 4})
    assert not sel["dcf"]["applies"] and "debt is raw material" in sel["dcf"]["reason"]
    assert sel["ddm"]["applies"] and sel["excess_return"]["applies"] and sel["nav"]["applies"]
    assert "price / tangible book" in sel["trading_comps"]["reason"] and not sel["lbo"]["applies"]
    sel = B.select_methods({"stage": "early", "drivers_traceable": False, "positive_ebitda": False, "n_peers": 5})
    assert not sel["dcf"]["applies"] and "growth rate" in sel["dcf"]["reason"]
    assert sel["trading_comps"]["applies"] and "EV / revenue" in sel["trading_comps"]["reason"]
    assert not sel["precedents"]["applies"] and not sel["nav"]["applies"]
    sel = B.select_methods({"sector": "reit", "n_peers": 6, "reliable_asset_values": True})
    assert sel["nav"]["applies"] and sel["nav"]["role"] == "primary" and sel["dcf"]["applies"]
    sel = B.select_methods({"distinct_segments": 3, "n_peers": 2, "n_precedents": 6, "context": "minority"})
    assert sel["sotp"]["applies"] and not sel["trading_comps"]["applies"]
    assert sel["precedents"]["role"] == "cross-check" and sel["precedents"]["factors"]["context"][0] == 0.5
    sel = B.select_methods(PROFILE)
    assert sel["lbo"]["role"] == "floor"
    w = B.rule_weights(sel, ["dcf", "trading_comps", "precedents", "lbo"], tv_share=0.7)
    assert set(w) == {"dcf", "trading_comps", "precedents"} and sum(w.values()) == pytest.approx(1)
    # DCF weight falls as terminal value takes more of EV
    assert B.rule_weights(sel, ["dcf", "trading_comps"], 0.9)["dcf"] < B.rule_weights(sel, ["dcf", "trading_comps"],
                                                                                         0.5)["dcf"]
    assert B.tv_factor(0.5) == 1 and B.tv_factor(1.0) == pytest.approx(0.2) and B.tv_factor(0.8) == pytest.approx(0.6)


@lo
def test_weighted_value_matches_recompute(model):
    wb, v = model.wb, model.v
    V = wb["Valuation inputs"]
    sel = B.select_methods(PROFILE)
    base = recompute("base")
    lows = {"dcf": min(base["per_share"], base["per_share_exit"]), "trading_comps": 30.0, "precedents": 38.0}
    highs = {"dcf": max(base["per_share"], base["per_share_exit"]), "trading_comps": 45.0, "precedents": 52.0}
    assert v["weighted"] == ["dcf", "trading_comps", "precedents"] and v["floors"] == ["lbo"]
    rule = B.rule_weights(sel, v["weighted"], base["tv_share"])                 # live terminal value share
    used = v["prefilled_weights"]
    assert sum(used.values()) == pytest.approx(1) and all(round(x / 0.05, 6).is_integer() for x in used.values())
    assert used == B._round_weights(B.rule_weights(sel, v["weighted"], PROFILE["tv_share"]))
    for i, k in enumerate(v["weighted"]):
        r = v["first_row"] + i
        assert V[f"K{r}"].value == pytest.approx(rule[k], rel=1e-9), k
        assert V[f"L{r}"].value == pytest.approx(used[k])
        assert len(V[f"N{r}"].value) > 40
    mid = sum(used[k] * (lows[k] + highs[k]) / 2 for k in used)
    assert _named(wb, "weighted_value_per_share") == pytest.approx(mid, rel=1e-9)
    assert _named(wb, "weighted_value_low") == pytest.approx(sum(used[k] * lows[k] for k in used), rel=1e-9)
    fits = {V.cell(row=r, column=1).value: V.cell(row=r, column=2).value for r in range(5, 13)}
    assert fits[B.METHODS["ddm"]] == "No" and fits[B.METHODS["sotp"]] == "No"


# ------------------------------------------------------------------------------------------------ engine gates
@lo
def test_finish_check_demands_statements_scenarios_and_weighting(tmp_path):
    from engines.sheets.engine import finish_check
    from test_sheets_finance import build as build_simple
    build_simple(tmp_path / "simple.xlsx")             # comps + precedents + margin-based DCF, no statements
    gaps = qa.model_gaps(str(tmp_path / "simple.xlsx"))
    assert any("three_statement" in g for g in gaps) and any("scenario" in g.lower() for g in gaps)
    assert any("weight" in g for g in gaps)
    msg = finish_check("done", ["simple.xlsx"], SimpleNamespace(root=tmp_path))
    assert msg and "valuation_model" in msg and "three_statement" in msg
    # a weighting table whose justification was cleared is refused
    p = tmp_path / "full.xlsx"
    _, _, _, v = build_model(p)
    wb = load_workbook(p)
    wb["Valuation inputs"][f"N{v['first_row']}"].value = None
    wb.save(tmp_path / "nojust.xlsx")
    assert any("no justification" in g for g in qa.model_gaps(str(tmp_path / "nojust.xlsx")))


def test_three_statement_names_missing_inputs():
    wb = B.banker_workbook("x", "y", middle=("Inputs", "Model"))
    sb = B.scenario_book(wb["Inputs"], 3)
    sb.add("Units", 1.0, name="units_start", source="s")
    with pytest.raises(ValueError, match="dso"):
        B.three_statement(wb["Model"], 3, YEARS, SEGMENTS)


@lo
def test_spread_to_model_end_to_end(tmp_path):
    """Apple's filed balance sheet opens the model; the forecast balances and the cash ties."""
    sp = S.spread(S.load_companyfacts(AAPL), 3)
    hm = S.history_metrics(sp)[sp.years[-1]]
    wb = B.banker_workbook("Apple", "Spread to model", "T", "2026-11-13",
                           middle=("Historicals", "Inputs", "Model", "DCF", "Outputs"))
    S.write_spread(wb["Historicals"], 4, sp)
    sb = B.scenario_book(wb["Inputs"], 3)
    S.link_opening_balances(sb, sp)
    rev = sp.value("revenue", "FY2026")
    sb.add("Revenue, last actual ($m)", rev, fmt="usd", name="rev_start", source="Historicals FY2026")
    sb.add("Revenue growth (%)", 0.05, 0.08, 0.01, "pct", "rev_growth", "Test")
    sb.add("Gross margin (%)", round(hm["gross_margin"], 4), None, None, "pct", "gm", "Historicals FY2026")
    for k, v in {"sga_pct": hm["sga_pct"] + hm["rnd_pct"], "tax_rate": hm["tax_rate"], "dso": hm["dso_days"],
                 "dio": hm["dio_days"], "dpo": hm["dpo_days"], "capex_pct": hm["capex_pct"], "useful_life_new": 6,
                 "remaining_life_existing": 4, "term_rate": 0.04, "revolver_rate": 0.05, "cash_rate": 0.03,
                 "min_cash": 25000, "revolver_capacity": 15000, "term_draw": 0, "term_amort": 10000,
                 "payout_ratio": hm["payout"], "buyback": 90000}.items():
        sb.add(k, round(v, 6), fmt="num", name=k, source="Historicals / test")
    m = B.three_statement(wb["Model"], 3, YEARS, [{"name": "All", "drivers": [
        {"key": "rev", "label": "Revenue ($m)", "start": "rev_start", "growth": "rev_growth", "fmt": "usd"}],
        "revenue": "{rev}", "gross_margin": "gm"}])
    p = tmp_path / "apple.xlsx"
    wb.save(p)
    rc = load_workbook(qa.recalc(str(p), str(tmp_path / "rc")), data_only=True)
    M = rc["Model"]
    assert M[f"B{m['rows']['total_assets']}"].value == pytest.approx(sp.value("total_assets", "FY2026"))
    assert [M[f"{c}{m['rows']['bal_check']}"].value for c in ["B"] + m["cols"]] == pytest.approx([0] * 6, abs=1e-6)
    assert rc["Checks"]["F3"].value == "OK"
