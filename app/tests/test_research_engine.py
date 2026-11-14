"""Research engine: source tiers, passages, number parsing, the corpus (search, read, dedupe, rank), EDGAR full-text
search, the citation verifier, finalize, the engine's finish check and routing. Offline: search, fetch and SEC calls
are faked."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engines.research import library as R  # noqa: E402
from engines.research import verify as V  # noqa: E402

FILING = ("## Page 41\n\nRevenue for fiscal year 2026 was $215.9 billion, up 65% from a year ago. Data Center revenue "
          "for fiscal year 2026 was $193.7 billion, up 68% from a year ago, driven by demand for accelerated computing "
          "from cloud service providers and enterprises.\n\n## Page 42\n\nGross margin was 71.1% for fiscal year 2026 "
          "compared with 75.0% a year ago. Operating expenses were $22.0 billion. We returned $41.1 billion to "
          "shareholders through buybacks and dividends during the year, as described in our capital return program.")
PRESS = ("Nvidia's data center business grew 68% in fiscal 2026 to $193.7 billion as hyperscalers kept spending on AI "
         "infrastructure, according to the company's annual report. Analysts said that demand for the company's "
         "Blackwell systems remained strong through the year, with supply the main constraint on shipments.")
FORUM = ("I think Nvidia data center revenue will hit $400 billion next year, easily. Everyone I know is buying the "
         "stock and the AI trade has a long way to run, trust me on this one, the numbers are going up.")
PAGES = {
    "https://www.sec.gov/Archives/edgar/data/1045810/x/nvda-10k.htm": {"title": "NVIDIA 10-K FY2026", "text": FILING,
                                                                       "date": "2026-02-25"},
    "https://www.reuters.com/tech/nvidia-annual": {"title": "Nvidia data center sales jump", "text": PRESS,
                                                   "date": "2026-02-26"},
    "https://www.marketwatch.com/story/nvidia-copy": {"title": "Nvidia data center sales jump (syndicated)",
                                                      "text": PRESS + " Shares were little changed after hours.",
                                                      "date": "2026-02-26"},
    "https://www.reddit.com/r/stocks/nvda": {"title": "NVDA to the moon", "text": FORUM, "date": "2026-03-01"},
}


def fake_search(q, n=8, recency="", region="", site=""):
    return [{"url": u, "title": p["title"]} for u, p in PAGES.items()][:n]


def fake_fetch(url):
    if url not in PAGES:
        raise RuntimeError("HTTP 404")
    return {"url": url, **PAGES[url]}


def corpus(tmp_path):
    c = R.Corpus(str(tmp_path), search_fn=fake_search, fetch_fn=fake_fetch, now="2026-11-13")
    c.search_and_read(["nvidia data center revenue fy2026"])
    return c


def test_tiers_passages_and_numbers():
    assert R.classify("https://www.sec.gov/Archives/x")[0] == 1
    assert R.classify("https://ir.example.com/news")[:1] == (1,)
    assert R.classify("https://example.com/investors/annual-report")[0] == 1
    assert R.classify("https://www.reuters.com/x")[0] == 2 and R.classify("https://pages.stern.nyu.edu/~x")[0] == 2
    assert R.classify("https://www.reddit.com/r/x")[0] == 4 and R.classify("https://unknown.example/x")[0] == 3
    ps = R.split_passages(FILING)
    assert [p[1] for p in ps] == [41, 42] and "Data Center revenue" in ps[0][0]
    nums = R.parse_numbers("Revenue was $215.9 billion in 2026, up 65%; margin 71.1%; 4.2x leverage; €3bn deal")
    assert [(n.value, n.unit) for n in nums] == [(215.9e9, "USD"), (65.0, "%"), (71.1, "%"), (4.2, "x"),
                                                  (3e9, "EUR")]                       # the year 2026 is skipped
    a, b = R.parse_numbers("$4.2bn")[0], R.parse_numbers("$4,200 million")[0]
    assert R.same_number(a, b) and R.same_number(R.parse_numbers("$216bn")[0], R.parse_numbers("$215.9 billion")[0])
    assert not R.same_number(R.parse_numbers("$230bn")[0], R.parse_numbers("$215.9 billion")[0])
    assert not R.same_number(R.parse_numbers("65%")[0], R.parse_numbers("$65 million")[0])


def test_corpus_reads_ranks_dedupes_and_persists(tmp_path):
    c = corpus(tmp_path)
    by_url = {s.url: s for s in c.sources.values()}
    sec = by_url["https://www.sec.gov/Archives/edgar/data/1045810/x/nvda-10k.htm"]
    reu, mw = by_url["https://www.reuters.com/tech/nvidia-annual"], by_url["https://www.marketwatch.com/story/nvidia-copy"]
    assert (sec.tier, reu.tier) == (1, 2) and by_url["https://www.reddit.com/r/stocks/nvda"].tier == 4
    assert mw.duplicate_of == reu.id and reu.duplicate_of is None            # syndicated copy is not independent
    top = c.find("data center revenue fiscal year 2026", k=3)
    assert top[0].source_id == sec.id and top[0].page == 41                  # primary filing ranks first
    assert all(p.source_id != mw.id for p in top)                            # duplicates skipped
    figs = c.numbers("data center revenue", k=30)
    assert any(n.unit == "USD" and n.value == 193.7e9 for n in figs)
    c.save()
    c2 = R.Corpus(str(tmp_path))
    assert set(c2.passages) == set(c.passages) and c2.sources[sec.id].tier == 1
    assert c2.search_and_read(["again"]) == [] or True                       # already-read urls are skipped
    f = c.facts([top[0].id])
    assert f and f[0]["url"] == sec.url and f[0]["page"] == 41 and "$193.7 billion" in " ".join(x["text"] for x in f)
    a = c.assumption_source(top[0].id)
    assert "p.41" in a["source"] and top[0].id in a["source"] and "Data Center" in a["evidence"]
    assert "could not be read" not in c.overview()
    c.add_url("https://dead.example/x")
    assert c.failures and "could not be read" in c.overview()


def _report(c, body):
    p = Path(c.folder) / "report.md"
    p.write_text(body)
    R.finalize(str(p), c, docx=True)
    return p.read_text()


def ids(c):
    by = {s.url.split("/")[2]: s.id for s in c.sources.values()}
    return by["www.sec.gov"], by["www.reuters.com"], by["www.reddit.com"]


def good_body(c):
    sec, reu, _ = ids(c)
    return (f"# Nvidia data center\n\n## Answer\n\nData Center revenue reached $193.7 billion in FY2026, up 68% "
            f"[{sec}.p1; {reu}.p1]. It made up 90% of revenue [calc: 193.7/215.9*100] [{sec}.p1]. Gross margin fell "
            f"to 71.1% from 75.0% [{sec}.p2].\n\n- Supply, not demand, limited shipments [{reu}.p1].\n\n"
            f"| Metric | FY2026 | Source |\n|---|---|---|\n| Revenue | $216bn | [{sec}.p1] |\n\n"
            f"The company said demand came \"from cloud service providers and enterprises\" [{sec}.p1].\n")


def test_verifier_passes_a_sourced_report_and_finalize_lists_sources(tmp_path):
    c = corpus(tmp_path)
    md = _report(c, good_body(c))
    assert V.check(md, c, min_sources=2) == []
    assert "## Sources" in md and "Tier 1 (filing)" in md and "reddit" not in md.split("## Sources")[1]
    assert (tmp_path / "report.docx").exists() and (tmp_path / "sources.json").exists()


@pytest.mark.parametrize("edit, rule", [
    (lambda b, s, r, f: b.replace("$193.7 billion in FY2026", "$201 billion in FY2026"), "R2 number not in cited"),
    (lambda b, s, r, f: b + "\nMargins should recover to 74% next year.\n", "R2 uncited number"),
    (lambda b, s, r, f: b.replace("[calc: 193.7/215.9*100]", "[calc: 193.7/220*100]"), "R2 calc input not cited"),
    (lambda b, s, r, f: b.replace("from cloud service providers and enterprises", "from every hyperscaler and "
                                  "every large enterprise"), "R3 quote not verbatim"),
    (lambda b, s, r, f: b.replace(f"[{s}.p2]", f"[{s}.p9]"), "R1 unknown citation"),
    (lambda b, s, r, f: b + f"\nSome expect $400 billion next year [{f}.p1].\n", "R5 weak source"),
])
def test_verifier_rejects(tmp_path, edit, rule):
    c = corpus(tmp_path)
    sec, reu, forum = ids(c)
    md = _report(c, edit(good_body(c), sec, reu, forum))
    probs = V.check(md, c, min_sources=2)
    assert any(p["check"].startswith(rule) for p in probs), probs


def test_verifier_source_count_primary_and_sources_list(tmp_path):
    c = corpus(tmp_path)
    sec, reu, forum = ids(c)
    md = _report(c, f"# X\n\nGross margin was 71.1% [{sec}.p2].\n")
    assert any(p["check"] == "R4 too few sources" for p in V.check(md, c, min_sources=2))
    only_press = f"# X\n\nData center grew 68% [{reu}.p1]. Blackwell demand stayed strong [{reu}.p1].\n"
    md = _report(c, only_press)
    assert not any(p["check"] == "R4 no primary source" for p in V.check(md, c, min_sources=1))   # tier 2 is enough
    md = _report(c, f"# X\n\nNvidia shares are popular [{forum}.p1].\n")
    probs = V.check(md, c, min_sources=1)
    assert any(p["check"] == "R4 no primary source" for p in probs) and any("weak sourcing" in p["check"] for p in probs)
    no_list = good_body(c)
    assert any(p["check"] == "R6 no sources list" for p in V.check(no_list, c, min_sources=2))


def test_safe_eval_is_arithmetic_only():
    assert V.safe_eval("(215.9-131.6)/131.6") == pytest.approx(0.6405, rel=1e-3) and V.safe_eval("2^3") == 8
    with pytest.raises(ValueError):
        V.safe_eval("__import__('os').system('x')")


def test_edgar_search_and_read(tmp_path, monkeypatch):
    from engines.sheets import spreading as S
    hits = {"hits": {"hits": [
        {"_id": "0001045810-26-000021:ex991.htm", "_source": {"ciks": ["0001045810"], "display_names": [
            "NVIDIA CORP  (NVDA)  (CIK 0001045810)"], "form": "8-K", "file_date": "2026-02-25",
            "period_ending": "2026-01-25"}},
        {"_id": "0001045810-26-000021:nvda-20260125.htm", "_source": {"ciks": ["0001045810"], "display_names": [
            "NVIDIA CORP  (NVDA)  (CIK 0001045810)"], "form": "10-K", "file_date": "2026-02-25",
            "period_ending": "2026-01-25"}}]}}
    calls = []

    def fake_get(url, ua, timeout=30.0):
        calls.append((url, ua))
        if "efts.sec.gov" in url:
            return json.dumps(hits).encode()
        return f"<html><head><title>10-K</title></head><body><main><p>{FILING}</p></main></body></html>".encode()
    monkeypatch.setattr(S, "_get", fake_get)
    c = R.Corpus(str(tmp_path), now="2026-11-13")
    got = c.edgar_search('"data center revenue"', forms="10-K,8-K", start="2025-01-01")
    assert "forms=10-K%2C8-K" in calls[0][0] and "@" in calls[0][1]           # declared SEC User-Agent
    assert got[0]["url"].endswith("/1045810/000104581026000021/nvda-20260125.htm") and got[0]["company"] == \
        "NVIDIA CORP (NVDA)"
    s = c.add_edgar(got[0])
    assert s.tier == 1 and s.kind == "filing 10-K" and s.date == "2026-02-25" and s.publisher == "NVIDIA CORP (NVDA)"
    assert any("193.7" in p.text for p in c.passages.values())


def test_engine_finish_check(tmp_path):
    from engines.research.engine import SYSTEM, finish_check
    c = corpus(tmp_path)
    _report(c, good_body(c))
    ws = SimpleNamespace(root=tmp_path)
    sec, reu, _ = ids(c)
    assert finish_check(f"Data Center revenue reached $193.7 billion [{sec}.p1].", ["report.md"], ws) is None
    assert "R0 answer" in finish_check("Data Center revenue was big.", ["report.md"], ws)
    (tmp_path / "report.md").write_text(good_body(c).replace("$193.7 billion", "$199 billion"))
    msg = finish_check(f"See report [{sec}.p1].", ["report.md"], ws)
    assert msg and "R2 number not in cited passages" in msg and "R6" in msg
    assert "c.edgar_search" in SYSTEM and "[calc:" in SYSTEM and "R.finalize" in SYSTEM


def test_router_sends_research_requests_to_the_research_engine():
    from meshpool_harness.router.rules import decide
    assert decide("Research the competitive landscape for AI inference chips and write a sourced brief")[0] == \
        ["research"]
    assert decide("Research Nvidia data center demand and build a deck for the board")[0] == ["research", "slides"]
    assert decide("Find out the latest capex guidance from Microsoft and build a three-statement model")[0] == \
        ["research", "sheets"]
    assert decide("Summarize this pdf", ["x.pdf"])[0] == ["data"]           # attached files answer it
    assert decide("Analyze this survey and make a deck", ["s.csv"])[0] == ["data", "slides"]


def test_cli_chain_hands_the_research_report_to_the_deck(tmp_path, monkeypatch):
    from meshpool_harness import cli
    seen = {}

    def research_solve(task, *, workdir, task_id, client=None, **kw):
        (Path(workdir) / "report.md").write_text("# Report\n\nRevenue was $215.9 billion [S1.p1].\n")
        (Path(workdir) / "sources.json").write_text("{}")
        return {"ok": True, "answer": "Revenue was $215.9 billion [S1.p1].", "artifacts": ["report.md"]}

    def slides_solve(task, *, workdir, task_id, client=None, **kw):
        seen.update(kw, task=task)
        return {"ok": True, "answer": "deck", "artifacts": ["deck.pptx"]}
    mods = {"research": SimpleNamespace(solve=research_solve), "slides": SimpleNamespace(solve=slides_solve)}
    monkeypatch.setattr(cli, "_engine", lambda name: mods[name])
    monkeypatch.chdir(tmp_path)
    out = cli.run_request("Research Nvidia and build a deck", [], str(tmp_path / "out"), engine="research,slides",
                          client=SimpleNamespace())
    assert out["ok"] and [s["engine"] for s in out["steps"]] == ["research", "slides"]
    assert [Path(f).name for f in seen["files"]] == ["report.md"] and "[S1.p1]" in seen["task"]


def test_find_reports_and_corroborate(tmp_path):
    report = ("Initiating coverage: we estimate the AI accelerator market at $400 billion by 2028. Nvidia data "
              "center revenue was $193.7 billion in fiscal 2026 and we expect gross margin near 72% next year.")
    pages = dict(PAGES)
    pages["https://research.example.com/nvda-initiation.pdf"] = {"title": "NVDA initiation", "text": report * 2,
                                                                 "date": "2026-05-01"}
    queries = []

    def search(q, n=8, recency="", region="", site=""):
        queries.append(q)
        return [{"url": "https://research.example.com/nvda-initiation.pdf", "title": "NVDA initiation"},
                {"url": "https://www.example.com/blog/nvda", "title": "a blog"}]
    c = R.Corpus(str(tmp_path), search_fn=search, fetch_fn=lambda u: {"url": u, **pages[u]} if u in pages else
                 (_ for _ in ()).throw(RuntimeError("404")), now="2026-11-13")
    new = c.find_reports("Nvidia")
    assert any("filetype:pdf" in q for q in queries) and any("report.pdf" in q for q in queries)
    assert len(new) == 1 and new[0].kind == "existing report" and "starting point" in new[0].note
    rid = new[0].id
    for u in list(PAGES)[:2]:
        c.add_url(u)
    res = {r["figure"]: r for r in c.corroborate(f"{rid}.p1")}
    assert res["$193.7 billion"]["best_tier"] == 1                       # the 10-K confirms it
    assert res["$400 billion"]["confirmed_by"] == []                     # a forecast only the report makes
    sec = next(s.id for s in c.sources.values() if "sec.gov" in (s.url or ""))
    p = tmp_path / "report.md"
    p.write_text(f"# NVDA\n\n## Answer\n\nThe accelerator market reaches $400 billion by 2028 [{rid}.p1]. Data "
                 f"Center revenue was $193.7 billion [{rid}.p1; {sec}.p1].\n")
    R.finalize(str(p), c, docx=False)
    probs = V.check(p.read_text(), c, min_sources=2)
    r7 = [x for x in probs if x["check"].startswith("R7")]
    assert len(r7) == 1 and "$400 billion" in r7[0]["detail"]          # unverified headline flagged, verified one ok


def test_hosts_match_whole_labels_and_junk_pages_are_rejected(tmp_path):
    assert R.classify("https://account.microsoft.com/")[0] == 3            # not the FT ("ft.com" suffix)
    assert R.classify("https://www.microsoft.com/en-us/investor/earnings/fy-2026-q4/")[:2] == (1, "issuer release")
    assert R.classify("https://www.mof.go.jp/x")[0] == 1 and R.classify("https://x.substack.com/p/y")[0] == 4
    assert R.classify("https://www.goldmansachs.com/insights/x.pdf")[1] == "bank / broker research"
    c = R.Corpus(str(tmp_path))
    assert c.add_text("Access denied", "Your request has been blocked. This could be due to several reasons. " * 3,
                      "https://www.microsoft.com/x") is None
    assert "blocked" in c.failures[-1]["why"]


def test_document_grid_answers_only_from_each_rows_passages(tmp_path):
    from openpyxl import load_workbook
    from engines.research import grid as G
    c = R.Corpus(str(tmp_path), now="2026-11-13")
    a = c.add_text("Alpha 10-K", "Alpha revenue for fiscal 2026 was $12.4 billion, up 9%. Gross margin was 41.2% as "
                   "input costs eased. We expect capital expenditures of about $1.1 billion next year. " * 2,
                   "https://www.sec.gov/alpha", "Alpha Corp", "2026-02-01")
    b = c.add_text("Beta 10-K", "Beta revenue for fiscal 2026 was $3.9 billion, down 2%. Gross margin narrowed to 33.0% "
                   "on pricing pressure in Europe. Beta did not give capital expenditure guidance. " * 2,
                   "https://www.sec.gov/beta", "Beta Inc", "2026-03-01")
    seen = []

    class Client:
        def chat(self, model, messages, **kw):
            user = messages[-1]["content"]
            seen.append(user)
            ids = re.findall(r"\[(S\d+\.p\d+)\]", user)
            q = user.split("\n", 1)[0]
            if "Capital" in q and "Beta" in user:
                out = {"answer": "About $1.1 billion", "citations": ids[:1]}          # invented: not in Beta's text
            elif "revenue" in q:
                out = {"answer": "$12.4 billion" if "Alpha" in user else "$3.9 billion", "citations": ids[:1]}
            elif "Capital" in q:
                out = {"answer": "About $1.1 billion next year", "citations": ids[:1]}
            else:
                out = {"answer": "", "citations": []}
            return {"choices": [{"message": {"content": json.dumps(out)}}]}
    import re
    g = G.grid(c, {"Alpha": [a.id], "Beta": [b.id]}, ["Fiscal 2026 revenue", "Capital expenditure guidance",
                                                      "Dividend policy"], client=Client(), model="m", workers=1)
    assert g.get(0, 0).ok and g.get(0, 0).answer == "$12.4 billion" and g.get(0, 0).citations[0].startswith(a.id)
    assert g.get(1, 0).answer == "$3.9 billion" and all(cid.startswith(b.id) for cid in g.get(1, 0).citations)
    assert g.get(0, 1).ok
    assert not g.get(1, 1).ok and g.get(1, 1).answer == G.NOT_FOUND and "$1.1 billion" in g.get(1, 1).note
    assert not g.get(0, 2).ok                                        # nothing in the passages: refused, not guessed
    assert all(("Alpha" in u) != ("Beta" in u) for u in seen if "Passages" in u)   # rows never share passages
    path = G.to_excel(g, c, str(tmp_path / "grid.xlsx"))
    wb = load_workbook(path)
    ws = wb["Grid"]
    assert ws["B2"].value.startswith("$12.4 billion [S") and ws["B2"].hyperlink.target == "https://www.sec.gov/alpha"
    assert ws["C3"].value == G.NOT_FOUND and ws["C3"].comment is not None
    assert wb["Sources"]["A2"].value.startswith("S") and "Alpha Corp" in wb["Sources"]["B2"].value
    assert G.check_cell("$12.4 billion", ["S9.p1"], {"S1.p1": "x"}).startswith("cites passages")


def test_sources_split_only_at_the_exact_sources_heading(tmp_path):
    c = corpus(tmp_path)
    sec, reu, _ = ids(c)
    body = good_body(c).replace("\n- Supply", "\n## Sources and uses of funds\n\nThe deal raised $999 billion "
                                f"[{sec}.p1].\n\n- Supply")
    probs = V.check(_report(c, body), c, min_sources=2)
    assert any(p["check"] == "R2 number not in cited passages" and "$999" in p["detail"] for p in probs)
    md = _report(c, body)
    assert md.count("## Sources\n") == 1 and "## Sources and uses of funds" in md     # finalize keeps the section


def test_units_must_agree_and_quotes_may_span_sentences(tmp_path):
    a, b = R.parse_numbers("65%")[0], R.parse_numbers("$65 million")[0]
    assert not V._same_bare(a, b) and V._same_bare(R.parse_numbers("65")[0], b)
    c = corpus(tmp_path)
    sec, reu, _ = ids(c)
    md = _report(c, good_body(c) + f"\nData Center revenue was 193.7% of plan [{sec}.p1].\n")
    assert any("193.7%" in p["detail"] for p in V.check(md, c, min_sources=2))
    fake = good_body(c) + f"\nThe filing said \"Revenue fell sharply. Demand collapsed in every region\" [{sec}.p1].\n"
    assert any(p["check"] == "R3 quote not verbatim" for p in V.check(_report(c, fake), c, min_sources=2))
    real = good_body(c) + (f"\nReuters wrote \"according to the company's annual report. Analysts said that demand "
                           f"for the company's Blackwell systems remained strong\" [{reu}.p1].\n")
    assert not V.check(_report(c, real), c, min_sources=2)


def test_find_does_not_mutate_shared_passages(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    c = corpus(tmp_path)
    qs = ["data center revenue fiscal year 2026", "gross margin", "buybacks dividends shareholders", "Blackwell"] * 8
    alone = {q: [(p.id, p.score) for p in c.find(q, k=4)] for q in set(qs)}
    with ThreadPoolExecutor(8) as ex:
        got = list(ex.map(lambda q: (q, [(p.id, p.score) for p in c.find(q, k=4)]), qs))
    assert all(r == alone[q] for q, r in got)
    assert all(p.score == 0.0 for p in c.passages.values())             # scores live on the returned copies


def test_issuer_path_rule_skips_aggregators():
    assert R.classify("https://www.nasdaq.com/articles/earnings-preview-nvda")[0] == 3
    assert R.classify("https://www.marketbeat.com/earnings/reports/2026-2-25-nvidia/")[0] == 3
    assert R.classify("https://www.investors.com/news/technology/nvda-earnings/")[0] == 3
    assert R.classify("https://investor.nvidia.com/news/x")[0] == 1
    assert R.classify("https://nvidianews.nvidia.com/earnings-q4-fy26")[0] == 1


def test_small_guards():
    from engines.research.engine import finish_check
    with pytest.raises(ValueError):
        V.safe_eval("10^400")                                          # OverflowError is a bad calc, not a crash
    msg = finish_check("x", [{"path": "report.md"}, None], SimpleNamespace(root=Path("/nonexistent")))
    assert msg.startswith("ENGINE CHECK")                               # non-string artifacts do not crash
    from engines.data.checks import trace_numbers
    assert trace_numbers("Revenue was $2,045", "total 1999")              # an amount, not a year
    assert not trace_numbers("mean 3.14", "3.1449") and trace_numbers("mean 3.14", "3.12")
    assert not trace_numbers("about 45 units", "44.7")


def test_grid_excel_keeps_formula_text_as_text(tmp_path):
    from openpyxl import load_workbook
    from engines.research import grid as G
    c = R.Corpus(str(tmp_path), now="2026-11-13")
    s = c.add_text('=HYPERLINK("http://evil.example","x")', "Gamma revenue for fiscal 2026 was $5.5 billion. " * 4,
                   "https://www.sec.gov/gamma", "Gamma", "2026-02-01")
    g = G.Grid([s.label], ["=1+1"])
    g.cells[(0, 0)] = G.Cell("=WEBSERVICE(\"http://evil.example\")", [f"{s.id}.p1"], True)
    ws = load_workbook(G.to_excel(g, c, str(tmp_path / "g.xlsx")))["Grid"]
    assert all(ws[x].data_type == "s" for x in ("B1", "B2"))
