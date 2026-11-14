"""Spread SEC EDGAR XBRL company facts into standard annual statements (income statement, balance sheet, cash flow).

    from engines.sheets import spreading as S
    facts = S.fetch_companyfacts("0000320193", user_agent="Firm Name analyst@firm.com")   # or S.load_companyfacts(path)
    # user_agent defaults to $SEC_USER_AGENT; S.cik_for_ticker("AAPL") finds a CIK
    sp = S.spread(facts, years=3)                       # last three fiscal years from 10-K filings
    sp.value("revenue", "FY2024"); sp.gaps; sp.checks   # every cell carries its tag, accession, form and filing date
    t = S.write_spread(wb["Historicals"], 3, sp)        # blue sourced inputs, cell comments, in-sheet checks

Rules this module keeps:
- Annual figures only: 10-K family forms, duration items covering one fiscal year (340-390 days), balance sheet items
  at the fiscal year end. When a figure was restated, the most recently filed value wins and the source says so.
- Each standard line has an ordered list of us-gaap tags. The first tag reported for that year is used and recorded.
  Nothing is estimated: a line no tag covers is a gap (listed with the tags tried), and its cell stays empty.
- Derived lines (e.g. other current assets = total current assets - named current assets) are formulas over reported
  figures and say so in their source; a derived line with a missing input is a gap too.
- Checks: balance sheet balances, cash flow change in cash ties (activities and cash roll), net income ties between
  the income statement and the cash flow statement. A check that cannot run says why instead of passing.

SEC fair-access policy: send a User-Agent that names the requester (company/name and e-mail) and stay under 10
requests per second (https://www.sec.gov/os/accessing-edgar-data).
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

API = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
TICKERS = "https://www.sec.gov/files/company_tickers.json"
ANNUAL_FORMS = ("10-K", "10-K/A", "10-KT", "10-KT/A")
SCALE = 1e6                                            # statements in $m; shares in m; per-share items unscaled

# key, label, statement, kind (duration|instant), sign (+1 as reported, -1 shown negative), unit, tags in order.
# Costs and outflows are shown negative (label says so) so totals add up without sign mistakes.
LINES: list[tuple[str, str, str, str, int, str, list[str]]] = [
    ("revenue", "Revenue ($m)", "IS", "duration", 1, "USD",
     ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
      "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet"]),
    ("cogs", "Cost of revenue ($m, negative)", "IS", "duration", -1, "USD",
     ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfServices"]),
    ("gross_profit", "Gross profit ($m)", "IS", "duration", 1, "USD", ["GrossProfit"]),
    ("rnd", "Research and development ($m, negative)", "IS", "duration", -1, "USD",
     ["ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"]),
    ("sga", "Selling, general and administrative ($m, negative)", "IS", "duration", -1, "USD",
     ["SellingGeneralAndAdministrativeExpense", "GeneralAndAdministrativeExpense"]),
    ("operating_income", "Operating income ($m)", "IS", "duration", 1, "USD", ["OperatingIncomeLoss"]),
    ("interest_expense", "Interest expense ($m, negative)", "IS", "duration", -1, "USD",
     ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"]),
    ("pretax_income", "Pre-tax income ($m)", "IS", "duration", 1, "USD",
     ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic"]),
    ("income_tax", "Income tax ($m, negative)", "IS", "duration", -1, "USD", ["IncomeTaxExpenseBenefit"]),
    ("net_income", "Net income attributable to the company ($m)", "IS", "duration", 1, "USD",
     ["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"]),
    ("nci_income", "Net income attributable to minorities ($m)", "IS", "duration", 1, "USD",
     ["NetIncomeLossAttributableToNoncontrollingInterest"]),
    ("diluted_shares", "Diluted weighted average shares (m)", "IS", "duration", 1, "shares",
     ["WeightedAverageNumberOfDilutedSharesOutstanding"]),
    ("diluted_eps", "Diluted EPS ($ per share)", "IS", "duration", 1, "USD/shares", ["EarningsPerShareDiluted"]),

    ("cash", "Cash and cash equivalents ($m)", "BS", "instant", 1, "USD",
     ["CashAndCashEquivalentsAtCarryingValue", "Cash"]),
    ("st_investments", "Short-term investments ($m)", "BS", "instant", 1, "USD",
     ["MarketableSecuritiesCurrent", "ShortTermInvestments", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"]),
    ("receivables", "Accounts receivable ($m)", "BS", "instant", 1, "USD",
     ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"]),
    ("inventory", "Inventory ($m)", "BS", "instant", 1, "USD", ["InventoryNet"]),
    ("current_assets", "Total current assets ($m)", "BS", "instant", 1, "USD", ["AssetsCurrent"]),
    ("ppe", "Property, plant and equipment, net ($m)", "BS", "instant", 1, "USD",
     ["PropertyPlantAndEquipmentNet",
      "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"]),
    ("goodwill", "Goodwill ($m)", "BS", "instant", 1, "USD", ["Goodwill"]),
    ("intangibles", "Intangible assets, net ($m)", "BS", "instant", 1, "USD",
     ["IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet"]),
    ("total_assets", "Total assets ($m)", "BS", "instant", 1, "USD", ["Assets"]),
    ("payables", "Accounts payable ($m)", "BS", "instant", 1, "USD", ["AccountsPayableCurrent"]),
    ("short_debt", "Short-term borrowings and commercial paper ($m)", "BS", "instant", 1, "USD",
     ["CommercialPaper", "ShortTermBorrowings"]),
    ("current_debt", "Current portion of long-term debt ($m)", "BS", "instant", 1, "USD",
     ["LongTermDebtCurrent", "DebtCurrent"]),
    ("current_liabilities", "Total current liabilities ($m)", "BS", "instant", 1, "USD", ["LiabilitiesCurrent"]),
    ("long_debt", "Long-term debt ($m)", "BS", "instant", 1, "USD",
     ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"]),
    ("total_liabilities", "Total liabilities ($m)", "BS", "instant", 1, "USD", ["Liabilities"]),
    ("equity_parent", "Shareholders' equity, company share ($m)", "BS", "instant", 1, "USD", ["StockholdersEquity"]),
    ("nci", "Minority interest ($m)", "BS", "instant", 1, "USD", ["MinorityInterest"]),
    ("total_equity", "Total equity ($m)", "BS", "instant", 1, "USD",
     ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "StockholdersEquity"]),
    ("total_liab_equity", "Total liabilities and equity ($m)", "BS", "instant", 1, "USD",
     ["LiabilitiesAndStockholdersEquity"]),

    ("cf_net_income", "Net income, cash flow start ($m)", "CF", "duration", 1, "USD", ["ProfitLoss", "NetIncomeLoss"]),
    ("da", "Depreciation and amortisation ($m)", "CF", "duration", 1, "USD",
     ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet", "DepreciationAndAmortization",
      "Depreciation"]),
    ("sbc", "Share-based compensation ($m)", "CF", "duration", 1, "USD", ["ShareBasedCompensation"]),
    ("cfo", "Cash from operating activities ($m)", "CF", "duration", 1, "USD",
     ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]),
    ("capex", "Capital expenditure ($m, negative)", "CF", "duration", -1, "USD",
     ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]),
    ("cfi", "Cash from investing activities ($m)", "CF", "duration", 1, "USD",
     ["NetCashProvidedByUsedInInvestingActivities", "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations"]),
    ("dividends", "Dividends paid ($m, negative)", "CF", "duration", -1, "USD",
     ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"]),
    ("buybacks", "Share repurchases ($m, negative)", "CF", "duration", -1, "USD", ["PaymentsForRepurchaseOfCommonStock"]),
    ("cff", "Cash from financing activities ($m)", "CF", "duration", 1, "USD",
     ["NetCashProvidedByUsedInFinancingActivities", "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations"]),
    ("fx_effect", "Effect of exchange rates on cash ($m)", "CF", "duration", 1, "USD",
     ["EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
      "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations",
      "EffectOfExchangeRateOnCashAndCashEquivalents"]),
    ("net_change_cash", "Net change in cash ($m)", "CF", "duration", 1, "USD",
     ["CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect",
      "CashAndCashEquivalentsPeriodIncreaseDecrease",
      "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseExcludingExchangeRateEffect"]),
    ("cf_cash_end", "Cash, cash flow basis, end of year ($m)", "CF", "instant", 1, "USD",
     ["CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "CashAndCashEquivalentsAtCarryingValue"]),
]
LINE = {k: dict(key=k, label=lab, statement=st, kind=kd, sign=sg, unit=u, tags=t)
        for k, lab, st, kd, sg, u, t in LINES}
STATEMENTS = {"IS": "Income statement", "BS": "Balance sheet", "CF": "Cash flow statement"}
# Tags whose net-change companion (the line above) is "excluding exchange rate effect": FX must then be added.
_EXCL_FX = "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseExcludingExchangeRateEffect"


# ------------------------------------------------------------------------------------------------- fetching
def _cik10(cik) -> str:
    s = re.sub(r"\D", "", str(cik))
    if not s:
        raise ValueError(f"not a CIK: {cik!r}")
    return s.zfill(10)


def _get(url: str, user_agent: str, timeout: float = 30.0) -> bytes:
    if not user_agent or "@" not in user_agent:
        raise ValueError("SEC requires a User-Agent naming the requester, e.g. 'Firm Name analyst@firm.com'")
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity",
                                               "Accept": "application/json"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:                       # 429 / transient network errors: back off, stay polite
            last = e
            time.sleep(1.0 + attempt)
    raise RuntimeError(f"SEC request failed for {url}: {last}")


def default_user_agent() -> str:
    """SEC_USER_AGENT from the environment (e.g. "Firm Name analyst@firm.com"), else Meshpool's public contact."""
    import os
    return os.environ.get("SEC_USER_AGENT") or "Meshpool research hello@meshpool.ai"


