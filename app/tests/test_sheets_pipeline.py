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
                "cash_yield": (4.0, "F17 Treasury yield"),                       # 4.0 meant 4%: normalised
                "down_growth": (0.03, "F18 half the 3-year growth"), "down_margin_cut": (0.01, "assumption"),
                "capex_overrun": (0.15, "assumption")}
        out = {"company": "Costco", "title": "Costco capital allocation", "first_year": 2027, "years": 5,
               "inputs": {k: {"value": v, "source": s, "rationale": "test",
                              **({"low": v * 0.5, "high": v * 1.5} if k in ("rev_growth", "min_cash") else {})}
                          for k, (v, s) in vals.items()}}
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
    assert abs(res["inputs"]["rev_growth"]["low"] - 0.035) < 1e-9 and abs(res["inputs"]["rev_growth"]["high"] - 0.105) < 1e-9
    assert qa.value(res["path"], "Outputs!B21") in ("2027", "2028", "2029", "2030", "2031")


@pytest.mark.skipif(not find_soffice(), reason="LibreOffice not installed")
def test_the_pipeline_revises_its_inputs_with_the_reviewers_issues(tmp_path, monkeypatch):
    """Round 1 not signed off: the issues go back to the input call, the workbook is rebuilt and reviewed again."""
    from engines.sheets import engine as E
    monkeypatch.setenv("MH_REVIEW_ROUNDS", "2")
    asked = []

    class Model(Inputs):
        def chat(self, *, model, messages, **kw):
            asked.append(messages[-1]["content"])
            return super().chat(model=model, messages=messages, **kw)

    class Review:
        def __init__(self):
            self.rounds = []

        def run_round(self, path, rep, final=False):
            k = len(self.rounds) + 1
            rnd = {"round": k, "signed_off": k == 2, "score": 6.0 + k, "copy": path, "issues": [
                {"severity": "blocking", "persona": "md", "where": "Inputs!rev_growth", "issue": "unsourced growth",
                 "fix": "anchor on the 3-year history"}] if k == 1 else []}
            self.rounds.append(rnd)
            return rnd

        def best(self):
            return max(self.rounds, key=lambda r: r["score"])

        def report(self, k):
            return f"best round {k}"

    ws = E.SheetsWorkspace(str(tmp_path), str(tmp_path / ".run"))
    out = E._pipeline("capital allocation model for Costco", ws, Model(), "m", "t", Review())
    assert len(asked) == 2 and "unsourced growth" in asked[1] and "anchor on the 3-year history" in asked[1]
    assert out["ok"] and "best round 2" in out["answer"]
