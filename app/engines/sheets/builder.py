"""Small, tested helpers so model-written workbook code stays consistent.

Conventions (financial-model standard): inputs in blue font on a light yellow fill, formulas in black,
links to other sheets in green; Inputs / Calc / Outputs sheets; named ranges for key assumptions.
"""
from __future__ import annotations

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName

FONT = "Calibri"
INPUT_FONT = Font(name=FONT, color="0000FF")
INPUT_FILL = PatternFill("solid", fgColor="FFF7D6")
CALC_FONT = Font(name=FONT, color="000000")
LINK_FONT = Font(name=FONT, color="007A33")
HEADER_FONT = Font(name=FONT, bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="1B2333")
TITLE_FONT = Font(name=FONT, bold=True, size=14)
TOTAL_BORDER = Border(top=Side(style="thin"))
FMT = {"usd": '$#,##0;($#,##0);"-"', "usd2": '$#,##0.00;($#,##0.00)', "pct": '0.0%', "int": '#,##0;(#,##0)',
       "x": '0.0"x";(0.0"x")', "num": '#,##0.0;(#,##0.0)'}


def new_workbook(sheets=("Inputs", "Calc", "Outputs")) -> Workbook:
    wb = Workbook()
    wb.active.title = sheets[0]
    for s in sheets[1:]:
        wb.create_sheet(s)
    for ws in wb.worksheets:
        ws.sheet_view.showGridLines = False
    return wb


def title(ws, text, row=1):
    ws.cell(row=row, column=1, value=text).font = TITLE_FONT


def header(ws, row, labels, col=1):
    for i, lab in enumerate(labels):
        c = ws.cell(row=row, column=col + i, value=lab)
        c.font, c.fill = HEADER_FONT, HEADER_FILL
        c.alignment = Alignment(horizontal="center" if i else "left")


def input_cell(ws, ref, value, fmt=None, name=None):
    """Write a typed input, style it as an input, and optionally create a workbook-level named range."""
    c = ws[ref]
    c.value = value
    c.font, c.fill = INPUT_FONT, INPUT_FILL
    if fmt:
        c.number_format = FMT.get(fmt, fmt)
    if name:
        define_name(ws.parent, name, f"'{ws.title}'!${c.column_letter}${c.row}")
    return c


def define_name(wb, name: str, attr_text: str):
    """Workbook-level named range (replaces an existing one with the same name)."""
    dn = DefinedName(name, attr_text=attr_text)
    try:
        wb.defined_names[name] = dn
    except TypeError:
        wb.defined_names.append(dn)


def formula(ws, ref, expr, fmt=None, link=False, total=False):
    c = ws[ref]
    c.value = expr if expr.startswith("=") else "=" + expr
    c.font = LINK_FONT if link else CALC_FONT
    if fmt:
        c.number_format = FMT.get(fmt, fmt)
    if total:
        c.border = TOTAL_BORDER
        c.font = Font(name=FONT, bold=True, color=c.font.color.rgb if c.font.color else "000000")
    return c


def widths(ws, mapping=None, default=14, first=34):
    ws.column_dimensions["A"].width = first
    for i in range(2, ws.max_column + 1):
        ws.column_dimensions[get_column_letter(i)].width = default
    for k, v in (mapping or {}).items():
        ws.column_dimensions[k].width = v


def chart(ws, kind, data_ref, cats_ref, anchor, title_text="", y_fmt=None, height=7.5, width=15):
    """data_ref/cats_ref: (min_col, min_row, max_col, max_row) on ws; data includes the header row."""
    ch = {"bar": BarChart, "line": LineChart, "pie": PieChart}[kind]()
    if kind == "bar":
        ch.type = "col"
        ch.gapWidth = 60
    ch.title = title_text or None
    ch.height, ch.width = height, width
    data = Reference(ws, min_col=data_ref[0], min_row=data_ref[1], max_col=data_ref[2], max_row=data_ref[3])
    cats = Reference(ws, min_col=cats_ref[0], min_row=cats_ref[1], max_col=cats_ref[2], max_row=cats_ref[3])
    ch.add_data(data, titles_from_data=True)
    ch.set_categories(cats)
    if y_fmt and kind != "pie":
        ch.y_axis.numFmt = FMT.get(y_fmt, y_fmt)
    ws.add_chart(ch, anchor)
    return ch


# --------------------------------------------------------------------------- banker-style workbook structure
TAB_ORDER = ["Cover", "Inputs", "Calc", "Outputs", "Checks"]
TAB_COLORS = {"Cover": "1B2333", "Inputs": "F2C200", "Calc": "8A93A3", "Outputs": "2557D6", "Checks": "157347"}
SECTION_FONT = Font(name=FONT, bold=True, color="1B2333", size=11)
SECTION_FILL = PatternFill("solid", fgColor="E7ECF5")


