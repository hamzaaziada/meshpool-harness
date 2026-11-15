"""Partner and MD review of the sheets engine's workbook: the digest the reviewers read, and the review loop (feedback
to the agent, re-review after the next finish, best version kept, review.json, honest not-run reporting)."""
import json
import re
import shutil
import sys
import threading
import warnings
from pathlib import Path

import pytest
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
warnings.filterwarnings("ignore")

from meshpool_harness.core.client import LiveClient, MockClient, tool_call  # noqa: E402
from engines.sheets import engine as E  # noqa: E402
from engines.sheets.review_digest import digest  # noqa: E402
from test_sheets_finance import build  # noqa: E402

HAS_LO = shutil.which("soffice") is not None or shutil.which("libreoffice") is not None


def _variant(path: Path, hard_code: bool = False, inconsistent: bool = False, div0: bool = False) -> Path:
    """The finance test workbook, optionally with a hard-coded growth inside a DCF formula, one forecast cell that
    breaks its row's pattern, or a #DIV/0! on Outputs."""
    build(str(path))
    wb = load_workbook(path)
    D = wb["DCF"]
    rev = next(r for r in range(1, D.max_row + 1) if str(D.cell(r, 1).value or "").startswith("Revenue"))
    if hard_code:
        D.cell(rev, 4).value = D.cell(rev, 4).value + "*1.07"
    if inconsistent:
        D.cell(rev, 5).value = "=" + D.cell(rev, 5).coordinate.replace("E", "D") + "*2.5"
    if div0:
        wb["Outputs"]["H2"] = "=1/0"
    wb.save(path)
    return path


@pytest.fixture(scope="module")
def books(tmp_path_factory):
    d = tmp_path_factory.mktemp("books")
    (d / "v1").mkdir()
    (d / "v2").mkdir()
    return {"v1": _variant(d / "v1" / "Model.xlsx", hard_code=True), "v2": _variant(d / "v2" / "Model.xlsx")}


# ---------------------------------------------------------------- digest
def test_digest_has_the_key_sections_within_budget(tmp_path):
    p = _variant(tmp_path / "Model.xlsx", hard_code=True, inconsistent=True, div0=True)
    t = digest(str(p), report={"rules": {"X1": "pass", "X2": "fail"}, "warnings": ["X10 Inputs!A9: label has no unit"]})
    assert len(t) <= 25000
    for sec in ("TABS AND PURPOSE", "KEY OUTPUTS", "CHECKS TAB", "FORGE WORKBOOK RULES", "ERROR CELLS",
                "ASSUMPTION BOOK", "HARD-CODED NUMBERS INSIDE FORMULAS", "FORMULA SAMPLES"):
        assert f"## {sec}" in t
    assert "- DCF (" in t and "- Comps (" in t                                    # tabs with purpose
    assert re.search(r"dcf_per_share \(DCF!\w+\d+\) = [\d,.]+", t)               # outputs with values
    assert re.search(r"dcf_wacc \(DCF!\w+\d+\) = \d+\.\d%", t) and "dcf_tv_share" in t
    assert "Risk free (%) [risk_free] = 4.2%" in t and "source: Test source for risk_free" in t
    assert "range 3.8% to 4.6%" in t                                              # name, value, unit, range, source
    assert "EV bridge ties" in t and "status OK" in t                             # the Checks tab
    assert "X1=pass X2=fail" in t and "label has no unit" in t                    # forge rules and warnings
    assert "1.07 in =" in t                                                        # hard-code inside a formula
    assert "INCONSISTENT across periods" in t                                      # a broken row pattern
    assert "Outputs!H2 = #DIV/0!" in t                                             # error cells
    if HAS_LO:
        assert "values recalculated in LibreOffice" in t
    small = digest(str(p), budget=6000)
    assert len(small) <= 6000 and "## KEY OUTPUTS" in small and "dcf_per_share" in small and "## CHECKS TAB" in small


def test_digest_of_the_banker_example_reads_tab_purposes_from_the_cover():
    t = digest(str(ROOT / "engines" / "sheets" / "examples" / "banker.xlsx"))
    assert "- Calc (" in t and "Calculations. Formulas only" in t
    assert "Start revenue ($K) = 1,000 (no Source column)" in t
    assert "Summary ties to Calc" in t and "Master status (Checks!F3): OK" in t


# ---------------------------------------------------------------- loop
MD_DIMS = ("integrity", "assumptions", "valuation", "structure", "outputs")
PARTNER_DIMS = ("relevance", "clarity", "scenarios")
BLOCKER = {"severity": "blocking", "scope": "logic", "where": "DCF!C14",
           "issue": "Hard-coded growth: revenue formula multiplies by 1.07 instead of a sourced input",
           "fix": "Replace *1.07 with a named, sourced growth input on Inputs"}


