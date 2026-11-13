"""Workbook QA: LibreOffice recalculation, error scan, circular refs, hardcoded numbers, structure."""
from __future__ import annotations

import re
import shutil
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