def banker_workbook(title_text: str, purpose: str, author: str = "", date: str = "",
                    middle=("Inputs", "Calc", "Outputs")) -> Workbook:
    """Cover -> Inputs -> Calc -> Outputs -> Checks, colored tabs, gridlines off, frozen headers, cover with tab map."""
    sheets = ["Cover", *middle, "Checks"]
    wb = new_workbook(tuple(sheets))
    for ws in wb.worksheets:
        ws.sheet_properties.tabColor = TAB_COLORS.get(ws.title, "8A93A3")
        ws.freeze_panes = "B4" if ws.title not in ("Cover", "Checks") else None
    c = wb["Cover"]
    c["B2"] = title_text
    c["B2"].font = Font(name=FONT, bold=True, size=18, color="1B2333")
    c["B3"] = purpose
    c["B3"].font = Font(name=FONT, size=11, color="5B6577")
    c["B5"], c["C5"] = "Prepared by", author
    c["B6"], c["C6"] = "Date", date
    c["B8"] = "Tab"
    c["C8"] = "What it contains"
    for cell in (c["B8"], c["C8"]):
        cell.font, cell.fill = HEADER_FONT, HEADER_FILL
    desc = {"Inputs": "All assumptions and raw data. Blue cells are the only cells to change.",
            "Calc": "Calculations. Formulas only; no typed numbers.",
            "Outputs": "Summary tables and charts for review.",
            "Checks": "Error checks. All must show OK before the file is shared."}
    for i, s in enumerate(sheets[1:], start=9):
        c.cell(row=i, column=2, value=s).hyperlink = f"#'{s}'!A1"
        c.cell(row=i, column=2).font = Font(name=FONT, color="2557D6", underline="single")
        c.cell(row=i, column=3, value=desc.get(s, ""))
    r = 9 + len(sheets)
    c.cell(row=r + 1, column=2, value="Legend").font = Font(name=FONT, bold=True)
    leg = [("Input (change me)", INPUT_FONT, INPUT_FILL), ("Formula", CALC_FONT, None), ("Link to another tab", LINK_FONT, None)]
    for k, (lab, f, fill) in enumerate(leg, start=r + 2):
        cell = c.cell(row=k, column=2, value=lab)
        cell.font = f
        if fill:
            cell.fill = fill
    c.column_dimensions["A"].width = 3
    c.column_dimensions["B"].width = 24
    c.column_dimensions["C"].width = 70
    ch = wb["Checks"]
    title(ch, "Checks")
    header(ch, 3, ["Check", "Difference", "Status"])
    return wb


def section(ws, row, text, ncols=6):
    """Shaded section band, e.g. 'Revenue build'."""
    for col in range(1, ncols + 1):
        ws.cell(row=row, column=col).fill = SECTION_FILL
    ws.cell(row=row, column=1, value=text).font = SECTION_FONT


def add_check(wb, label: str, diff_formula: str, tol: float = 0.01):
    """Append a check row: difference formula must be ~0. Status shows OK / ERROR. Returns the status cell ref."""
    ch = wb["Checks"]
    if "check_tolerance" not in wb.defined_names:          # the tolerance is a labelled input, not a typed literal
        ch["H3"] = "Tolerance"
        ch["H3"].font = Font(name=FONT, bold=True)
        input_cell(ch, "I3", tol, "num", name="check_tolerance")
    r = max(4, max((c.row for c in ch["A"] if c.value is not None), default=3) + 1)
    ch.cell(row=r, column=1, value=label)
    formula(ch, f"B{r}", diff_formula if diff_formula.startswith("=") else "=" + diff_formula, "num", link=True)
    formula(ch, f"C{r}", f'=IF(ABS(B{r})<=check_tolerance,"OK","ERROR")')
    ch["E3"] = "All checks"
    ch["E3"].font = Font(name=FONT, bold=True)
    formula(ch, "F3", f'=IF(COUNTIF(C4:C{r},"ERROR")=0,"OK","ERROR")')
    if "MasterCheck" not in wb.defined_names:
        define_name(wb, "MasterCheck", "'Checks'!$F$3")
    return f"Checks!C{r}"


# --------------------------------------------------------------------------- finance kit (investment-bank standard)
# Every block below writes live formulas only. Typed numbers live in blue input cells: the assumption book on Inputs
# (value, source, evidence/rationale, low, high) and the peer/deal rows of the comps and precedents tables (each row
# with its own Source). Results are exposed as workbook names so other tabs (football field, checks) can link to them.
import re as _re  # noqa: E402

