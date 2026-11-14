"""Lender cash flow and credit analysis, all in code (no model needed): the borrower's spread in, the bank's measures
out. Built so a small local model only has to supply the loan request; every number here is computed and testable.

    from engines.credit import analysis as A
    fin = A.from_spread(spreading.spread(facts))           # or A.from_table("borrower.xlsx") / A.Financials(...)
    loan = A.Loan(amount=250.0, rate=0.07, amort_years=7)  # $m, annual rate, level payments
    res = A.analyse(fin, loan, A.Policy())                 # dict: per-year measures, pro forma, capacity, stress...

Definitions (stated in the memo):
  EBITDA = operating income + depreciation and amortisation (pre-tax income + interest + D&A when operating
           income is not reported).
  Cash taxes ~ income tax expense (the spread has no cash taxes paid; say so).
  Unfinanced capex = capex x (1 - financed share). Distributions = dividends (+ buybacks unless excluded).
  Cash available for debt service (CADS) = EBITDA - cash taxes - unfinanced capex - distributions.
  Debt service = interest expense + scheduled principal (last year-end's current portion of long-term debt).
  DSCR = CADS / debt service. FCCR = (CADS + rent) / (debt service + rent).
  Leverage = total funded debt / EBITDA; net leverage nets cash. Debt / TNW = total liabilities / (equity -
  goodwill - intangibles). Current ratio, quick ratio = (cash + receivables) / current liabilities.
  UCA operating cash flow = cash from operations as reported (net income + non-cash charges +/- working capital).
  Debt capacity = the smaller of (a) the loan whose level payment keeps pro forma DSCR at the policy minimum and
  (b) the policy leverage multiple x EBITDA less existing debt.
"""
from __future__ import annotations

import csv
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

KEYS = ["revenue", "cogs", "sga", "operating_income", "interest_expense", "pretax_income", "income_tax",
        "net_income", "da", "sbc", "rent", "capex", "dividends", "buybacks", "cfo", "cash", "receivables",
        "inventory", "current_assets", "ppe", "goodwill", "intangibles", "total_assets", "payables", "short_debt",
        "current_debt", "current_liabilities", "long_debt", "total_liabilities", "total_equity"]
# costs and outflows are carried negative (as in spreading.py); the analysis uses magnitudes where it needs them
NEGATIVE = {"cogs", "sga", "interest_expense", "income_tax", "capex", "dividends", "buybacks", "rent"}
LABELS = {
    "revenue": ["revenue", "revenues", "net revenue", "net sales", "sales", "total revenue", "turnover"],
    "cogs": ["cost of revenue", "cost of sales", "cost of goods sold", "cogs"],
    "sga": ["sg&a", "selling general and administrative", "operating expenses", "opex", "overheads"],
    "operating_income": ["operating income", "operating profit", "ebit", "income from operations"],
    "interest_expense": ["interest expense", "interest", "finance costs", "interest paid"],
    "pretax_income": ["pre-tax income", "pretax income", "income before taxes", "profit before tax", "ebt"],
    "income_tax": ["income tax", "income taxes", "tax expense", "taxes", "provision for income taxes"],
    "net_income": ["net income", "net profit", "profit after tax", "net earnings"],
    "da": ["depreciation and amortisation", "depreciation and amortization", "d&a", "depreciation"],
    "sbc": ["share-based compensation", "stock-based compensation", "sbc"],
    "rent": ["rent", "rent expense", "lease expense", "operating lease expense"],
    "capex": ["capital expenditure", "capital expenditures", "capex", "purchases of property and equipment",
              "purchase of fixed assets"],
    "dividends": ["dividends", "dividends paid", "distributions", "owner distributions", "drawings"],
    "buybacks": ["share repurchases", "buybacks", "repurchase of shares"],
    "cfo": ["cash from operating activities", "operating cash flow", "cash from operations",
            "net cash provided by operating activities"],
    "cash": ["cash", "cash and cash equivalents", "cash and equivalents"],
    "receivables": ["accounts receivable", "receivables", "trade receivables", "debtors"],
    "inventory": ["inventory", "inventories", "stock"],
    "current_assets": ["total current assets", "current assets"],
    "ppe": ["pp&e", "property plant and equipment", "fixed assets", "net fixed assets",
            "property, plant and equipment, net"],
    "goodwill": ["goodwill"],
    "intangibles": ["intangible assets", "intangibles", "other intangibles"],
    "total_assets": ["total assets"],
    "payables": ["accounts payable", "payables", "trade payables", "creditors"],
    "short_debt": ["short-term debt", "short term borrowings", "revolver", "line of credit", "commercial paper",
                   "notes payable", "bank overdraft"],
    "current_debt": ["current portion of long-term debt", "current portion of ltd", "cpltd",
                     "current maturities of long-term debt"],
    "current_liabilities": ["total current liabilities", "current liabilities"],
    "long_debt": ["long-term debt", "long term debt", "term loan", "senior notes", "long-term borrowings"],
    "total_liabilities": ["total liabilities"],
    "total_equity": ["total equity", "shareholders' equity", "stockholders' equity", "net worth", "owners' equity",
                     "total shareholders' equity"],
}


