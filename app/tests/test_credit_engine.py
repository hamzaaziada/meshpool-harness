"""Credit engine: lender cash flow measures by hand, the workbook recalculated in LibreOffice against the Python
analysis, the fixed pipeline with a mocked small model (two calls at most), the commentary number filter, routing."""
import json
import shutil
import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engines.credit import analysis as A  # noqa: E402

HAS_LO = shutil.which("soffice") is not None or shutil.which("libreoffice") is not None
CSV = """Acme Manufacturing (USD millions),FY2024,FY2025
Net sales,100,110
Cost of goods sold,(60),(66)
Operating income,15,17
Depreciation,5,6
Interest expense,(2),(2.5)
Income taxes,(3),(3.4)
Net income,10,11.1
Capital expenditures,(4),(5)
Dividends paid,(1),(2)
Cash from operating activities,14,12
Cash,8,6
Accounts receivable,12,16
Inventory,10,13
Total current assets,32,37
"Property, plant and equipment, net",40,42
Goodwill,5,5
Total assets,80,87
Accounts payable,7,8
Line of credit,3,4
Current portion of long-term debt,4,4
Total current liabilities,16,18
"Long-term debt, net of current portion",20,16
Total liabilities,38,36
Total stockholders’ equity,42,51
Total liabilities and equity,80,87
Customer deposits,1,1
"""


@pytest.fixture
def fin(tmp_path):
    p = tmp_path / "acme.csv"
    p.write_text(CSV)
    return A.from_table(str(p), name="Acme Manufacturing")


def test_measures_by_hand(fin):
    assert fin.years == ["FY2024", "FY2025"] and fin.v("FY2025", "cogs") == -66 and fin.v("FY2025", "capex") == -5
    assert fin.sources["_unmapped"] == ["Customer deposits"]
    loan = A.Loan(amount=10, rate=0.08, amort_years=5)
    r = A.analyse(fin, loan, A.Policy())
    h = r["history"][-1]
    assert h["ebitda"] == 23 and h["cads"] == pytest.approx(23 - 3.4 - 5 - 2)          # 12.6
    assert h["debt_service"] == pytest.approx(2.5 + 4) and h["dscr"] == pytest.approx(12.6 / 6.5)
    assert h["leverage"] == pytest.approx(24 / 23) and h["tnw"] == 46 and h["debt_tnw"] == pytest.approx(36 / 46)
    assert h["current_ratio"] == pytest.approx(37 / 18) and h["dso"] == pytest.approx(16 / 110 * 365)
    pay = 10 * 0.08 / (1 - 1.08 ** -5)
    assert r["pro_forma"]["new_loan_payment"] == pytest.approx(pay)
    assert r["pro_forma"]["dscr"] == pytest.approx(12.6 / (6.5 + pay))
    cap = r["capacity"]
    assert cap["by_dscr"] == pytest.approx(A.pv(0.08, 5, 12.6 / 1.25 - 6.5))
    assert cap["by_leverage"] == pytest.approx(3.5 * 23 - 24)
    assert cap["max_loan"] == pytest.approx(min(cap["by_dscr"], cap["by_leverage"])) and cap["binding"] == "DSCR"
    dscr_cov = r["covenants"][0]
    assert dscr_cov["pass"] and dscr_cov["ebitda_cushion"] == pytest.approx((12.6 - 1.25 * (6.5 + pay)) / 23)
    assert r["stress"][0]["dscr"] == pytest.approx((12.6 - 2.3) / (6.5 + pay))
    assert any("Receivable days" in f for f in r["flags"]) and any("earnings quality" not in f for f in r["flags"])
    assert r["rating"]["grade"] in (1, 2, 3)
    big = A.analyse(fin, A.Loan(amount=60, rate=0.08, amort_years=5), A.Policy())
    assert not big["capacity"]["within_capacity"] and not big["covenants"][0]["pass"]


