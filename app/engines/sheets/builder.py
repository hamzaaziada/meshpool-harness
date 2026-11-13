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


def freeze(ws, ref):
    """Freeze panes at ref. Resets first: openpyxl stacks a new set of pane selections on every assignment, and
    Office rejects the duplicates (X0)."""
    from openpyxl.worksheet.views import Selection
    ws.freeze_panes = None
    ws.sheet_view.selection = [Selection()]
    ws.freeze_panes = ref


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

    def link(self, label: str, expr: str, fmt=None, name=None, source: str = "", rationale: str = "") -> str:
        """A value that is a formula over other inputs or the Historicals tab (e.g. an opening balance), named."""
        ws, r = self.ws, self.row
        ws.cell(row=r, column=1, value=label)
        _f(ws, f"B{r}", expr, fmt)
        if name:
            define_name(ws.parent, name, _abs(ws, f"B{r}"))
        ws.cell(row=r, column=3, value=source or None)
        ws.cell(row=r, column=4, value=rationale or None).alignment = Alignment(wrap_text=True, vertical="top")
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

    return _dcf_tail(ws, r, hdr, rows, cols, N, prefix, width, title_text)


BRIDGE_ITEMS = [("other_claims", "Less: minorities and preferred ($m)", -1),
                ("minorities", "Less: minority interest ($m)", -1), ("preferred", "Less: preferred equity ($m)", -1),
                ("leases", "Less: lease liabilities ($m)", -1), ("pensions", "Less: unfunded pensions ($m)", -1),
                ("investments", "Plus: equity investments and other non-operating assets ($m)", 1)]


def _dcf_tail(ws, r, hdr, rows, cols, N, prefix, width, title_text, extra_grid=False) -> dict:
    """WACC build-up, terminal value (Gordon + exit multiple), EV -> equity bridge and sensitivity grids, shared by
    dcf_block and dcf_from_statements. rows must hold t, ebitda, fcf, dp, pv (forecast columns cols)."""
    wb = ws.parent
    F0, L = cols[0], cols[-1]
    P = lambda k: f"{prefix}{k}"  # noqa: E731

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
    for key, lab, sign in BRIDGE_ITEMS:                       # itemised: each claim or asset on its own line
        if N.get(key):
            single(f"b_{key}", lab, f"={'-' if sign < 0 else ''}{N[key]}")
            claims += f"{'-' if sign < 0 else '+'}{N[key]}"
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
    other = claims
    pv_x = lambda w: f"SUMPRODUCT(${F0}${fr}:${L}${fr}/(1+{w})^${F0}${dpr}:${L}${dpr})"  # noqa: E731
    for i in range(5):
        rr = gh + 1 + i
        e = f"={P('wacc')}-2*{N['wacc_step']}" if i == 0 else f"=A{rr - 1}+{N['wacc_step']}"
        _f(ws, f"A{rr}", e, "pct").fill = SECTION_FILL
        for c in gcols:
            w, gg = f"$A{rr}", f"{c}${gh}"
            ev_x = pv_x(w) + f"+${L}${fr}*(1+{gg})/({w}-{gg})/(1+{w})^${L}${tr_}"
            _f(ws, f"{c}{rr}", f'=IF({w}<={gg},"NM",IF({N["shares"]}=0,0,({ev_x}-{N["debt"]}+{N["cash"]}{other})'
                               f'/{N["shares"]}))', "usd2")
    centre = f"{gcols[2]}{gh + 3}"
    r = gh + 6
    out_extra = {}
    if extra_grid and N.get("exit_step"):
        r += 1
        section(ws, r, "Sensitivity: value per share ($), WACC (rows) x exit multiple (columns)", width)
        r += 1
        xh = r
        ws.cell(row=xh, column=1, value="WACC (%) \\ exit multiple (x)").font = Font(name=FONT, bold=True)
        for j, c in enumerate(gcols):
            e = f"={N['exit_multiple']}-2*{N['exit_step']}" if j == 0 else f"={gcols[j - 1]}{xh}+{N['exit_step']}"
            _f(ws, f"{c}{xh}", e, "x").fill = SECTION_FILL
        er_ = rows["ebitda"]
        for i in range(5):
            rr = xh + 1 + i
            e = f"={P('wacc')}-2*{N['wacc_step']}" if i == 0 else f"=A{rr - 1}+{N['wacc_step']}"
            _f(ws, f"A{rr}", e, "pct").fill = SECTION_FILL
            for c in gcols:
                w, m = f"$A{rr}", f"{c}${xh}"
                ev_x = pv_x(w) + f"+${L}${er_}*{m}/(1+{w})^${L}${tr_}"
                _f(ws, f"{c}{rr}", f'=IF({N["shares"]}=0,0,({ev_x}-{N["debt"]}+{N["cash"]}{other})/{N["shares"]})',
                   "usd2")
        out_extra = {"grid_exit": f"'{ws.title}'!B{xh + 1}:F{xh + 5}",
                     "grid_exit_centre": f"'{ws.title}'!{gcols[2]}{xh + 3}"}
        if "Checks" in wb.sheetnames:
            add_check(wb, f"{title_text}: exit-multiple grid centre equals exit-multiple value per share",
                      f"='{ws.title}'!{gcols[2]}{xh + 3}-{P('per_share_exit')}")
        r = xh + 6
    if "Checks" in wb.sheetnames:
        add_check(wb, f"{title_text}: sensitivity centre equals base value per share",
                  f"='{ws.title}'!{centre}-{P('per_share')}")
    if ws.freeze_panes is None:
        ws.freeze_panes = f"B{hdr + 1}"
    ws.column_dimensions["A"].width = max(ws.column_dimensions["A"].width or 0, 46)
    for c in cols + gcols:
        ws.column_dimensions[c].width = max(ws.column_dimensions[c].width or 0, 13)
    return {"header_row": hdr, "rows": rows, "cols": cols, "sheet_name": ws.title,
            "grid": f"'{ws.title}'!B{gh + 1}:F{gh + 5}",
            "grid_centre": f"'{ws.title}'!{centre}", "names": {k: P(k) for k in
            ("wacc", "ev", "ev_exit", "tv_share", "equity", "per_share", "per_share_exit")}, "next_row": r + 1,
            **out_extra}


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


# --------------------------------------------------------------------------- scenarios, three statements, valuation
# The integrated model: a scenario book on Inputs (base / bull / bear per assumption, one switch), a three-statement
# model whose revenue and costs come from operating drivers and whose balance sheet comes from schedules (working-capital
# days, PP&E roll-forward with depreciation from useful lives, debt with interest on opening balances and a revolver
# that keeps minimum cash, equity roll-forward), a DCF that takes unlevered free cash flow from those statements, pinned
# copies per scenario for a scenario summary, and a valuation summary that decides which methods fit and weights them.
SCENARIOS = ("base", "bull", "bear")
SCEN_COLS = ["Assumption", "Selected case", "Base", "Bull", "Bear", "Source", "Evidence / rationale"]


class ScenarioBook:
    """Inputs as a scenario book: Assumption | Selected case | Base | Bull | Bear | Source | Evidence / rationale.
    The first row is the switch `scenario` (1 = Base, 2 = Bull, 3 = Bear). book.add(...) names the selected value
    <name> (=CHOOSE(scenario, base, bull, bear)) and the cases <name>_base / _bull / _bear; <name>_low / _high point
    at the lower / higher case. Use the plain assumption book for inputs that do not change between cases."""

    def __init__(self, ws, row: int = 3, default: int = 1):
        self.ws, self.header_row = ws, row
        header(ws, row, SCEN_COLS)
        for col, w in zip("ABCDEFG", (44, 14, 12, 12, 12, 40, 56)):
            ws.column_dimensions[col].width = max(ws.column_dimensions[col].width or 0, w)
        r = row + 1
        ws.cell(row=r, column=1, value="Scenario selected (1 = Base, 2 = Bull, 3 = Bear)").font = Font(name=FONT,
                                                                                                       bold=True)
        input_cell(ws, f"B{r}", default, "int", name="scenario")
        _f(ws, f"C{r}", '=CHOOSE(scenario,"Base case","Bull case","Bear case")')
        define_name(ws.parent, "scenario_name", _abs(ws, f"C{r}"))
        ws.cell(row=r, column=6, value="Model control: the case every Selected value and the statements use")
        ws.cell(row=r, column=7, value="Bull and bear move each driver to the end of its evidenced range; the "
                "scenario summary shows all three cases side by side")
        if "Checks" in ws.parent.sheetnames:
            add_check(ws.parent, "Scenario switch is 1, 2 or 3", "=IF(ISERROR(CHOOSE(scenario,0,0,0)),1,0)")
        self.row = r + 1

    def section(self, text: str):
        section(self.ws, self.row, text, len(SCEN_COLS))
        self.row += 1

    def add(self, label: str, base, bull=None, bear=None, fmt=None, name=None, source: str = "",
            rationale: str = "") -> str:
        if not name:
            raise ValueError("scenario inputs need a name")
        ws, r = self.ws, self.row
        bull = base if bull is None else bull
        bear = base if bear is None else bear
        ws.cell(row=r, column=1, value=label)
        cells = {}
        for col, v, s in (("C", base, "base"), ("D", bull, "bull"), ("E", bear, "bear")):
            input_cell(ws, f"{col}{r}", v, fmt, name=f"{name}_{s}")
            cells[s] = col
        _f(ws, f"B{r}", f"=CHOOSE(scenario,C{r},D{r},E{r})", fmt)
        define_name(ws.parent, name, _abs(ws, f"B{r}"))
        lo, hi = ("bull", "bear") if bull <= bear else ("bear", "bull")
        define_name(ws.parent, f"{name}_low", _abs(ws, f"{cells[lo]}{r}"))
        define_name(ws.parent, f"{name}_high", _abs(ws, f"{cells[hi]}{r}"))
        ws.cell(row=r, column=6, value=source or None)
        ws.cell(row=r, column=7, value=rationale or None).alignment = Alignment(wrap_text=True, vertical="top")
        self.row += 1
        return name

    def link(self, label: str, expr: str, fmt=None, name=None, source: str = "", rationale: str = "") -> str:
        """A value that is a formula over other inputs or the Historicals tab (e.g. an opening balance), named."""
        ws, r = self.ws, self.row
        ws.cell(row=r, column=1, value=label)
        for col in "BCDE":
            _f(ws, f"{col}{r}", expr if col == "B" else f"=B{r}", fmt)
        if name:
            define_name(ws.parent, name, _abs(ws, f"B{r}"))
        ws.cell(row=r, column=6, value=source or None)
        ws.cell(row=r, column=7, value=rationale or None).alignment = Alignment(wrap_text=True, vertical="top")
        self.row += 1
        return name or _abs(ws, f"B{r}")