@dataclass
class Financials:
    """Annual statements in $m (or one consistent unit): years oldest first, values[year][key], sources[year][key]."""
    name: str
    years: list[str]
    values: dict[str, dict[str, float | None]]
    sources: dict[str, dict[str, str]] = field(default_factory=dict)
    unit: str = "$m"

    def v(self, year: str, key: str, default: float | None = None) -> float | None:
        x = self.values.get(year, {}).get(key)
        return default if x is None else x


@dataclass
class Loan:
    amount: float = 0.0                       # proposed new loan
    rate: float = 0.07                        # annual interest rate
    amort_years: float = 7.0                  # level-payment amortisation period
    term_years: float | None = None           # maturity (balloon if shorter than amortisation)
    floating: bool = True                     # rate stress applies
    refinance_existing: bool = False          # proceeds repay existing funded debt
    purpose: str = ""


@dataclass
class Policy:
    min_dscr: float = 1.25
    min_fccr: float = 1.20
    max_leverage: float = 3.5                 # total funded debt / EBITDA
    max_debt_tnw: float = 3.0                 # total liabilities / tangible net worth
    min_current_ratio: float = 1.2
    financed_capex_share: float = 0.0         # share of capex funded by new debt (not from cash flow)
    include_buybacks: bool = True             # count buybacks as distributions
    ebitda_stress: tuple = (0.10, 0.20, 0.30)
    rate_stress_bp: int = 200


def pmt(rate: float, years: float, amount: float) -> float:
    """Level annual payment (interest + principal) on amount over years."""
    if amount <= 0 or years <= 0:
        return 0.0
    if rate == 0:
        return amount / years
    return amount * rate / (1 - (1 + rate) ** -years)


def pv(rate: float, years: float, payment: float) -> float:
    """Loan amount a level annual payment supports."""
    if payment <= 0 or years <= 0:
        return 0.0
    if rate == 0:
        return payment * years
    return payment * (1 - (1 + rate) ** -years) / rate


def _mag(x):
    return None if x is None else abs(x)


def _div(a, b):
    return None if a is None or b in (None, 0) else a / b


def ebitda(fin: Financials, y: str) -> float | None:
    da = _mag(fin.v(y, "da"))
    oi = fin.v(y, "operating_income")
    if oi is None:
        pt, ie = fin.v(y, "pretax_income"), _mag(fin.v(y, "interest_expense"))
        oi = None if pt is None else pt + (ie or 0.0)
    return None if oi is None or da is None else oi + da


def funded_debt(fin: Financials, y: str) -> float:
    return sum(fin.v(y, k, 0.0) or 0.0 for k in ("short_debt", "current_debt", "long_debt"))


