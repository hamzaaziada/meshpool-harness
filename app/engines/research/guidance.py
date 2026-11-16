"""Guidance from the primary source: the company's latest earnings release (the exhibit filed with an 8-K under Item
2.02, or an announcement under 8.01) and its latest 10-Q and 10-K, read from SEC EDGAR, no model involved.

The strict reviewers kept rejecting decisions built on a capex plan quoted from a news aggregator or with an
unconfirmed unit. This pulls the sentences that state forward capital spending, buyback or dividend plans with
dollar amounts, each as a fact that names the form, filing date and URL.
"""
from __future__ import annotations

import html
import json
import re

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
TOPICS = {
    "capital expenditure guidance": re.compile(r"(?i)\b(capital expenditures?|capex|capital spending)\b"),
    "share repurchase plan": re.compile(r"(?i)\b(repurchase|buy ?back)\w*\b"),
    "dividend": re.compile(r"(?i)\bdividends?\b"),
    "liquidity and credit facilities": re.compile(r"(?i)\b(revolving credit|credit facilit|liquidity|letters? of credit|"
                                                  r"commercial paper|borrowing capacity|minimum cash)\w*\b"),
}
STATED = re.compile(r"(?i)\b(available|unused|undrawn|capacity|outstanding|maintain|sufficient|adequate|"
                    r"borrowings? under)\b")
FORWARD = re.compile(r"(?i)\b(expect|expects|expected|anticipate|anticipates|plan|plans|planned|estimate|estimates|"
                     r"intend|intends|outlook|guidance|for (?:fiscal|the year|the full year)|authori[sz]ed?|declared?)\b")
MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?\s?(?:billion|million|thousand|B|M|bn)?", re.I)


def _text(raw: bytes) -> str:
    t = raw.decode("utf-8", "ignore")
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", html.unescape(t))


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.;])\s+(?=[A-Z])", text) if 40 <= len(s.strip()) <= 420]


def guidance_facts(cik: str, company: str, no: int, get=None, max_filings: int = 2) -> list[dict]:
    """Forward-looking capex, buyback and dividend sentences with amounts from the latest earnings releases (8-K
    exhibits 99.x) and the latest 10-Q/10-K, as sourced facts. `get(url) -> bytes` is the transport (tests)."""
    from engines.sheets import spreading as SP
    get = get or (lambda u: SP._get(u, SP.default_user_agent()))
    cik10 = str(cik).zfill(10)
    try:
        sub = json.loads(get(SUBMISSIONS.format(cik=cik10)))
    except Exception:  # noqa: BLE001
        return []
    rec = sub.get("filings", {}).get("recent", {})
    rows = list(zip(rec.get("form", []), rec.get("filingDate", []), rec.get("accessionNumber", []),
                    rec.get("primaryDocument", []), rec.get("items", []) or [""] * len(rec.get("form", []))))
    picks = [r for r in rows if r[0] == "8-K" and re.search(r"2\.02|8\.01", str(r[4]))][:max_filings + 1]
    picks += [r for r in rows if r[0] == "10-Q"][:1] + [r for r in rows if r[0] == "10-K"][:1]
    out: list[dict] = []
    seen: set = set()
    for form, filed, acc, doc, _ in picks:
        acc_nd = acc.replace("-", "")
        docs = [doc]
        if form == "8-K":                                # the release is an exhibit: find it in the filing index
            try:
                idx = json.loads(get(ARCHIVE.format(cik=int(cik10), acc=acc_nd, doc="index.json")))
                docs = [i["name"] for i in idx.get("directory", {}).get("item", [])
                        if re.search(r"(?i)(ex-?99|exhibit99|ex99|pressrelease|earnings)", i.get("name", ""))
                        and i["name"].lower().endswith((".htm", ".html", ".txt"))][:2] or docs
            except Exception:  # noqa: BLE001
                pass
        for d in docs:
            url = ARCHIVE.format(cik=int(cik10), acc=acc_nd, doc=d)
            try:
                text = _text(get(url))
            except Exception:  # noqa: BLE001
                continue
            text = re.sub(r"^.*?(?:Exhibit 99\.\d+\s*)?Press Release\s+", "", text[:600], count=1) + text[600:] \
                if "Press Release" in text[:600] else text   # drop the exhibit's file header
            for s in _sentences(text):
                topic = next((k for k, rx in TOPICS.items() if rx.search(s)), None)
                if not MONEY.search(s) or not (FORWARD.search(s) or (topic == "liquidity and credit facilities"
                                                                     and STATED.search(s))):
                    continue                             # plans, or the liquidity a cash floor can be anchored on
                key = re.sub(r"\W+", "", s.lower())[:120]
                if not topic or key in seen:
                    continue
                seen.add(key)
                out.append({"id": f"F{no + len(out)}", "research": True, "company": company,
                            "label": topic, "source": url, "tier": 1,
                            "text": f"{company} {topic}: \"{s}\" ({form} filed {filed}, {d}; {url})"})
                if len(out) >= 16:
                    return out
    return out
