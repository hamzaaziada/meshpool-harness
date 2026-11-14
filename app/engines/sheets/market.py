"""Live market inputs for the cost of capital, each with its source, date and a range:

    from engines.sheets import market as MK
    MK.treasury_yield()                 # U.S. Treasury par yield curve, 10-year, latest business day
    MK.damodaran_erp()                  # Damodaran's implied equity risk premium (and its variants as the range)
    MK.damodaran_beta("Software (System & Application)")   # industry betas, levered / unlevered / cash-corrected
    MK.add_wacc_inputs(book, industry="Semiconductor")     # risk_free, equity_risk_premium, beta_unlevered rows

Damodaran's data is free to use with attribution (pages.stern.nyu.edu/~adamodar); ask before redistributing it
commercially. Treasury data is public. Nothing here is guessed: a source that cannot be read raises.
"""
from __future__ import annotations

import html
import io
import re
import time
import urllib.request

TREASURY = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
            "?data=daily_treasury_yield_curve&field_tdr_date_value={year}")
DAMODARAN_HOME = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/home.htm"
DAMODARAN_BETAS = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/Betas.html"
TENORS = {"1M": "BC_1MONTH", "3M": "BC_3MONTH", "1Y": "BC_1YEAR", "2Y": "BC_2YEAR", "5Y": "BC_5YEAR",
          "10Y": "BC_10YEAR", "20Y": "BC_20YEAR", "30Y": "BC_30YEAR"}


def _get(url: str, timeout: float = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Meshpool research hello@meshpool.ai"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def treasury_yield(tenor: str = "10Y", year: int | None = None, text: str | None = None) -> dict:
    """Latest par yield for the tenor. Returns {value (decimal), date, source, url}. With the current year and no
    text given, an empty feed (the first days of January) falls back to last year's feed."""
    this_year = year is None
    year = year or int(time.strftime("%Y"))
    url = TREASURY.format(year=year)
    fetched = text is None
    text = text if text is not None else _get(url)
    tag = TENORS[tenor]
    rows = re.findall(r"<d:NEW_DATE[^>]*>(\d{4}-\d{2}-\d{2})T[^<]*<.*?<d:" + tag + r"[^>]*>([\d.]+)<", text, re.S)
    if not rows:
        if fetched and this_year:
            return treasury_yield(tenor, year - 1)
        raise ValueError(f"no {tenor} yields in the Treasury feed for {year}")
    d, v = max(rows)
    return {"value": float(v) / 100, "date": d, "url": url,
            "source": f"U.S. Treasury, Daily Treasury Par Yield Curve Rates, {tenor} on {d} (home.treasury.gov)"}


def damodaran_erp(text: str | None = None) -> dict:
    """Implied ERP from Damodaran's home page: the headline (trailing 12 months, adjusted payout) and the
    variants he publishes beside it, which make the low / high range."""
    raw = text if text is not None else _get(DAMODARAN_HOME)
    t = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)))
    m = re.search(r"Implied ERP on ([A-Z][a-z]+ \d{1,2}, \d{4}) = ([\d.]+) ?%(.{0,300})", t)
    if not m:
        raise ValueError("the implied ERP was not found on Damodaran's home page")
    tail = m.group(3).split("Implied ERP")[0]               # only the variants published beside the headline
    others = [float(x) / 100 for x in re.findall(r"([\d.]+) ?% \(", tail)]
    v = float(m.group(2)) / 100
    vals = [v] + others
    return {"value": v, "date": m.group(1), "low": min(vals), "high": max(vals), "url": DAMODARAN_HOME,
            "source": f"A. Damodaran, implied equity risk premium on {m.group(1)} (trailing 12 months, adjusted "
                      "payout), pages.stern.nyu.edu/~adamodar"}


def _beta_table(text: str | None = None):
    import pandas as pd
    raw = text if text is not None else _get(DAMODARAN_BETAS)
    df = pd.read_html(io.StringIO(raw))[0]
    df.columns = [str(c) for c in df.iloc[0]]
    return df.iloc[1:].reset_index(drop=True)


def industries(text: str | None = None) -> list[str]:
    return [str(x) for x in _beta_table(text)["Industry Name"]]


def damodaran_beta(industry: str, text: str | None = None) -> dict:
    """Industry betas (US): levered, unlevered, unlevered corrected for cash, D/E and tax rate, with the source.
    The industry is matched on its name (case-insensitive, then the closest name); the match is returned."""
    import difflib
    df = _beta_table(text)
    names = [str(x) for x in df["Industry Name"]]
    hit = next((n for n in names if n.lower() == industry.lower()), None)
    if hit is None:
        hit = next((n for n in names if industry.lower() in n.lower()), None)
    if hit is None:
        close = difflib.get_close_matches(industry, names, n=1, cutoff=0.4)
        if not close:
            raise KeyError(f"no Damodaran industry like '{industry}'; see industries()")
        hit = close[0]
    row = df[df["Industry Name"] == hit].iloc[0]
    pct = lambda x: float(str(x).rstrip("%")) / 100  # noqa: E731
    return {"industry": hit, "firms": int(row["Number of firms"]), "beta": float(row["Beta"]),
            "unlevered": float(row["Unlevered beta"]),
            "unlevered_cash": float(row["Unlevered beta corrected for cash"]), "de": pct(row["D/E Ratio"]),
            "tax": pct(row["Effective Tax rate"]), "url": DAMODARAN_BETAS,
            "source": f"A. Damodaran, Betas by Sector (US), {hit} ({int(row['Number of firms'])} firms), "
                      "pages.stern.nyu.edu/~adamodar"}


def add_wacc_inputs(book, industry: str | None = None, rf: dict | None = None, erp: dict | None = None,
                    beta: dict | None = None) -> dict:
    """Write risk_free, equity_risk_premium and (with an industry) beta_unlevered into an assumption book, each
    sourced and dated, with ranges: risk-free +/- 50bp, the ERP variants Damodaran publishes, the industry's
    cash-corrected unlevered beta +/- 0.15. Returns what was written."""
    rf = rf or treasury_yield()
    erp = erp or damodaran_erp()
    book.add("Risk-free rate: US Treasury 10-year (%)", round(rf["value"], 4), "pct", name="risk_free",
             source=rf["source"], rationale="Duration-matched government yield for US-dollar cash flows",
             low=round(rf["value"] - 0.005, 4), high=round(rf["value"] + 0.005, 4))
    book.add("Equity risk premium (%)", round(erp["value"], 4), "pct", name="equity_risk_premium",
             source=erp["source"], rationale="Forward-looking implied premium; the range is the variants published "
             "beside it", low=round(erp["low"], 4), high=round(erp["high"], 4))
    out = {"risk_free": rf, "equity_risk_premium": erp}
    if industry:
        beta = beta or damodaran_beta(industry)
        b = beta["unlevered_cash"]
        book.add("Unlevered beta, industry, corrected for cash (x)", round(b, 2), "0.00;(0.00)", name="beta_unlevered",
                 source=beta["source"], rationale=f"Industry median relevered at the target structure in the DCF "
                 f"(industry D/E {beta['de']:.0%})", low=round(b - 0.15, 2), high=round(b + 0.15, 2))
        out["beta_unlevered"] = beta
    return out