def year_measures(fin: Financials, y: str, prev: str | None, pol: Policy) -> dict:
    e = ebitda(fin, y)
    tax = _mag(fin.v(y, "income_tax", 0.0))
    capex = _mag(fin.v(y, "capex", 0.0))
    unfin = capex * (1 - pol.financed_capex_share)
    dist = _mag(fin.v(y, "dividends", 0.0)) + (_mag(fin.v(y, "buybacks", 0.0)) if pol.include_buybacks else 0.0)
    interest = _mag(fin.v(y, "interest_expense", 0.0))
    cpltd = fin.v(prev, "current_debt") if prev else None
    principal = cpltd if cpltd is not None else (fin.v(y, "current_debt", 0.0) or 0.0)
    rent = _mag(fin.v(y, "rent", 0.0))
    cads = None if e is None else e - tax - unfin - dist
    ds = interest + principal
    debt = funded_debt(fin, y)
    tnw = None
    if fin.v(y, "total_equity") is not None:
        tnw = fin.v(y, "total_equity") - (fin.v(y, "goodwill", 0.0) or 0) - (fin.v(y, "intangibles", 0.0) or 0)
    rev, prev_rev = fin.v(y, "revenue"), fin.v(prev, "revenue") if prev else None
    cl = fin.v(y, "current_liabilities")
    cogs = _mag(fin.v(y, "cogs"))
    return {
        "year": y, "revenue": rev, "revenue_growth": _div(rev - prev_rev, prev_rev) if rev and prev_rev else None,
        "ebitda": e, "ebitda_margin": _div(e, rev), "net_income": fin.v(y, "net_income"),
        "cash_taxes": tax, "capex": capex, "unfinanced_capex": unfin, "distributions": dist, "cads": cads,
        "interest": interest, "principal": principal, "debt_service": ds, "dscr": _div(cads, ds),
        "rent": rent, "fccr": _div(None if cads is None else cads + rent, ds + rent),
        "cfo": fin.v(y, "cfo"), "cfo_to_net_income": _div(fin.v(y, "cfo"), fin.v(y, "net_income")),
        "funded_debt": debt, "leverage": _div(debt, e), "net_leverage": _div(debt - (fin.v(y, "cash", 0.0) or 0), e),
        "interest_coverage": _div(e, interest), "tnw": tnw,
        "debt_tnw": _div(fin.v(y, "total_liabilities"), tnw) if tnw and tnw > 0 else None,
        "current_ratio": _div(fin.v(y, "current_assets"), cl),
        "quick_ratio": _div((fin.v(y, "cash", 0.0) or 0) + (fin.v(y, "receivables", 0.0) or 0), cl),
        "dso": _div(fin.v(y, "receivables"), rev) * 365 if _div(fin.v(y, "receivables"), rev) is not None else None,
        "dio": _div(fin.v(y, "inventory"), cogs) * 365 if _div(fin.v(y, "inventory"), cogs) is not None else None,
        "dpo": _div(fin.v(y, "payables"), cogs) * 365 if _div(fin.v(y, "payables"), cogs) is not None else None,
    }


