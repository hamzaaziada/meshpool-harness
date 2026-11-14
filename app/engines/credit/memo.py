"""Credit memo from the analysis: deterministic tables and wording, optional commentary (business, risks,
mitigants) from a model, each number in it checked against the computed facts."""
from __future__ import annotations

import re

from .analysis import Financials


def _x(v, fmt="{:.2f}x"):
    return "NM" if v is None else fmt.format(v)


def _m(v, u="$m"):
    if v is None:
        return "n/a"
    return f"({abs(v):,.1f})" if v < 0 else f"{v:,.1f}"


def money(v, u="$m"):
    """$10.0m for a $m unit; otherwise the number then the unit."""
    if v is None:
        return "n/a"
    return f"${v:,.1f}m" if u == "$m" else f"{v:,.1f} {u}"


def _p(v):
    return "n/a" if v is None else f"{v:.1%}"


def recommendation(res: dict) -> tuple[str, str]:
    cov = [c for c in res["covenants"] if c["pass"] is not None]
    fails = [c["covenant"] for c in cov if not c["pass"]]
    cap = res["capacity"]
    if not fails and cap["within_capacity"] and res["rating"]["grade"] <= 3:
        return "Approve", "The loan fits within debt capacity and every policy test passes on a pro forma basis."
    if len(fails) <= 1 and cap["max_loan"] >= 0.75 * max(cap["requested"], 1e-9):
        why = f"fails {fails[0]}" if fails else "sits at the edge of capacity or has a weaker score"
        return "Approve with conditions", f"The request {why}; structure or covenants should close the gap."
    return "Decline as structured", ("Policy tests failed: " + ", ".join(fails) if fails else
                                     "The request exceeds debt capacity") + "."


def facts(res: dict) -> list[str]:
    """Computed facts a commentary model may use (and the only numbers it may state)."""
    h, pf, cap = res["history"][-1], res["pro_forma"], res["capacity"]
    u = res["unit"]
    out = [f"Revenue {h['year']}: {_m(h['revenue'])} {u}", f"EBITDA {h['year']}: {_m(h['ebitda'])} {u}",
           f"EBITDA margin: {_p(h['ebitda_margin'])}", f"Revenue growth: {_p(h['revenue_growth'])}",
           f"Historical DSCR: {_x(h['dscr'])}", f"Pro forma DSCR: {_x(pf['dscr'])}",
           f"Pro forma leverage: {_x(pf['leverage'])}", f"Debt capacity: {_m(cap['max_loan'])} {u}",
           f"Requested loan: {_m(cap['requested'])} {u}", f"Current ratio: {_x(h['current_ratio'])}"]
    if res["ebitda_decline_to_min_dscr"] is not None:
        out.append(f"EBITDA can fall {res['ebitda_decline_to_min_dscr']:.1%} before DSCR reaches the minimum")
    return out + res["flags"]


def check_commentary(text: str, allowed: list[str]) -> str:
    """Drop sentences whose numbers are not among the computed facts (small models invent figures)."""
    from engines.research.library import parse_numbers, same_number
    ok_nums = [n for f in allowed for n in parse_numbers(f)]
    keep = []
    for s in re.split(r"(?<=[.!?])\s+", text.strip()):
        nums = [n for n in parse_numbers(s) if not (n.unit == "" and n.scale == 1 and abs(n.value) < 10
                                                       and n.decimals == 0)]
        if all(any(same_number(n, a) or abs(n.value - a.value) <= 0.051 for a in ok_nums) for n in nums):
            keep.append(s)
    return " ".join(keep)