def scenario_book(ws, row: int = 3, default: int = 1) -> ScenarioBook:
    return ScenarioBook(ws, row, default)


def _names_in(wb) -> set[str]:
    return {k.lower() for k in wb.defined_names.keys()}


def _resolver(wb, scenario):
    """Expression rewriter: with a pinned scenario ("bull"), every name x that has a case x_bull reads that case."""
    have = _names_in(wb)
    if not scenario:
        return lambda e: e

    def fix(expr: str) -> str:
        out, i = [], 0
        for m in _re.finditer(r'"[^"]*"|\b[A-Za-z_][A-Za-z0-9_.]*\b(?!\s*\()(?!!)', expr):
            tok = m.group(0)
            out.append(expr[i:m.start()])
            if not tok.startswith('"') and f"{tok}_{scenario}".lower() in have:
                tok = f"{tok}_{scenario}"
            out.append(tok)
            i = m.end()
        out.append(expr[i:])
        return "".join(out)
    return fix


TS_NAMES = {"tax": "tax_rate", "dso": "dso", "dio": "dio", "dpo": "dpo", "capex_pct": "capex_pct",
            "life_new": "useful_life_new", "life_existing": "remaining_life_existing", "term_rate": "term_rate",
            "revolver_rate": "revolver_rate", "cash_rate": "cash_rate", "min_cash": "min_cash",
            "revolver_capacity": "revolver_capacity", "term_draw": "term_draw", "term_amort": "term_amort",
            "payout": "payout_ratio", "buyback": "buyback",
            "cash_open": "cash_open", "ar_open": "ar_open", "inv_open": "inv_open", "other_ca_open": "other_ca_open",
            "ppe_open": "ppe_open", "other_nca_open": "other_nca_open", "ap_open": "ap_open",
            "other_cl_open": "other_cl_open", "revolver_open": "revolver_open", "term_debt_open": "term_debt_open",
            "other_ncl_open": "other_ncl_open", "equity_open": "equity_open", "sbc": None}
DEFAULT_OPEX = [{"key": "sga", "label": "Selling, general and administrative ($m, negative)", "pct": "sga_pct"}]