def fetch_companyfacts(cik, user_agent: str | None = None, cache_dir: str | None = None) -> dict:
    """Download https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json (cached in cache_dir if given)."""
    user_agent = user_agent or default_user_agent()
    c = _cik10(cik)
    if cache_dir:
        p = Path(cache_dir) / f"CIK{c}.json"
        if p.exists():
            return json.loads(p.read_text())
    data = json.loads(_get(API.format(cik=c), user_agent))
    if cache_dir:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        (Path(cache_dir) / f"CIK{c}.json").write_text(json.dumps(data))
    return data


def cik_for_ticker(ticker: str, user_agent: str | None = None) -> str:
    """Ticker -> 10-digit CIK via SEC's company_tickers.json."""
    rows = json.loads(_get(TICKERS, user_agent or default_user_agent())).values()
    for r in rows:
        if str(r.get("ticker", "")).upper() == ticker.upper():
            return _cik10(r["cik_str"])
    raise KeyError(f"ticker {ticker} not found in SEC company_tickers.json")


def load_companyfacts(path) -> dict:
    return json.loads(Path(path).read_text())


# ------------------------------------------------------------------------------------------------- spreading
@dataclass
class Cell:
    value: float | None
    tag: str | None = None
    accn: str | None = None
    form: str | None = None
    filed: str | None = None
    end: str | None = None
    url: str | None = None
    derived: str | None = None                       # formula description for derived lines
    restated: bool = False                           # a later filing changed the originally reported value

    def source(self) -> str:
        if self.value is None:
            return ""
        if self.derived:
            return f"Derived: {self.derived}"
        s = f"SEC {self.form} filed {self.filed}, accession {self.accn}, us-gaap:{self.tag}"
        return s + (" (restated in a later filing; latest value used)" if self.restated else "")