@pytest.mark.skipif(not HAS_LO, reason="LibreOffice needed for recalculation")
def test_workbook_matches_the_analysis(fin, tmp_path):
    from engines.credit import workbook as W
    from engines.forge import validate_xlsx as VX
    from engines.forge.ooxml_check import _schema_dir
    from engines.sheets import qa
    loan, pol = A.Loan(amount=10, rate=0.08, amort_years=5, refinance_existing=True), A.Policy()
    r = A.analyse(fin, loan, pol)
    p = tmp_path / "credit.xlsx"
    out = W.build(fin, loan, pol, str(p))
    wb = load_workbook(qa.recalc(str(p), str(tmp_path / "rc")), data_only=True)
    C, K = wb["Cash flow"], wb["Capacity"]
    for i, c in enumerate(out["cols"]):
        h = r["history"][i]
        for key, mk in (("ebitda", "ebitda"), ("cads", "cads"), ("debt_service", "ds"), ("dscr", "dscr"),
                        ("leverage", "lev"), ("debt_tnw", "dtnw"), ("current_ratio", "cr")):
            assert C[f"{c}{out['cash_flow_rows'][mk]}"].value == pytest.approx(h[key], rel=1e-9), (key, c)
    n = out["capacity_cells"]
    pf, cap = r["pro_forma"], r["capacity"]
    for cell, v in ((n["pf_ds"], pf["debt_service"]), (n["pf_dscr"], pf["dscr"]), (n["pf_lev"], pf["leverage"]),
                    (n["cap_dscr"], cap["by_dscr"]), (n["cap_lev"], cap["by_leverage"]), (n["capacity"], cap["max_loan"])):
        assert K[cell].value == pytest.approx(v, rel=1e-9), cell
    assert wb["Checks"]["F3"].value == "OK"
    assert qa.check(str(p))["issues"] == [] and qa.missing_sources(str(p)) == []
    res = VX.validate(str(p))
    allowed = {"X11": "not_run", "X7": "not_run"} | ({} if _schema_dir() else {"X0": "not_run"})
    bad = {k: v for k, v in res["rules"].items() if v != "pass" and allowed.get(k) != v}
    assert not bad, (bad, [f["message"] for f in res["failures"] if f["status"] == "fail"][:8])


def test_pipeline_with_a_small_model(fin, tmp_path):
    from meshpool_harness.core.client import MockClient
    from engines.credit.engine import parse_request, solve
    assert parse_request("Underwrite a $12.5m 7-year term loan at 6.75% to fund a new plant for Acme (ACME)") == {
        "amount": 12.5, "rate": 0.0675, "amort_years": 7.0, "ticker": "ACME", "purpose": "fund a new plant for acme"}
    assert parse_request("loan of $750k")["amount"] == 0.75
    csv = tmp_path / "acme.csv"
    csv.write_text(CSV)
    commentary = {"business": "Acme makes industrial parts. EBITDA was 23.0 in FY2025 and margins are 99%.",
                  "risks": ["Receivable days rose sharply.", "Customer concentration could reach 45%."],
                  "mitigants": ["Pro forma DSCR is 1.37x."]}
    client = MockClient([{"role": "assistant", "content": json.dumps(commentary)}])
    out = solve("Credit memo for a $10m 5-year term loan at 8% for Acme", workdir=str(tmp_path / "w"),
                task_id="t", files=[str(csv)], client=client, model="local-8b")
    assert out["ok"] and len(client.requests) == 1                    # amount, rate, term parsed: one call only
    assert client.requests[0]["model"] == "local-8b" and client.requests[0]["temperature"] == 0.0
    names = sorted(Path(a).name for a in out["artifacts"])
    assert any(n.endswith(".xlsx") for n in names) and any(n.endswith(".docx") for n in names)
    memo = next(Path(a) for a in out["artifacts"] if a.endswith(".md")).read_text()
    assert "Recommendation: Approve" in memo and "| DSCR |" in memo and "Customer deposits" in memo
    assert "$10.0m term loan at 8.00%" in memo
    assert "Acme makes industrial parts" in memo and "99%" not in memo and "45%" not in memo   # invented numbers cut
    assert "Pro forma DSCR" in out["answer"]
    missing = solve("Credit memo for a $10m loan", workdir=str(tmp_path / "x"), task_id="t", files=[], client=None)
    assert not missing["ok"] and "ticker" in missing["reason"]


def test_router_sends_credit_work_to_the_credit_engine():
    from meshpool_harness.router.rules import decide
    assert decide("Prepare a credit memo with DSCR for this borrower", ["fs.xlsx"])[0] == ["credit"]
    assert decide("What is the debt capacity of Acme at 1.25x DSCR?")[0] == ["credit"]
    assert decide("Fix the failing DSCR unit test in the loan module")[0] != ["credit"]