def three_statement(ws, row, years, segments, opex=None, capex_growth=None, names=None, scenario=None,
                    prefix="ts_", last_actual=None, title_text="Three-statement model") -> dict:
    """Integrated income statement, balance sheet and cash flow, driven from named inputs (scenario book).

    years: forecast years [2026, ...] (ints become "2026E"); column B is the last actual year (last_actual, default
      "<first year - 1>A") and holds the opening balance sheet and the drivers' last actual values.
    segments: [{"name": "Devices", "drivers": [...], "revenue": "{units}*{price}", "cogs": "{units}*{unit_cost}"}]
      drivers: {"key", "label", "start": name of the last actual value, "growth": name or (start_name, end_name)
      for a linear ramp | "step": name for an additive change | neither = flat, "fmt"}, or {"key", "label",
      "formula": "{capacity}*{utilisation}"} ({key@prev} = prior year; a formula with @prev also needs "start").
      Revenue patterns: units x price, capacity x utilisation x price per unit, customers x ARPU.
      COGS: "cogs" expression (a positive cost, e.g. "{units}*{unit_cost}") or "gross_margin": name.
    opex: [{"key", "label" (say "negative"), "pct": name of % of revenue} | {"key", "label", "formula": expr}].
    capex_growth: optional expression for growth capex, e.g. "MAX(0,{capacity}-{capacity@prev})*capex_per_unit";
      maintenance capex = revenue x capex_pct.
    names: override input names (TS_NAMES): tax, dso, dio, dpo, capex_pct, life_new (useful life of new assets,
      years), life_existing (remaining life of opening PP&E, years), term_rate, revolver_rate, cash_rate, min_cash,
      revolver_capacity, term_draw (per year), term_amort (scheduled repayment per year), payout (dividends % of
      net income), buyback (per year), and the opening balance sheet: cash_open, ar_open, inv_open, other_ca_open,
      ppe_open, other_nca_open, ap_open, other_cl_open, revolver_open, term_debt_open, other_ncl_open, equity_open.
    scenario: None reads the live switch; "base" / "bull" / "bear" pins this copy to that case.
    No circularity: interest is charged on opening balances; the revolver draws only what keeps cash at the minimum.
    Adds checks: balance sheet balances every year, cash flow closing cash equals balance sheet cash, net income on
    the cash flow equals the income statement, revolver and PP&E never negative."""
    wb = ws.parent
    N = {**TS_NAMES, **(names or {})}
    fix = _resolver(wb, scenario)
    opex = opex if opex is not None else DEFAULT_OPEX
    labels = [y if isinstance(y, str) else f"{y}E" for y in years]
    if len(labels) < 3:
        raise ValueError("three_statement needs at least three forecast years")
    if last_actual is None:
        y0 = years[0]
        last_actual = f"{y0 - 1}A" if isinstance(y0, int) else "Last actual"
    need = {N[k] for k in TS_NAMES if N[k]} | {o["pct"] for o in opex if "pct" in o}
    if N.get("sbc"):                       # share-based compensation: a real expense (in EBITDA and EPS), non-cash
        opex = list(opex) + [{"key": "sbc", "label": "Share-based compensation ($m, negative)", "pct": N["sbc"]}]
    for s in segments:
        if "gross_margin" in s:
            need.add(s["gross_margin"])
        for d in s["drivers"]:
            for k in ("start", "step"):
                if d.get(k):
                    need.add(d[k])
            g = d.get("growth")
            need |= set(g) if isinstance(g, (tuple, list)) else ({g} if g else set())
    have = _names_in(wb)
    missing = sorted(n for n in need if n.lower() not in have)
    if missing:
        raise ValueError(f"three_statement: define these named inputs first (scenario_book / assumption_book): "
                         f"{', '.join(missing)}")
    nm = lambda k: fix(N[k])  # noqa: E731
    cols = [get_column_letter(3 + i) for i in range(len(labels))]
    A0, F0, L = "B", cols[0], cols[-1]
    allc = [A0] + cols
    width = len(allc) + 1
    rows: dict[str, int] = {}
    keys_seen: set[str] = set()
    section(ws, row, title_text + (f" ({scenario} case, pinned)" if scenario else " (selected case)"), width)
    hdr = row + 1
    header(ws, hdr, ["Line item", last_actual] + labels)
    r = hdr + 1

    def sub(expr, c_idx):
        """{key} -> this column's cell of that row; {key@prev} -> previous column's. c_idx indexes allc."""
        def rep(m):
            k, prev = m.group(1), m.group(2)
            if k not in rows:
                raise ValueError(f"three_statement: unknown row {{{k}}} in {expr}")
            return f"{allc[c_idx - 1] if prev else allc[c_idx]}{rows[k]}"
        return fix(_re.sub(r"\{(\w+)(@prev)?\}", rep, expr))

    specs: dict[str, tuple] = {}

    def line(key, label, fcst=None, actual=None, fmt="usd", total=False, at=None):
        """Register a row. fcst: formula for every forecast column; actual: formula for column B (None leaves it
        empty). Formulas are written once every row exists, so a row may read rows further down."""
        nonlocal r
        if at is None:
            if key in rows:
                raise ValueError(f"three_statement: duplicate row key {key}")
            at = r
            r += 1
        rows[key] = at
        ws.cell(row=at, column=1, value=label)
        specs[key] = (at, fcst, actual, fmt, total)
        return at

    def flush():
        for key, (at, fcst, actual, fmt, total) in specs.items():
            if actual is not None:
                _f(ws, f"{A0}{at}", sub(actual, 0), fmt, total=total)
            if fcst is not None:
                for i in range(1, len(allc)):
                    _f(ws, f"{allc[i]}{at}", sub(fcst, i), fmt, total=total)

    def band(text):
        nonlocal r
        r += 1
        section(ws, r, text, width)
        r += 1

    rows["t"] = r
    ws.cell(row=r, column=1, value="Forecast year (number)")
    for c in cols:
        _f(ws, f"{c}{r}", f"=COLUMNS(${F0}${hdr}:{c}${hdr})", "int")
    r += 1
    trng = f"${F0}${rows['t']}:${L}${rows['t']}"

    # ---- revenue and cost of revenue from drivers
    band("Revenue and cost of revenue build (operating drivers)")
    seg_rev, seg_cogs = [], []
    for si, s in enumerate(segments):
        for d in s["drivers"]:
            k = d["key"]
            if k in keys_seen or k in rows:
                raise ValueError(f"three_statement: driver key {k} used twice")
            keys_seen.add(k)
            fmt = d.get("fmt", "num")
            lab = d.get("label", k)
            if "formula" in d:
                f = "=" + d["formula"].lstrip("=")
                if "@prev" in f:
                    if not d.get("start"):
                        raise ValueError(f"driver {k}: a formula with @prev needs a start value")
                    line(k, lab, f, "=" + d["start"], fmt)
                else:
                    line(k, lab, f, f, fmt)
                continue
            g = d.get("growth")
            if isinstance(g, (tuple, list)):
                a, b = g
                nxt = f"={{{k}@prev}}*(1+{a}+({b}-{a})*({{t}}-1)/MAX(1,COUNT({trng})-1))"
            elif g:
                nxt = f"={{{k}@prev}}*(1+{g})"
            elif d.get("step"):
                nxt = f"={{{k}@prev}}+{d['step']}"
            else:
                nxt = f"={{{k}@prev}}"
            line(k, lab, nxt, "=" + d["start"], fmt)
        rk = f"seg_rev_{si}"
        rev_expr = s.get("revenue") or "*".join(f"{{{d['key']}}}" for d in s["drivers"])
        line(rk, f"Revenue: {s['name']} ($m)", "=" + rev_expr, "=" + rev_expr)
        seg_rev.append(rk)
        ck = f"seg_cogs_{si}"
        if s.get("cogs"):
            line(ck, f"Cost of revenue: {s['name']} ($m, negative)", f"=-({s['cogs']})")
        elif s.get("gross_margin"):
            line(ck, f"Cost of revenue: {s['name']} ($m, negative)", f"=-{{{rk}}}*(1-{s['gross_margin']})")
        else:
            raise ValueError(f"segment {s['name']}: give cogs (driver expression) or gross_margin (name)")
        seg_cogs.append(ck)

    # ---- income statement (interest and depreciation rows are filled from the schedules below)
    band("Income statement")
    sumrows = lambda ks: "=" + "+".join(f"{{{k}}}" for k in ks)  # noqa: E731
    line("revenue", "Revenue ($m)", sumrows(seg_rev), sumrows(seg_rev), total=True)
    line("growth", "Revenue growth (%)", "=IF({revenue@prev}=0,0,{revenue}/{revenue@prev}-1)", None, "pct")
    line("cogs", "Cost of revenue ($m, negative)", sumrows(seg_cogs))
    line("gross_profit", "Gross profit ($m)", "={revenue}+{cogs}", total=True)
    line("gross_margin", "Gross margin (%)", "=IF({revenue}=0,0,{gross_profit}/{revenue})", None, "pct")
    for o in opex:
        if "pct" in o:
            line(o["key"], o["label"], f"=-{{revenue}}*{o['pct']}")
        else:
            line(o["key"], o["label"], "=" + o["formula"].lstrip("="))
    line("ebitda", "EBITDA ($m)", "=" + "+".join(["{gross_profit}"] + [f"{{{o['key']}}}" for o in opex]), total=True)
    line("ebitda_margin", "EBITDA margin (%)", "=IF({revenue}=0,0,{ebitda}/{revenue})", None, "pct")
    is_da = r
    r += 1                                                    # D&A, EBIT, interest, tax: written after schedules
    is_rows = ["ebit", "int_exp", "int_inc", "ebt", "tax", "net_income"]
    is_at = {k: r + i for i, k in enumerate(is_rows)}
    rows.update(is_at, da=is_da)
    r += len(is_rows)

    # ---- working capital
    band("Working capital schedule (year-end balances from days)")
    line("ar", "Accounts receivable ($m)", f"={{revenue}}*{nm('dso')}/365", "=" + nm("ar_open"))
    line("inv", "Inventory ($m)", f"=-{{cogs}}*{nm('dio')}/365", "=" + nm("inv_open"))
    line("ap", "Accounts payable ($m)", f"=-{{cogs}}*{nm('dpo')}/365", "=" + nm("ap_open"))
    line("nwc", "Net working capital ($m)", "={ar}+{inv}-{ap}", "={ar}+{inv}-{ap}", total=True)

    # ---- PP&E roll-forward
    band("PP&E roll-forward (depreciation from useful lives)")
    line("ppe_beg", "PP&E at start of year ($m)", "={ppe_end@prev}")
    line("add_maint", "Additions: maintenance investment ($m)", f"={{revenue}}*{nm('capex_pct')}")
    adds = ["add_maint"]
    if capex_growth:
        line("add_growth", "Additions: growth investment ($m)", "=" + capex_growth.lstrip("="))
        adds.append("add_growth")
    line("additions", "Additions to PP&E, total ($m)", sumrows(adds), total=True)
    po, le = nm("ppe_open"), nm("life_existing")
    line("dep_old", "Depreciation of opening PP&E ($m)",
         f"=IF({le}<=0,0,MIN({po}/{le},MAX(0,{po}-{po}/{le}*({{t}}-1))))")
    vint = []
    ln = nm("life_new")
    for j, c in enumerate(cols):
        k = f"dep_v{j}"
        cap, tj = f"${c}${rows['additions']}", f"${c}${rows['t']}"
        line(k, f"Depreciation of {labels[j]} additions ($m)",
             f"=IF({{t}}<{tj},0,IF({ln}<=0,0,MIN({cap}/{ln},MAX(0,{cap}-{cap}/{ln}*({{t}}-{tj})))))")
        vint.append(k)
    line("dep", "Depreciation, total ($m)", "={dep_old}+" + "+".join(f"{{{k}}}" for k in vint), total=True)
    line("ppe_end", "PP&E at end of year ($m)", "={ppe_beg}+{additions}-{dep}", "=" + po, total=True)

    # ---- debt
    band("Debt schedule (interest on opening balances: no circularity)")
    line("term_beg", "Term debt at start of year ($m)", "={term_end@prev}")
    line("term_draw", "Term debt drawn ($m)", "=" + nm("term_draw"))
    line("term_repay", "Term debt repaid ($m, negative)", f"=-MIN({{term_beg}}+{{term_draw}},{nm('term_amort')})")
    line("term_end", "Term debt at end of year ($m)", "={term_beg}+{term_draw}+{term_repay}",
         "=" + nm("term_debt_open"), total=True)
    line("term_int", "Interest on term debt ($m, negative)", f"=-{{term_beg}}*{nm('term_rate')}")
    line("rev_beg", "Revolver at start of year ($m)", "={rev_end@prev}")
    cash_pre_row = rows["cash_pre"] = r
    r += 1                                                     # cash before revolver: needs the cash flow rows
    line("rev_flow", "Revolver drawn / (repaid) ($m)",
         f"=MAX(-{{rev_beg}},MIN({nm('revolver_capacity')}-{{rev_beg}},{nm('min_cash')}-{{cash_pre}}))")
    line("rev_end", "Revolver at end of year ($m)", "={rev_beg}+{rev_flow}", "=" + nm("revolver_open"), total=True)
    line("rev_int", "Interest on revolver ($m, negative)", f"=-{{rev_beg}}*{nm('revolver_rate')}")
    line("shortfall", "Cash below minimum after revolver ($m)", f"=MAX(0,{nm('min_cash')}-{{cash_pre}}-{{rev_flow}})")

    # ---- equity
    band("Equity roll-forward")
    line("eq_beg", "Equity at start of year ($m)", "={eq_end@prev}")
    eq_ni = r
    r += 1
    rows["eq_ni"] = eq_ni
    line("dividends", "Dividends paid ($m, negative)")          # formula written below, once net income exists
    line("buybacks", "Share repurchases ($m, negative)", "=-" + nm("buyback"))
    if N.get("sbc"):
        line("eq_sbc", "Share-based compensation credited to equity ($m)", "=-{sbc}")
    line("eq_end", "Equity at end of year ($m)", None, "=" + nm("equity_open"), total=True)

    # ---- balance sheet
    band("Balance sheet")
    bs_cash = r
    r += 1
    line("bs_ar", "Accounts receivable ($m)", "={ar}", "={ar}")
    line("bs_inv", "Inventory ($m)", "={inv}", "={inv}")
    line("bs_oca", "Other current assets, held flat ($m)", "={bs_oca@prev}", "=" + nm("other_ca_open"))
    line("bs_ppe", "PP&E, net ($m)", "={ppe_end}", "={ppe_end}")
    line("bs_onca", "Other non-current assets incl. goodwill, held flat ($m)", "={bs_onca@prev}",
         "=" + nm("other_nca_open"))
    rows["bs_cash"] = bs_cash
    asset_keys = ["bs_cash", "bs_ar", "bs_inv", "bs_oca", "bs_ppe", "bs_onca"]
    line("total_assets", "Total assets", sumrows(asset_keys), sumrows(asset_keys), total=True)
    line("bs_ap", "Accounts payable ($m)", "={ap}", "={ap}")
    line("bs_ocl", "Other current liabilities, held flat ($m)", "={bs_ocl@prev}", "=" + nm("other_cl_open"))
    line("bs_rev", "Revolver ($m)", "={rev_end}", "={rev_end}")
    line("bs_term", "Term debt ($m)", "={term_end}", "={term_end}")
    line("bs_oncl", "Other non-current liabilities, held flat ($m)", "={bs_oncl@prev}", "=" + nm("other_ncl_open"))
    liab_keys = ["bs_ap", "bs_ocl", "bs_rev", "bs_term", "bs_oncl"]
    line("total_liabilities", "Total liabilities", sumrows(liab_keys), sumrows(liab_keys), total=True)
    line("total_equity", "Total equity", "={eq_end}", "={eq_end}", total=True)
    line("total_le", "Total liabilities and equity", "={total_liabilities}+{total_equity}",
         "={total_liabilities}+{total_equity}", total=True)
    line("bal_check", "Check: assets minus liabilities and equity ($m)", "={total_assets}-{total_le}",
         "={total_assets}-{total_le}")

    # ---- cash flow statement
    band("Cash flow statement")
    line("cf_ni", "Net income ($m)", "={net_income}")
    line("cf_da", "Add back: depreciation ($m)", "={dep}")
    if N.get("sbc"):
        line("cf_sbc", "Add back: share-based compensation ($m)", "=-{sbc}")
    line("cf_ar", "(Increase) / decrease in receivables ($m)", "={ar@prev}-{ar}")
    line("cf_inv", "(Increase) / decrease in inventory ($m)", "={inv@prev}-{inv}")
    line("cf_ap", "Increase / (decrease) in payables ($m)", "={ap}-{ap@prev}")
    line("cf_nwc", "(Increase) / decrease in working capital ($m)", "=SUM({cf_ar}:{cf_ap})")
    line("cfo", "Cash from operating activities ($m)",
         "={cf_ni}+{cf_da}+{cf_nwc}" + ("+{cf_sbc}" if N.get("sbc") else ""), total=True)
    line("cf_capex", "Capital expenditure ($m, negative)", "=-{additions}")
    line("cfi", "Cash from investing activities ($m)", "={cf_capex}", total=True)
    line("cf_draw", "Term debt drawn ($m)", "={term_draw}")
    line("cf_repay", "Term debt repaid ($m, negative)", "={term_repay}")
    line("cf_div", "Dividends paid ($m, negative)", "={dividends}")
    line("cf_buy", "Share repurchases ($m, negative)", "={buybacks}")
    line("cf_rev", "Revolver drawn / (repaid) ($m)", "={rev_flow}")
    line("cff", "Cash from financing activities ($m)", "=SUM({cf_draw}:{cf_rev})", total=True)
    line("cf_net", "Net change in cash ($m)", "={cfo}+{cfi}+{cff}", total=True)
    line("cash_beg", "Cash at start of year ($m)", "={bs_cash@prev}")
    line("cash_end", "Cash at end of year ($m)", "={cash_beg}+{cf_net}", total=True)
    line("cash_check", "Check: cash flow closing cash minus balance sheet cash ($m)", "={cash_end}-{bs_cash}")
    end_row = r

    # ---- rows reserved above, defined now that the rows they read exist
    def put(at, key, label, fcst, actual=None, fmt="usd", total=False):
        line(key, label, fcst, actual, fmt, total, at=at)
    put(is_da, "da", "Depreciation and amortisation ($m, negative)", "=-{dep}")
    put(is_at["ebit"], "ebit", "EBIT ($m)", "={ebitda}+{da}", total=True)
    put(is_at["int_exp"], "int_exp", "Interest expense ($m, negative)", "={term_int}+{rev_int}")
    put(is_at["int_inc"], "int_inc", "Interest income on opening cash ($m)", f"={{bs_cash@prev}}*{nm('cash_rate')}")
    put(is_at["ebt"], "ebt", "Pre-tax income ($m)", "={ebit}+{int_exp}+{int_inc}", total=True)
    put(is_at["tax"], "tax", "Income tax ($m, negative; none on losses)", f"=-MAX(0,{{ebt}})*{nm('tax')}")
    put(is_at["net_income"], "net_income", "Net income ($m)", "={ebt}+{tax}", total=True)
    put(cash_pre_row, "cash_pre", "Cash before revolver ($m)",
        "={bs_cash@prev}+{cfo}+{cfi}+{term_draw}+{term_repay}+{dividends}+{buybacks}")
    put(eq_ni, "eq_ni", "Net income ($m)", "={net_income}")
    put(rows["dividends"], "dividends", "Dividends paid ($m, negative)", f"=-MAX(0,{{net_income}})*{nm('payout')}")
    put(rows["eq_end"], "eq_end", "Equity at end of year ($m)", "={eq_beg}+{eq_ni}+{dividends}+{buybacks}"
        + ("+{eq_sbc}" if N.get("sbc") else ""), "=" + nm("equity_open"), total=True)
    put(bs_cash, "bs_cash", "Cash ($m)", "={cash_end}", "=" + nm("cash_open"))
    r = end_row
    flush()

    P = lambda k: f"{prefix}{k}"  # noqa: E731
    rng = lambda k, first=F0: f"'{ws.title}'!${first}${rows[k]}:${L}${rows[k]}"  # noqa: E731
    define_name(wb, P("revenue_final"), _abs(ws, f"{L}{rows['revenue']}"))
    define_name(wb, P("ebitda_margin_final"), _abs(ws, f"{L}{rows['ebitda_margin']}"))
    define_name(wb, P("net_income_final"), _abs(ws, f"{L}{rows['net_income']}"))
    r += 1
    section(ws, r, "Model summary", width)
    r += 1
    ws.cell(row=r, column=1, value="Largest cash shortfall below minimum ($m)")
    _f(ws, f"B{r}", f"=MAX({rng('shortfall')})", "usd")
    define_name(wb, P("max_shortfall"), _abs(ws, f"B{r}"))
    r += 1
    ws.cell(row=r, column=1, value="Peak revolver balance ($m)")
    _f(ws, f"B{r}", f"=MAX({rng('rev_end', A0)})", "usd")
    define_name(wb, P("peak_revolver"), _abs(ws, f"B{r}"))
    r += 1
    tag = f"{title_text}" + (f" ({scenario})" if scenario else "")
    if "Checks" in wb.sheetnames:
        add_check(wb, f"{tag}: balance sheet balances, every year incl. opening",
                  f"=SUMPRODUCT(ABS({rng('bal_check', A0)}))")
        add_check(wb, f"{tag}: cash flow closing cash equals balance sheet cash",
                  f"=SUMPRODUCT(ABS({rng('cash_check')}))")
        add_check(wb, f"{tag}: net income on the cash flow equals the income statement",
                  f"=SUMPRODUCT(ABS({rng('cf_ni')}-{rng('net_income')}))")
        add_check(wb, f"{tag}: revolver and PP&E never negative",
                  f"=SUMPRODUCT(--({rng('rev_end', A0)}<0))+SUMPRODUCT(--({rng('ppe_end', A0)}<0))")
    if ws.freeze_panes is None or ws.freeze_panes == "B4":
        freeze(ws, f"B{hdr + 1}")
    ws.column_dimensions["A"].width = max(ws.column_dimensions["A"].width or 0, 50)
    for c in allc:
        ws.column_dimensions[c].width = max(ws.column_dimensions[c].width or 0, 13)
    return {"sheet": ws.title, "header_row": hdr, "rows": rows, "cols": cols, "actual_col": A0, "labels": labels,
            "last_actual": last_actual, "names": N, "scenario": scenario, "prefix": prefix,
            "next_row": r + 1}


