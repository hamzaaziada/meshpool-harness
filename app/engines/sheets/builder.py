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
FMT = {"usd": '$#,##0;($#,##0);"-"', "usd2": '$#,##0.00', "pct": '0.0%', "int": '#,##0', "x": '0.0"x"',
       "num": '#,##0.0'}


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
        col, row = c.column_letter, c.row
        dn = DefinedName(name, attr_text=f"'{ws.title}'!${col}${row}")
        try:
            ws.parent.defined_names[name] = dn
        except TypeError:
            ws.parent.defined_names.append(dn)
    return c


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
        dn = DefinedName("MasterCheck", attr_text="'Checks'!$F$3")
        try:
            wb.defined_names["MasterCheck"] = dn
        except TypeError:
            wb.defined_names.append(dn)
    return f"Checks!C{r}"
