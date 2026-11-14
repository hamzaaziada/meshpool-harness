"""Credit workbook: Cover, Inputs (loan terms and credit policy, sourced), Spread (sourced statements), Cash flow
(per-year measures as live formulas), Capacity (pro forma, debt capacity, covenants with EBITDA cushion, stress),
Checks. The formulas reproduce analysis.analyse exactly; tests recalculate it in LibreOffice and compare."""
from __future__ import annotations

from openpyxl.utils import get_column_letter

from engines.sheets import builder as B

from .analysis import KEYS, Financials, Loan, Policy

SPREAD_LABELS = {
    "revenue": "Revenue", "cogs": "Cost of revenue (negative)", "sga": "SG&A (negative)",
    "operating_income": "Operating income", "interest_expense": "Interest expense (negative)",
    "pretax_income": "Pre-tax income", "income_tax": "Income tax (negative)", "net_income": "Net income",
    "da": "Depreciation and amortisation", "sbc": "Share-based compensation", "rent": "Rent / lease expense (negative)",
    "capex": "Capital expenditure (negative)", "dividends": "Dividends / distributions (negative)",
    "buybacks": "Share repurchases (negative)", "cfo": "Cash from operating activities", "cash": "Cash",
    "receivables": "Accounts receivable", "inventory": "Inventory", "current_assets": "Total current assets",
    "ppe": "PP&E, net", "goodwill": "Goodwill", "intangibles": "Intangible assets", "total_assets": "Total assets",
    "payables": "Accounts payable", "short_debt": "Short-term borrowings / revolver",
    "current_debt": "Current portion of long-term debt", "current_liabilities": "Total current liabilities",
    "long_debt": "Long-term debt", "total_liabilities": "Total liabilities", "total_equity": "Total equity"}