DCF_STMT_NAMES = {"rf": "risk_free", "beta_u": "beta_unlevered", "erp": "equity_risk_premium", "kd": "cost_of_debt",
                  "debt_weight": "debt_weight", "g": "terminal_growth", "exit_multiple": "exit_multiple",
                  "mid_year": "mid_year", "shares": "diluted_shares", "wacc_step": "wacc_step", "g_step": "g_step",
                  "exit_step": None, "other_claims": None, "size_premium": None, "minorities": None,
                  "preferred": None, "leases": None, "pensions": None, "investments": None}


def dcf_from_statements(ws, row, model, names=None, prefix="dcf_", title_text="DCF") -> dict:
    """Unlevered DCF whose free cash flow is read from a three_statement model (live links, same year columns).

    UFCF = EBITDA + tax on EBIT (unlevered, none on losses) + capex + change in working capital, all from the
    statements. Valuation date = end of the last actual year; net debt from the opening balance sheet (term debt +
    revolver - cash). Needs named inputs (names= overrides DCF_STMT_NAMES): risk_free, beta_unlevered,
    equity_risk_premium, cost_of_debt, debt_weight, terminal_growth, exit_multiple, mid_year (1 = mid-year
    discounting), diluted_shares, wacc_step, g_step; optional exit_step (adds a WACC x exit multiple grid),
    other_claims, size_premium. Tax rate comes from the model. Names: <prefix>wacc, ev, ev_exit, tv_share, equity,
    per_share, per_share_exit (same as dcf_block)."""
    wb = ws.parent
    fix = _resolver(wb, model.get("scenario"))
    M = model["names"]
    N = {**DCF_STMT_NAMES, **(names or {})}
    if N.get("exit_step") is None and "exit_step" in _names_in(wb):
        N["exit_step"] = "exit_step"
    need = [v for k, v in N.items() if v and k not in ("other_claims", "size_premium", "exit_step", "minorities",
                                                         "preferred", "leases", "pensions", "investments")]
    missing = [n for n in need if n.lower() not in _names_in(wb)]
    if missing:
        raise ValueError(f"dcf_from_statements: define these named inputs first: {', '.join(missing)}")
    N = {k: (fix(v) if v else v) for k, v in N.items()}
    N["tax"] = fix(M["tax"])
    N["debt"] = f"({fix(M['term_debt_open'])}+{fix(M['revolver_open'])})"
    N["cash"] = fix(M["cash_open"])
    msh = model["sheet"]
    mrow = model["rows"]
    cols = model["cols"]
    labels = model["labels"]
    width = len(cols) + 2
    rows: dict[str, int] = {}
    r = row
    section(ws, r, f"{title_text}: unlevered free cash flow from the statements", width)
    hdr = r + 1
    header(ws, hdr, ["Line item", "Valuation date"] + labels)
    r = hdr + 1
    same = msh == ws.title
    ref = lambda k, c: f"{c}{mrow[k]}" if same else f"'{msh}'!{c}{mrow[k]}"  # noqa: E731

    def line(key, label, fn, fmt="usd", total=False):
        nonlocal r
        rows[key] = r
        ws.cell(row=r, column=1, value=label)
        for c in cols:
            e = fn(c)
            _f(ws, f"{c}{r}", e if e.startswith("=") else "=" + e, fmt, total=total)
        r += 1
    line("t", "Forecast year (number)", lambda c: ref("t", c), "int")
    line("revenue", "Revenue ($m)", lambda c: ref("revenue", c))
    line("ebitda", "EBITDA ($m)", lambda c: ref("ebitda", c), total=True)
    line("da", "Depreciation and amortisation ($m, negative)", lambda c: ref("da", c))
    line("ebit", "EBIT ($m)", lambda c: f"{c}{rows['ebitda']}+{c}{rows['da']}")
    line("tax", "Tax on EBIT, unlevered ($m, negative; none on losses)",
         lambda c: f"-MAX(0,{c}{rows['ebit']})*{N['tax']}")
    line("capex", "Capital expenditure ($m, negative)", lambda c: ref("cf_capex", c))
    line("nwc", "(Increase) / decrease in working capital ($m)", lambda c: ref("cf_nwc", c))
    line("fcf", "Unlevered free cash flow ($m)",
         lambda c: f"{c}{rows['ebitda']}+{c}{rows['tax']}+{c}{rows['capex']}+{c}{rows['nwc']}", total=True)
    line("dp", "Discount period (years, mid-year convention)", lambda c: f"{c}{rows['t']}-{N['mid_year']}/2", "num")
    line("df", "Discount factor (x)", lambda c: f"1/(1+{prefix}wacc)^{c}{rows['dp']}", "0.0000;(0.0000)")
    line("pv", "PV of free cash flow ($m)", lambda c: f"{c}{rows['fcf']}*{c}{rows['df']}")
    out = _dcf_tail(ws, r, hdr, rows, cols, N, prefix, width, title_text, extra_grid=True)
    out["scenario"] = model.get("scenario")
    return out