def analyse(fin: Financials, loan: Loan | None = None, pol: Policy | None = None) -> dict:
    loan, pol = loan or Loan(), pol or Policy()
    yrs = fin.years
    hist = [year_measures(fin, y, yrs[i - 1] if i else None, pol) for i, y in enumerate(yrs)]
    last = hist[-1]
    y = yrs[-1]
    # pro forma on the latest year: existing debt service (scheduled principal = this year-end's current portion),
    # plus the new loan's level payment; refinanced debt drops out
    existing_principal = fin.v(y, "current_debt", 0.0) or 0.0
    existing_interest = last["interest"]
    existing_debt = last["funded_debt"]
    if loan.refinance_existing:
        repaid = min(loan.amount, existing_debt)
        share = repaid / existing_debt if existing_debt else 0.0
        existing_principal *= 1 - share
        existing_interest *= 1 - share
        existing_debt -= repaid
    new_pmt = pmt(loan.rate, loan.amort_years, loan.amount)
    pf_ds = existing_interest + existing_principal + new_pmt
    e, cads = last["ebitda"], last["cads"]
    pf_debt = existing_debt + loan.amount
    pf = {"existing_debt_service": existing_interest + existing_principal, "new_loan_payment": new_pmt,
          "debt_service": pf_ds, "dscr": _div(cads, pf_ds),
          "fccr": _div(None if cads is None else cads + last["rent"], pf_ds + last["rent"]),
          "funded_debt": pf_debt, "leverage": _div(pf_debt, e),
          "net_leverage": _div(pf_debt - (fin.v(y, "cash", 0.0) or 0), e)}
    # capacity
    exist_ds = existing_interest + existing_principal
    cap_dscr = pv(loan.rate, loan.amort_years, cads / pol.min_dscr - exist_ds) if cads else 0.0
    cap_lev = max(0.0, pol.max_leverage * e - existing_debt) if e and e > 0 else 0.0
    capacity = {"by_dscr": max(0.0, cap_dscr), "by_leverage": cap_lev, "max_loan": max(0.0, min(cap_dscr, cap_lev)),
                "binding": "DSCR" if cap_dscr <= cap_lev else "leverage",
                "requested": loan.amount, "within_capacity": loan.amount <= max(0.0, min(cap_dscr, cap_lev)) + 1e-9}
    # covenants on the pro forma latest year, with EBITDA cushion (how far EBITDA can fall before a breach)
    cov = []

    def add(name, value, limit, kind, cushion):
        ok = None if value is None else (value >= limit if kind == "min" else value <= limit)
        cov.append({"covenant": name, "value": value, "limit": limit, "kind": kind, "pass": ok,
                    "ebitda_cushion": cushion})
    dscr_cushion = None
    if e and cads is not None and pf_ds:
        dscr_cushion = (cads - pol.min_dscr * pf_ds) / e          # CADS moves 1:1 with EBITDA (taxes held)
    add("Minimum DSCR (pro forma)", pf["dscr"], pol.min_dscr, "min", dscr_cushion)
    fc_cushion = None
    if e and cads is not None:
        fc_cushion = (cads + last["rent"] - pol.min_fccr * (pf_ds + last["rent"])) / e
    add("Minimum FCCR (pro forma)", pf["fccr"], pol.min_fccr, "min", fc_cushion)
    add("Maximum leverage (pro forma)", pf["leverage"], pol.max_leverage, "max",
        1 - pf_debt / pol.max_leverage / e if e and e > 0 else None)
    add("Maximum debt / TNW", last["debt_tnw"], pol.max_debt_tnw, "max", None)
    add("Minimum current ratio", last["current_ratio"], pol.min_current_ratio, "min", None)
    # stress: EBITDA down; rate up on a floating new loan
    stress = []
    for s in pol.ebitda_stress:
        c2 = None if cads is None else cads - e * s
        stress.append({"case": f"EBITDA -{s:.0%}", "ebitda": e * (1 - s) if e else None, "dscr": _div(c2, pf_ds),
                       "leverage": _div(pf_debt, e * (1 - s)) if e else None})
    if loan.floating and loan.amount:
        r2 = loan.rate + pol.rate_stress_bp / 10000
        ds2 = exist_ds + pmt(r2, loan.amort_years, loan.amount)
        stress.append({"case": f"Rate +{pol.rate_stress_bp}bp", "ebitda": e, "dscr": _div(cads, ds2),
                       "leverage": pf["leverage"]})
    breakeven = dscr_cushion if dscr_cushion is not None else None        # EBITDA decline to DSCR = minimum
    flags = trend_flags(hist)
    return {"borrower": fin.name, "unit": fin.unit, "years": yrs, "history": hist, "pro_forma": pf,
            "capacity": capacity, "covenants": cov, "stress": stress, "ebitda_decline_to_min_dscr": breakeven,
            "flags": flags, "rating": scorecard(hist[-1], pf, flags), "loan": asdict(loan), "policy": asdict(pol)}


def trend_flags(hist: list[dict]) -> list[str]:
    out = []
    a, b = (hist[-2], hist[-1]) if len(hist) > 1 else (None, hist[-1])
    if b["revenue_growth"] is not None and b["revenue_growth"] < 0:
        out.append(f"Revenue fell {abs(b['revenue_growth']):.1%} in {b['year']}")
    if a and a["ebitda_margin"] is not None and b["ebitda_margin"] is not None and \
            b["ebitda_margin"] < a["ebitda_margin"] - 0.02:
        out.append(f"EBITDA margin fell from {a['ebitda_margin']:.1%} to {b['ebitda_margin']:.1%}")
    if b["cfo"] is not None and b["cfo"] < 0:
        out.append(f"Negative operating cash flow in {b['year']}")
    if b["cfo_to_net_income"] is not None and b["net_income"] and b["net_income"] > 0 and b["cfo_to_net_income"] < 0.8:
        out.append(f"Operating cash flow is {b['cfo_to_net_income']:.0%} of net income (earnings quality)")
    for k, lab in (("dso", "Receivable days"), ("dio", "Inventory days")):
        if a and a[k] and b[k] and b[k] > a[k] * 1.15:
            out.append(f"{lab} rose from {a[k]:.0f} to {b[k]:.0f}")
    if a and a["leverage"] and b["leverage"] and b["leverage"] > a["leverage"] + 0.5:
        out.append(f"Leverage rose from {a['leverage']:.1f}x to {b['leverage']:.1f}x")
    if b["tnw"] is not None and b["tnw"] <= 0:
        out.append("Tangible net worth is negative")
    return out