def build(fin: Financials, loan: Loan, pol: Policy, path: str, date: str = "") -> dict:
    u = fin.unit
    wb = B.banker_workbook(f"Credit analysis: {fin.name}", f"Cash flow, debt service coverage, leverage, capacity and "
                           f"covenants for the proposed loan of {loan.amount:,.1f} ({u})", "Meshpool credit engine", date,
                           middle=("Inputs", "Spread", "Cash flow", "Capacity"))
    I = wb["Inputs"]
    B.title(I, "Loan terms and credit policy")
    book = B.assumption_book(I, 3)
    book.section("Proposed loan")
    src_t, src_p = "Loan request / term sheet", "Bank credit policy (defaults; replace with the bank's own)"
    book.add(f"Loan amount ({u})", loan.amount, "num", "loan_amount", src_t, loan.purpose or "Requested amount")
    book.add("Interest rate (% per year)", loan.rate, "pct", "loan_rate", src_t, "Requested pricing")
    book.add("Amortisation (years, level payments)", loan.amort_years, "num", "loan_amort", src_t, "Requested terms")
    book.add("Refinance existing funded debt (1 = yes, 0 = no)", 1 if loan.refinance_existing else 0, "int",
             "refinance", src_t, "Use of proceeds")
    book.section("Credit policy")
    for lab, v, f, n, why in (
            ("Minimum DSCR (x)", pol.min_dscr, "x", "min_dscr", "Common bank minimum for term loans"),
            ("Minimum FCCR (x)", pol.min_fccr, "x", "min_fccr", "Fixed-charge coverage floor"),
            ("Maximum funded debt / EBITDA (x)", pol.max_leverage, "x", "max_leverage", "Leverage ceiling"),
            ("Maximum total liabilities / TNW (x)", pol.max_debt_tnw, "x", "max_debt_tnw", "Balance sheet leverage"),
            ("Minimum current ratio (x)", pol.min_current_ratio, "x", "min_current_ratio", "Liquidity floor"),
            ("Capex funded by new debt (%)", pol.financed_capex_share, "pct", "financed_capex",
             "Unfinanced capex is paid from cash flow"),
            ("Count buybacks as distributions (1 = yes)", 1 if pol.include_buybacks else 0, "int", "count_buybacks",
             "Cash returned to owners is not available for debt service"),
            ("Rate stress (percentage points)", pol.rate_stress_bp / 10000, "pct", "rate_stress", "Floating-rate shock")):
        book.add(lab, v, f, n, src_p, why)
    for i, s in enumerate(pol.ebitda_stress, start=1):
        book.add(f"EBITDA stress case {i} (% decline)", s, "pct", f"ebitda_stress_{i}", src_p, "Downside case")

    # ---- spread (blue inputs with sources)
    S = wb["Spread"]
    B.title(S, f"{fin.name}: spread ({u})")
    cols = [get_column_letter(2 + i) for i in range(len(fin.years))]
    src_col = get_column_letter(2 + len(fin.years))
    B.header(S, 4, ["Line item"] + [f"{y}A" for y in fin.years] + ["Source"])
    B.freeze(S, "B5")
    row = {}
    r = 5
    for k in KEYS:
        row[k] = r
        S.cell(row=r, column=1, value=f"{SPREAD_LABELS[k]} ({u})")
        srcs = set()
        for c, y in zip(cols, fin.years):
            v = fin.v(y, k)
            if v is not None:
                B.input_cell(S, f"{c}{r}", v, "num")
                srcs.add(fin.sources.get(y, {}).get(k, "Provided"))
        S[f"{src_col}{r}"] = "; ".join(sorted(srcs))[:250] if srcs else "Not reported (treated as zero)"
        r += 1
    S.column_dimensions["A"].width = 44
    S.column_dimensions[src_col].width = 70
    ref = lambda k, c: f"Spread!{c}{row[k]}"  # noqa: E731

    # ---- cash flow measures per year
    C = wb["Cash flow"]
    B.title(C, f"Cash flow and debt service coverage ({u})")
    B.header(C, 4, ["Measure"] + [f"{y}A" for y in fin.years])
    B.freeze(C, "B5")
    m: dict[str, int] = {}
    r = 5

    def line(key, label, fn, fmt="num", total=False):
        nonlocal r
        m[key] = r
        C.cell(row=r, column=1, value=label)
        for i, c in enumerate(cols):
            B._f(C, f"{c}{r}", fn(c, cols[i - 1] if i else None), fmt, total=total)
        r += 1
    A_ = lambda k, c: f"ABS(N({ref(k, c)}))"  # noqa: E731
    line("ebitda", f"EBITDA ({u})", lambda c, p: f"=IF(ISNUMBER({ref('operating_income', c)}),{ref('operating_income', c)},"
         f"N({ref('pretax_income', c)})+{A_('interest_expense', c)})+{A_('da', c)}", total=True)
    line("margin", "EBITDA margin (%)", lambda c, p: f'=IF(N({ref("revenue", c)})=0,"NM",{c}{m["ebitda"]}/{ref("revenue", c)})',
         "pct")
    line("tax", f"Less: cash taxes, as income tax expense; a benefit counts as zero ({u})",
         lambda c, p: f"=MAX(0,-N({ref('income_tax', c)}))")
    line("capex", f"Less: unfinanced capex ({u})", lambda c, p: f"={A_('capex', c)}*(1-financed_capex)")
    line("dist", f"Less: distributions ({u})", lambda c, p: f"={A_('dividends', c)}+{A_('buybacks', c)}*count_buybacks")
    line("cads", f"Cash available for debt service ({u})",
         lambda c, p: f"={c}{m['ebitda']}-{c}{m['tax']}-{c}{m['capex']}-{c}{m['dist']}", total=True)
    line("interest", f"Interest paid, as reported interest ({u})", lambda c, p: f"={A_('interest_expense', c)}")
    line("principal", f"Scheduled principal: prior year-end current portion ({u})",
         lambda c, p: f"=N({ref('current_debt', c)})" if p is None else
         f"=IF(ISNUMBER({ref('current_debt', p)}),{ref('current_debt', p)},N({ref('current_debt', c)}))")
    line("ds", f"Debt service ({u})", lambda c, p: f"={c}{m['interest']}+{c}{m['principal']}", total=True)
    line("dscr", "DSCR (x)", lambda c, p: f'=IF({c}{m["ds"]}=0,"NM",{c}{m["cads"]}/{c}{m["ds"]})', "x", total=True)
    line("rent", f"Rent and lease payments ({u})", lambda c, p: f"={A_('rent', c)}")
    line("fccr", "FCCR (x)", lambda c, p: f'=IF({c}{m["ds"]}+{c}{m["rent"]}=0,"NM",({c}{m["cads"]}+{c}{m["rent"]})/'
         f'({c}{m["ds"]}+{c}{m["rent"]}))', "x")
    line("debt", f"Funded debt ({u})", lambda c, p: f"=N({ref('short_debt', c)})+N({ref('current_debt', c)})+"
         f"N({ref('long_debt', c)})")
    line("lev", "Funded debt / EBITDA (x)", lambda c, p: f'=IF({c}{m["ebitda"]}<=0,"NM",{c}{m["debt"]}/{c}{m["ebitda"]})', "x")
    line("tnw", f"Tangible net worth ({u})", lambda c, p: f"=N({ref('total_equity', c)})-N({ref('goodwill', c)})-"
         f"N({ref('intangibles', c)})")
    line("dtnw", "Total liabilities / TNW (x)", lambda c, p: f'=IF({c}{m["tnw"]}<=0,"NM",N({ref("total_liabilities", c)})/'
         f'{c}{m["tnw"]})', "x")
    line("cr", "Current ratio (x)", lambda c, p: f'=IF(N({ref("current_liabilities", c)})=0,"NM",'
         f'N({ref("current_assets", c)})/{ref("current_liabilities", c)})', "x")
    line("icov", "EBITDA / interest (x)", lambda c, p: f'=IF({c}{m["interest"]}=0,"NM",{c}{m["ebitda"]}/{c}{m["interest"]})', "x")
    line("cfo_ni", "Operating cash flow / net income (x)", lambda c, p: f'=IF(OR(N({ref("net_income", c)})=0,'
         f'NOT(ISNUMBER({ref("cfo", c)}))),"NM",{ref("cfo", c)}/{ref("net_income", c)})', "x")
    C.column_dimensions["A"].width = 52
    for c in cols:
        C.column_dimensions[c].width = 14

    # ---- capacity, covenants, stress (latest year)
    K = wb["Capacity"]
    B.title(K, f"Pro forma, debt capacity, covenants and stress ({fin.years[-1]}, {u})")
    L = cols[-1]
    cf = lambda k: f"'Cash flow'!{L}{m[k]}"  # noqa: E731
    n: dict[str, str] = {}
    r = 4

    def one(key, label, expr, fmt="num", total=False):
        nonlocal r
        K.cell(row=r, column=1, value=label)
        B._f(K, f"B{r}", expr, fmt, total=total)
        n[key] = f"B{r}"
        B.define_name(wb, f"cr_{key}", f"'Capacity'!$B${r}")
        r += 1
    B.section(K, r, "Pro forma debt service", 3)
    r += 1
    one("exist_debt0", f"Existing funded debt ({u})", f"={cf('debt')}")
    one("refi_share", "Share of existing debt refinanced (%)",
        f"=IF(OR(refinance=0,{n['exist_debt0']}=0),0,MIN(loan_amount,{n['exist_debt0']})/{n['exist_debt0']})", "pct")
    one("exist_ds", f"Existing debt service after refinancing ({u})",
        f"=({cf('interest')}+N(Spread!{L}{row['current_debt']}))*(1-{n['refi_share']})")
    one("exist_debt", f"Existing funded debt after refinancing ({u})",
        f"={n['exist_debt0']}-MIN(loan_amount,{n['exist_debt0']})*refinance")
    one("new_pmt", f"New loan annual payment ({u})", "=IF(loan_amount=0,0,PMT(loan_rate,loan_amort,-loan_amount))")
    one("pf_ds", f"Pro forma debt service ({u})", f"={n['exist_ds']}+{n['new_pmt']}", total=True)
    one("pf_dscr", "Pro forma DSCR (x)", f'=IF({n["pf_ds"]}=0,"NM",{cf("cads")}/{n["pf_ds"]})', "x", total=True)
    one("pf_fccr", "Pro forma FCCR (x)", f'=IF({n["pf_ds"]}+{cf("rent")}=0,"NM",({cf("cads")}+{cf("rent")})/'
        f'({n["pf_ds"]}+{cf("rent")}))', "x")
    one("pf_debt", f"Pro forma funded debt ({u})", f"={n['exist_debt']}+loan_amount")
    one("pf_lev", "Pro forma funded debt / EBITDA (x)", f'=IF({cf("ebitda")}<=0,"NM",{n["pf_debt"]}/{cf("ebitda")})', "x")
    r += 1
    B.section(K, r, "Debt capacity", 3)
    r += 1
    one("cap_dscr", f"Loan supported at minimum DSCR ({u})",
        f"=MAX(0,IF({cf('cads')}/min_dscr-{n['exist_ds']}<=0,0,PV(loan_rate,loan_amort,-({cf('cads')}/min_dscr-"
        f"{n['exist_ds']}))))")
    one("cap_lev", f"Loan supported at maximum leverage ({u})",
        f"=IF({cf('ebitda')}<=0,0,MAX(0,max_leverage*{cf('ebitda')}-{n['exist_debt']}))")
    one("capacity", f"Debt capacity: the lower of the two ({u})", f"=MIN({n['cap_dscr']},{n['cap_lev']})", total=True)
    one("headroom", f"Capacity less requested loan ({u})", f"={n['capacity']}-loan_amount")
    r += 1
    B.section(K, r, "Covenant tests (pro forma) and EBITDA cushion", 4)
    r += 1
    B.header(K, r, ["Covenant", "Value", "Limit", "Pass (1 = yes)", "EBITDA can fall by (%)"])
    r += 1
    cov_first = r
    covs = [("Minimum DSCR", n["pf_dscr"], "min_dscr", "min",
             f"=IF({cf('ebitda')}<=0,\"NM\",({cf('cads')}-min_dscr*{n['pf_ds']})/{cf('ebitda')})"),
            ("Minimum FCCR", n["pf_fccr"], "min_fccr", "min",
             f"=IF({cf('ebitda')}<=0,\"NM\",({cf('cads')}+{cf('rent')}-min_fccr*({n['pf_ds']}+{cf('rent')}))/{cf('ebitda')})"),
            ("Maximum funded debt / EBITDA", n["pf_lev"], "max_leverage", "max",
             f"=IF({cf('ebitda')}<=0,\"NM\",1-{n['pf_debt']}/max_leverage/{cf('ebitda')})"),
            ("Maximum total liabilities / TNW", cf("dtnw"), "max_debt_tnw", "max", None),
            ("Minimum current ratio", cf("cr"), "min_current_ratio", "min", None)]
    for lab, val, lim, kind, cush in covs:
        K.cell(row=r, column=1, value=lab)
        B._f(K, f"B{r}", f"={val}", "x")
        B._f(K, f"C{r}", f"={lim}", "x")
        cmp = ">=" if kind == "min" else "<="
        B._f(K, f"D{r}", f'=IF(ISNUMBER(B{r}),IF(B{r}{cmp}C{r},1,0),0)', "int")
        if cush:
            B._f(K, f"E{r}", cush, "pct")
        r += 1
    B.define_name(wb, "cr_covenants_passed", f"'Capacity'!$D${cov_first}:$D${r - 1}")
    r += 1
    B.section(K, r, "Stress tests (pro forma)", 4)
    r += 1
    B.header(K, r, ["Case", f"EBITDA ({u})", "DSCR (x)", "Funded debt / EBITDA (x)"])
    r += 1
    for i in range(1, len(pol.ebitda_stress) + 1):
        K.cell(row=r, column=1, value=f"EBITDA stress case {i}")
        B._f(K, f"B{r}", f"={cf('ebitda')}-ABS({cf('ebitda')})*ebitda_stress_{i}", "num")   # a loss gets worse
        B._f(K, f"C{r}", f'=IF({n["pf_ds"]}=0,"NM",({cf("cads")}-ABS({cf("ebitda")})*ebitda_stress_{i})/{n["pf_ds"]})',
             "x")
        B._f(K, f"D{r}", f'=IF(B{r}<=0,"NM",{n["pf_debt"]}/B{r})', "x")
        r += 1
    K.cell(row=r, column=1, value="Rate stress on the new loan")
    B._f(K, f"B{r}", f"={cf('ebitda')}", "num")
    B._f(K, f"C{r}", f'=IF({n["exist_ds"]}+PMT(loan_rate+rate_stress,loan_amort,-loan_amount)=0,"NM",'
         f'{cf("cads")}/({n["exist_ds"]}+IF(loan_amount=0,0,PMT(loan_rate+rate_stress,loan_amort,-loan_amount))))', "x")
    B._f(K, f"D{r}", f"={n['pf_lev']}", "x")
    K.column_dimensions["A"].width = 52
    for c in "BCDE":
        K.column_dimensions[c].width = 16

    # ---- checks: the spread balances where the totals are reported
    for c, y in zip(cols, fin.years):
        if all(fin.v(y, k) is not None for k in ("total_assets", "total_liabilities", "total_equity")):
            B.add_check(wb, f"Spread {y}: total assets = total liabilities + total equity",
                        f"=Spread!{c}{row['total_assets']}-Spread!{c}{row['total_liabilities']}-"
                        f"Spread!{c}{row['total_equity']}", tol=0.5)
    B.add_check(wb, "Capacity is the lower of the DSCR and leverage limits",
                f"='Capacity'!{n['capacity']}-MIN('Capacity'!{n['cap_dscr']},'Capacity'!{n['cap_lev']})")
    wb.save(path)
    return {"path": path, "spread_rows": row, "cash_flow_rows": m, "capacity_cells": n, "cols": cols}