def reply(persona, score, issues=(), headline=""):
    dims = MD_DIMS if persona == "md" else PARTNER_DIMS
    return {"persona": persona, "target": "workbook", "scores": {k: score for k in dims}, "headline": headline,
            "verdict": "sign_off" if score >= 8 else "revise", "issues": list(issues), "research_requests": []}


class Stub:
    """The agent replays a script; reviewer calls (their system prompt) are answered per round and persona."""

    def __init__(self, script, rounds, fail=None):
        self.agent = MockClient(script)
        self.rounds, self.fail = rounds, fail
        self.review_calls = []
        self.lock = threading.Lock()

    def chat(self, *, model, messages, tools=None, **kw):
        sysm = messages[0]["content"] if messages and isinstance(messages[0].get("content"), str) else ""
        if "You are reviewing a financial MODEL" in sysm:
            who = "partner" if "senior partner" in sysm else "md"
            k = int(re.search(r"-r(\d+)-", kw.get("task_id", "")).group(1))
            with self.lock:
                self.review_calls.append({"round": k, "persona": who, "model": model,
                                          "material": messages[1]["content"]})
            if self.fail:
                raise RuntimeError(self.fail)
            return {"choices": [{"message": {"content": json.dumps(self.rounds[k - 1][who])}}]}
        return self.agent.chat(model=model, messages=messages, tools=tools, **kw)


def _passing(monkeypatch):
    def check(answer, artifacts, ws, report=None):
        if report is not None:
            report["rules"] = {"X1": "pass", "X2": "pass"}
            report["warnings"] = []
        return None
    monkeypatch.setattr(E, "finish_check", check)


def _cp(src):
    return tool_call("bash", {"command": f"cp '{src}' Model.xlsx"})


def _finish(src, answer="Built Model.xlsx"):
    return tool_call("finish", {"answer": answer, "evidence": [f"cp '{src}' Model.xlsx"], "artifacts": ["Model.xlsx"]})


def test_review_feedback_reaches_the_agent_and_round_two_signs_off(tmp_path, monkeypatch, books):
    _passing(monkeypatch)
    rounds = [{"md": reply("md", 6, [BLOCKER], "DCF growth is a plug"), "partner": reply("partner", 7.5)},
              {"md": reply("md", 8.6, headline="Defensible"), "partner": reply("partner", 8.4)}]
    c = Stub([_cp(books["v1"]), _finish(books["v1"]), _cp(books["v2"]), _finish(books["v2"], "Fixed growth")], rounds)
    r = E.solve("Build an IC model", workdir=str(tmp_path), task_id="t", max_turns=12, client=c, model="mock",
                review=True, review_model="strong-model")
    assert r["ok"] and r["reason"] == "finished"
    # round 1: the finish reply says review, and the issues come to the agent as a user message
    msgs = c.agent.requests[2]["messages"]
    assert any(m["role"] == "tool" and m["content"].startswith("REVIEW:") for m in msgs)
    note = next(m["content"] for m in msgs if m["role"] == "user" and "Partner and MD review, round 1" in m["content"])
    assert "DCF!C14" in note and "Hard-coded growth" in note and "[blocking]" in note
    assert "Fix the blocking and major issues" in note and "call finish again" in note
    # the reviewers read the digest of the workbook each round, with the strong model
    assert sorted((x["round"], x["persona"]) for x in c.review_calls) == [(1, "md"), (1, "partner"), (2, "md"),
                                                                          (2, "partner")]
    assert {x["model"] for x in c.review_calls} == {"strong-model"}
    m1 = next(x["material"] for x in c.review_calls if x["round"] == 1)
    assert "## KEY OUTPUTS" in m1 and "## ASSUMPTION BOOK" in m1 and "1.07 in =" in m1
    m2 = next(x["material"] for x in c.review_calls if x["round"] == 2)
    assert "1.07 in =" not in m2
    a = r["answer"]
    assert a.startswith("Fixed growth") and "Partner and MD review (2 rounds" in a
    assert "Round 1: MD (bulge-bracket bank) 6.0/10 (revise); senior partner (strategy firm) 7.5/10 (revise); " \
           "1 blocking" in a
    assert "Round 2: MD (bulge-bracket bank) 8.6/10 (signs off); senior partner (strategy firm) 8.4/10 (signs off)" in a
    assert "Both reviewers signed off on the delivered version (round 2)" in a
    rj = json.loads((tmp_path / ".run" / "review.json").read_text())
    assert [x["round"] for x in rj["rounds"]] == [1, 2] and rj["delivered_round"] == 2
    assert rj["rounds"][0]["issues"][0]["where"] == "DCF!C14" and rj["rounds"][1]["signed_off"]
    assert (tmp_path / ".run" / "review" / "round1" / "Model.xlsx").exists()
    assert (tmp_path / ".run" / "review" / "round1" / "digest.txt").read_text().startswith("WORKBOOK DIGEST")
    assert r["review"]["signed_off"] and r["review"]["delivered_round"] == 2


