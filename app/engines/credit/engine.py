"""meshpool-credit: lender cash flow and credit analysis as a fixed pipeline, not an agent loop.

At most two short model calls, so it runs on a small local model (7-8B) as well as a large cloud one:
  1. fill the loan request fields the regex parser could not find (JSON, temperature 0);
  2. optional commentary (borrower, risks, mitigants) from the computed facts; every number it writes is checked
     against those facts and sentences that fail are dropped. MH_CREDIT_COMMENTARY=0 skips it; a large model
     (MH_CREDIT_DEPTH=full) gets a longer brief.
Everything else (spreading, cash flow measures, capacity, covenants, stress, workbook, memo) is code.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from engines.credit import analysis as A  # noqa: E402

AMOUNT = re.compile(r"(?:\$|usd\s?)\s?(\d+(?:\.\d+)?)\s?(k|m|mm|mn|million|bn|b|billion)?\b", re.I)


def parse_request(text: str) -> dict:
    """Loan fields from plain words. Amounts are returned in $m."""
    out: dict = {}
    t = text.lower()
    m = AMOUNT.search(text)
    if m:
        v, sc = float(m.group(1)), (m.group(2) or "").lower()
        out["amount"] = v * {"k": 1e-3, "m": 1, "mm": 1, "mn": 1, "million": 1, "bn": 1e3, "b": 1e3,
                             "billion": 1e3}.get(sc, 1e-6)
    r = re.search(r"(\d+(?:\.\d+)?)\s?%\s?(?:interest|rate|coupon|fixed|floating|sofr)?", t)
    if r:
        out["rate"] = float(r.group(1)) / 100
    a = re.search(r"(\d+(?:\.\d+)?)[- ]?(?:year|yr)s?\b", t)
    if a:
        out["amort_years"] = float(a.group(1))
    if re.search(r"\brefinanc", t):
        out["refinance_existing"] = True
    if re.search(r"\bfixed[- ]rate\b|\bfixed\b", t):
        out["floating"] = False
    tk = re.search(r"\b(?:ticker|nyse|nasdaq)[:\s]+([A-Z]{1,5})\b", text) or re.search(r"\(([A-Z]{1,5})\)", text)
    if tk:
        out["ticker"] = tk.group(1)
    p = re.search(r"\bto (fund|finance|acquire|buy|build|refinance|expand)\b([^.,;(]{0,60})", t)
    if p:
        out["purpose"] = (p.group(1) + p.group(2)).strip()
    return out


ASK = ("Extract the loan request as JSON with keys: borrower (company name), ticker (or null), amount_musd (number, "
       "in millions of US dollars, or null), rate (decimal, e.g. 0.07, or null), amort_years (number or null), "
       "refinance_existing (true/false), purpose (short text or null). Reply with JSON only.\nRequest: ")


def _llm_json(client, model: str, prompt: str, max_tokens: int = 200) -> dict:
    resp = client.chat(model=model, messages=[{"role": "user", "content": prompt}], max_tokens=max_tokens,
                       temperature=0.0, task_id="credit", role="worker",
                       extra={"response_format": {"type": "json_object"}})
    txt = resp["choices"][0]["message"].get("content") or "{}"
    try:
        return json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
    except ValueError:
        return {}


def commentary(client, model: str, res: dict, borrower_text: str = "") -> dict:
    from engines.credit.memo import check_commentary, facts
    fs = facts(res)
    full = os.environ.get("MH_CREDIT_DEPTH", "short") == "full"
    n = "3-5" if full else "2-3"
    prompt = (f"You are a bank credit analyst. Using ONLY these facts, write JSON with keys business (one or two "
              f"sentences on what the borrower does and how it earns cash), risks (list of {n} short sentences) and "
              f"mitigants (list of {n} short sentences). Use no number that is not in the facts.\nFacts:\n- "
              + "\n- ".join(fs) + (f"\nAbout the borrower: {borrower_text[:1500]}" if borrower_text else ""))
    d = _llm_json(client, model, prompt, 700 if full else 350)
    out = {}
    if isinstance(d.get("business"), str):
        out["business"] = check_commentary(d["business"], fs)
    for k in ("risks", "mitigants"):
        if isinstance(d.get(k), list):
            out[k] = [y for y in (check_commentary(str(x), fs) for x in d[k][:6]) if y]
    return out


def solve(task: str, *, workdir: str, task_id: str, files: list[str] | None = None, client=None,
          model: str = "deepseek-v4.1-flash", **_) -> dict:
    t0 = time.time()
    wd = Path(workdir)
    wd.mkdir(parents=True, exist_ok=True)
    req = parse_request(task)
    if client is not None and not ({"amount", "rate", "amort_years"} <= req.keys()):
        try:
            d = _llm_json(client, model, ASK + task[:3000])
            for k_in, k_out in (("amount_musd", "amount"), ("rate", "rate"), ("amort_years", "amort_years"),
                                ("ticker", "ticker"), ("purpose", "purpose"), ("borrower", "borrower")):
                if d.get(k_in) not in (None, "") and k_out not in req:
                    req[k_out] = d[k_in]
            if d.get("refinance_existing") is True:
                req["refinance_existing"] = True
        except Exception:                                      # noqa: BLE001 - the regex result stands
            pass
    tables = [f for f in files or [] if Path(f).suffix.lower() in (".xlsx", ".xlsm", ".csv")]
    if tables:
        fin = A.from_table(tables[0], name=req.get("borrower"))
    elif req.get("ticker"):
        from engines.sheets import spreading as S
        fin = A.from_spread(S.spread(S.fetch_companyfacts(S.cik_for_ticker(req["ticker"]),
                                                          cache_dir=str(wd / ".edgar")), 3))
    else:
        return {"ok": False, "answer": "", "artifacts": [], "turns": 0, "usage": {},
                "reason": "Attach the borrower's financial statements (xlsx or csv: line items down, years across) "
                          "or give a public company's ticker, e.g. 'ticker: ACME'."}
    loan = A.Loan(amount=float(req.get("amount") or 0), rate=float(req.get("rate") or 0.07),
                  amort_years=float(req.get("amort_years") or 7), floating=req.get("floating", True),
                  refinance_existing=bool(req.get("refinance_existing")), purpose=req.get("purpose") or "")
    pol = A.Policy()
    res = A.analyse(fin, loan, pol)
    from engines.credit import memo, workbook
    stem = re.sub(r"[^A-Za-z0-9]+", "_", fin.name).strip("_")[:40] or "borrower"
    xl = str(wd / f"credit_{stem}.xlsx")
    workbook.build(fin, loan, pol, xl, date=time.strftime("%Y-%m-%d"))
    com = {}
    if client is not None and os.environ.get("MH_CREDIT_COMMENTARY", "1") == "1":
        try:
            com = commentary(client, model, res)
        except Exception:                                      # noqa: BLE001 - the memo stands without it
            com = {}
    md = str(wd / f"credit_memo_{stem}.md")
    memo.write(res, fin, md, com)
    (wd / f"credit_{stem}.json").write_text(json.dumps(res, indent=1, default=str))
    rec, why = memo.recommendation(res)
    pf, cap = res["pro_forma"], res["capacity"]
    answer = (f"{rec}: {why} Pro forma DSCR {memo._x(pf['dscr'])} (minimum {pol.min_dscr:.2f}x), funded debt / "
              f"EBITDA {memo._x(pf['leverage'])} (maximum {pol.max_leverage:.2f}x), debt capacity "
              f"{memo.money(cap['max_loan'], fin.unit)} vs {memo.money(cap['requested'], fin.unit)} requested.")
    arts = [xl, md, str(Path(md).with_suffix(".docx")), str(wd / f"credit_{stem}.json")]
    return {"ok": True, "answer": answer, "artifacts": [a for a in arts if os.path.exists(a)],
            "evidence": [f"computed in code in {time.time() - t0:.1f}s"], "turns": 1, "reason": "", "usage": {}}