def scenario_summary(ws, row, model_args: dict, dcf_args: dict | None = None, scenarios=SCENARIOS,
                     live_prefix: str | None = "dcf_", title_text="Scenario summary") -> dict:
    """Builds one pinned copy of the model and DCF per case (tabs "Base case", "Bull case", "Bear case", placed
    before Checks) and a side-by-side table on ws. model_args: the keyword arguments you passed to three_statement
    (years, segments, opex, capex_growth, names, last_actual); dcf_args: those of dcf_from_statements (names).
    Adds a check that the live model equals the pinned copy of the selected case. Names per case:
    <case>_dcf_per_share, <case>_dcf_ev, <case>_dcf_tv_share, <case>_ts_revenue_final, ..."""
    wb = ws.parent
    dcf_args = dict(dcf_args or {})
    dcf_args.pop("prefix", None)
    model_args = {k: v for k, v in model_args.items() if k not in ("scenario", "prefix", "title_text")}
    built = {}
    for s in scenarios:
        nm_ = f"{s.capitalize()} case"
        sh = wb[nm_] if nm_ in wb.sheetnames else wb.create_sheet(
            nm_, index=wb.sheetnames.index("Checks") if "Checks" in wb.sheetnames else None)
        sh.sheet_view.showGridLines = False
        sh.sheet_properties.tabColor = TAB_COLORS.get("Calc")
        title(sh, f"{nm_}: statements and DCF pinned to the {s} inputs")
        m = three_statement(sh, 3, scenario=s, prefix=f"{s}_ts_", title_text="Three-statement model", **model_args)
        d = dcf_from_statements(sh, m["next_row"] + 1, m, prefix=f"{s}_dcf_", title_text=f"DCF ({s})", **dcf_args)
        built[s] = (m, d)
    section(ws, row, title_text, 9)
    hdr = row + 1
    cols = ["Final-year revenue ($m)", "Final-year EBITDA margin (%)", "Enterprise value ($m)", "Equity value ($m)",
            "Value per share, Gordon ($)", "Value per share, exit multiple ($)", "Terminal value share of EV (%)",
            "Largest cash shortfall ($m)"]
    header(ws, hdr, ["Case"] + cols)
    keys = [("ts_revenue_final", "usd"), ("ts_ebitda_margin_final", "pct"), ("dcf_ev", "usd"), ("dcf_equity", "usd"),
            ("dcf_per_share", "usd2"), ("dcf_per_share_exit", "usd2"), ("dcf_tv_share", "pct"),
            ("ts_max_shortfall", "usd")]
    r = hdr + 1
    first = r
    for s in scenarios:
        ws.cell(row=r, column=1, value=f"{s.capitalize()} case")
        for j, (k, fmt) in enumerate(keys):
            _f(ws, f"{get_column_letter(2 + j)}{r}", f"={s}_{k}", fmt)
        define_name(wb, f"{s}_value_per_share", _abs(ws, f"F{r}"))
        r += 1
    out = {"first_row": first, "last_row": r - 1, "sheets": {s: built[s][0]["sheet"] for s in scenarios},
           "models": built}
    if live_prefix and f"{live_prefix}per_share" in _names_in(wb) and tuple(scenarios) == SCENARIOS:
        ws.cell(row=r, column=1, value="Selected case in the live model").font = Font(name=FONT, bold=True)
        _f(ws, f"F{r}", f"={live_prefix}per_share", "usd2")
        _f(ws, f"G{r}", f"={live_prefix}per_share_exit", "usd2")
        out["live_row"] = r
        if "Checks" in wb.sheetnames:
            add_check(wb, "Live model equals the pinned copy of the selected case (value per share)",
                      f"={live_prefix}per_share-CHOOSE(scenario,base_dcf_per_share,bull_dcf_per_share,"
                      f"bear_dcf_per_share)")
        r += 1
    ws.column_dimensions["A"].width = max(ws.column_dimensions["A"].width or 0, 36)
    for j in range(len(cols)):
        L = get_column_letter(2 + j)
        ws.column_dimensions[L].width = max(ws.column_dimensions[L].width or 0, 16)
    out["next_row"] = r + 1
    return out