def test_reviewers_that_cannot_run_are_reported_not_faked(tmp_path, monkeypatch, books):
    _passing(monkeypatch)
    c = Stub([_cp(books["v1"]), _finish(books["v1"])], [], fail="HTTP 401: Invalid or missing API key")
    r = E.solve("Build an IC model", workdir=str(tmp_path), task_id="t", max_turns=10, client=c, model="mock",
                review=True)
    assert r["ok"] and r["reason"] == "finished"
    a = r["answer"]
    assert "the reviewers could not run" in a and "HTTP 401" in a and "No review scores are reported" in a
    assert "/10" not in a
    assert len(c.review_calls) == 2                       # a refused key: no other model is tried
    rj = json.loads((tmp_path / ".run" / "review.json").read_text())
    assert rj["rounds"][0]["not_run"] == ["md", "partner"] and "HTTP 401" in rj["stopped"]
    assert not r["review"]["signed_off"]


def test_best_version_is_delivered_when_round_two_scores_worse(tmp_path, monkeypatch, books):
    _passing(monkeypatch)
    major = {"severity": "major", "scope": "assumptions", "where": "Inputs!B9", "issue": "Exit multiple unsourced",
             "fix": "Source it from the comps median"}
    rounds = [{"md": reply("md", 7.4, [major]), "partner": reply("partner", 7.6)},
              {"md": reply("md", 5.0, [BLOCKER]), "partner": reply("partner", 6.0)}]
    c = Stub([_cp(books["v1"]), _finish(books["v1"]), _cp(books["v2"]), _finish(books["v2"])], rounds)
    r = E.solve("Build an IC model", workdir=str(tmp_path), task_id="t", max_turns=12, client=c, model="mock",
                review=True, review_rounds=2)
    assert r["ok"]
    assert E._sha(tmp_path / "Model.xlsx") == E._sha(books["v1"])      # round 1's workbook, not the worse round 2
    assert r["artifacts"] == [str(tmp_path / "Model.xlsx")]
    a = r["answer"]
    assert "Delivered the round 1 version" in a and "Not signed off (the review budget ran out)" in a
    assert "best-scoring version (round 1" in a and "Exit multiple unsourced" in a
    rj = json.loads((tmp_path / ".run" / "review.json").read_text())
    assert rj["best_round"] == 1 and rj["delivered_round"] == 1 and len(rj["rounds"]) == 2


def test_cap_with_passing_workbook_gets_a_final_review(tmp_path, monkeypatch, books):
    _passing(monkeypatch)
    c = Stub([_cp(books["v2"])] + [tool_call("bash", {"command": f"echo refine {i}"}) for i in range(4)],
             [{"md": reply("md", 8.2), "partner": reply("partner", 8.1)}])
    r = E.solve("Build an IC model", workdir=str(tmp_path), task_id="t", max_turns=5, client=c, model="mock",
                review=True)
    assert r["ok"] and r["reason"].startswith("turn cap 5 reached")
    assert "Round 1 (at the turn cap): MD (bulge-bracket bank) 8.2/10 (signs off)" in r["answer"]
    assert "Both reviewers signed off" in r["answer"]


def test_review_is_on_for_live_clients_and_off_for_stubs_or_when_disabled(monkeypatch):
    live = object.__new__(LiveClient)
    monkeypatch.delenv("MH_MODEL_REVIEW", raising=False)
    assert E.review_enabled(live) and not E.review_enabled(MockClient([]))
    assert E.review_enabled(MockClient([]), True) and not E.review_enabled(live, False)
    monkeypatch.setenv("MH_MODEL_REVIEW", "0")
    assert not E.review_enabled(live)


def test_without_review_the_finish_gate_and_answer_are_unchanged(tmp_path, monkeypatch, books):
    _passing(monkeypatch)
    c = MockClient([_cp(books["v2"]), _finish(books["v2"])])
    r = E.solve("Build", workdir=str(tmp_path), task_id="t", max_turns=6, client=c, model="mock")
    assert r["ok"] and r["answer"] == "Built Model.xlsx" and "review" not in r
    assert not (tmp_path / ".run" / "review.json").exists()
