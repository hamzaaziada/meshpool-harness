"""capital_allocation(): cash generated and its uses, every number a formula, checks that tie, recalculated."""
import pytest

from engines.forge.common import find_soffice
from engines.sheets import builder as B
from engines.sheets import qa


@pytest.mark.skipif(not find_soffice(), reason="LibreOffice not installed")
def test_capital_allocation_block_recalculates_clean_and_ties(tmp_path):
    wb = B.new_workbook(("Inputs", "Model", "Checks"))
    book = B.assumption_book(wb["Inputs"])
    for label, v, fmt, name in [("Revenue FY2026 ($m)", 303150, "usd", "revenue_base"), ("Revenue growth", 0.07, "pct", "rev_growth"),
                                ("OCF margin", 0.052, "pct", "ocf_margin"), ("Capex FY2027 ($m)", 7500, "usd", "capex_base"),
                                ("Capex growth", 0.05, "pct", "capex_growth"), ("Opening cash ($m)", 20210, "usd", "cash"),
                                ("Cash floor ($m)", 8000, "usd", "min_cash"), ("Shares (m)", 443.1, "num", "shares"),
                                ("Share price ($)", 1106.21, "usd2", "share_price"), ("Regular DPS ($)", 5.88, "usd2", "dps"),
                                ("DPS growth", 0.1, "pct", "dps_growth"), ("Special DPS ($)", 30, "usd2", "special_dps"),
                                ("Buyback per year ($m)", 1000, "usd", "buyback"), ("Cash yield", 0.04, "pct", "cash_yield")]:
        book.add(label, v, fmt, name=name, source="test")
    out = B.capital_allocation(wb["Model"], 1, [2027, 2028, 2029, 2030, 2031])
    p = tmp_path / "ca.xlsx"
    wb.save(p)
    res = qa.check(str(p))
    bad = [i for i in res["issues"] if not (isinstance(i, dict) and i.get("check") == "hardcoded_number")]
    assert bad == [], bad
    assert qa.value(str(p), "Checks!F3") == "OK"                         # every check ties
    fcf1 = qa.value(str(p), "Model!B6")
    assert abs(fcf1 - (303150 * 1.07 * 0.052 - 7500)) < 0.5             # FY2027 free cash flow
    assert out["cash_end"] == "ca_cash_end"