# --------------------------------------------------------------------------- valuation method selection and weighting
METHODS = {
    "dcf": "Discounted cash flow (from the statements)",
    "trading_comps": "Trading comparables",
    "precedents": "Precedent transactions",
    "sotp": "Sum of the parts",
    "nav": "Net asset value / asset-based",
    "ddm": "Dividend discount",
    "excess_return": "Excess return (residual income)",
    "lbo": "LBO (what a financial sponsor could pay)",
}
FACTORS = [("data_quality", "Data quality"), ("comparability", "Comparability"),
           ("tv", "Terminal-value dependence"), ("stage", "Stage fit"), ("context", "Control / minority fit")]
FINANCIALS = ("bank", "insurance")
ASSET_HEAVY = ("reit", "asset_heavy", "holding", "bank", "insurance")
STAGE_FIT = {
    "early": {"dcf": .6, "trading_comps": .8, "precedents": .6, "sotp": .5, "nav": .3, "ddm": .2, "excess_return": .5,
              "lbo": .2},
    "growth": {"dcf": .9, "trading_comps": 1, "precedents": .8, "sotp": .8, "nav": .4, "ddm": .5, "excess_return": .8,
               "lbo": .5},
    "mature": {"dcf": 1, "trading_comps": 1, "precedents": 1, "sotp": 1, "nav": .8, "ddm": 1, "excess_return": 1,
               "lbo": 1},
    "distressed": {"dcf": .5, "trading_comps": .5, "precedents": .5, "sotp": .6, "nav": 1, "ddm": .2,
                   "excess_return": .5, "lbo": .4},
}
STAGE_WHY = {"early": "early stage: cash flows are far out and uncertain, multiples rest on revenue",
             "growth": "growth stage: drivers are observable but margins are not yet at steady state",
             "mature": "mature: cash flows, peers and deals are all informative",
             "distressed": "distressed: value rests on assets and recovery more than on going-concern cash flows"}
CONTEXT_FIT = {
    "control": {"dcf": 1, "trading_comps": .8, "precedents": 1, "sotp": 1, "nav": 1, "ddm": 1, "excess_return": 1,
                "lbo": 1},
    "minority": {"dcf": 1, "trading_comps": 1, "precedents": .5, "sotp": .9, "nav": .8, "ddm": 1, "excess_return": 1,
                 "lbo": .3},
}
CONTEXT_WHY = {
    ("control", "trading_comps"): "share prices are minority prices; a buyer of control pays a premium over them",
    ("control", "precedents"): "deal multiples include the control premium a buyer pays",
    ("minority", "precedents"): "deal multiples include a control premium a minority holder does not get",
    ("minority", "lbo"): "a minority holder cannot lever up or sell the company",
    ("minority", "nav"): "a minority holder cannot realise the assets",
}
COMPARABILITY = {"high": 1.0, "medium": .7, "low": .4}


def select_methods(profile: dict) -> dict:
    """Rules deciding which valuation methods fit a company, and the default score of each weighting factor.

    profile keys (all optional): sector general|bank|insurance|reit|asset_heavy|holding; stage early|growth|mature|
    distressed; context minority|control; drivers_traceable (cash flows tie to observable drivers); positive_ebitda;
    n_peers, peer_comparability high|medium|low; n_precedents, precedent_comparability, precedents_recent (most
    within ~5 years); distinct_segments (business lines with different economics and peers); sponsor_relevant;
    pays_dividends; reliable_asset_values (appraisals or marks); tv_share (estimate of the DCF terminal value share,
    used only to pre-fill the weights; the workbook recomputes it live).
    Returns {method: {"applies", "role" (primary | cross-check | floor | not used), "reason",
    "factors": {factor: (score, why)}}}."""
    p = {"sector": "general", "stage": "mature", "context": "minority", "drivers_traceable": True,
         "positive_ebitda": True, "n_peers": 0, "peer_comparability": "medium", "n_precedents": 0,
         "precedent_comparability": "medium", "precedents_recent": True, "distinct_segments": 1,
         "sponsor_relevant": False, "pays_dividends": False, "reliable_asset_values": False, **profile}
    sector, stage, ctx = p["sector"], p["stage"], p["context"]
    for k, ok in (("stage", STAGE_FIT), ("context", CONTEXT_FIT), ("peer_comparability", COMPARABILITY),
                  ("precedent_comparability", COMPARABILITY)):
        if p[k] not in ok:
            raise ValueError(f"select_methods: {k} must be one of {list(ok)}, not {p[k]!r}")
    fin = sector in FINANCIALS
    out = {}

    def put(k, applies, role, reason):
        out[k] = {"applies": applies, "role": role if applies else "not used", "reason": reason}

    if fin:
        put("dcf", False, "", "Banks and insurers: debt is raw material, not financing, so unlevered free cash flow "
            "and WACC are not meaningful; value equity directly (dividend discount, excess return)")
    elif not p["drivers_traceable"]:
        put("dcf", False, "", "Cash flows cannot be tied to observable drivers (volumes, capacity, prices, "
            "customers); a DCF would rest on an assumed growth rate. Rely on market methods")
    else:
        put("dcf", True, "primary", "Cash flows tie to operating drivers that are forecast in the integrated "
            "statements" + ("; near-term cash flow is negative, so watch the terminal value share"
                            if not p["positive_ebitda"] else ""))
    n = p["n_peers"]
    if n < 3:
        put("trading_comps", False, "", f"Only {n} listed peers with similar economics; fewer than 3 gives no "
            "meaningful range")
    else:
        basis = ("P/E and price / tangible book" if fin else
                 "EV / revenue (EBITDA is negative or not yet representative)" if not p["positive_ebitda"]
                 else "EV / EBITDA")
        put("trading_comps", True, "primary", f"{n} listed peers; use {basis}, median and interquartile range")
    n = p["n_precedents"]
    if n < 3:
        put("precedents", False, "", f"Only {n} comparable transactions; fewer than 3 gives no meaningful range")
    else:
        put("precedents", True, "primary" if ctx == "control" else "cross-check",
            f"{n} comparable transactions" + ("" if ctx == "control" else
                                               "; prices include a control premium, so a cross-check here"))
    if p["distinct_segments"] >= 2:
        put("sotp", True, "primary", f"{p['distinct_segments']} business lines with different growth, margins and "
            "peer multiples; value each on its own peers and add them up")
    else:
        put("sotp", False, "", "One business line; a sum of the parts adds nothing")
    if sector in ASSET_HEAVY or stage == "distressed" or p["reliable_asset_values"]:
        put("nav", True, "primary" if sector in ("reit", "asset_heavy", "holding") or stage == "distressed"
            else "cross-check", "Value sits in separable assets with observable values (" +
            ("distressed: recovery value" if stage == "distressed" else
             {"reit": "property appraisals", "asset_heavy": "reserves, fleet or plant valuations",
              "holding": "listed and private holdings", "bank": "tangible book value",
              "insurance": "embedded value / book value"}.get(sector, "appraisals or marks")) + ")")
    else:
        put("nav", False, "", "Value comes from operating cash flows, not separable assets")
    if fin and p["pays_dividends"]:
        put("ddm", True, "primary", "Regulated capital caps what can be paid out; dividends are the cash equity "
            "holders receive")
    else:
        put("ddm", False, "", "Not a bank or insurer paying regular dividends" if not fin else
            "Pays no regular dividend")
    if fin:
        put("excess_return", True, "primary", "Value = book equity + present value of (return on equity - cost of "
            "equity) x book equity; ties value to regulatory capital")
    else:
        put("excess_return", False, "", "Used for banks and insurers, where book equity is the operating capital")
    if fin:
        put("lbo", False, "", "Financial institutions are not leveraged-buyout candidates")
    elif p["sponsor_relevant"]:
        put("lbo", True, "floor", "A financial sponsor could buy it (stable cash flow, debt capacity): what it can "
            "pay at target leverage and return is a floor in a sale; shown, not weighted")
    else:
        put("lbo", False, "", "Not a realistic sponsor target (cash flow, size or leverage capacity)")

    tvs = p.get("tv_share")
    for k, m in out.items():
        f = {}
        if k == "dcf":
            f["data_quality"] = (.9, "drivers sourced in the assumption book") if p.get("drivers_sourced", True) \
                else (.6, "drivers partly judgement")
        elif k == "trading_comps":
            f["data_quality"] = (min(1.0, p["n_peers"] / 6), f"{p['n_peers']} peers (6 or more scores 1.0)")
        elif k == "precedents":
            rec = 1.0 if p["precedents_recent"] else .6
            f["data_quality"] = (round(min(1.0, p["n_precedents"] / 6) * rec, 4),
                                 f"{p['n_precedents']} deals" + ("" if p["precedents_recent"] else
                                                                 ", mostly older than 5 years (x0.6)"))
        elif k == "nav":
            f["data_quality"] = (.9, "appraised or marked asset values") if p["reliable_asset_values"] or \
                sector in ("reit", "holding") else (.6, "book values, no recent appraisal")
        else:
            f["data_quality"] = (.8, "segment / regulatory disclosures are adequate")
        if k == "trading_comps":
            f["comparability"] = (COMPARABILITY[p["peer_comparability"]], f"{p['peer_comparability']} similarity of "
                                  "peers' business mix, growth and margins")
        elif k == "precedents":
            f["comparability"] = (COMPARABILITY[p["precedent_comparability"]], f"{p['precedent_comparability']} "
                                  "similarity of targets and deal conditions")
        else:
            f["comparability"] = (1.0, "not a peer-based method")
        if k == "dcf":
            f["tv"] = (None, "live: 1.0 when terminal value is at or below the threshold share of EV, falling to the "
                       "floor as it approaches 100%")
        else:
            f["tv"] = (1.0, "value does not hinge on a DCF terminal value")
        f["stage"] = (STAGE_FIT[stage][k], STAGE_WHY[stage])
        f["context"] = (CONTEXT_FIT[ctx][k], CONTEXT_WHY.get((ctx, k), f"{ctx} context: no adjustment"))
        m["factors"] = f
    out["_profile"] = p
    out["_tv_share_estimate"] = tvs
    return out