def write(res: dict, fin: Financials, path: str, commentary: dict | None = None) -> str:
    u = res["unit"]
    rec, why = recommendation(res)
    L = res["loan"]
    pf, cap, h = res["pro_forma"], res["capacity"], res["history"]
    lines = [f"# Credit memo: {res['borrower']}", "",
             f"**Request:** {money(L['amount'], u)} term loan at {L['rate']:.2%}, {L['amort_years']:g}-year "
             f"amortisation" + (f"; purpose: {L['purpose']}" if L.get("purpose") else "") +
             ("; refinances existing debt" if L.get("refinance_existing") else "") + ".", "",
             f"**Recommendation: {rec}.** {why}", "",
             f"**Indicative score:** {res['rating']['grade']} ({res['rating']['label']}) on a 1-5 scale from DSCR, "
             f"leverage, liquidity and trend. It is a screening score, not the bank's regulatory risk rating.", "",
             "## Key numbers", "",
             f"- Pro forma DSCR {_x(pf['dscr'])} against a {res['policy']['min_dscr']:.2f}x minimum.",
             f"- Pro forma funded debt / EBITDA {_x(pf['leverage'])} against a {res['policy']['max_leverage']:.2f}x "
             f"maximum.",
             f"- Debt capacity {money(cap['max_loan'], u)} ({cap['binding']} binds) against the "
             f"{money(cap['requested'], u)} requested.",
             f"- EBITDA can fall {_p(res['ebitda_decline_to_min_dscr'])} before DSCR reaches the minimum."]
    if commentary:
        for k, title in (("business", "Borrower"), ("risks", "Key risks"), ("mitigants", "Mitigants")):
            v = commentary.get(k)
            if v:
                lines += ["", f"## {title}", ""] + ([f"- {x}" for x in v] if isinstance(v, list) else [v])
    lines += ["", f"## Historical performance and cash flow ({u})", "",
              "| Measure | " + " | ".join(x["year"] for x in h) + " |", "|---|" + "---|" * len(h)]
    rows = [("Revenue", "revenue", _m), ("Revenue growth", "revenue_growth", _p), ("EBITDA", "ebitda", _m),
            ("EBITDA margin", "ebitda_margin", _p), ("Cash available for debt service", "cads", _m),
            ("Debt service", "debt_service", _m), ("DSCR", "dscr", _x), ("FCCR", "fccr", _x),
            ("Funded debt / EBITDA", "leverage", _x), ("Total liabilities / TNW", "debt_tnw", _x),
            ("Current ratio", "current_ratio", _x), ("Operating cash flow / net income", "cfo_to_net_income", _x)]
    for lab, k, f in rows:
        lines.append(f"| {lab} | " + " | ".join(f(x[k]) for x in h) + " |")
    lines += ["", "## Covenants (pro forma)", "", "| Covenant | Value | Limit | Result | EBITDA cushion |",
              "|---|---|---|---|---|"]
    for c in res["covenants"]:
        res_txt = "n/a" if c["pass"] is None else ("Pass" if c["pass"] else "Fail")
        lines.append(f"| {c['covenant']} | {_x(c['value'])} | {c['limit']:.2f}x | {res_txt} | "
                     f"{_p(c['ebitda_cushion'])} |")
    lines += ["", "## Stress tests", "", "| Case | DSCR | Funded debt / EBITDA |", "|---|---|---|"]
    for s in res["stress"]:
        lines.append(f"| {s['case']} | {_x(s['dscr'])} | {_x(s['leverage'])} |")
    if res["flags"]:
        lines += ["", "## Flags", ""] + [f"- {f}" for f in res["flags"]]
    lines += ["", "## Definitions", "",
              "- EBITDA = operating income + depreciation and amortisation.",
              "- Cash available for debt service = EBITDA - cash taxes (income tax expense) - unfinanced capex - "
              "distributions (dividends" + (" and buybacks" if res["policy"]["include_buybacks"] else "") + ").",
              "- Debt service = interest expense + scheduled principal (the prior year-end current portion of "
              "long-term debt). Pro forma adds the new loan's level annual payment.",
              "- Debt capacity = the lower of the loan the minimum DSCR supports and policy leverage x EBITDA less "
              "existing debt.",
              "", "## Sources", ""]
    seen = sorted({s for y in fin.years for s in fin.sources.get(y, {}).values()})
    lines += [f"- {s}" for s in seen[:20]] or ["- Provided financial statements"]
    if fin.sources.get("_unmapped"):
        lines += ["", "Lines not mapped to the spread (review): " + ", ".join(fin.sources["_unmapped"][:20])]
    text = "\n".join(lines) + "\n"
    with open(path, "w") as f:
        f.write(text)
    try:
        from engines.research.library import _to_docx
        from pathlib import Path
        _to_docx(text.replace("**", ""), Path(path).with_suffix(".docx"))
    except Exception:                                          # noqa: BLE001 - the markdown memo stands alone
        pass
    return text