@dataclass
class Spread:
    entity: str
    cik: str
    years: list[str]                                 # labels, oldest first: "FY2022", ...
    ends: dict[str, str]                             # label -> fiscal year end date
    cells: dict[str, dict[str, Cell]]                # line key -> label -> Cell
    gaps: list[dict] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)

    def value(self, key: str, year: str) -> float | None:
        return self.cells.get(key, {}).get(year, Cell(None)).value

    def to_dict(self) -> dict:
        return {"entity": self.entity, "cik": self.cik, "years": self.years, "ends": self.ends,
                "lines": {k: {y: c.__dict__ for y, c in v.items()} for k, v in self.cells.items()},
                "gaps": self.gaps, "checks": self.checks}


def fy_label(end: str) -> str:
    """Fiscal year label from a year-end date, by the nearest-year rule: a 52/53-week year that ends in the first
    week of January (e.g. 2022-01-01) is the prior fiscal year (FY2021), so consecutive years never collide."""
    y, m, d = int(end[:4]), int(end[5:7]), int(end[8:10])
    return f"FY{y - 1 if m == 1 and d <= 7 else y}"


def name_part(label: str) -> str:
    """A year label as it goes into a defined name (letters, digits and underscores only)."""
    return re.sub(r"[^a-z0-9_]", "_", label.lower())


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _filing_url(cik: str, accn: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/"


def _annual_facts(facts: dict, tag: str, unit: str, kind: str) -> dict[str, list[dict]]:
    """end date -> facts from 10-K family filings for this tag (durations: one fiscal year long)."""
    node = facts.get("facts", {}).get("us-gaap", {}).get(tag)
    if not node:
        return {}
    out: dict[str, list[dict]] = {}
    for f in node.get("units", {}).get(unit, []):
        if f.get("form") not in ANNUAL_FORMS or "end" not in f or f.get("val") is None:
            continue
        if kind == "duration":
            if "start" not in f or not 340 <= _days(f["start"], f["end"]) <= 390:
                continue
        elif "start" in f:
            continue
        out.setdefault(f["end"], []).append(f)
    return out


def fiscal_year_ends(facts: dict) -> list[str]:
    """Fiscal year end dates (sorted) seen on annual income-statement facts in 10-K filings."""
    ends = set()
    for key in ("revenue", "net_income"):
        for t in LINE[key]["tags"]:
            ends |= set(_annual_facts(facts, t, "USD", "duration"))
    # drop near-duplicates (52/53-week calendars report the same year end once; keep distinct years)
    out = []
    for e in sorted(ends):
        if out and _days(out[-1], e) < 300:
            out[-1] = max(out[-1], e)
        else:
            out.append(e)
    return out


def _pick(facts: dict, line: dict, end: str, cik: str) -> Cell | None:
    for tag in line["tags"]:
        got = _annual_facts(facts, tag, line["unit"], line["kind"]).get(end)
        if not got:
            continue
        got = sorted(got, key=lambda f: (f.get("filed", ""), f.get("accn", "")))
        latest, first = got[-1], got[0]
        scale = 1.0 if line["unit"] == "USD/shares" else SCALE
        return Cell(value=line["sign"] * latest["val"] / scale, tag=tag, accn=latest.get("accn"),
                    form=latest.get("form"), filed=latest.get("filed"), end=end,
                    url=_filing_url(cik, latest.get("accn", "")), restated=latest["val"] != first["val"])
    return None


DERIVED = {   # key: (label, statement, [(sign, line)], description) - other lines that make the condensed BS whole
    "other_current_assets": ("Other current assets ($m)", "BS",
                             [(1, "current_assets"), (-1, "cash"), (-1, "st_investments"), (-1, "receivables"),
                              (-1, "inventory")],
                             "total current assets minus cash, short-term investments, receivables and inventory"),
    "other_noncurrent_assets": ("Other non-current assets ($m)", "BS",
                                [(1, "total_assets"), (-1, "current_assets"), (-1, "ppe")],
                                "total assets minus total current assets and net PP&E"),
    "other_current_liabilities": ("Other current liabilities ($m)", "BS",
                                  [(1, "current_liabilities"), (-1, "payables"), (-1, "short_debt"),
                                   (-1, "current_debt")],
                                  "total current liabilities minus payables and current debt"),
    "other_noncurrent_liabilities": ("Other non-current liabilities ($m)", "BS",
                                     [(1, "total_liabilities"), (-1, "current_liabilities"), (-1, "long_debt")],
                                     "total liabilities minus total current liabilities and long-term debt"),
    "total_debt": ("Total debt ($m)", "BS", [(1, "short_debt"), (1, "current_debt"), (1, "long_debt")],
                   "short-term borrowings plus current and long-term debt"),
}
# Lines a company may simply not have: absent means zero in derived lines, and the gap is reported as "not reported".
OPTIONAL_ZERO = {"st_investments", "inventory", "short_debt", "current_debt", "long_debt", "nci", "goodwill",
                 "intangibles", "nci_income", "dividends", "buybacks", "fx_effect", "rnd", "interest_expense"}


def spread(facts: dict, years: int | list[str] = 3) -> Spread:
    """Annual IS/BS/CF for the last `years` fiscal years (or the given fiscal year end dates)."""
    cik = _cik10(facts.get("cik", 0))
    entity = facts.get("entityName", "")
    all_ends = fiscal_year_ends(facts)
    ends = all_ends[-years:] if isinstance(years, int) else list(years)
    if not ends:
        raise ValueError(f"{entity or cik}: no annual us-gaap income statement facts in 10-K filings "
                         "(foreign filers report under IFRS; spread them from the 20-F instead)")
    labels = [fy_label(e) for e in ends]
    if len(set(labels)) != len(labels):
        labels = [f"FY{e.replace('-', '_')}" for e in ends]
    end_of = dict(zip(labels, ends))
    prior = {lab: (all_ends[all_ends.index(e) - 1] if e in all_ends and all_ends.index(e) > 0 else None)
             for lab, e in end_of.items()}
    cells: dict[str, dict[str, Cell]] = {}
    gaps = []
    for key, line in LINE.items():
        cells[key] = {}
        for lab, e in end_of.items():
            c = _pick(facts, line, e, cik)
            if c is None:
                cells[key][lab] = Cell(None, end=e)
                gaps.append({"line": key, "label": line["label"], "year": lab, "tags_tried": line["tags"],
                             "kind": "not reported" if key in OPTIONAL_ZERO else "missing"})
            else:
                cells[key][lab] = c
    # prior-year cash (cash flow basis) for the cash roll check
    cells["cf_cash_begin"] = {}
    for lab in labels:
        pe = prior[lab]
        c = _pick(facts, LINE["cf_cash_end"], pe, cik) if pe else None
        cells["cf_cash_begin"][lab] = c or Cell(None)
    for key, (label, st, parts, desc) in DERIVED.items():
        cells[key] = {}
        for lab in labels:
            vals, missing = [], []
            for sg, k in parts:
                v = cells[k][lab].value
                if v is None and k not in OPTIONAL_ZERO:
                    missing.append(k)
                vals.append(sg * (v or 0.0))
            if missing:
                cells[key][lab] = Cell(None, end=end_of[lab])
                gaps.append({"line": key, "label": label, "year": lab, "tags_tried": [],
                             "kind": "missing input", "missing": missing})
            else:
                cells[key][lab] = Cell(sum(vals), end=end_of[lab], derived=desc)
    sp = Spread(entity, cik, labels, end_of, cells, gaps)
    sp.checks = run_checks(sp)
    return sp


def _v(sp, k, y):
    return sp.value(k, y)


def run_checks(sp: Spread, tol: float = 0.5) -> list[dict]:
    """tol in $m. status: pass | fail | not_run (with the reason)."""
    out = []

    def add(name, year, diff, why=None):
        if diff is None:
            out.append({"check": name, "year": year, "status": "not_run", "diff": None, "detail": why})
        else:
            out.append({"check": name, "year": year, "status": "pass" if abs(diff) <= tol else "fail",
                        "diff": round(diff, 6), "detail": why})

    for y in sp.years:
        ta, tle = _v(sp, "total_assets", y), _v(sp, "total_liab_equity", y)
        tl, te = _v(sp, "total_liabilities", y), _v(sp, "total_equity", y)
        add("Balance sheet balances: total assets = total liabilities and equity", y,
            None if None in (ta, tle) else ta - tle, None if None not in (ta, tle) else "total assets or total "
            "liabilities and equity not reported")
        if None in (ta, tl, te):
            add("Balance sheet balances: total assets = liabilities + equity", y, None,
                "total liabilities or total equity not reported (common when a company shows only the combined "
                "total); not derived")
        else:
            add("Balance sheet balances: total assets = liabilities + equity", y, ta - tl - te,
                None if abs(ta - tl - te) <= tol else "a gap here usually means temporary or mezzanine equity "
                "sits between liabilities and equity")
        parts = [_v(sp, k, y) for k in ("cfo", "cfi", "cff")]
        nc = _v(sp, "net_change_cash", y)
        fx = _v(sp, "fx_effect", y) or 0.0
        if None in parts or nc is None:
            add("Cash flow ties: operating + investing + financing + FX = change in cash", y, None,
                "an activity subtotal or the change in cash is not reported")
        else:
            excl = sp.cells["net_change_cash"][y].tag == _EXCL_FX
            add("Cash flow ties: operating + investing + financing + FX = change in cash", y,
                sum(parts) + (0.0 if excl else fx) - nc)
        b, e = _v(sp, "cf_cash_begin", y), _v(sp, "cf_cash_end", y)
        if None in (b, e, nc):
            add("Cash flow ties: opening cash + change in cash = closing cash", y, None,
                "prior-year closing cash or the change in cash is not reported")
        else:
            excl = sp.cells["net_change_cash"][y].tag == _EXCL_FX
            add("Cash flow ties: opening cash + change in cash = closing cash", y, b + nc + (fx if excl else 0.0) - e)
        ni, nci, cfni = _v(sp, "net_income", y), _v(sp, "nci_income", y), _v(sp, "cf_net_income", y)
        pt, tx = _v(sp, "pretax_income", y), _v(sp, "income_tax", y)
        if None in (pt, tx, ni):
            add("Net income ties: pre-tax income + tax = net income", y, None, "pre-tax income, tax or net income "
                "not reported")
        else:
            d = pt + tx - ni - (nci or 0.0)
            add("Net income ties: pre-tax income + tax = net income", y, d, None if abs(d) <= tol else
                "a gap usually means equity-method income or discontinued operations sit below tax; check the "
                "income statement")
        if ni is None or cfni is None:
            add("Net income ties: income statement = cash flow statement", y, None, "net income not reported")
        elif sp.cells["cf_net_income"][y].tag == sp.cells["net_income"][y].tag:
            add("Net income ties: income statement = cash flow statement", y, None,
                "the cash flow starts from the same tag as the income statement; nothing independent to compare")
        else:
            add("Net income ties: income statement = cash flow statement", y, ni + (nci or 0.0) - cfni)
    return out


# ------------------------------------------------------------------------------------------------- metrics
def history_metrics(sp: Spread) -> dict[str, dict[str, float | None]]:
    """Historical ratios that anchor forecast assumptions (each None when an input is a gap):
    revenue growth, gross margin, SG&A and R&D % revenue, DSO / DIO / DPO (days, year-end balances), capex and D&A %
    revenue, D&A % opening PP&E, effective tax rate, dividend payout."""
    out: dict[str, dict[str, float | None]] = {}

    def ratio(a, b, k=1.0):
        return None if a is None or b in (None, 0) else a / b * k
    for i, y in enumerate(sp.years):
        g = lambda k: _v(sp, k, y)  # noqa: E731
        prev = sp.years[i - 1] if i else None
        rev, cogs = g("revenue"), g("cogs")
        out[y] = {
            "revenue_growth": ratio(rev, _v(sp, "revenue", prev)) - 1 if prev and ratio(rev, _v(sp, "revenue", prev))
            is not None else None,
            "gross_margin": None if rev in (None, 0) or cogs is None else (rev + cogs) / rev,
            "sga_pct": ratio(-(g("sga") or 0) if g("sga") is not None else None, rev),
            "rnd_pct": ratio(-(g("rnd") or 0) if g("rnd") is not None else None, rev),
            "dso_days": ratio(g("receivables"), rev, 365),
            "dio_days": ratio(g("inventory"), -cogs if cogs else None, 365),
            "dpo_days": ratio(g("payables"), -cogs if cogs else None, 365),
            "capex_pct": ratio(-g("capex") if g("capex") is not None else None, rev),
            "da_pct": ratio(g("da"), rev),
            "da_pct_opening_ppe": ratio(g("da"), _v(sp, "ppe", prev)) if prev else None,
            "tax_rate": ratio(-g("income_tax") if g("income_tax") is not None else None, g("pretax_income")),
            "payout": ratio(-g("dividends") if g("dividends") is not None else None, g("net_income")),
        }
    return out


def opening_balances(sp: Spread, year: str | None = None) -> dict:
    """Condensed opening balance sheet for three_statement(): cash, receivables, inventory, other current assets,
    PP&E, other non-current assets, payables, other current liabilities, debt, other non-current liabilities,
    equity. Raises when an item it needs is a gap (it does not guess)."""
    y = year or sp.years[-1]
    out, missing = {}, []
    for k, _, lines in OPENING_LINKS:
        tot = 0.0
        for line in lines:
            v = _v(sp, line, y)
            if v is None and line in OPTIONAL_ZERO:
                v = 0.0
            if v is None:
                missing.append(line)
            else:
                tot += v
        out[k] = tot
    if missing:
        raise ValueError(f"{sp.entity} {y}: cannot build an opening balance sheet; gaps in {missing}")
    return out


# model opening name, label, spread lines summed (short-term investments are not operating cash: they sit in other
# current assets, so the model's cash and minimum-cash logic use cash and equivalents only)
OPENING_LINKS = [("cash_open", "Cash, opening ($m)", ["cash"]),
                 ("ar_open", "Accounts receivable, opening ($m)", ["receivables"]),
                 ("inv_open", "Inventory, opening ($m)", ["inventory"]),
                 ("other_ca_open", "Other current assets incl. short-term investments, opening ($m)",
                  ["other_current_assets", "st_investments"]),
                 ("ppe_open", "PP&E net, opening ($m)", ["ppe"]),
                 ("other_nca_open", "Other non-current assets incl. goodwill, opening ($m)", ["other_noncurrent_assets"]),
                 ("ap_open", "Accounts payable, opening ($m)", ["payables"]),
                 ("other_cl_open", "Other current liabilities, opening ($m)", ["other_current_liabilities"]),
                 ("term_debt_open", "Debt (all borrowings), opening ($m)", ["total_debt"]),
                 ("other_ncl_open", "Other non-current liabilities, opening ($m)", ["other_noncurrent_liabilities"]),
                 ("equity_open", "Total equity, opening ($m)", ["total_equity"])]


def link_opening_balances(book, sp: Spread, year: str | None = None, name_prefix: str = "h_",
                          names: dict | None = None) -> dict:
    """Write the opening balance sheet into an assumption or scenario book as live links to the Historicals tab
    (names from write_spread), so the model starts from the filed balance sheet. A line the company does not report
    (e.g. inventory) links to zero and says so; a line that is a real gap raises instead of being guessed.
    Also adds revolver_open = 0 (debt is carried as term debt). Returns {model name: book name}."""
    y = year or sp.years[-1]
    names = names or {}
    wb = book.ws.parent
    have = {k.lower() for k in wb.defined_names.keys()}
    out = {}
    for key, label, lines in OPENING_LINKS:
        nm = names.get(key, key)
        terms, srcs, absent = [], [], []
        for line in lines:
            h = f"{name_prefix}{line}_{name_part(y)}"
            if h.lower() in have:
                terms.append(h)
                srcs.append(sp.cells[line][y].source() or "SEC 10-K")
            elif line in OPTIONAL_ZERO:
                absent.append(line)
            else:
                raise ValueError(f"{sp.entity} {y}: opening {line} is a gap; fill it from the filing before modelling")
        src = (f"Historicals tab, {y}: " + "; ".join(srcs)) if terms else f"Not reported in the {y} 10-K: zero"
        if absent:
            src += f" ({', '.join(absent)} not reported: zero)"
        book.link(label, "=" + ("+".join(terms) or "0"), "usd", name=nm, source=src[:250],
                  rationale="Filed balance sheet at the valuation date")
        out[key] = nm
    book.link("Revolver drawn, opening ($m)", "=0", "usd", name=names.get("revolver_open", "revolver_open"),
              source="Model convention: all opening borrowings sit in term debt",
              rationale="Opening debt is carried as one term balance; the revolver only funds shortfalls")
    out["revolver_open"] = names.get("revolver_open", "revolver_open")
    return out


# ------------------------------------------------------------------------------------------------- workbook
def write_spread(ws, row: int, sp: Spread, order=("IS", "BS", "CF"), name_prefix: str = "h_") -> dict:
    """Historicals tab: one block per statement, years across ("FY2024A"), every reported figure a blue input with
    its source in the Source column and a cell comment; derived lines are live formulas; gaps stay empty and are
    listed; in-sheet check rows per year, each added to the Checks tab when the data is complete.
    Names: <name_prefix><line>_<fy> (e.g. h_revenue_fy2024) for every reported or derived cell."""
    from openpyxl.comments import Comment
    from openpyxl.utils import get_column_letter
    from openpyxl.styles import Font
    from . import builder as B
    wb = ws.parent
    cols = [get_column_letter(2 + i) for i in range(len(sp.years))]
    src_col = get_column_letter(2 + len(sp.years))
    B.title(ws, f"{sp.entity}: historical financial statements (SEC XBRL, $m)", row - 2 if row > 2 else 1)
    B.header(ws, row, ["Line item"] + [f"{y}A" for y in sp.years] + ["Source"])
    B.freeze(ws, f"B{row + 1}")
    r = row + 1
    where: dict[str, int] = {}
    nm = lambda k, y: f"{name_prefix}{k}_{name_part(y)}"  # noqa: E731
    blocks = {"IS": [k for k, l in LINE.items() if l["statement"] == "IS"],
              "BS": [k for k, l in LINE.items() if l["statement"] == "BS" and k != "total_liab_equity"]
              + ["other_current_assets", "other_noncurrent_assets", "other_current_liabilities",
                 "other_noncurrent_liabilities", "total_debt", "total_liab_equity"],
              "CF": [k for k, l in LINE.items() if l["statement"] == "CF"]}
    for st in order:
        B.section(ws, r, STATEMENTS[st], len(cols) + 2)
        r += 1
        for key in blocks[st]:
            label = LINE[key]["label"] if key in LINE else DERIVED[key][0]
            fmt = "usd2" if key == "diluted_eps" else ("num" if key == "diluted_shares" else "usd")
            ws.cell(row=r, column=1, value=label)
            where[key] = r
            tags = set()
            for c, y in zip(cols, sp.years):
                cell = sp.cells[key][y]
                if key in DERIVED:
                    if cell.value is None:
                        continue
                    parts = DERIVED[key][2]
                    expr = "=" + "".join(("+" if sg > 0 else "-") + f"{c}{where[k]}" for sg, k in parts
                                         if k in where).lstrip("+")
                    B._f(ws, f"{c}{r}", expr, fmt)
                elif cell.value is not None:
                    B.input_cell(ws, f"{c}{r}", cell.value, fmt)
                    ws[f"{c}{r}"].comment = Comment(cell.source() + f"\n{cell.url}", "SEC EDGAR")
                    tags.add(cell.tag)
                else:
                    continue
                B.define_name(wb, nm(key, y), f"'{ws.title}'!${c}${r}")
            if key in DERIVED:
                src = "Derived: " + DERIVED[key][3]
            elif tags:
                accns = sorted({sp.cells[key][y].accn for y in sp.years if sp.cells[key][y].value is not None})
                src = f"SEC 10-K XBRL us-gaap:{', '.join(sorted(tags))}; accession {', '.join(accns)}"
            else:
                src = None
            missing = [y for y in sp.years if sp.cells[key][y].value is None]
            if missing:
                src = ((src + "; ") if src else "") + f"GAP {', '.join(missing)}: not reported under " + (
                    ", ".join(LINE[key]["tags"]) if key in LINE else "its inputs")
            ws[f"{src_col}{r}"] = src
            r += 1
        r += 1
    # checks, per year, as live formulas
    B.section(ws, r, "Checks (each should be zero, $m)", len(cols) + 2)
    r += 1
    check_rows = []
    defs = [("Check: balance sheet balances, assets minus liabilities and equity ($m)",
             ["total_assets", "total_liab_equity"], "={total_assets}-{total_liab_equity}"),
            ("Check: cash flow activities plus FX minus change in cash ($m)",
             ["cfo", "cfi", "cff", "net_change_cash"], "={cfo}+{cfi}+{cff}+{fx_effect}-{net_change_cash}"),
            ("Check: income statement net income minus cash flow net income ($m)",
             ["net_income", "cf_net_income"], "={net_income}+{nci_income}-{cf_net_income}"),
            ("Check: pre-tax income plus tax minus net income ($m)",
             ["pretax_income", "income_tax", "net_income"], "={pretax_income}+{income_tax}-{net_income}-{nci_income}")]
    for label, need, expr in defs:
        ws.cell(row=r, column=1, value=label)
        ok_years = []
        for c, y in zip(cols, sp.years):
            if any(sp.cells[k][y].value is None for k in need):
                continue
            if "fx_effect" in expr and sp.cells["net_change_cash"][y].tag == _EXCL_FX:
                e = expr.replace("+{fx_effect}", "")
            else:
                e = expr
            e = re.sub(r"\{(\w+)\}", lambda m: f"N({c}{where[m.group(1)]})", e)
            B._f(ws, f"{c}{r}", e, "usd")
            ok_years.append(c)
        if ok_years and "Checks" in wb.sheetnames:
            B.add_check(wb, f"Historicals: {label[7:].split(',')[0].split(' minus')[0]} ({len(ok_years)} years)",
                        "=" + "+".join(f"ABS('{ws.title}'!{c}{r})" for c in ok_years))
        check_rows.append(r)
        r += 1
    if sp.gaps:
        r += 1
        B.section(ws, r, "Gaps: lines not reported (left empty, not estimated)", len(cols) + 2)
        r += 1
        for g in sp.gaps:
            ws.cell(row=r, column=1, value=f"{g['label']}, {g['year']}")
            ws.cell(row=r, column=2, value=g["kind"] + (": " + ", ".join(g["tags_tried"][:3]) if g["tags_tried"]
                                                        else ": " + ", ".join(g.get("missing", []))))
            r += 1
    ws.column_dimensions["A"].width = 52
    for c in cols:
        ws.column_dimensions[c].width = 14
    ws.column_dimensions[src_col].width = 70
    ws.cell(row=r + 1, column=1, value=f"Entity: {sp.entity}, CIK {sp.cik}. Source: SEC EDGAR XBRL company facts "
            f"API ({API.format(cik=sp.cik)}).").font = Font(name=B.FONT, italic=True, color="5B6577")
    return {"rows": where, "cols": dict(zip(sp.years, cols)), "check_rows": check_rows, "next_row": r + 3,
            "name": nm}


# ------------------------------------------------------------------------------------------------- quarters, LTM
QUARTER_FORMS = ANNUAL_FORMS + ("10-Q", "10-Q/A")


def _facts_by_end(facts: dict, tag: str, unit: str, kind: str, lo: int, hi: int) -> dict[str, dict]:
    """end date -> latest-filed fact (durations lo..hi days long; instants when kind == 'instant')."""
    node = facts.get("facts", {}).get("us-gaap", {}).get(tag)
    out: dict[str, dict] = {}
    if not node:
        return out
    for f in node.get("units", {}).get(unit, []):
        if f.get("form") not in QUARTER_FORMS or "end" not in f or f.get("val") is None:
            continue
        if kind == "duration":
            if "start" not in f or not lo <= _days(f["start"], f["end"]) <= hi:
                continue
        elif "start" in f:
            continue
        cur = out.get(f["end"])
        if cur is None or (f.get("filed", ""), f.get("accn", "")) > (cur.get("filed", ""), cur.get("accn", "")):
            out[f["end"]] = f
    return out


@dataclass
class Quarters:
    entity: str
    cik: str
    labels: list[str]                       # "Q1 FY2026", oldest first
    ends: dict[str, str]
    cells: dict[str, dict[str, Cell]]
    gaps: list[dict] = field(default_factory=list)

    def value(self, key: str, label: str) -> float | None:
        return self.cells.get(key, {}).get(label, Cell(None)).value


def spread_quarters(facts: dict, quarters: int = 8, keys: list[str] | None = None) -> Quarters:
    """Quarterly IS/CF flows and quarter-end balances from 10-Q and 10-K filings. Fiscal quarters are labelled from
    the fiscal year-end dates (XBRL's fy/fp fields describe the filing, not the period). A fourth quarter that is
    not reported for three months on its own is derived as the full year less the first three quarters, and the
    source says so. Cash flow lines are reported year-to-date in 10-Qs; quarters are the differences of YTD values."""
    cik = _cik10(facts.get("cik", 0))
    fy_ends = fiscal_year_ends(facts)
    if not fy_ends:
        raise ValueError("no annual facts: cannot place quarters in fiscal years")
    keys = keys or [k for k in LINE if k not in ("cf_cash_end",)]

    def fy_of(end: str) -> tuple[str, int] | None:
        """(fiscal year label, quarter number) for a quarter end date."""
        from datetime import timedelta
        ends = list(fy_ends)
        nxt = date.fromisoformat(ends[-1])
        while nxt <= date.fromisoformat(end) + timedelta(days=200):
            nxt = nxt + timedelta(days=364)
            ends.append(nxt.isoformat())
        for i, e in enumerate(ends):
            if end <= e or _days(e, end) <= 7:
                start = ends[i - 1] if i else None
                if start is None:
                    return None
                q = max(1, min(4, round(_days(start, end) / 91.3)))
                return fy_label(e), q
        return None
    q_ends: set[str] = set()
    for key in ("revenue", "net_income"):
        for t in LINE[key]["tags"]:
            q_ends |= set(_facts_by_end(facts, t, "USD", "duration", 80, 100))
    q_ends |= set(fy_ends)
    placed = {}
    for e in sorted(q_ends):
        lab = fy_of(e)
        if lab:
            placed[f"Q{lab[1]} {lab[0]}"] = e
    labels = sorted(placed, key=lambda k: placed[k])[-quarters:]
    ends = {k: placed[k] for k in labels}
    cells: dict[str, dict[str, Cell]] = {}
    gaps = []
    for key in keys:
        line = LINE[key]
        cells[key] = {}
        scale = 1.0 if line["unit"] == "USD/shares" else SCALE
        for lab in labels:
            e = ends[lab]
            got = None
            for tag in line["tags"]:
                if line["kind"] == "instant":
                    f = _facts_by_end(facts, tag, line["unit"], "instant", 0, 0).get(e)
                    if f:
                        got = Cell(line["sign"] * f["val"] / scale, tag, f.get("accn"), f.get("form"), f.get("filed"), e,
                                   _filing_url(cik, f.get("accn", "")))
                elif line["statement"] == "CF":                    # YTD in 10-Qs: difference of YTD values
                    ytd = _facts_by_end(facts, tag, line["unit"], "duration", 80, 380)
                    nodes = {k: v for k, v in ytd.items()}
                    f = nodes.get(e)
                    if f:
                        span_d = _days(f["start"], f["end"])
                        if span_d <= 100:
                            v = f["val"]
                            how = None
                        else:                                       # find the YTD ending one quarter earlier
                            prev = [x for x in nodes.values() if x["start"] == f["start"] and x["end"] < e
                                    and 60 <= _days(x["end"], e) <= 100]
                            if not prev:
                                continue
                            v = f["val"] - prev[0]["val"]
                            how = f"year-to-date to {e} less year-to-date to {prev[0]['end']}"
                        got = Cell(line["sign"] * v / scale, tag, f.get("accn"), f.get("form"), f.get("filed"), e,
                                   _filing_url(cik, f.get("accn", "")), derived=how and f"{how} (us-gaap:{tag}, "
                                   f"{f.get('form')} {f.get('accn')})")
                else:
                    q = _facts_by_end(facts, tag, line["unit"], "duration", 80, 100).get(e)
                    if q:
                        got = Cell(line["sign"] * q["val"] / scale, tag, q.get("accn"), q.get("form"), q.get("filed"),
                                   e, _filing_url(cik, q.get("accn", "")))
                    elif lab.startswith("Q4"):                      # full year less the first three quarters
                        fy = _facts_by_end(facts, tag, line["unit"], "duration", 340, 390).get(e)
                        prior = [x for x in labels if x.split()[1] == lab.split()[1] and x != lab]
                        qs = [_facts_by_end(facts, tag, line["unit"], "duration", 80, 100).get(ends[x]) for x in prior]
                        if fy and line["unit"] == "shares":        # a share count does not add up over quarters
                            got = Cell(line["sign"] * fy["val"] / scale, tag, fy.get("accn"), fy.get("form"),
                                       fy.get("filed"), e, _filing_url(cik, fy.get("accn", "")),
                                       derived=f"fiscal-year weighted average used for Q4 (us-gaap:{tag}, "
                                               f"10-K {fy.get('accn')})")
                        elif fy and len(qs) == 3 and all(qs):
                            v = fy["val"] - sum(x["val"] for x in qs)
                            approx = "; approximate: per-share amounts do not add exactly" \
                                if line["unit"] == "USD/shares" else ""
                            got = Cell(line["sign"] * v / scale, tag, fy.get("accn"), fy.get("form"), fy.get("filed"),
                                       e, _filing_url(cik, fy.get("accn", "")),
                                       derived=f"fiscal year less Q1-Q3 (us-gaap:{tag}, 10-K {fy.get('accn')}{approx})")
                if got:
                    break
            if got is None:
                cells[key][lab] = Cell(None, end=e)
                gaps.append({"line": key, "label": line["label"], "year": lab, "tags_tried": line["tags"],
                             "kind": "not reported" if key in OPTIONAL_ZERO else "missing"})
            else:
                cells[key][lab] = got
    return Quarters(facts.get("entityName", ""), cik, labels, ends, cells, gaps)


def ltm(q: Quarters, key: str) -> tuple[float | None, str]:
    """Last twelve months: the sum of the last four quarters for a flow, the latest value for a balance. Returns the
    value and how it was built; None when a quarter is missing (never guessed)."""
    line = LINE[key]
    flow = line["kind"] == "duration" and line["unit"] != "shares"      # a share count is not summed: latest quarter
    labs = q.labels[-4:] if flow else q.labels[-1:]
    vals = [q.value(key, lab) for lab in labs]
    if len(labs) < (4 if flow else 1) or any(v is None for v in vals):
        return None, f"LTM not available: {key} missing in {[lb for lb, v in zip(labs, vals) if v is None]}"
    how = (f"sum of {', '.join(labs)}" if flow else
           f"latest quarter, {labs[0]}" if line["kind"] == "duration" else f"as at {q.ends[labs[0]]}")
    return sum(vals) if flow else vals[0], how


def calendarize(by_fy_end: dict[str, float], year: int) -> tuple[float | None, str]:
    """Calendar-year value from fiscal-year values: CY = (m/12) x FY ending in the year + ((12 - m)/12) x FY ending
    the next year, m = the fiscal year-end month. by_fy_end: {"2026-06-30": value, ...}."""
    def eff(e: str) -> tuple[int, int]:
        """(year, month) the fiscal year effectively ends in: a 52/53-week year ending 2 Oct is September, and one
        ending 2 January is December of the year before."""
        y, m = int(e[:4]), int(e[5:7]) - (int(e[8:10]) < 15)
        return (y - 1, 12) if m == 0 else (y, m)
    ends = sorted(by_fy_end)
    this = next((e for e in ends if eff(e)[0] == year), None)
    if this is None:
        return None, f"no fiscal year ends in {year}"
    m = eff(this)[1]
    if m == 12:
        return by_fy_end[this], "fiscal year is the calendar year"
    nxt = next((e for e in ends if e > this), None)
    if nxt is None:
        return None, f"needs the fiscal year after {this}"
    return (m / 12) * by_fy_end[this] + ((12 - m) / 12) * by_fy_end[nxt], \
        f"{m}/12 x FY ending {this} + {12 - m}/12 x FY ending {nxt}"