def tv_factor(tv_share: float, ok: float = .6, floor: float = .2) -> float:
    """DCF terminal-value dependence factor: 1 at or below `ok`, linear down to `floor` at 100%."""
    return max(floor, min(1.0, 1 - (tv_share - ok) / (1 - ok) * (1 - floor)))


def rule_weights(sel: dict, methods: list[str], tv_share: float | None = None, ok=.6, floor=.2) -> dict:
    """Normalised product of factor scores for the weighted methods (floors excluded)."""
    score = {}
    for k in methods:
        m = sel[k]
        if not m["applies"] or m["role"] == "floor":
            continue
        s = 1.0
        for fk, (v, _) in m["factors"].items():
            s *= tv_factor(tv_share if tv_share is not None else ok, ok, floor) if fk == "tv" and v is None else v
        score[k] = s
    tot = sum(score.values())
    return {k: (v / tot if tot else 0.0) for k, v in score.items()}


def _round_weights(w: dict, step: float = .05) -> dict:
    if not w:
        return {}
    r = {k: round(v / step) * step for k, v in w.items()}
    big = max(w, key=w.get)
    r[big] = round(r[big] + 1 - sum(r.values()), 10)
    return {k: round(v, 10) for k, v in r.items()}


def valuation_summary(ws, row, methods, profile, marker=None, tv_share_name="dcf_tv_share", tv_ok=.6, tv_floor=.2,
                      title_text="Valuation summary") -> dict:
    """Decide which methods fit, weight the ones that do, and show the weighted value per share and a football
    field. methods: [{"key": "dcf" | "trading_comps" | "precedents" | "sotp" | "nav" | "ddm" | "excess_return" |
    "lbo", "label": optional, "low": "=formula", "high": "=formula", "mid": optional "=formula" (default the
    average)}] in value per share. profile: see select_methods. marker: {"label", "value"} for the football field.

    Writes: (1) every method with fits / role / why; (2) the weighting table: the factor scores (blue, editable, each
    with its reason), their product, the rule-based weight (live; the DCF terminal-value factor reads tv_share_name),
    the weight used (blue input, pre-filled with the rule-based weight rounded to 5%) and the justification;
    (3) weighted value per share low / mid / high (names weighted_value_low, weighted_value_per_share,
    weighted_value_high); (4) a football field with the weighted range and the marker. Methods the rules exclude are
    listed with the reason and never enter the weighting; an LBO is shown as a floor, not weighted. Check: weights
    used sum to 100%. A method the rules say fits but that was not built is listed as a gap."""
    wb = ws.parent
    sel = select_methods(profile)
    given = {m["key"]: m for m in methods}
    bad = [k for k in given if k not in METHODS]
    if bad:
        raise ValueError(f"valuation_summary: unknown methods {bad}; use {list(METHODS)}")
    have = _names_in(wb)
    tvs_live = tv_share_name if tv_share_name and tv_share_name.lower() in have else None
    r = row
    section(ws, r, f"{title_text}: which methods fit, and why", 4)
    r += 1
    header(ws, r, ["Method", "Fits?", "Role", "Why"])
    r += 1
    gaps = []
    for k, lab in METHODS.items():
        m = sel[k]
        built = k in given
        fits = "Yes" if m["applies"] else "No"
        if m["applies"] and not built:
            fits = "Yes, not built"
            gaps.append(k)
        if not m["applies"] and built:
            fits = "No (built, not weighted)"
        ws.cell(row=r, column=1, value=given.get(k, {}).get("label") or lab)
        ws.cell(row=r, column=2, value=fits)
        ws.cell(row=r, column=3, value=m["role"])
        ws.cell(row=r, column=4, value=m["reason"]).alignment = Alignment(wrap_text=True, vertical="top")
        r += 1
    r += 1
    section(ws, r, "Weighting parameters", 4)
    r += 1
    header(ws, r, ["Parameter", "Value", "Source / justification"])
    r += 1
    ws.cell(row=r, column=1, value="Terminal value share of EV above which the DCF weight falls (%)")
    input_cell(ws, f"B{r}", tv_ok, "pct", name="val_tv_ok")
    ws.cell(row=r, column=3, value="Judgement: beyond ~60% of EV in the terminal value, the answer mostly rests on "
            "the growth / exit assumption (Damodaran; McKinsey Valuation, continuing value)")
    r += 1
    ws.cell(row=r, column=1, value="Lowest DCF terminal-value factor (x)")
    input_cell(ws, f"B{r}", tv_floor, "0.00;(0.00)", name="val_tv_floor")
    ws.cell(row=r, column=3, value="Judgement: a DCF keeps some weight while its drivers are sourced")
    r += 2

    weighted = [k for k in METHODS if k in given and sel[k]["applies"] and sel[k]["role"] != "floor"]
    if not weighted:
        raise ValueError("valuation_summary: none of the methods given fits this profile; see select_methods()")
    tv_est = sel["_tv_share_estimate"]
    pre = _round_weights(rule_weights(sel, weighted, tv_est, tv_ok, tv_floor))
    section(ws, r, "Weighting: factor scores (0 to 1, editable), rule-based weight and weight used", 14)
    r += 1
    hdr = r
    header(ws, hdr, ["Method", "Low ($/share)", "High ($/share)", "Mid ($/share)"] + [f[1] for f in FACTORS]
           + ["Score (product)", "Rule-based weight (%)", "Weight used (%)", "Weighted mid ($/share)",
              "Source / justification"])
    r += 1
    first = r
    for k in weighted:
        m, g = sel[k], given[k]
        ws.cell(row=r, column=1, value=g.get("label") or METHODS[k])
        _f(ws, f"B{r}", g["low"], "usd2")
        _f(ws, f"C{r}", g["high"], "usd2")
        _f(ws, f"D{r}", g.get("mid") or f"=AVERAGE(B{r}:C{r})", "usd2")
        why = []
        for j, (fk, flab) in enumerate(FACTORS):
            c = get_column_letter(5 + j)
            v, reason = m["factors"][fk]
            if fk == "tv" and v is None:
                if tvs_live:
                    _f(ws, f"{c}{r}", f"=MAX(val_tv_floor,MIN(1,1-({tvs_live}-val_tv_ok)/(1-val_tv_ok)*"
                                      f"(1-val_tv_floor)))", "0.00;(0.00)")
                else:
                    input_cell(ws, f"{c}{r}", tv_factor(tv_est if tv_est is not None else tv_ok, tv_ok, tv_floor),
                               "0.00;(0.00)")
                    reason = "no live terminal value share found; estimate used"
            else:
                input_cell(ws, f"{c}{r}", v, "0.00;(0.00)")
            why.append(f"{flab}: {reason}")
        _f(ws, f"J{r}", f"=PRODUCT(E{r}:I{r})", "0.000;(0.000)")
        input_cell(ws, f"L{r}", pre[k], "pct")
        _f(ws, f"M{r}", f"=L{r}*D{r}", "usd2")
        ws.cell(row=r, column=14, value=f"{m['reason']}. " + "; ".join(why)).alignment = Alignment(
            wrap_text=True, vertical="top")
        r += 1
    last = r - 1
    for rr in range(first, last + 1):
        _f(ws, f"K{rr}", f"=IF(SUM($J${first}:$J${last})=0,0,J{rr}/SUM($J${first}:$J${last}))", "pct")
    ws.cell(row=r, column=1, value="Total").font = Font(name=FONT, bold=True)
    _f(ws, f"K{r}", f"=SUM(K{first}:K{last})", "pct", total=True)
    _f(ws, f"L{r}", f"=SUM(L{first}:L{last})", "pct", total=True)
    define_name(wb, "valuation_weights_total", _abs(ws, f"L{r}"))
    tot_row = r
    r += 2
    W = f"$L${first}:$L${last}"
    res = {}
    for key, lab, col in (("weighted_value_low", "Weighted value per share, low ($)", "B"),
                          ("weighted_value_per_share", "Weighted value per share, mid ($)", "D"),
                          ("weighted_value_high", "Weighted value per share, high ($)", "C")):
        ws.cell(row=r, column=1, value=lab).font = Font(name=FONT, bold=True)
        _f(ws, f"B{r}", f"=SUMPRODUCT({W},${col}${first}:${col}${last})", "usd2", total=True)
        define_name(wb, key, _abs(ws, f"B{r}"))
        res[key] = f"'{ws.title}'!B{r}"
        r += 1
    ws.cell(row=r, column=1, value="Largest gap between weight used and rule-based weight (%)")
    _f(ws, f"B{r}", f"=SUMPRODUCT(MAX(ABS(L{first}:L{last}-K{first}:K{last})))", "pct")
    r += 1
    floors = [k for k in given if sel[k]["applies"] and sel[k]["role"] == "floor"]
    for k in floors:
        ws.cell(row=r, column=1, value=f"Weighted value minus {METHODS[k]} low ($/share)")
        lo = given[k]["low"].lstrip("=")
        _f(ws, f"B{r}", f"=weighted_value_per_share-({lo})", "usd2")
        r += 1
    if gaps:
        ws.cell(row=r, column=1, value="Methods that fit but were not built: " + ", ".join(METHODS[k] for k in gaps)
                ).font = Font(name=FONT, bold=True, color="C00000")
        r += 1
    if "Checks" in wb.sheetnames:
        add_check(wb, "Valuation weights used sum to 100%", f"='{ws.title}'!L{tot_row}-1")
    r += 1
    ff_methods = [{"label": ws.cell(row=rr, column=1).value, "low": f"=B{rr}", "high": f"=C{rr}"}
                  for rr in range(first, last + 1)]
    ff_methods += [{"label": given[k].get("label") or METHODS[k], "low": given[k]["low"], "high": given[k]["high"]}
                   for k in floors]
    ff_methods.append({"label": "Weighted value (low to high)", "low": "=weighted_value_low",
                       "high": "=weighted_value_high"})
    ff = football_field(ws, r, ff_methods, marker=marker, title_text=f"{title_text}: football field",
                        anchor=f"F{r}")
    for c, w in zip("ABCDEFGHIJKLMN", (44, 14, 14, 14, 12, 13, 13, 11, 12, 12, 14, 13, 14, 80)):
        ws.column_dimensions[c].width = max(ws.column_dimensions[c].width or 0, w)
    return {"selection": {k: v for k, v in sel.items() if not k.startswith("_")}, "weighted": weighted,
            "prefilled_weights": pre, "first_row": first, "last_row": last, "total_row": tot_row, "gaps": gaps,
            "floors": floors, "football_field": ff, "names": res, "next_row": ff["next_row"]}