from openpyxl.formula.tokenizer import Token as _Token, Tokenizer as _Tokenizer  # noqa: E402

BOOK_COLS = ["Assumption", "Value", "Source", "Evidence / rationale", "Low", "High"]


def _abs(ws, coord: str) -> str:
    col, row = _re.match(r"([A-Z]+)(\d+)", coord.replace("$", "")).groups()
    return f"'{ws.title}'!${col}${row}"


def _name_sheet(wb, name: str):
    for k, dn in wb.defined_names.items():
        if k.lower() == name.lower():
            dest = str(dn.attr_text)
            return dest.rpartition("!")[0].strip("'") if "!" in dest else None
    return None


def _crosses(ws, expr: str) -> bool:
    """True when the formula reads another sheet (directly or through a defined name): styled as a link (green)."""
    try:
        toks = _Tokenizer(expr).items
    except Exception:
        return "!" in expr
    for t in toks:
        if t.type == _Token.OPERAND and t.subtype == _Token.RANGE:
            v = t.value
            if "!" in v:
                if v.rpartition("!")[0].strip("'") != ws.title:
                    return True
            else:
                sh = _name_sheet(ws.parent, v)
                if sh and sh != ws.title:
                    return True
    return False


def _f(ws, coord, expr, fmt=None, total=False):
    expr = expr if expr.startswith("=") else "=" + expr
    return formula(ws, coord, expr, fmt, link=_crosses(ws, expr), total=total)


def model_constant(wb, name: str, label: str, value, fmt=None) -> str:
    """A definitional constant (e.g. the quartile point 25%), kept visible, blue and named on the Checks tab next to
    the check tolerance, so formulas never carry magic numbers. Returns the name."""
    if name in wb.defined_names:
        return name
    ch = wb["Checks"] if "Checks" in wb.sheetnames else wb.create_sheet("Checks")
    r = 4
    while ch.cell(row=r, column=8).value is not None:
        r += 1
    ch.cell(row=r, column=8, value=label)
    input_cell(ch, f"I{r}", value, fmt, name=name)
    ch.column_dimensions["H"].width = max(ch.column_dimensions["H"].width or 0, 28)
    return name


class AssumptionBook:
    """Inputs tab as an assumption book: Assumption | Value | Source | Evidence / rationale | Low | High.
    book.add(...) writes the blue value cell (the same cell input_cell would), names it, and names the range ends
    <name>_low / <name>_high so sensitivities and the football field can use them."""

    def __init__(self, ws, row: int = 3):
        self.ws, self.header_row = ws, row
        header(ws, row, BOOK_COLS)
        self.row = row + 1
        for col, w in zip("ABCDEF", (38, 14, 34, 52, 12, 12)):
            ws.column_dimensions[col].width = w

    def section(self, text: str):
        section(self.ws, self.row, text, len(BOOK_COLS))
        self.row += 1

    def add(self, label: str, value, fmt=None, name=None, source: str = "", rationale: str = "",
            low=None, high=None) -> str:
        ws, r = self.ws, self.row
        ws.cell(row=r, column=1, value=label)
        input_cell(ws, f"B{r}", value, fmt, name=name)
        ws.cell(row=r, column=3, value=source or None)
        ws.cell(row=r, column=4, value=rationale or None).alignment = Alignment(wrap_text=True, vertical="top")
        for col, v, suffix in (("E", low, "_low"), ("F", high, "_high")):
            if v is not None:
                input_cell(ws, f"{col}{r}", v, fmt, name=f"{name}{suffix}" if name else None)
        self.row += 1
        return name or _abs(ws, f"B{r}")


def assumption_book(ws, row: int = 3) -> AssumptionBook:
    return AssumptionBook(ws, row)


STAT_ROWS = [("mean", "Mean", "AVERAGE({rng})"), ("median", "Median", "MEDIAN({rng})"),
             ("p25", "25th percentile", "PERCENTILE({rng},pctl_low)"),
             ("p75", "75th percentile", "PERCENTILE({rng},pctl_high)")]

COMPS_INPUTS = [("price", "Share price ($)", "usd2"), ("shares", "Diluted shares (m)", "num"),
                ("debt", "Total debt ($m)", "usd"), ("cash", "Cash ($m)", "usd"),
                ("revenue", "Revenue LTM ($m)", "usd"), ("ebitda", "EBITDA LTM ($m)", "usd")]
