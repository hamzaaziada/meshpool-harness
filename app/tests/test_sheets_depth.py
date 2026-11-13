"""Model depth: quarterly spreading with LTM and calendarisation (real Apple 10-Q/10-K facts), share-based
compensation through the three statements, the treasury-stock share count, the itemised EV bridge and the alerts
tab. Recalculated in LibreOffice and checked against Python."""
import shutil
import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from engines.sheets import builder as B  # noqa: E402
from engines.sheets import qa  # noqa: E402
from engines.sheets import spreading as S  # noqa: E402

HAS_LO = shutil.which("soffice") is not None or shutil.which("libreoffice") is not None
Q = ROOT / "tests" / "fixtures" / "edgar" / "CIK0000320193_quarterly.json"


def test_quarters_ltm_and_calendarisation():
    facts = S.load_companyfacts(Q)
    q = S.spread_quarters(facts, 8, ["revenue", "net_income", "cfo", "capex", "cash", "total_assets"])
    assert q.labels[-4:] == ["Q1 FY2026", "Q2 FY2026", "Q3 FY2026", "Q4 FY2026"]
    assert q.ends["Q4 FY2026"] == "2026-09-26"
    fy = S.spread(facts, 1)                                   # the 10-K annual figure
    assert sum(q.value("revenue", lab) for lab in q.labels[-4:]) == pytest.approx(fy.value("revenue", "FY2026"))
    q4 = q.cells["revenue"]["Q4 FY2026"]
    assert q4.derived and "less Q1-Q3" in q4.derived and q4.form == "10-K"
    assert q.cells["cfo"]["Q4 FY2026"].derived.startswith("year-to-date")       # cash flow from YTD differences
    assert q.value("capex", "Q4 FY2026") < 0
    v, how = S.ltm(q, "revenue")
    assert v == pytest.approx(fy.value("revenue", "FY2026")) and how.startswith("sum of")
    assert S.ltm(q, "cash") == (q.value("cash", "Q4 FY2026"), "as at 2026-09-26")
    assert S.calendarize({"2025-06-30": 100.0, "2026-06-30": 120.0}, 2025)[0] == pytest.approx(110.0)
    assert S.calendarize({"2025-09-27": 100.0, "2026-09-26": 140.0}, 2025)[0] == pytest.approx(110.0)  # 9/12 + 3/12
    assert S.calendarize({"2025-12-31": 90.0}, 2025)[0] == 90.0
    assert S.calendarize({"2025-06-30": 100.0}, 2025)[0] is None                # never guessed


def depth_book(path):
    import test_sheets_statements as TS
    wb = B.banker_workbook("Depth", "SBC, share count, bridge, alerts", "T", "2026-11-13",
                           middle=("Inputs", "Model", "DCF", "Outputs"))
    sb = B.scenario_book(wb["Inputs"], 3)
    for k, (b, u, d, f) in TS.CASES.items():
        sb.add(k, b, u, d, f, k, source="Test")
    for k, v in TS.OPEN.items():
        sb.add(k, v, fmt="usd", name=k, source="Test")
    for k, v in {**TS.VAL, "sbc_pct": 0.02, "basic_shares": 48.0, "share_price": 36.0, "opt_n": 4.0,
                 "opt_strike": 18.0, "rsus": 1.0, "conv_shares": 3.0, "conv_price": 50.0, "minorities": 25.0,
                 "lease_liab": 40.0, "eq_invest": 15.0}.items():
        if k != "diluted_shares":
            sb.add(k, v, fmt="num", name=k, source="Test")
    B.diluted_shares(wb["Outputs"], 3, options=[("opt_n", "opt_strike")], rsus="rsus",
                     converts=[("conv_shares", "conv_price")])
    m = B.three_statement(wb["Model"], 3, names={"sbc": "sbc_pct"}, **TS.MODEL_ARGS)
    d = B.dcf_from_statements(wb["DCF"], 3, m, names={"minorities": "minorities", "leases": "lease_liab",
                                                       "investments": "eq_invest"})
    al = B.alerts(wb, m, d)
    wb.save(path)
    return m, d, al


@pytest.mark.skipif(not HAS_LO, reason="LibreOffice needed")
def test_sbc_share_count_bridge_and_alerts(tmp_path):
    p = tmp_path / "depth.xlsx"
    m, d, al = depth_book(p)
    wb = load_workbook(qa.recalc(str(p), str(tmp_path / "rc")), data_only=True)
    M, D = wb["Model"], wb["DCF"]
    row = lambda k: [M[f"{c}{m['rows'][k]}"].value for c in m["cols"]]  # noqa: E731
    assert row("sbc") == pytest.approx([-x * 0.02 for x in row("revenue")])              # expense in EBITDA
    for i in range(len(m["cols"])):
        assert row("cfo")[i] == pytest.approx(row("cf_ni")[i] + row("cf_da")[i] + row("cf_nwc")[i] - row("sbc")[i])
        prev_eq = M[f"{(['B'] + m['cols'])[i]}{m['rows']['eq_end']}"].value
        assert row("eq_end")[i] == pytest.approx(prev_eq + row("net_income")[i] + row("dividends")[i]
                                                 + row("buybacks")[i] - row("sbc")[i])
    assert row("bal_check") == pytest.approx([0] * len(m["cols"]), abs=1e-7) and wb["Checks"]["F3"].value == "OK"
    fcf = [D[f"{c}{d['rows']['fcf']}"].value for c in d["cols"]]
    ebitda, tax, capex, nwc = ([D[f"{c}{d['rows'][k]}"].value for c in d["cols"]] for k in ("ebitda", "tax", "capex", "nwc"))
    assert fcf == pytest.approx([a + b + c_ + e for a, b, c_, e in zip(ebitda, tax, capex, nwc)])   # SBC not added back

    def named(n):
        sh, _, c = str(wb.defined_names[n].attr_text).rpartition("!")
        return wb[sh.strip("'")][c.replace("$", "")].value
    assert named("diluted_shares") == pytest.approx(48 + 4 * (1 - 18 / 36) + 1 + 0)          # convert out of the money
    ev = named("dcf_ev")
    debt, cash = 300.0 + 10.0, 80.0
    assert named("dcf_equity") == pytest.approx(ev - debt + cash - 25 - 40 + 15)
    assert named("dcf_per_share") == pytest.approx(named("dcf_equity") / named("diluted_shares"))
    labels = [D.cell(row=r, column=1).value for r in range(1, D.max_row + 1)]
    assert "Less: minority interest ($m)" in labels and "Plus: equity investments and other non-operating assets ($m)" in labels
    A = wb["Alerts"]
    status = {A.cell(row=r, column=1).value: A.cell(row=r, column=3).value for r in range(4, A.max_row + 1)}
    assert set(status.values()) <= {"OK", "REVIEW"} and len(status) >= 6
    tv = named("dcf_tv_share")
    assert status["Terminal value share of enterprise value (%)"] == ("OK" if tv <= 0.75 else "REVIEW")
    assert status["Terminal growth minus the risk-free rate (pp)"] == "OK"                      # 2.5% < 4.2%
