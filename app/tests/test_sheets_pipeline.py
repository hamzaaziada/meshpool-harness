"""The capital-allocation recipe: one model call for sourced inputs, the workbook written in code, checks pass."""
import json

import pytest

from engines.forge.common import find_soffice
from engines.sheets import pipeline as PL


class Inputs:
    def chat(self, *, model, messages, **kw):
        vals = {"revenue_base": (303150, "F2 10-K FY2026"), "rev_growth": (0.07, "assumption"),
                "ocf_margin": (0.052, "F7/F2"), "capex_base": (7500, "F34 expansion plan"),
                "capex_growth": (0.03, "assumption"), "cash": (20210, "F5"), "min_cash": (8000, "assumption"),
                "shares": (443.1, "F1"), "share_price": (1106.21, "F18"), "dps": (5.88, "F12"),
                "dps_growth": (0.1, "assumption"), "special_dps": (30, "F35"), "buyback": (1000, "F36"),
                "cash_yield": (4.0, "F17 Treasury yield")}                       # 4.0 meant 4%: normalised
        out = {"company": "Costco", "title": "Costco capital allocation", "first_year": 2027, "years": 5,
               "inputs": {k: {"value": v, "source": s, "rationale": "test"} for k, (v, s) in vals.items()}}
        return {"choices": [{"message": {"content": json.dumps(out)}}]}


def test_recipe_applies_to_capital_allocation_requests_only(monkeypatch):
    assert PL.applies("Build an Excel capital allocation model for Costco") == "capital_allocation"
    assert PL.applies("Model the buyback and special dividend") == "capital_allocation"
    assert PL.applies("Build a DCF for NVIDIA") is None
    monkeypatch.setenv("MH_SHEETS_PIPELINE", "0")
    assert PL.applies("capital allocation model") is None


@pytest.mark.skipif(not find_soffice(), reason="LibreOffice not installed")
def test_capital_allocation_recipe_builds_a_workbook_that_passes_the_engine_checks(tmp_path):
    from engines.sheets.engine import SheetsWorkspace, finish_check
    from engines.sheets import qa
    ws = SheetsWorkspace(str(tmp_path), str(tmp_path / ".run"))
    res = PL.build("Build a capital allocation model for Costco", ws, Inputs(), "m")
    assert res["inputs"]["cash_yield"]["value"] == 0.04
    assert finish_check("", [res["path"]], ws) is None
    assert qa.value(res["path"], "Checks!F3") == "OK"
    assert "assumption" in " ".join(res["notes"])
