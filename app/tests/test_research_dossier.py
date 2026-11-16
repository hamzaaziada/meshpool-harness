"""The dossier researches the company before a deck or model is planned (engines/research/dossier.py)."""
import json
import re

from engines.research import dossier as D

PAGE = ("Cleopatra Hospitals Group completed a sale and leaseback of three hospitals in March 2025 as part of its "
        "asset-light strategy. Cleopatra Hospitals Group reported revenue of EGP 6.2 billion in 2025, up 31% on 2024. "
        "Management expects capital expenditure to fall to EGP 400 million in 2026 under the asset-light model.")


def search(q, n, recency="", region="", site=""):
    return [{"url": "https://ir.cleopatrahospitals.example/2025-results", "title": "Cleopatra Hospitals Group results",
             "snippet": "asset-light"}] if "cleopatra" in q.lower() else []


def fetch(url):
    return {"title": "Cleopatra Hospitals Group FY2025 results", "text": PAGE * 2, "url": url, "date": "2026-03-01"}


class Model:
    def __init__(self):
        self.asked = []

    def chat(self, *, model, messages, **kw):
        sys_, user = messages[0]["content"], messages[-1]["content"]
        self.asked.append(sys_[:40])
        if "which companies it is about" in sys_:
            out = {"companies": [{"name": "Cleopatra Hospitals Group S.A.E.", "short": "Cleopatra Hospitals Group",
                                  "ticker": "CLHO", "exchange": "EGX", "country": "Egypt", "market_symbol": "",
                                  "ir_site": ""}],
                   "decision": "value the group", "questions": ["How many beds does the group operate?"]}
        elif "extract sourced facts" in sys_:
            pid = re.search(r"\[(S\d+\.p\d+)\]", user).group(1)
            out = {"facts": [{"passage": pid, "text": "Cleopatra Hospitals Group completed a sale and leaseback of "
                                                      "three hospitals in March 2025 as part of its asset-light strategy"},
                             {"passage": pid, "text": "Management expects capital expenditure to fall to EGP 400 "
                                                      "million in 2026 under the asset-light model"}]}
        elif "structural changes" in sys_:
            ids = re.findall(r"^(F\d+):", user, re.M)
            out = {"changes": [{"change": "Asset-light shift: sale and leaseback of three hospitals", "when": "2025",
                                "evidence": ids[:1], "affects": ["capex", "depreciation", "leases"],
                                "so_what": "model rent, not depreciation, on the sold hospitals"},
                               {"change": "Invented change", "when": "2026", "evidence": ["F999"], "affects": []}]}
        else:
            out = {}
        return {"choices": [{"message": {"content": json.dumps(out)}}]}


def test_dossier_researches_the_company_first_and_lists_what_changed(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_RESEARCH_EDGAR", "0")
    facts = [{"id": "F1", "text": "Revenue EGP 4.7 billion in 2024"}]
    d = D.build_dossier("Build a valuation model for Cleopatra Hospitals Group.", facts, client=Model(),
                        models=["m"], out_dir=str(tmp_path), search_fn=search, fetch_fn=fetch, now="2026-11-15",
                        max_questions=4)
    assert d["identity"]["companies"][0]["exchange"] == "EGX"
    assert d["questions"][0]["question"].startswith("What changed in Cleopatra Hospitals Group")
    assert d["facts"] and all(f["id"].startswith("F") and f["id"] != "F1" for f in d["facts"])
    assert any("asset-light" in f["text"] for f in d["facts"])
    assert [c["change"][:17] for c in d["changes"]] == ["Asset-light shift"]       # the unsupported one is dropped
    md = (tmp_path / "dossier.md").read_text()
    assert "What changed" in md and "Asset-light shift" in md
    assert "plan and model around it" in D.brief_block(d)


def test_checklist_covers_the_analyst_basics_and_the_briefs_own_questions():
    qs = D.questions_for({"companies": [{"short": "Acme", "country": "Egypt"}], "questions": ["Q from brief?"]},
                         now="2026-01-01", limit=20)
    kinds = [q["kind"] for q in qs]
    for k in ("strategy", "results", "guidance", "deals", "financing", "capex", "news", "brief"):
        assert k in kinds
    assert next(q for q in qs if q["kind"] == "news")["recency"] == "month"
    assert "since 2024" in next(q for q in qs if q["kind"] == "deals")["question"]
    assert D._queries(dict(qs[0], company="Acme"), {"companies": [{}]})[0].startswith('"Acme"')


def test_a_long_document_sharing_a_short_ones_boilerplate_is_not_its_duplicate(tmp_path):
    from engines.research.library import Corpus
    c = Corpus(str(tmp_path))
    boiler = " ".join(f"Costco Wholesale Corporation operates membership warehouses worldwide clause {i}." for i in range(40))
    short = c.add_text("Dividend notice", "The board declared a quarterly dividend of $1.47 per share. " + boiler)
    body = " ".join(f"Net sales for the quarter rose to ${100 + i} billion in segment {i} with margin detail." for i in range(400))
    long_ = c.add_text("Q4 earnings release", body + " " + boiler)
    copy = c.add_text("Syndicated dividend notice", "The board declared a quarterly dividend of $1.47 per share. " + boiler + " Reuters")
    assert getattr(long_, "duplicate_of", None) in (None, "")
    assert copy.duplicate_of == short.id


def test_xbrl_facts_add_the_companys_own_three_year_growth():
    from engines.forge import research as R

    def u(start, end, val, filed):
        return {"start": start, "end": end, "val": val, "form": "10-K", "filed": filed, "accn": "0000-00-1"}
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        u("2022-09-01", "2023-08-31", 100e9, "2023-10-10"), u("2025-09-01", "2026-08-31", 133.1e9, "2026-10-10")]}}}}}
    got = R.xbrl_facts(["ACME"], 1, ticker_map={"0": {"ticker": "ACME", "title": "ACME CORP", "cik_str": 1}},
                       fetch=lambda cik: facts)
    g = [f for f in got if "grew" in f["text"]]
    assert g and "10.0% a year over three years" in g[0]["text"] and g[0]["derived"]


def test_research_stays_on_the_subject_company():
    """A reviewer's unnamed question is about the brief's company; a page about another company does not answer it."""
    from types import SimpleNamespace as NS
    from engines.forge.research import _about_subject, _mentions
    assert _about_subject("What is the fiscal 2027 capex plan and expected free cash flow?", ["Costco"])
    assert _about_subject("Does Costco's buyback beat the Treasury yield?", ["Costco"])
    assert not _about_subject("How does Walmart's membership income compare?", ["Costco"])
    msft = NS(text="We expect to remain free cash flow positive in FY27.",
              source=NS(label="Microsoft FY2027 Q1 earnings call", url="https://www.microsoft.com/investor"))
    cost = NS(text="Our share repurchase program is under a $4,000 authorization.",
              source=NS(label="COSTCO WHOLESALE CORP 10-Q", url="https://www.sec.gov/x/cost-20260510.htm"))
    assert not _mentions(msft, ["Costco"]) and _mentions(cost, ["Costco"])
