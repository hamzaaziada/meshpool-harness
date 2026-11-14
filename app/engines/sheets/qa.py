"""Workbook QA: LibreOffice recalculation, error scan, circular refs, hardcoded numbers, structure."""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from openpyxl import load_workbook

ERRORS = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!", "Err:", "#ERR")
CALC_SHEETS = re.compile(r"calc|model|output|summary|dashboard|p&l|pnl|forecast|valuation", re.I)
INPUT_SHEETS = re.compile(r"input|assumption|data|source|raw", re.I)
FINANCE_SHEETS = re.compile(r"comps|precedent|dcf|valuation", re.I)


def recalc(xlsx_path: str, out_dir: str | None = None) -> str:
    """Round-trip through LibreOffice headless so every formula gets a computed cached value."""
    from meshpool_harness.config import find_soffice
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice not installed")
    out = Path(out_dir or tempfile.mkdtemp(prefix="recalc_"))
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([soffice, f"-env:UserInstallation={Path(profile).as_uri()}", "--headless", "--calc",
                        "--convert-to", "xlsx:Calc MS Excel 2007 XML", "--outdir", str(out), xlsx_path],
                       check=True, capture_output=True, timeout=180)
    return str(out / Path(xlsx_path).name)


def check(xlsx_path: str, expect_sheets: list[str] | None = None, allow_hardcoded: bool = False) -> dict:
    """Returns {"issues": [...], "stats": {...}}. issues empty == pass."""
    issues = []
    wb_f = load_workbook(xlsx_path, data_only=False)
    try:
        recalced = recalc(xlsx_path)
        wb_v = load_workbook(recalced, data_only=True)
    except RuntimeError as e:
        if "not installed" not in str(e):
            raise
        recalced, wb_v = None, None                  # no LibreOffice: structure checks only
    n_formula = n_numeric_const = 0
    for ws in wb_f.worksheets:
        wv = wb_v[ws.title] if wb_v is not None else None
        is_calc = bool(CALC_SHEETS.search(ws.title)) and not INPUT_SHEETS.search(ws.title)
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if v is None:
                    continue
                cv = wv[c.coordinate].value if wv is not None else None
                if isinstance(v, str) and v.startswith("="):
                    n_formula += 1
                    if cv is None and wv is not None:
                        issues.append({"check": "formula_no_value", "cell": f"{ws.title}!{c.coordinate}",
                                       "detail": v[:80]})
                    if isinstance(cv, str) and cv.startswith(ERRORS):
                        kind = "circular_ref" if "522" in cv or "523" in cv else "formula_error"
                        issues.append({"check": kind, "cell": f"{ws.title}!{c.coordinate}", "detail": f"{cv} <- {v[:80]}"})
                elif isinstance(v, str) and v.strip().startswith(ERRORS):
                    issues.append({"check": "error_literal", "cell": f"{ws.title}!{c.coordinate}", "detail": v})
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    n_numeric_const += 1
                    # numbers typed into calculation/output sheets are the classic hardcode failure
                    is_label = bool(c.font and c.font.bold) or (c.fill is not None and c.fill.fgColor is not None
                                                                and str(c.fill.fgColor.rgb).endswith("1B2333"))
                    if is_calc and not allow_hardcoded and c.column > 1 and not is_label:
                        issues.append({"check": "hardcoded_number", "cell": f"{ws.title}!{c.coordinate}",
                                       "detail": f"{v} typed into a calculation sheet; reference Inputs instead"})
    names = list(wb_f.defined_names.keys()) if hasattr(wb_f.defined_names, "keys") else [d.name for d in wb_f.defined_names.definedName]
    if expect_sheets:
        missing = [s for s in expect_sheets if s not in wb_f.sheetnames]
        if missing:
            issues.append({"check": "missing_sheets", "cell": "", "detail": missing})
    if n_formula == 0:
        issues.append({"check": "no_formulas", "cell": "", "detail": "workbook has no live formulas"})
    return {"issues": issues, "stats": {"sheets": wb_f.sheetnames, "formulas": n_formula,
                                        "numeric_constants": n_numeric_const, "named_ranges": names,
                                        "recalculated_copy": recalced}}


def value(xlsx_path: str, ref: str):
    """Computed value of 'Sheet!A1' or a named range after LibreOffice recalc (for gold-answer checks)."""
    try:
        wb = load_workbook(recalc(xlsx_path), data_only=True)
    except RuntimeError as e:
        if "not installed" in str(e):
            return None                              # no LibreOffice: value unknown, not a failure
        raise
    if "!" in ref:
        sh, cell = ref.split("!", 1)
        return wb[sh.strip("'")][cell.replace("$", "")].value
    dn = wb.defined_names[ref]
    (sh, cell), = list(dn.destinations)
    return wb[sh][cell.replace("$", "")].value


def _blue(c) -> bool:
    col = c.font.color if c.font is not None else None
    return bool(col is not None and col.type == "rgb" and str(col.rgb).upper().endswith("0000FF"))


