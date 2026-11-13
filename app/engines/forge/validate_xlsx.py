"""Validate a finished .xlsx against the Excel hard rules X1-X13 (rules.yaml), and measure its anatomy (step 3).

    python -m engines.forge.validate_xlsx model.xlsx [--rules client.yaml] [--recompute recompute.json]
           [--before previous.xlsx] [--no-recalc] [--out report.json] [--text]

The workbook is recalculated headless in LibreOffice first, so every check reads computed values.
Sidecars (written by the engine):
  recompute.json  {"checks": [{"cell": "Outputs!C5", "expected": 1234.5, "label": "2027 revenue"}]}
                  values the engine recomputed independently in code from the same inputs (X7)
Exit code 1 when any error-severity failure remains.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.formula.tokenizer import Token, Tokenizer
from openpyxl.utils.cell import column_index_from_string, coordinate_from_string, get_column_letter, range_boundaries

from .common import evidence_id, load_rules, sha256, soffice_convert, summarize
from .common import failure as F
from .ooxml_check import check as ooxml_check

RULES = [f"X{i}" for i in range(0, 14)]
CELL_RE = re.compile(r"^\$?([A-Z]{1,3})\$?(\d+)$")
YEAR_RE = re.compile(r"^(fy|cy)?\s*'?(\d{2}|\d{4})\s*([aebfp]|act|actual|est|budget|forecast)?$", re.I)


def _role(name: str, wb, R) -> str:
    for dn in _defined_names(wb):
        if dn[0].lower() == f"_role_{name}".lower():
            return str(dn[1]).strip("\"'").lower()
    roles = R["sheet_roles"]
    for role in ("checks", "output", "input", "calc"):
        if re.search(roles[role], name):
            return role
    return "calc"


def _defined_names(wb) -> list[tuple[str, str]]:
    out = []
    try:
        items = wb.defined_names.items()          # openpyxl >= 3.1
    except AttributeError:
        items = [(d.name, d) for d in wb.defined_names.definedName]
    for name, dn in items:
        out.append((name, dn.attr_text))
    for ws in wb.worksheets:
        try:
            for name, dn in ws.defined_names.items():
                out.append((name, dn.attr_text))
        except AttributeError:
            pass
    return out


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _label(ws, row: int) -> str:
    for c in range(1, min(ws.max_column, 6) + 1):
        v = ws.cell(row, c).value
        if isinstance(v, str) and v.strip() and not v.startswith("="):
            return v.strip()
    return ""


def _period_header(ws) -> tuple[int | None, list[int], dict]:
    """Header row with >=3 period labels (years like 2024, 2024A, FY25E). Returns (row, period columns, labels)."""
    for r in range(1, min(ws.max_row, 12) + 1):
        cols, labels = [], {}
        for c in range(1, ws.max_column + 1):
            v = ws.cell(r, c).value
            if v is None:
                continue
            s = str(v).strip()
            if isinstance(v, (int, float)) and not isinstance(v, bool) and 1900 <= v <= 2100 and float(v).is_integer():
                cols.append(c)
                labels[c] = s
            elif isinstance(v, str) and YEAR_RE.match(s):
                cols.append(c)
                labels[c] = s
        if len(cols) >= 3:
            return r, cols, labels
    return None, [], {}


def _refs(formula: str, host_sheet: str, names: dict, wb_sheets: set, cap: int = 4000) -> set[tuple[str, str]]:
    """Cells a formula reads, as (sheet, A1). Defined names are expanded to their destination cells."""
    out = set()
    try:
        toks = Tokenizer(formula).items
    except Exception:
        return out
    for t in toks:
        if t.type != Token.OPERAND or t.subtype != Token.RANGE:
            continue
        val = t.value
        if val.upper() in names:
            val = names[val.upper()]
            if val is None:
                continue
        sheet, _, rng = val.rpartition("!")
        sheet = sheet.strip("'") if sheet else host_sheet
        rng = rng.replace("$", "")
        if sheet not in wb_sheets:
            continue
        try:
            if ":" in rng:
                c0, r0, c1, r1 = range_boundaries(rng)
                if None in (c0, r0, c1, r1) or (c1 - c0 + 1) * (r1 - r0 + 1) > cap:
                    continue
                for r in range(r0, r1 + 1):
                    for c in range(c0, c1 + 1):
                        out.add((sheet, f"{get_column_letter(c)}{r}"))
            elif CELL_RE.match(rng):
                out.add((sheet, rng))
        except Exception:
            continue
    return out


def _r1c1(formula: str, row: int, col: int) -> str:
    """Relative R1C1 form of a formula: equal strings across a row mean one consistent pattern."""
    try:
        toks = Tokenizer(formula).items
    except Exception:
        return formula
    out = []
    for t in toks:
        if t.type == Token.OPERAND and t.subtype == Token.RANGE:
            sheet, bang, rng = t.value.rpartition("!")
            parts = []
            for ref in rng.split(":"):
                m = re.match(r"^(\$?)([A-Z]{1,3})(\$?)(\d+)$", ref)
                if not m:
                    parts.append(ref)
                    continue
                cabs, cl, rabs, rw = m.groups()
                c = column_index_from_string(cl)
                rr = f"R{rw}" if rabs else f"R[{int(rw) - row}]"
                cc = f"C{c}" if cabs else f"C[{c - col}]"
                parts.append(rr + cc)
            out.append(sheet + bang + ":".join(parts))
        else:
            out.append(t.value)
    return "".join(out)


def _literals(formula: str) -> list[float]:
    try:
        toks = Tokenizer(formula).items
    except Exception:
        return []
    out = []
    for t in toks:
        if t.type == Token.OPERAND and t.subtype == Token.NUMBER:
            try:
                out.append(float(t.value))
            except ValueError:
                pass
    return out


def _tarjan(graph: dict) -> list[list]:
    index, low, on, stack, sccs, counter = {}, {}, set(), [], [], [0]
    for root in list(graph):
        if root in index:
            continue
        work = [(root, iter(graph.get(root, ())))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on.add(root)
        while work:
            v, it = work[-1]
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter[0]
                    counter[0] += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(graph.get(w, ()))))
                    break
                elif w in on:
                    low[v] = min(low[v], index[w])
            else:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[v])
                if low[v] == index[v]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on.discard(w)
                        comp.append(w)
                        if w == v:
                            break
                    if len(comp) > 1 or v in graph.get(v, ()):
                        sccs.append(comp)
    return sccs


def _color(cell) -> str | None:
    try:
        c = cell.font.color
        if c is None:
            return None
        if c.type == "rgb":
            return str(c.rgb).upper()[-6:]
        if c.type == "theme":
            return f"theme:{c.theme}"
    except Exception:
        pass
    return None


def recalc(path: str, out_dir: str, timeout: int = 240) -> str:
    return str(soffice_convert(path, "xlsx:Calc MS Excel 2007 XML", out_dir, timeout))


def diff(before: str, after: str) -> list[dict]:
    """Cells whose value or formula changed between two workbooks (X11 before/after report)."""
    a, b = load_workbook(before), load_workbook(after)
    out = []
    for ws in b.worksheets:
        old = a[ws.title] if ws.title in a.sheetnames else None
        for row in ws.iter_rows():
            for c in row:
                ov = old[c.coordinate].value if old is not None else None
                if ov != c.value:
                    out.append({"sheet": ws.title, "cell": c.coordinate, "before": ov, "after": c.value,
                                "overwrote": ov not in (None, "")})
    for name in a.sheetnames:
        if name not in b.sheetnames:
            out.append({"sheet": name, "cell": None, "before": "sheet", "after": None, "overwrote": True})
    return out


def _opens_cleanly(path, failures):
    """X0: the file opens in Office without a repair prompt (schema + cross-part checks, ooxml_check.py)."""
    try:
        r = ooxml_check(path)
    except Exception as e:  # a file we cannot even unzip
        failures.append(F("X0", "package", f"Not a readable Office package: {e}", fix_scope="renderer"))
        return
    for pr in r["problems"][:20]:
        failures.append(F("X0", pr["check"], f"{pr['part']}: {pr['detail']}. Office would offer to repair "
                          "this file.", fix_scope="renderer"))
    if r["schema_validation"] == "not_run":
        failures.append(F("X0", "schema", "Office schemas not installed; only package and relationship checks "
                          "ran (python -m engines.forge.ooxml_check --install-schemas).", status="not_run",
                          severity="info", fix_scope="renderer"))


def validate(path: str, rules: dict | None = None, recompute: dict | None = None, before: str | None = None,
             do_recalc: bool | None = None) -> dict:
    rules = rules or load_rules()
    R = rules["xlsx"]
    file_sha = sha256(path)
    failures: list[dict] = []
    _opens_cleanly(path, failures)
    wb = load_workbook(path, data_only=False)
    sheets = set(wb.sheetnames)
    names_raw = _defined_names(wb)
    names = {}
    for n, dest in names_raw:
        names[n.upper()] = dest if dest and "#REF!" not in str(dest) else None

    # ---------------- recalc
    vals = None
    if do_recalc is None:
        do_recalc = R["recalc"]["enabled"]
    tmp = tempfile.TemporaryDirectory()
    if do_recalc:
        try:
            vals = load_workbook(recalc(path, tmp.name, R["recalc"]["timeout_s"]), data_only=True)
        except Exception as e:
            failures.append(F("X5", "recalc", f"Headless recalculation failed: {e}", fix_scope="renderer",
                              status="not_run"))
    if vals is None:
        vals = load_workbook(path, data_only=True)
        failures.append(F("X5", "recalc", "Values are cached values from the file, not a fresh recalculation.",
                          fix_scope="renderer", status="not_run", severity="info"))

    def v(sheet, coord):
        try:
            return vals[sheet][coord].value
        except Exception:
            return None

    roles = {ws.title: _role(ws.title, wb, R) for ws in wb.worksheets}
    allowed_lit = {float(x) for x in R["literals"]["allowed"]}
    in_colors = {str(c).upper()[-6:] for c in R["formatting"]["input_font_colors"] if c}
    link_colors = {str(c).upper()[-6:] for c in R["formatting"]["link_font_colors"] if c}
    graph: dict = defaultdict(set)
    sheet_stats = {}
    errors_found = []

    for ws in wb.worksheets:
        role = roles[ws.title]
        hdr_row, pcols, plabels = _period_header(ws)
        st = Counter()
        const_cells, literal_cells, inconsistent, style_issues = [], [], [], []
        row_patterns: dict[int, dict[int, str]] = defaultdict(dict)
        row_formats: dict[int, Counter] = defaultdict(Counter)
        hist_cols = [c for c, lab in plabels.items() if re.search(R["historicals"]["header_regex"], lab)]
        src_col = next((c for c in range(1, ws.max_column + 1)
                        if isinstance(ws.cell(hdr_row or 1, c).value, str)
                        and re.search(R["historicals"]["source_column_regex"], ws.cell(hdr_row or 1, c).value)), None)
        merged = list(ws.merged_cells.ranges)
        for row in ws.iter_rows():
            for c in row:
                val = c.value
                if val is None:
                    continue
                is_formula = isinstance(val, str) and val.startswith("=")
                if is_formula:
                    st["formulas"] += 1
                    deps = _refs(val, ws.title, names, sheets)
                    graph[(ws.title, c.coordinate)].update(deps)
                    if any(d[0] != ws.title for d in deps):
                        st["cross_sheet_formulas"] += 1
                    if "[" in val and re.search(r"\[[^\]]+\.(xls|xlsx|xlsm)\]|\[\d+\]", val):
                        st["external_refs"] += 1
                    bad = [x for x in _literals(val) if x not in allowed_lit]
                    if bad:
                        literal_cells.append((c.coordinate, val, bad))
                    if c.column in pcols and c.row != hdr_row:
                        row_patterns[c.row][c.column] = _r1c1(val, c.row, c.column)
                    col = _color(c)
                    cross = any(d[0] != ws.title for d in deps)
                    if cross and col not in link_colors and R["formatting"]["link_font_colors"]:
                        style_issues.append((c.coordinate, "link_color"))
                    elif not cross and col in in_colors:
                        style_issues.append((c.coordinate, "formula_styled_as_input"))
                    if c.column in pcols:
                        row_formats[c.row][c.number_format] += 1
                elif _is_num(val):
                    st["constants"] += 1
                    if c.row == hdr_row and c.column in pcols:
                        continue
                    if c.column in pcols:
                        row_formats[c.row][c.number_format] += 1
                    col = _color(c)
                    styled_input = col in in_colors
                    if role in ("calc", "output", "checks") and not styled_input:
                        const_cells.append(c.coordinate)
                    if role == "input" and not styled_input:
                        style_issues.append((c.coordinate, "input_not_styled"))
                    if c.column in hist_cols and not c.comment and not (src_col and ws.cell(c.row, src_col).value):
                        st["hist_unsourced"] += 1
                        if st["hist_unsourced"] <= 20:
                            failures.append(F("X4", "historical_unsourced", f"{ws.title}!{c.coordinate}: historical "
                                              "figure has no source reference. Add a cell comment or a Source column.",
                                              sheet=ws.title, cell=c.coordinate))
                rv = v(ws.title, c.coordinate)
                if isinstance(rv, str) and any(rv.startswith(e) for e in R["errors"]["error_values"]):
                    errors_found.append((ws.title, c.coordinate, rv))
                if (_is_num(val) or is_formula) and any(c.coordinate in mr for mr in merged):
                    style_issues.append((c.coordinate, "merged_data_cell"))

        # X1 / X9 constants
        numeric = st["formulas"] + st["constants"]
        sheet_stats[ws.title] = {"role": role, "formulas": st["formulas"], "constants": st["constants"],
                                 "formula_share": round(st["formulas"] / numeric, 3) if numeric else None,
                                 "cross_sheet_formulas": st["cross_sheet_formulas"], "period_columns": len(pcols),
                                 "hardcoded_on_calc": len(const_cells), "formula_literals": len(literal_cells),
                                 "merged_ranges": len(merged), "frozen": ws.freeze_panes}
        if const_cells:
            rule = "X9" if role == "output" else "X1"
            failures.append(F(rule, "hardcoded_value", f"{ws.title}: {len(const_cells)} typed-in numbers on a {role} "
                              f"sheet ({', '.join(const_cells[:8])}). Replace each with a formula that links to an "
                              "input cell.", sheet=ws.title, cell=const_cells[0], measured=len(const_cells), limit=0))
        for coord, f_, bad in literal_cells[:15]:
            failures.append(F("X2", "formula_literal", f"{ws.title}!{coord}: formula {f_[:60]} contains the number(s) "
                              f"{', '.join(f'{b:g}' for b in bad)}. Move each to a labelled input cell and reference it.",
                              sheet=ws.title, cell=coord))
        if len(literal_cells) > 15:
            failures.append(F("X2", "formula_literal", f"{ws.title}: {len(literal_cells) - 15} more formulas contain "
                              "hardcoded numbers.", sheet=ws.title, measured=len(literal_cells)))

        # X8 consistency across periods
        C = R["consistency"]
        for r, pats in row_patterns.items():
            cols = sorted(pats)
            if len(cols) < C["min_run"]:
                continue
            body = cols[1:] if C["allow_first_period_exception"] else cols
            hist = [c for c in body if c in hist_cols]
            fc = [c for c in body if c not in hist]
            common = Counter(pats[c] for c in fc).most_common(1)
            if not common:
                continue
            odd = [c for c in fc if pats[c] != common[0][0]]
            if odd:
                cells = ", ".join(f"{get_column_letter(c)}{r}" for c in odd[:6])
                inconsistent.append(cells)
                failures.append(F("X8", "inconsistent_formula", f"{ws.title} row {r} ({_label(ws, r) or 'unlabelled'}): "
                                  f"{cells} break the row's formula pattern. Use one formula across all forecast periods.",
                                  sheet=ws.title, cell=f"{get_column_letter(odd[0])}{r}", measured=len(odd)))

        # X10 formatting
        Fm = R["formatting"]
        if Fm["require_frozen_panes"] and role in ("calc", "output", "input") and numeric > 20 and not ws.freeze_panes:
            failures.append(F("X10", "no_frozen_panes", f"{ws.title}: header panes are not frozen.", sheet=ws.title,
                              fix_scope="renderer"))
        for r, fm in row_formats.items():
            if len(fm) > 1:
                failures.append(F("X10", "mixed_number_formats", f"{ws.title} row {r} ({_label(ws, r)}): {len(fm)} number "
                                  f"formats in one row ({', '.join(list(fm)[:3])}).", sheet=ws.title, cell=f"A{r}",
                                  fix_scope="renderer"))
            fmt = fm.most_common(1)[0][0]
            if Fm["require_paren_negatives"] and role in ("calc", "output") and fmt not in ("General", "@") \
                    and "%" not in fmt and not re.search(r";\s*[^;]*\(", fmt):
                failures.append(F("X10", "negatives_format", f"{ws.title} row {r}: number format '{fmt}' does not show "
                                  "negatives in parentheses.", sheet=ws.title, cell=f"A{r}", fix_scope="renderer",
                                  severity="warning"))
            lab = _label(ws, r)
            if lab and not re.search(Fm["unit_regex"], lab) and "%" not in fmt and "$" not in fmt:
                failures.append(F("X10", "no_unit", f"{ws.title} row {r}: label '{lab[:40]}' has no unit. Add the unit, "
                                  "e.g. 'Revenue ($K)'.", sheet=ws.title, cell=f"A{r}", severity="warning"))
        kinds = Counter(k for _, k in style_issues)
        for k, cnt in kinds.items():
            first = next(c for c, kk in style_issues if kk == k)
            msg = {"link_color": "cross-sheet links are not in the link colour",
                   "formula_styled_as_input": "formulas are styled as inputs",
                   "input_not_styled": "input constants are not styled as inputs",
                   "merged_data_cell": "merged cells sit in the data area"}[k]
            failures.append(F("X10", k, f"{ws.title}: {cnt} cells where {msg} (first {first}).", sheet=ws.title,
                              cell=first, measured=cnt, fix_scope="renderer",
                              severity="error" if k in ("merged_data_cell", "input_not_styled") else "warning"))

        # X3 plugs, X4 tie-outs
        for r in range(1, ws.max_row + 1):
            lab = _label(ws, r)
            if not lab:
                continue
            if re.search(R["balance"]["plug_label_regex"], lab):
                failures.append(F("X3", "plug_line", f"{ws.title} row {r}: '{lab}' is a balancing plug. Balance the sheet "
                                  "through linked flows instead.", sheet=ws.title, cell=f"A{r}"))
            if re.search(R["historicals"]["tieout_label_regex"], lab):
                for c in pcols:
                    x = v(ws.title, f"{get_column_letter(c)}{r}")
                    if _is_num(x) and abs(x) > R["historicals"]["tolerance"]:
                        failures.append(F("X4", "tieout_nonzero", f"{ws.title}!{get_column_letter(c)}{r}: tie-out "
                                          f"'{lab}' is {x:,.4g}, not 0. Fix the historical inputs to match the filing.",
                                          sheet=ws.title, cell=f"{get_column_letter(c)}{r}", measured=x, limit=0))
        if st["external_refs"]:
            failures.append(F("X13", "external_ref", f"{ws.title}: {st['external_refs']} formulas point at other workbooks.",
                              sheet=ws.title))

    # X3 balance sheet balances
    B = R["balance"]
    bs_found = False
    for ws in wb.worksheets:
        _, pcols, _ = _period_header(ws)
        rows = {}
        for r in range(1, ws.max_row + 1):
            lab = _label(ws, r)
            for key in ("assets", "liabilities", "equity", "liab_equity"):
                if lab and re.search(B[f"{key}_regex"], lab):
                    rows[key] = r
        if "assets" in rows and ("liab_equity" in rows or {"liabilities", "equity"} <= rows.keys()):
            bs_found = True
            for c in pcols:
                L = get_column_letter(c)
                a = v(ws.title, f"{L}{rows['assets']}")
                le = v(ws.title, f"{L}{rows['liab_equity']}") if "liab_equity" in rows else (
                    (v(ws.title, f"{L}{rows['liabilities']}") or 0) + (v(ws.title, f"{L}{rows['equity']}") or 0)
                    if _is_num(v(ws.title, f"{L}{rows['liabilities']}")) and _is_num(v(ws.title, f"{L}{rows['equity']}")) else None)
                if _is_num(a) and _is_num(le) and abs(a - le) > B["tolerance"]:
                    failures.append(F("X3", "bs_imbalance", f"{ws.title}!{L}: assets minus liabilities minus equity is "
                                      f"{a - le:,.4g}, not 0.", sheet=ws.title, cell=f"{L}{rows['assets']}",
                                      measured=a - le, limit=0))

    # X5 errors and names
    for sh, coord, e in errors_found[:25]:
        failures.append(F("X5", "error_value", f"{sh}!{coord}: evaluates to {e}. Fix the formula's references or "
                          "argument order.", sheet=sh, cell=coord))
    if len(errors_found) > 25:
        failures.append(F("X5", "error_value", f"{len(errors_found) - 25} more error cells.", measured=len(errors_found)))
    for n, dest in names_raw:
        sheet = str(dest).rpartition("!")[0].strip("'") if dest and "!" in str(dest) else None
        if dest is None or "#REF!" in str(dest) or (sheet and sheet not in sheets):
            failures.append(F("X5", "bad_name", f"Defined name {n} does not resolve ({dest}).", cell=n))
    referenced = set()
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    try:
                        for t in Tokenizer(c.value).items:
                            if t.type == Token.OPERAND and t.subtype == Token.RANGE and re.match(r"^[A-Za-z_][\w.]*$", t.value) \
                                    and not CELL_RE.match(t.value.upper()) and t.value.upper() not in names \
                                    and t.value.upper() not in ("TRUE", "FALSE"):
                                referenced.add((ws.title, c.coordinate, t.value))
                    except Exception:
                        pass
    for sh, coord, nm in sorted(referenced)[:20]:
        failures.append(F("X5", "undefined_name", f"{sh}!{coord}: formula uses name '{nm}', which is not defined.",
                          sheet=sh, cell=coord))

    # X6 circularity
    sccs = _tarjan(graph)
    iterate = bool(getattr(wb.calculation, "iterate", False))
    breaker = any(re.search(R["circularity"]["breaker_name_regex"], n) for n, _ in names_raw)
    for comp in sccs[:10]:
        cells = ", ".join(f"{s}!{c}" for s, c in sorted(comp)[:6])
        if iterate and breaker:
            failures.append(F("X6", "justified_cycle", f"Circular reference through {cells}; iterative calculation and a "
                              "circuit breaker are present. State why it is needed.", severity="warning"))
        else:
            failures.append(F("X6", "circular_reference", f"Circular reference through {cells}. Break it, e.g. compute "
                              "interest on the prior-period balance.", cell=cells.split(",")[0]))

    # X7 independent recompute
    if not recompute:
        failures.append(F("X7", "recompute", "No independent recompute of key outputs was supplied.", status="not_run",
                          fix_scope="renderer"))
    else:
        tol = R["recompute"]["rel_tolerance"]
        for chk in recompute.get("checks", []):
            sh, _, coord = chk["cell"].rpartition("!")
            got = v(sh.strip("'"), coord)
            exp = chk["expected"]
            if not _is_num(got) or abs(got - exp) > tol * max(1.0, abs(exp)):
                failures.append(F("X7", "recompute_mismatch", f"{chk['cell']} ({chk.get('label', '')}): workbook gives "
                                  f"{got}, code recompute gives {exp}. Fix the formula chain.", cell=chk["cell"],
                                  measured=got, limit=exp))

    # X9 chart series in the workbook must reference cells
    with zipfile.ZipFile(path) as z:
        members = z.namelist()
        for mname in members:
            if mname.startswith("xl/charts/chart") and mname.endswith(".xml"):
                x = z.read(mname).decode("utf-8", "replace")
                if "<c:numLit" in x or "<c:strLit" in x:
                    failures.append(F("X9", "chart_literal", f"{mname}: chart holds pasted values, not cell references.",
                                      fix_scope="renderer"))
    # X13 hygiene
    if any(mm.startswith("xl/externalLinks/") for mm in members):
        failures.append(F("X13", "external_link_part", "The workbook contains external link parts.",
                          measured=sum(mm.startswith("xl/externalLinks/") for mm in members), limit=0))
    if any(mm.endswith("vbaProject.bin") for mm in members) and not R["hygiene"]["allow_macros"]:
        failures.append(F("X13", "macros", "The workbook contains a VBA project; remove macros."))

    # X11 before/after
    changes = None
    if before:
        changes = diff(before, path)
        over = [c for c in changes if c["overwrote"]]
        failures.append(F("X11", "diff_report", f"{len(changes)} cells changed; {len(over)} replaced existing values "
                          "(listed in stats.diff).", status="fail" if False else "pass", severity="info"))
    else:
        failures.append(F("X11", "diff_report", "No previous version supplied; before/after diff not produced.",
                          status="not_run", severity="info"))

    # X12 checks sheet and master flag
    chk_sheets = [s for s, r in roles.items() if r == "checks"]
    if not chk_sheets:
        failures.append(F("X12", "no_checks_sheet", "No checks sheet. Add one row per integrity check and a master "
                          "flag on the cover sheet.", fix_scope="renderer"))
    else:
        flags = []
        for s in chk_sheets:
            for row in vals[s].iter_rows():
                for c in row:
                    if isinstance(c.value, bool):
                        flags.append(((s, c.coordinate), c.value))
        master = None
        for n, dest in names_raw:
            if n in R["checks"]["master_flag_names"] and dest and "!" in str(dest):
                sh, _, coord = str(dest).rpartition("!")
                master = ((sh.strip("'"), coord.replace("$", "")), v(sh.strip("'"), coord.replace("$", "")))
        if master is None:
            failures.append(F("X12", "no_master_flag", f"No master check flag (define one of "
                              f"{', '.join(R['checks']['master_flag_names'])}).", fix_scope="renderer"))
        else:
            others = [val for loc, val in flags if loc != master[0]]
            expect = all(others) if others else None
            mv = master[1]
            mv_b = mv if isinstance(mv, bool) else (bool(mv) if _is_num(mv) else (str(mv).upper() in ("OK", "PASS", "TRUE")))
            if expect is None:
                failures.append(F("X12", "no_check_rows", "The checks sheet has no TRUE/FALSE check cells.",
                                  fix_scope="renderer"))
            elif mv_b != expect:
                failures.append(F("X12", "master_flag_wrong", f"Master flag {master[0][0]}!{master[0][1]} is {mv} but the "
                                  f"AND of {len(others)} checks is {expect}.", cell=master[0][1]))
            elif not expect:
                failures.append(F("X12", "checks_failing", f"{sum(1 for o in others if not o)} integrity checks are FALSE.",
                                  measured=sum(1 for o in others if not o), limit=0))

    tmp.cleanup()
    stats = {"sheets": sheet_stats, "defined_names": len(names_raw), "cycles": len(sccs), "iterative_calc": iterate,
             "balance_sheet_found": bs_found, "errors_after_recalc": len(errors_found),
             "formulas": sum(s["formulas"] for s in sheet_stats.values()),
             "constants": sum(s["constants"] for s in sheet_stats.values()),
             "hardcoded_on_calc_or_output": sum(s["hardcoded_on_calc"] for s in sheet_stats.values()),
             "formula_literals": sum(s["formula_literals"] for s in sheet_stats.values())}
    if changes is not None:
        stats["diff"] = changes[:500]
    return {"file": str(path), "sha256": file_sha, "rules": summarize(failures, RULES), "failures": failures,
            "stats": stats, "evidence": {k: evidence_id(file_sha, k) for k in stats}}


def report_text(res: dict, limit: int = 40) -> str:
    fs = [f for f in res["failures"] if f["status"] == "fail"]
    fs.sort(key=lambda f: (f["severity"] != "error", f["fix_scope"] != "content"))
    return " ".join(f"{k}={v}" for k, v in res["rules"].items()) + "\n" + "\n".join(
        f"[{f['rule']}/{f['fix_scope']}/{f['severity']}] {f['message']}" for f in fs[:limit])


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--rules")
    ap.add_argument("--recompute")
    ap.add_argument("--before")
    ap.add_argument("--no-recalc", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--text", action="store_true")
    a = ap.parse_args()
    res = validate(a.xlsx, load_rules(a.rules), json.loads(Path(a.recompute).read_text()) if a.recompute else None,
                   a.before, do_recalc=False if a.no_recalc else None)
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1, default=str))
    print(report_text(res) if a.text or a.out else json.dumps(res, indent=1, default=str))
    sys.exit(1 if any(v == "fail" for v in res["rules"].values()) else 0)