COMPS_CALCS = [("equity", "Equity value ($m)", "{price}*{shares}", "usd"),
               ("ev", "Enterprise value ($m)", "{equity}+{debt}-{cash}", "usd"),
               ("ev_rev", "EV / Revenue LTM (x)", 'IF({revenue}<=0,"NM",{ev}/{revenue})', "x"),
               ("ev_ebitda", "EV / EBITDA LTM (x)", 'IF({ebitda}<=0,"NM",{ev}/{ebitda})', "x"),
               ("margin", "EBITDA margin (%)", 'IF({revenue}<=0,"NM",{ebitda}/{revenue})', "pct")]
PRECEDENT_TEXT = [("date", "Announced"), ("acquirer", "Acquirer"), ("name", "Target"), ("consideration", "Consideration")]
PRECEDENT_INPUTS = [("ev", "Enterprise value ($m)", "usd"), ("revenue", "Target revenue LTM ($m)", "usd"),
                    ("ebitda", "Target EBITDA LTM ($m)", "usd"), ("offer", "Offer price ($/share)", "usd2"),
                    ("unaffected", "Unaffected price ($/share)", "usd2")]
PRECEDENT_CALCS = [("ev_rev", "EV / Revenue LTM (x)", 'IF({revenue}<=0,"NM",{ev}/{revenue})', "x"),
                   ("ev_ebitda", "EV / EBITDA LTM (x)", 'IF({ebitda}<=0,"NM",{ev}/{ebitda})', "x"),
                   ("premium", "Premium to unaffected (%)", 'IF({unaffected}<=0,"NM",{offer}/{unaffected}-1)', "pct")]


def _peer_table(ws, row, items, text_cols, input_cols, calc_cols, stat_keys, title_text, prefix) -> dict:
    wb = ws.parent
    model_constant(wb, "pctl_low", "Lower quartile point (%)", 0.25, "pct")
    model_constant(wb, "pctl_high", "Upper quartile point (%)", 0.75, "pct")
    keys = [k for k, _ in text_cols] + [k for k, _, _ in input_cols] + [k for k, _, _, _ in calc_cols]
    if len(set(keys)) != len(keys):
        raise ValueError(f"duplicate column keys in {keys}")
    col = {k: get_column_letter(i + 1) for i, k in enumerate(keys)}
    src_col = len(keys) + 1
    fmts = {k: f for k, _, f in input_cols} | {k: f for k, _, _, f in calc_cols}
    for k in stat_keys:
        if k not in col:
            raise ValueError(f"stat column {k} is not a column")
    section(ws, row, title_text, src_col)
    hdr = row + 1
    header(ws, hdr, [lab for _, lab in text_cols] + [lab for _, lab, _ in input_cols]
           + [lab for _, lab, _, _ in calc_cols] + ["Source"])
    r = first = hdr + 1
    for it in items:
        for k, _ in text_cols:
            ws[f"{col[k]}{r}"] = it.get(k, "")
        for k, _, f in input_cols:
            input_cell(ws, f"{col[k]}{r}", it.get(k), f)
        for k, _, expr, f in calc_cols:
            try:
                e = _re.sub(r"\{(\w+)\}", lambda m: f"{col[m.group(1)]}{r}", expr)
            except KeyError as ex:
                raise ValueError(f"calc {k}: unknown column {ex}") from None
            _f(ws, f"{col[k]}{r}", e, f)
        ws.cell(row=r, column=src_col, value=it.get("source") or None)
        r += 1
    last = r - 1
    stats = {}
    for j, (sk, lab, fn) in enumerate(STAT_ROWS):
        ws.cell(row=r, column=1, value=lab).font = Font(name=FONT, bold=True)
        for k in stat_keys:
            c = _f(ws, f"{col[k]}{r}", f'=IFERROR({fn.format(rng=f"{col[k]}{first}:{col[k]}{last}")},"NM")', fmts[k])
            if j == 0:
                c.border = TOTAL_BORDER
            nm = f"{prefix}{k}_{sk}"
            define_name(wb, nm, _abs(ws, f"{col[k]}{r}"))
            stats.setdefault(k, {})[sk] = nm
        r += 1
    if ws.freeze_panes is None:
        ws.freeze_panes = f"B{hdr + 1}"
    for i in range(1, src_col + 1):
        L = get_column_letter(i)
        ws.column_dimensions[L].width = max(ws.column_dimensions[L].width or 0, 28 if i in (1, src_col) else 15)
    return {"header_row": hdr, "first_row": first, "last_row": last, "cols": col, "stats": stats, "next_row": r + 1}