def missing_sources(xlsx_path: str) -> list[str]:
    """Valuation workbooks (a Comps, Precedents, DCF or Valuation tab): every typed input (blue number) must carry a
    Source in its table's Source column (the assumption book on Inputs; the peer/deal rows on Comps/Precedents).
    Returns one entry per input row without a source, e.g. "Inputs!C7 (Risk-free rate (%))"."""
    wb = load_workbook(xlsx_path)
    if not any(FINANCE_SHEETS.search(s) for s in wb.sheetnames):
        return []
    out = []
    for ws in wb.worksheets:
        if re.search(r"check|cover", ws.title, re.I):
            continue
        is_inputs = bool(INPUT_SHEETS.search(ws.title))
        headers: dict[int, int | None] = {}                 # header row -> Source column (None: table has none)
        for row in ws.iter_rows():
            for c in row:
                if c.fill is not None and c.fill.fgColor is not None and str(c.fill.fgColor.rgb).endswith("1B2333"):
                    headers.setdefault(c.row, None)
                    if isinstance(c.value, str) and c.value.strip().lower().startswith("source"):
                        headers[c.row] = c.column
        done = set()
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if c.row in done or not (isinstance(v, (int, float)) and not isinstance(v, bool)) or not _blue(c):
                    continue
                label = ws.cell(row=c.row, column=1).value or ""
                above = [h for h in headers if h < c.row]
                sc = headers[max(above)] if above else None
                if sc is None:
                    if is_inputs:
                        out.append(f"{ws.title} row {c.row} ({label}): no Source column; use B.assumption_book")
                        done.add(c.row)
                    continue
                s = ws.cell(row=c.row, column=sc).value
                if s is None or not str(s).strip():
                    out.append(f"{ws.title}!{ws.cell(row=c.row, column=sc).coordinate} ({label})")
                done.add(c.row)
    return out


METHOD_FAMILIES = {   # valuation method -> names or sheet titles that show the workbook used it
    "DCF": (r"^(\w+_)?dcf_per_share$", r"^dcf"),
    "trading comps": (r"^comps_", r"^comps|trading comp"),
    "precedent transactions": (r"^prec_", r"^precedent"),
    "sum of the parts": (r"^sotp_", r"^sotp|sum of the parts"),
    "NAV": (r"^nav_", r"^nav\b|net asset"),
    "dividend discount": (r"^ddm_", r"^ddm|dividend discount"),
    "excess return": (r"^(er|excess_return)_", r"^excess return|residual income"),
    "LBO": (r"^lbo_", r"^lbo"),
}


def _labels(ws, col: int = 1) -> list[str]:
    return [str(c.value).strip() for c in ws.iter_cols(min_col=col, max_col=col).__next__() if c.value is not None]


def model_gaps(xlsx_path: str) -> list[str]:
    """Integrated-model and valuation rules the engine enforces before a finance workbook is accepted:
    - a DCF takes its cash flows from integrated statements (a balance sheet in the workbook);
    - integrated statements carry a balance-sheet-balances check and a cash-tie check on the Checks tab;
    - a DCF or integrated model has base / bull / bear cases behind one switch (names scenario, *_bull, *_bear);
    - more than one valuation method needs the weighting table: weights that sum to 100% (a check), a weighted
      value per share, and a justification on every weighted method.
    Returns one message per gap."""
    wb = load_workbook(xlsx_path)
    names = {k.lower() for k in wb.defined_names.keys()}
    titles = [s.lower() for s in wb.sheetnames]
    out = []
    has_bs = any(lab.lower() == "total liabilities and equity" for ws in wb.worksheets for lab in _labels(ws))
    used = [fam for fam, (nre, tre) in METHOD_FAMILIES.items()
            if any(re.search(nre, n) for n in names) or any(re.search(tre, t) for t in titles)]
    dcf = "DCF" in used
    checks = [lab.lower() for lab in _labels(wb["Checks"])] if "Checks" in wb.sheetnames else []
    if dcf and not has_bs:
        out.append("DCF without integrated statements: build the income statement, balance sheet and cash flow with "
                   "B.three_statement (drivers, working-capital days, PP&E roll-forward, debt, equity) and take free "
                   "cash flow from them with B.dcf_from_statements")
    if has_bs:
        if not any("balance sheet balances" in c for c in checks):
            out.append("No balance-sheet-balances check on the Checks tab (total assets minus total liabilities and "
                       "equity, every year)")
        if not any(re.search(r"cash (ties|equals|flow closing cash)|closing cash equals", c) for c in checks):
            out.append("No cash-tie check on the Checks tab (cash flow closing cash equals balance sheet cash)")
    if (dcf or has_bs) and not ("scenario" in names and any(n.endswith("_bull") for n in names)
                                and any(n.endswith("_bear") for n in names)):
        out.append("No scenarios: put the drivers in B.scenario_book (base / bull / bear with one switch named "
                   "scenario) and show all three with B.scenario_summary")
    if len(used) >= 2:
        if "weighted_value_per_share" not in names:
            out.append(f"{len(used)} valuation methods ({', '.join(used)}) but no weighting: use B.valuation_summary "
                       "to decide which methods fit and weight them, with the weights as editable inputs")
        else:
            if not any("weights" in c and "100%" in c for c in checks):
                out.append("No check that the valuation weights sum to 100%")
            out += _weighting_gaps(wb)
    return out


def _weighting_gaps(wb) -> list[str]:
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.value == "Weight used (%)":
                    hdr = c.row
                    jc = next((x.column for x in ws[hdr] if isinstance(x.value, str)
                               and x.value.lower().startswith("source / justification")), None)
                    out = []
                    r = hdr + 1
                    while ws.cell(row=r, column=1).value not in (None, "Total"):
                        j = ws.cell(row=r, column=jc).value if jc else None
                        if not (isinstance(j, str) and len(j.strip()) >= 20):
                            out.append(f"Weighted method '{ws.cell(row=r, column=1).value}' has no justification")
                        r += 1
                    return out
    return ["Weighted value per share found but no weighting table (Weight used (%) column)"]