# --------------------------------------------------------------------------- share count and model alerts
def diluted_shares(ws, row, basic="basic_shares", price="share_price", options=(), rsus=None, converts=(),
                   name="diluted_shares", title_text="Diluted shares (treasury stock method)") -> dict:
    """Fully diluted share count from named inputs: basic shares + in-the-money options under the treasury stock
    method (n x (1 - strike / price)) + RSUs + convertibles that are in the money (if-converted). options:
    [(count_name, strike_name)], converts: [(shares_name, conversion_price_name)]. Defines `name`."""
    wb = ws.parent
    section(ws, row, title_text, 3)
    r = row + 1
    header(ws, r, ["Component", "Shares (m)", "Note"])
    r += 1
    first = r
    ws.cell(row=r, column=1, value="Basic shares outstanding (m)")
    _f(ws, f"B{r}", f"={basic}", "num")
    r += 1
    for i, (n_, k_) in enumerate(options, 1):
        ws.cell(row=r, column=1, value=f"Options tranche {i}: net new shares (m)")
        _f(ws, f"B{r}", f"=IF({price}>{k_},{n_}*(1-{k_}/{price}),0)", "num")
        ws.cell(row=r, column=3, value="Treasury stock method: exercise proceeds buy back shares at the price")
        r += 1
    if rsus:
        ws.cell(row=r, column=1, value="Restricted stock units (m)")
        _f(ws, f"B{r}", f"={rsus}", "num")
        r += 1
    for i, (sh_, cp_) in enumerate(converts, 1):
        ws.cell(row=r, column=1, value=f"Convertible {i}: shares if converted (m)")
        _f(ws, f"B{r}", f"=IF({price}>{cp_},{sh_},0)", "num")
        ws.cell(row=r, column=3, value="Counted only when in the money (dilutive)")
        r += 1
    ws.cell(row=r, column=1, value="Diluted shares (m)").font = Font(name=FONT, bold=True)
    _f(ws, f"B{r}", f"=SUM(B{first}:B{r - 1})", "num", total=True)
    define_name(wb, name, _abs(ws, f"B{r}"))
    return {"cell": f"'{ws.title}'!B{r}", "next_row": r + 2}


def alerts(wb, model: dict, dcf: dict | None = None, sheet: str = "Alerts") -> dict:
    """Banker sanity alerts on the forecast (they warn, they do not fail the master check): revenue growth fades,
    capex covers depreciation while growing, growth and margins rarely rise together, terminal growth at or below
    the risk-free rate, terminal value share of EV, the revolver never short. Each row shows the test and REVIEW or
    OK; thresholds are labelled constants on the Checks tab."""
    ws = wb[sheet] if sheet in wb.sheetnames else wb.create_sheet(
        sheet, index=wb.sheetnames.index("Checks") if "Checks" in wb.sheetnames else None)
    ws.sheet_view.showGridLines = False
    title(ws, "Model alerts: tests a reviewer would run (REVIEW = look again; they do not fail the model)")
    header(ws, 3, ["Alert", "Measured", "Status", "Why it matters"])
    msh, rows, cols = model["sheet"], model["rows"], model["cols"]
    F0, L = cols[0], cols[-1]
    rng = lambda k, a=F0, b=L: f"'{msh}'!{a}{rows[k]}:{b}{rows[k]}"  # noqa: E731
    cell = lambda k, c: f"'{msh}'!{c}{rows[k]}"  # noqa: E731
    tv_max = model_constant(wb, "alert_tv_share_max", "Alert: terminal value share of EV above (%)", 0.75, "pct")
    r = 4
    out = {}

    def add(key, label, measured, ok_expr, why, fmt="num"):
        nonlocal r
        ws.cell(row=r, column=1, value=label)
        _f(ws, f"B{r}", measured, fmt)
        _f(ws, f"C{r}", f'=IF({ok_expr.format(v=f"B{r}")},"OK","REVIEW")')
        ws.cell(row=r, column=4, value=why)
        out[key] = f"'{sheet}'!C{r}"
        r += 1
    add("growth_fades", "Final-year revenue growth minus first-year growth (pp)",
        f"={cell('growth', L)}-{cell('growth', F0)}", "{v}<=0",
        "Growth should generally fade toward a steady state (BIWS)", "pct")
    add("capex_vs_dep", "Forecast years where additions are below depreciation while revenue grows (#)",
        f"=SUMPRODUCT(--({rng('additions')}<{rng('dep')}),--({rng('growth')}>0))", "{v}=0",
        "A growing business usually invests more than it depreciates", "int")
    if len(cols) > 1:
        add("growth_and_margin", "Years where revenue growth and EBITDA margin both rise (#)",
            f"=SUMPRODUCT(--({rng('growth', cols[1], L)}>{rng('growth', F0, cols[-2])}),"
            f"--({rng('ebitda_margin', cols[1], L)}>{rng('ebitda_margin', F0, cols[-2])}))", "{v}=0",
            "Growth and margins rarely rise at the same time; check the drivers", "int")
    add("shortfall", "Largest cash shortfall below minimum after the revolver ($m)",
        f"=MAX({rng('shortfall')})", "{v}=0", "The plan needs more funding than the revolver provides", "usd")
    if dcf:
        p = dcf.get("names", {}).get("tv_share", "dcf_tv_share").rsplit("tv_share", 1)[0]
        add("tv_share", "Terminal value share of enterprise value (%)", f"={p}tv_share", f"{{v}}<={tv_max}",
            "Most of the value rests on the terminal assumptions; extend the forecast or cross-check", "pct")
        dr, dn = dcf["rows"], dcf["sheet_name"]
        add("g_vs_rf", "Terminal growth minus the risk-free rate (pp)", f"='{dn}'!B{dr['g']}-'{dn}'!B{dr['rf']}",
            "{v}<=0", "Long-run growth above the risk-free rate implies outgrowing the economy forever (Damodaran)",
            "pct")
    ws.column_dimensions["A"].width = 70
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 10
    ws.column_dimensions["D"].width = 70
    return {"sheet": sheet, "status": out}