def comps_table(ws, row, peers, inputs=None, calcs=None, stats=None, title_text="Trading comparables",
                prefix="comps_") -> dict:
    """Peers x metrics. peers: [{"name": "Peer A", "source": "10-K FY25 p.45; price 2026-11-12", "price": 41.2,
    "shares": 120, "debt": 800, "cash": 150, "revenue": 2400, "ebitda": 410}, ...]. Raw numbers are blue inputs on
    the row; equity value, EV and multiples are formulas (EV/EBITDA shows "NM" when EBITDA <= 0 and is left out of
    the statistics). Mean, median, 25th and 75th percentile rows for every multiple/margin column; each is a name
    such as comps_ev_ebitda_median. Override inputs [(key, label, fmt)] / calcs [(key, label, "{a}/{b}", fmt)]."""
    calcs = calcs or COMPS_CALCS
    stats = stats or [k for k, _, _, f in calcs if f in ("x", "pct")]
    return _peer_table(ws, row, peers, [("name", "Company")], inputs or COMPS_INPUTS, calcs, stats, title_text,
                       prefix)


def precedents_table(ws, row, deals, inputs=None, calcs=None, stats=None, text=None,
                     title_text="Precedent transactions", prefix="prec_") -> dict:
    """Deals with multiples paid and premiums. deals: [{"date": "2025-03-14", "acquirer": "...", "name": "Target",
    "consideration": "Cash", "ev": 5200, "revenue": 1300, "ebitda": 260, "offer": 48.0, "unaffected": 36.5,
    "source": "Merger proxy DEFM14A, p.31"}]. Names: prec_ev_ebitda_median, prec_premium_p75, ..."""
    calcs = calcs or PRECEDENT_CALCS
    stats = stats or [k for k, _, _, f in calcs if f in ("x", "pct")]
    return _peer_table(ws, row, deals, text or PRECEDENT_TEXT, inputs or PRECEDENT_INPUTS, calcs, stats, title_text,
                       prefix)


DCF_NAMES = {"rf": "risk_free", "beta_u": "beta_unlevered", "erp": "equity_risk_premium", "kd": "cost_of_debt",
             "tax": "tax_rate", "debt_weight": "debt_weight", "g": "terminal_growth", "exit_multiple": "exit_multiple",
             "da_pct": "da_pct", "capex_pct": "capex_pct", "nwc_pct": "nwc_pct", "revenue_base": "revenue_base",
             "mid_year": "mid_year", "debt": "total_debt", "cash": "cash", "shares": "diluted_shares",
             "wacc_step": "wacc_step", "g_step": "g_step", "other_claims": None, "size_premium": None}