def scorecard(last: dict, pf: dict, flags: list[str]) -> dict:
    """Indicative 1 (strong) to 5 (weak) score from DSCR, leverage, liquidity and trend. Not a regulatory rating."""
    def band(x, cuts, higher_better=True):
        if x is None:
            return 5
        for i, c in enumerate(cuts, start=1):
            if (x >= c) if higher_better else (x <= c):
                return i
        return 5
    s = {"dscr": band(pf["dscr"], (2.0, 1.5, 1.25, 1.0)), "leverage": band(pf["leverage"], (2.0, 3.0, 4.0, 5.0), False),
         "liquidity": band(last["current_ratio"], (2.0, 1.5, 1.2, 1.0)), "trend": min(5, 1 + len(flags))}
    score = round(sum(s.values()) / len(s), 2)
    grade = 1 if score < 1.75 else 2 if score < 2.5 else 3 if score < 3.25 else 4 if score < 4 else 5
    label = {1: "Strong", 2: "Satisfactory", 3: "Acceptable", 4: "Watch", 5: "Weak"}[grade]
    return {"components": s, "score": score, "grade": grade, "label": label}


# ------------------------------------------------------------------------------------------------ inputs
def from_spread(sp, years: list[str] | None = None) -> Financials:
    """From spreading.spread(...) (SEC filers). Values in $m with their SEC sources."""
    yrs = years or sp.years
    vals, srcs = {}, {}
    for y in yrs:
        vals[y], srcs[y] = {}, {}
        for k in KEYS:
            key = {"buybacks": "buybacks", "short_debt": "short_debt"}.get(k, k)
            cell = sp.cells.get(key, {}).get(y)
            if cell is not None and cell.value is not None:
                vals[y][k] = cell.value
                srcs[y][k] = cell.source()
    return Financials(sp.entity, yrs, vals, srcs)


def _norm_label(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9& ]", " ", str(s).lower().replace("’", "").replace("'", ""))).strip()


def match_label(label: str) -> str | None:
    """Map a borrower's line label to a standard key (exact synonym first, then the longest synonym it contains)."""
    t = _norm_label(label)
    t = re.sub(r"\s*\(.*?\)\s*", " ", t).strip()
    if re.search(r"liabilities (and|&) ", t):            # total liabilities and equity is a check line, not a key
        return None
    best, blen = None, 0
    for k, syns in LABELS.items():
        for s in syns:
            ns = _norm_label(s)
            if t == ns:
                return k
            if re.search(rf"(^| ){re.escape(ns)}( |$)", t) and len(ns) > blen:
                best, blen = k, len(ns)
    return best


def from_table(path: str, name: str | None = None, sheet: str | None = None) -> Financials:
    """A borrower spread from CSV or Excel: first column line labels, a header row of years, values in one unit.
    Labels are mapped to standard keys by synonyms; costs may be positive or negative (they are normalised).
    Unmapped rows are returned in .sources['_unmapped'] for review."""
    p = Path(path)
    rows: list[list] = []
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(p, data_only=True)
        ws = wb[sheet] if sheet else wb.worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    else:
        with p.open(newline="") as f:
            rows = [r for r in csv.reader(f)]
    hdr_i = next(i for i, r in enumerate(rows) if sum(bool(re.search(r"(19|20)\d{2}", str(c or ""))) for c in r[1:]) >= 1)
    hdr = rows[hdr_i]
    cols = [(j, re.search(r"(?:FY)?\s*((19|20)\d{2})", str(c)).group(0).strip()) for j, c in enumerate(hdr)
            if j > 0 and c is not None and re.search(r"(19|20)\d{2}", str(c))]
    years = [y if y.upper().startswith("FY") else f"FY{y}" for _, y in cols]
    vals = {y: {} for y in years}
    srcs: dict = {y: {} for y in years}
    unmapped = []
    for r in rows[hdr_i + 1:]:
        if not r or r[0] in (None, ""):
            continue
        k = match_label(r[0])
        if k is None:
            if not re.search(r"liabilities (and|&) ", _norm_label(r[0])):   # the L&E total is a check line
                unmapped.append(str(r[0]))
            continue
        for (j, _), y in zip(cols, years):
            x = r[j] if j < len(r) else None
            try:
                v = float(str(x).replace(",", "").replace("(", "-").replace(")", "").replace("$", "")) \
                    if x not in (None, "", "-") else None
            except ValueError:
                v = None
            if v is None or k in vals[y]:
                continue
            vals[y][k] = -abs(v) if k in NEGATIVE else v
            srcs[y][k] = f"{p.name}, row '{r[0]}', column {hdr[j]}"
    srcs["_unmapped"] = unmapped
    return Financials(name or p.stem, years, vals, srcs)