def dcf_block(ws, row, years, drivers, revenue=None, margin="ebitda_margin", names=None, prefix="dcf_",
              title_text="DCF") -> dict:
    """Driver-based unlevered DCF, every number linked to named inputs (put them in the assumption book first).

    years: ["2027E", ...] (ints become "2027E"). drivers: revenue drivers, one row each:
      {"key": "mw", "label": "Active capacity (MW)", "start": "mw_start", "growth": "mw_growth", "fmt": "num"}
      ("step": name for additive change; no growth/step = flat), or {"key", "label", "formula": "={a}*{b}"} with
      {key} = same year and {key@prev} = prior year.
    revenue: expression over driver keys, e.g. "{mw}*{util}*{price_per_mw}" (default: product of all drivers).
    margin: EBITDA margin input name, or (start_name, end_name) for a linear ramp to steady state.
    names: override the input names in DCF_NAMES (rf, beta_u, erp, kd, tax, debt_weight, g, exit_multiple, da_pct,
      capex_pct, nwc_pct, revenue_base = last actual revenue, mid_year = 1 for mid-year discounting / 0 for year-end,
      debt, cash, shares, wacc_step, g_step; optional other_claims (minorities, preferred), size_premium).
    Writes: forecast (revenue -> EBITDA -> D&A, tax, capex, working capital as negatives -> unlevered FCF, discount
    factor, PV), WACC build-up (relevered beta, CAPM), terminal value by Gordon growth with the exit-multiple
    cross-check, EV -> equity -> value per share, a 5x5 WACC x g grid of value per share, and a check that the
    grid centre equals the base case. Names: <prefix>wacc, ev, ev_exit, tv_share, equity, per_share, per_share_exit."""
    wb = ws.parent
    N = {**DCF_NAMES, **(names or {})}
    labels = [y if isinstance(y, str) else f"{y}E" for y in years]
    if len(labels) < 2:
        raise ValueError("dcf_block needs at least two forecast years")
    cols = [get_column_letter(2 + i) for i in range(len(labels))]
    F0, L = cols[0], cols[-1]
    width = max(len(labels) + 1, 6)
    P = lambda k: f"{prefix}{k}"  # noqa: E731
    rows: dict[str, int] = {}
    r = row
    section(ws, r, f"{title_text}: unlevered free cash flow forecast", width)
    hdr = r + 1
    header(ws, hdr, ["Line item"] + labels)
    r = hdr + 1

    def sub(expr, i):
        def rep(m):
            k, prev = m.group(1), m.group(2)
            if k not in rows:
                raise ValueError(f"dcf_block: unknown row {{{k}}} in {expr}")
            return f"{cols[i - 1] if prev else cols[i]}{rows[k]}"
        return _re.sub(r"\{(\w+)(@prev)?\}", rep, expr)

    def line(key, label, first, nxt=None, fmt="usd", total=False):
        nonlocal r
        rows[key] = r
        ws.cell(row=r, column=1, value=label)
        for i, c in enumerate(cols):
            e = first if i == 0 or nxt is None else nxt
            _f(ws, f"{c}{r}", sub(e, i), fmt, total=total)
        r += 1

    def nm(x):
        return x[1:] if x.startswith("=") else x

    rows["t"] = r                                                  # period counter: reads the header width
    ws.cell(row=r, column=1, value="Period (years)")
    for c in cols:
        _f(ws, f"{c}{r}", f"=COLUMNS(${F0}${hdr}:{c}${hdr})", "int")
    r += 1
    for d in drivers:
        k, lab, fmt = d["key"], d.get("label", d["key"]), d.get("fmt", "num")
        if "formula" in d:
            line(k, lab, d["formula"], None, fmt)
            continue
        start = "=" + nm(d["start"])
        if d.get("growth"):
            nxt = f"={{{k}@prev}}*(1+{nm(d['growth'])})"
        elif d.get("step"):
            nxt = f"={{{k}@prev}}+{nm(d['step'])}"
        else:
            nxt = f"={{{k}@prev}}"
        line(k, lab, start, nxt, fmt)
    rev = revenue or "*".join(f"{{{d['key']}}}" for d in drivers)
    line("revenue", "Revenue ($m)", "=" + nm(rev), None, "usd", total=True)
    rb = N["revenue_base"]
    line("growth", "Revenue growth (%)", f"=IF({rb}=0,0,{{revenue}}/{rb}-1)",
         "=IF({revenue@prev}=0,0,{revenue}/{revenue@prev}-1)", "pct")
    if isinstance(margin, (tuple, list)):
        a, b = margin
        tr = f"${F0}${rows['t']}:${L}${rows['t']}"
        line("margin", "EBITDA margin (%)", f"={a}+({b}-{a})*({{t}}-1)/MAX(1,COUNT({tr})-1)", None, "pct")
    else:
        line("margin", "EBITDA margin (%)", f"={nm(margin)}", None, "pct")
    line("ebitda", "EBITDA ($m)", "={revenue}*{margin}", None, "usd", total=True)
    line("da", "Depreciation and amortisation ($m, negative)", f"=-{{revenue}}*{N['da_pct']}")
    line("ebit", "EBIT ($m)", "={ebitda}+{da}")
    line("tax", "Tax on EBIT ($m, negative; none on losses)", f"=-MAX(0,{{ebit}})*{N['tax']}")
    line("capex", "Capex ($m, negative)", f"=-{{revenue}}*{N['capex_pct']}")
    line("nwc", "Investment in working capital ($m, negative)", f"=-({{revenue}}-{rb})*{N['nwc_pct']}",
         f"=-({{revenue}}-{{revenue@prev}})*{N['nwc_pct']}")
    line("fcf", "Unlevered free cash flow ($m)", "={ebitda}+{tax}+{capex}+{nwc}", None, "usd", total=True)
    line("dp", "Discount period (years, mid-year convention)", f"={{t}}-{N['mid_year']}/2", None, "num")
    line("df", "Discount factor (x)", f"=1/(1+{P('wacc')})^{{dp}}", None, "0.0000;(0.0000)")
    line("pv", "PV of free cash flow ($m)", "={fcf}*{df}")

    def single(key, label, expr, fmt="usd", total=False, name=None):
        nonlocal r
        rows[key] = r
        ws.cell(row=r, column=1, value=label)
        _f(ws, f"B{r}", expr, fmt, total=total)
        if name:
            define_name(wb, P(name), _abs(ws, f"B{r}"))
        r += 1
        return f"B{rows[key]}"

    r += 1
    section(ws, r, "WACC build-up", width)
    r += 1
    rf = single("rf", "Risk-free rate (%)", f"={N['rf']}", "pct")
    bu = single("beta_u", "Unlevered beta (peer median, x)", f"={N['beta_u']}", "0.00;(0.00)")
    dw = single("dw", "Target debt / total capital (%)", f"={N['debt_weight']}", "pct")
    bl = single("beta_l", "Relevered beta (x)", f"={bu}*(1+(1-{N['tax']})*{dw}/(1-{dw}))", "0.00;(0.00)")
    erp = single("erp", "Equity risk premium (%)", f"={N['erp']}", "pct")
    ke_expr = f"={rf}+{bl}*{erp}" + (f"+{N['size_premium']}" if N.get("size_premium") else "")
    ke = single("ke", "Cost of equity (%)", ke_expr, "pct")
    kd = single("kd", "Pre-tax cost of debt (%)", f"={N['kd']}", "pct")
    kdat = single("kd_at", "After-tax cost of debt (%)", f"={kd}*(1-{N['tax']})", "pct")
    single("wacc", "WACC (%)", f"=(1-{dw})*{ke}+{dw}*{kdat}", "pct", total=True, name="wacc")

    r += 1
    section(ws, r, "Terminal value and enterprise value", width)
    r += 1
    g = single("g", "Terminal growth rate (%)", f"={N['g']}", "pct")
    fcfn = single("fcf_n", "Final-year free cash flow ($m)", f"={L}{rows['fcf']}")
    tn = single("t_n", "Final discount period (years, end of year)", f"={L}{rows['t']}", "num")
    tv = single("tv", "Terminal value, Gordon growth ($m)", f"={fcfn}*(1+{g})/({P('wacc')}-{g})")
    pvtv = single("pv_tv", "PV of terminal value ($m)", f"={tv}/(1+{P('wacc')})^{tn}")
    spv = single("sum_pv", "Sum of PV of free cash flow ($m)", f"=SUM({F0}{rows['pv']}:{L}{rows['pv']})")
    ev = single("ev", "Enterprise value ($m)", f"={spv}+{pvtv}", total=True, name="ev")
    single("tv_share", "Terminal value share of EV (%)", f"=IF({ev}=0,0,{pvtv}/{ev})", "pct", name="tv_share")
    em = single("exit_mult", "Exit multiple (EV / EBITDA, x)", f"={N['exit_multiple']}", "x")
    ebn = single("ebitda_n", "Final-year EBITDA ($m)", f"={L}{rows['ebitda']}")
    tvx = single("tv_exit", "Terminal value, exit multiple ($m)", f"={ebn}*{em}")
    evx = single("ev_exit", "Enterprise value, exit multiple ($m)", f"={spv}+{tvx}/(1+{P('wacc')})^{tn}",
                 name="ev_exit")
    single("impl_mult", "Implied exit multiple from Gordon value (x)", f'=IF({ebn}<=0,"NM",{tv}/{ebn})', "x")
    single("impl_g", "Implied growth from exit-multiple value (%)",
           f'=IF({tvx}+{fcfn}=0,"NM",({tvx}*{P("wacc")}-{fcfn})/({tvx}+{fcfn}))', "pct")

    r += 1
    section(ws, r, "Enterprise value to equity value", width)
    r += 1
    b0 = r
    single("b_ev", "Enterprise value ($m)", f"={ev}")
    single("b_debt", "Less: total debt ($m)", f"=-{N['debt']}")
    single("b_cash", "Plus: cash ($m)", f"={N['cash']}")
    claims = ""
    if N.get("other_claims"):
        single("b_other", "Less: minorities and preferred ($m)", f"=-{N['other_claims']}")
        claims = f"-{N['other_claims']}"
    eq = single("equity", "Equity value ($m)", f"=SUM(B{b0}:B{r - 1})", total=True, name="equity")
    sh = single("shares", "Diluted shares (m)", f"={N['shares']}", "num")
    single("per_share", "Value per share ($)", f"=IF({sh}=0,0,{eq}/{sh})", "usd2", total=True, name="per_share")
    single("per_share_exit", "Value per share, exit multiple ($)",
           f"=IF({sh}=0,0,({evx}-{N['debt']}+{N['cash']}{claims})/{sh})", "usd2", name="per_share_exit")

    r += 1
    section(ws, r, "Sensitivity: value per share ($), WACC (rows) x terminal growth (columns)", width)
    r += 1
    gh = r
    ws.cell(row=gh, column=1, value="WACC (%) \\ terminal growth (%)").font = Font(name=FONT, bold=True)
    gcols = [get_column_letter(2 + j) for j in range(5)]
    for j, c in enumerate(gcols):
        e = f"={N['g']}-2*{N['g_step']}" if j == 0 else f"={gcols[j - 1]}{gh}+{N['g_step']}"
        _f(ws, f"{c}{gh}", e, "pct").fill = SECTION_FILL
    fr, dpr, tr_ = rows["fcf"], rows["dp"], rows["t"]
    other = f"-{N['other_claims']}" if N.get("other_claims") else ""
    for i in range(5):
        rr = gh + 1 + i
        e = f"={P('wacc')}-2*{N['wacc_step']}" if i == 0 else f"=A{rr - 1}+{N['wacc_step']}"
        _f(ws, f"A{rr}", e, "pct").fill = SECTION_FILL
        for c in gcols:
            w, gg = f"$A{rr}", f"{c}${gh}"
            ev_x = (f"SUMPRODUCT(${F0}${fr}:${L}${fr}/(1+{w})^${F0}${dpr}:${L}${dpr})"
                    f"+${L}${fr}*(1+{gg})/({w}-{gg})/(1+{w})^${L}${tr_}")
            _f(ws, f"{c}{rr}", f'=IF({w}<={gg},"NM",IF({N["shares"]}=0,0,({ev_x}-{N["debt"]}+{N["cash"]}{other})'
                               f'/{N["shares"]}))', "usd2")
    centre = f"{gcols[2]}{gh + 3}"
    r = gh + 6
    if "Checks" in wb.sheetnames:
        add_check(wb, f"{title_text}: sensitivity centre equals base value per share",
                  f"='{ws.title}'!{centre}-{P('per_share')}")
    if ws.freeze_panes is None:
        ws.freeze_panes = f"B{hdr + 1}"
    ws.column_dimensions["A"].width = max(ws.column_dimensions["A"].width or 0, 46)
    for c in cols + gcols:
        ws.column_dimensions[c].width = max(ws.column_dimensions[c].width or 0, 13)
    return {"header_row": hdr, "rows": rows, "cols": cols, "grid": f"'{ws.title}'!B{gh + 1}:F{gh + 5}",
            "grid_centre": f"'{ws.title}'!{centre}", "names": {k: P(k) for k in
            ("wacc", "ev", "ev_exit", "tv_share", "equity", "per_share", "per_share_exit")}, "next_row": r + 1}


def football_field(ws, row, methods, fmt="usd2", marker=None, title_text="Valuation summary (football field)",
                   unit="Value per share ($)", anchor=None) -> dict:
    """Valuation ranges by method, as live links, plus a floating-bar chart.
    methods: [{"label": "Trading comps, EV/EBITDA IQR", "low": "=...", "high": "=..."}, ...] (formulas that link to
    the comps/precedents/DCF results). marker: optional {"label": "Offer price ($/share)", "value": "=offer_price"}.
    Adds a check that every low <= high."""
    wb = ws.parent
    section(ws, row, title_text, 4)
    hdr = row + 1
    header(ws, hdr, ["Method", f"Low: {unit}", f"High: {unit}", "Range width"])
    r = first = hdr + 1
    for m in methods:
        ws.cell(row=r, column=1, value=m["label"])
        _f(ws, f"B{r}", m["low"], fmt)
        _f(ws, f"C{r}", m["high"], fmt)
        _f(ws, f"D{r}", f"=C{r}-B{r}", fmt)
        r += 1
    last = r - 1
    out = {"first_row": first, "last_row": last}
    if marker:
        r += 1
        ws.cell(row=r, column=1, value=marker["label"]).font = Font(name=FONT, bold=True)
        _f(ws, f"B{r}", marker["value"], fmt)
        out["marker"] = f"'{ws.title}'!B{r}"
    from openpyxl.chart.shapes import GraphicalProperties
    from openpyxl.drawing.line import LineProperties
    ch = BarChart()
    ch.type, ch.grouping, ch.overlap, ch.gapWidth = "bar", "stacked", 100, 60
    ch.title = title_text
    ch.height, ch.width = 7.5, 16
    ch.add_data(Reference(ws, min_col=2, min_row=hdr, max_row=last), titles_from_data=True)
    ch.add_data(Reference(ws, min_col=4, min_row=hdr, max_row=last), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=1, min_row=first, max_row=last))
    ch.series[0].graphicalProperties = GraphicalProperties(noFill=True, ln=LineProperties(noFill=True))
    ch.series[1].graphicalProperties = GraphicalProperties(solidFill="2557D6")
    ch.legend = None
    ch.y_axis.numFmt = FMT.get(fmt, fmt)
    ws.add_chart(ch, anchor or f"F{row}")
    if "Checks" in wb.sheetnames:
        add_check(wb, "Football field: every low is at or below its high",
                  f"=SUMPRODUCT(--('{ws.title}'!B{first}:B{last}>'{ws.title}'!C{first}:C{last}))")
    ws.column_dimensions["A"].width = max(ws.column_dimensions["A"].width or 0, 40)
    for c in "BCD":
        ws.column_dimensions[c].width = max(ws.column_dimensions[c].width or 0, 16)
    out["next_row"] = r + 2
    return out
