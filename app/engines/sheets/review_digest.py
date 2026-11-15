"""A compact text digest of a built workbook for the partner and MD reviewers (engines.review.personas).

    text = digest("Model.xlsx", report={"rules": {...}, "warnings": [...]})

Built from the recalculated workbook (LibreOffice; cached values when it is not installed, and the digest says so):
the tab list with each tab's purpose, key outputs with values, the Checks tab, the forge validate_xlsx rule statuses
and failures, error cells (#REF!, #DIV/0! ...), the assumption book (name, value, unit, source, range), hard-coded
numbers inside formulas, and formula samples per calculation tab (first and last formula column of each row, so a
reviewer can spot hard-codes and a formula that changes across the row).

The digest stays within a character budget (default 25,000). Sections are filled in priority order, each up to its
own share: outputs, checks and validation, then assumptions, then the formula detail.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

ERRORS = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!", "Err:", "#ERR")
INPUT_TABS = re.compile(r"input|assumption|scenario book|drivers", re.I)
SKIP_CALC = re.compile(r"^(cover|checks?|alerts|readme|notes?)$", re.I)
TOP_OUTPUT = re.compile(r"per_share|weighted|^offer|premium$|accret|dilut|eps_acc|tv_share|wacc$|^dcf_ev|deal_ev|"
                        r"_irr|moic|lbo_floor|master|implied", re.I)
KEY_OUTPUT = re.compile(r"_ev$|^dcf_|equity|offer|premium|eps|irr|valuation|_value|scenario|_base$|_bull$|_bear$|"
                        r"synerg|purchase|check|median", re.I)
KEY_ROW = re.compile(r"revenue|ebitda|ebit\b|free cash|fcf|net income|enterprise|equity value|per share|wacc|terminal|"
                     r"discount|capex|working capital|tax|eps|accretion|dilution|debt|cash", re.I)
SHARES = {"tabs": 0.07, "outputs": 0.15, "checks": 0.09, "validation": 0.09, "errors": 0.04, "assumptions": 0.23,
          "literals": 0.05, "formulas": 0.28}
DEFAULT_ALLOWED = {0, 1, 2, 4, 12, 52, 100, 365, 1000, 1000000, 1000000000}


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        if v != v:
            return "NaN"
        a = abs(v)
        if a != 0 and a < 1:
            return f"{v:.4g}"
        return f"{v:,.2f}".rstrip("0").rstrip(".") if a < 1e6 else f"{v:,.0f}"
    if isinstance(v, int):
        return f"{v:,}"
    s = str(v).replace("\n", " ").strip()
    return s if len(s) <= 120 else s[:117] + "..."


def _fv(cell, v) -> str:
    """A computed value shown the way the cell is formatted where it matters: percentages as %, multiples as x."""
    fmt = str(getattr(cell, "number_format", "") or "")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "%" in fmt:
            return f"{v * 100:.1f}%" if abs(v) >= 0.01 or v == 0 else f"{v * 100:.2f}%"
        if re.search(r'0"?x"?$|"x"', fmt):
            return f"{v:.2f}x"
    return _fmt(v)


def _unit(label: str) -> str:
    m = re.findall(r"\(([^()]*)\)", label or "")
    return m[-1].strip() if m else ""


def _title(ws) -> str:
    for r in range(1, 4):
        for c in range(1, 4):
            v = ws.cell(r, c).value
            if isinstance(v, str) and v.strip() and not v.startswith("="):
                return v.strip()
    return ""


def _values_book(path: str):
    """(recalculated workbook with values, note). Falls back to the cached values in the file."""
    try:
        from engines.sheets.qa import recalc
        out = recalc(path, tempfile.mkdtemp(prefix="digest_"))
        return load_workbook(out, data_only=True), "values recalculated in LibreOffice"
    except Exception as e:  # noqa: BLE001 - no LibreOffice or a failed conversion: cached values, said plainly
        return load_workbook(path, data_only=True), f"values are the file's cached values (recalculation failed: " \
                                                   f"{type(e).__name__})"


def _names(wb) -> dict[str, tuple[str, str]]:
    """Single-cell defined names -> (sheet, coordinate)."""
    out = {}
    try:
        items = list(wb.defined_names.items())
    except AttributeError:
        items = [(d.name, d) for d in wb.defined_names.definedName]
    for n, dn in items:
        try:
            dests = list(dn.destinations)
        except Exception:  # noqa: BLE001
            continue
        if len(dests) == 1:
            sh, coord = dests[0]
            coord = coord.replace("$", "")
            if ":" not in coord and sh in wb.sheetnames:
                out[n] = (sh, coord)
    return out


def _cell_names(names: dict) -> dict[tuple[str, str], str]:
    return {v: k for k, v in names.items()}


def tabs_section(wf) -> list[str]:
    purpose = {}
    if "Cover" in wf.sheetnames:                       # banker_workbook's tab map: Tab | What it contains
        ws = wf["Cover"]
        for row in ws.iter_rows(max_row=min(ws.max_row, 80)):
            vals = [c.value for c in row if c.value is not None]
            if len(vals) >= 2 and isinstance(vals[0], str) and vals[0] in wf.sheetnames:
                purpose[vals[0]] = str(vals[1])
    out = []
    for ws in wf.worksheets:
        what = purpose.get(ws.title) or _title(ws)
        hidden = " [hidden]" if ws.sheet_state != "visible" else ""
        out.append(f"- {ws.title}{hidden} ({ws.max_row} rows x {ws.max_column} cols): {_fmt(what)}")
    return out


def outputs_section(wf, wv, names) -> list[str]:
    """Named results that are formulas off the input tabs (per share, EV, WACC, TV share, scenarios ...), the Cover's
    key outputs, and any football field table."""
    rows, seen = [], set()
    if "Cover" in wf.sheetnames:
        ws, wsv = wf["Cover"], wv["Cover"]
        for row in ws.iter_rows(max_row=min(ws.max_row, 120)):
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("=") and c.column > 1:
                    lab = ws.cell(c.row, c.column - 1).value
                    if lab:
                        rows.append(f"- Cover: {_fmt(lab)} = {_fv(c, wsv[c.coordinate].value)}  [{c.value[:60]}]")
                        seen.add(("Cover", c.coordinate))
    top, key, other = [], [], []
    for n, (sh, coord) in sorted(names.items()):
        if INPUT_TABS.search(sh) or (sh, coord) in seen or re.search(r"football", sh, re.I):
            continue
        f = wf[sh][coord].value
        if not (isinstance(f, str) and f.startswith("=")):
            continue
        line = f"- {n} ({sh}!{coord}) = {_fv(wf[sh][coord], wv[sh][coord].value)}"
        (top if TOP_OUTPUT.search(n) else key if KEY_OUTPUT.search(n) else other).append(line)
    football = []
    for ws in wf.worksheets:                          # football field: label | low | high per method
        if not re.search(r"football|valuation summary", ws.title, re.I):
            continue
        wsv = wv[ws.title]
        football.append(f"- {ws.title} table (label: values across the row):")
        for r in range(1, min(ws.max_row, 60) + 1):
            lab = ws.cell(r, 1).value
            nums = [(ws.cell(r, c), wsv.cell(r, c).value) for c in range(2, min(ws.max_column, 8) + 1)]
            nums = [(c, x) for c, x in nums if isinstance(x, (int, float)) and not isinstance(x, bool)]
            if isinstance(lab, str) and nums:
                football.append(f"    {_fmt(lab)}: " + " | ".join(_fv(c, x) for c, x in nums))
    return rows + top + football + key + other


def checks_section(wf, wv) -> list[str]:
    if "Checks" not in wf.sheetnames:
        return ["- no Checks tab"]
    ws, wsv = wf["Checks"], wv["Checks"]
    out, bad = [], 0
    for row in ws.iter_rows():
        for c in row:
            if c.value == "All checks" or (isinstance(c.value, str) and c.value.lower().startswith("all checks")):
                out.append(f"- Master status ({ws.title}!{get_column_letter(c.column + 1)}{c.row}): "
                           f"{_fmt(wsv.cell(c.row, c.column + 1).value)}")
    for r in range(1, ws.max_row + 1):
        lab = ws.cell(r, 1).value
        if not isinstance(lab, str) or lab.strip().lower() in ("check", "checks"):
            continue
        vals = [wsv.cell(r, c).value for c in range(2, 5)]
        status = next((v for v in vals if isinstance(v, str) and v.strip().upper() in ("OK", "ERROR", "FAIL", "TRUE",
                                                                                        "FALSE", "REVIEW")), None)
        if status is None and not any(isinstance(v, (int, float)) for v in vals):
            continue
        diff = next((v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)), None)
        bad += str(status).upper() not in ("OK", "TRUE")
        out.append(f"- {_fmt(lab)}: difference {_fmt(diff)}, status {_fmt(status)}")
    out.insert(0, f"- {sum(1 for x in out if 'status' in x and 'Master' not in x)} checks, {bad} not OK")
    return out


def validation_section(report: dict | None) -> list[str]:
    if not report:
        return ["- forge validate_xlsx was not run for this digest"]
    rules = report.get("rules") or {}
    out = ["- rules: " + " ".join(f"{k}={v}" for k, v in rules.items())] if rules else []
    fails = report.get("failures") or []
    for f in fails[:25]:
        if isinstance(f, dict):
            out.append(f"- {f.get('rule')} [{f.get('severity')}] {f.get('cell') or f.get('sheet') or ''}: "
                       f"{f.get('message')}")
        else:
            out.append(f"- {f}")
    warns = report.get("warnings") or []
    if warns:
        out.append(f"- warnings ({len(warns)}):")
        out += [f"    {w}" for w in warns[:25]]
    return out or ["- no rule results"]


def errors_section(wf, wv) -> list[str]:
    out = []
    for ws in wf.worksheets:
        wsv = wv[ws.title]
        for row in ws.iter_rows():
            for c in row:
                f = c.value
                if f is None:
                    continue
                v = wsv[c.coordinate].value
                if isinstance(v, str) and v.startswith(ERRORS):
                    out.append(f"- {ws.title}!{c.coordinate} = {v}  [{str(f)[:80]}]")
                elif isinstance(f, str) and f.startswith("=") and v is None:
                    out.append(f"- {ws.title}!{c.coordinate} has no computed value  [{f[:80]}]")
    return out or ["- none"]


def _header_map(ws, r: int) -> dict[str, int]:
    m = {}
    for c in range(1, min(ws.max_column, 20) + 1):
        v = ws.cell(r, c).value
        if not isinstance(v, str):
            continue
        t = v.strip().lower()
        for key, pat in (("value", r"^value"), ("selected", r"^selected"), ("base", r"^base"), ("bull", r"^bull"),
                         ("bear", r"^bear"), ("source", r"^source"), ("evidence", r"^evidence|rationale"),
                         ("low", r"^low"), ("high", r"^high"), ("unit", r"^unit")):
            if re.search(pat, t) and key not in m:
                m[key] = c
    return m


def assumptions_section(wf, wv, names) -> list[str]:
    """Every assumption-book row: name, value (selected / base / bull / bear for a scenario book), unit, source,
    range."""
    by_cell = _cell_names(names)
    out = []
    for ws in wf.worksheets:
        if not INPUT_TABS.search(ws.title):
            continue
        wsv = wv[ws.title]
        hdr = None
        for r in range(1, ws.max_row + 1):
            a = ws.cell(r, 1).value
            if isinstance(a, str) and a.strip().lower().startswith("assumption"):
                m = _header_map(ws, r)
                if "source" in m or "value" in m or "base" in m:
                    hdr = m
                    out.append(f"{ws.title} (header row {r}):")
                    continue
            if hdr is None or not a:
                continue
            vcol = hdr.get("value") or hdr.get("selected") or hdr.get("base")
            if vcol is None:
                continue
            raw = ws.cell(r, vcol).value
            if raw is None and all(ws.cell(r, c).value is None for c in hdr.values()):
                out.append(f"  [{_fmt(a)}]")             # section band
                continue
            val = _fv(ws.cell(r, vcol), wsv.cell(r, vcol).value)
            name = by_cell.get((ws.title, f"{get_column_letter(vcol)}{r}"), "")
            parts = [f"  - {_fmt(a)}" + (f" [{name}]" if name else "") + f" = {val}"]
            unit = _unit(str(a)) or (_fmt(ws.cell(r, hdr['unit']).value) if "unit" in hdr else "")
            if unit:
                parts.append(f"unit {unit}")
            if "base" in hdr and "bull" in hdr:
                parts.append("base/bull/bear " + "/".join(_fv(ws.cell(r, hdr[k]), wsv.cell(r, hdr[k]).value)
                                                          for k in ("base", "bull", "bear")))
            if isinstance(raw, str) and raw.startswith("="):
                parts.append(f"formula {raw[:50]}")
            if "low" in hdr or "high" in hdr:
                lo = _fv(ws.cell(r, hdr["low"]), wsv.cell(r, hdr["low"]).value) if "low" in hdr else "?"
                hi = _fv(ws.cell(r, hdr["high"]), wsv.cell(r, hdr["high"]).value) if "high" in hdr else "?"
                parts.append(f"range {lo} to {hi}")
            src = ws.cell(r, hdr["source"]).value if "source" in hdr else None
            parts.append(f"source: {_fmt(src) or 'MISSING'}")
            out.append("; ".join(parts))
        if hdr is None:                                  # an Inputs tab without an assumption book: label | value
            rows = []
            for r in range(1, ws.max_row + 1):
                a, b = ws.cell(r, 1).value, wsv.cell(r, 2).value
                if isinstance(a, str) and b is not None and not isinstance(b, str):
                    rows.append(f"  - {_fmt(a)} = {_fmt(b)} (no Source column)")
            if rows:
                out += [f"{ws.title} (no assumption book):"] + rows
    return out or ["- no assumption book found"]


def _allowed_literals() -> set:
    try:
        from engines.forge.common import load_rules
        return {float(x) for x in load_rules()["xlsx"]["literals"]["allowed"]}
    except Exception:  # noqa: BLE001
        return {float(x) for x in DEFAULT_ALLOWED}


def literals_section(wf) -> list[str]:
    from engines.forge.validate_xlsx import _literals
    allowed = _allowed_literals()
    out = []
    for ws in wf.worksheets:
        if INPUT_TABS.search(ws.title) or ws.title == "Cover":
            continue
        for row in ws.iter_rows():
            for c in row:
                f = c.value
                if isinstance(f, str) and f.startswith("="):
                    bad = [x for x in _literals(f) if x not in allowed]
                    if bad:
                        out.append(f"- {ws.title}!{c.coordinate}: {', '.join(f'{x:g}' for x in bad)} in {f[:90]}")
    return out or ["- none"]


def formulas_section(wf, wv, budget: int = 8000) -> list[str]:
    """Per calculation tab: for each formula row, the first and last formula column (formula and value), and whether
    the row's formulas follow one pattern. Key rows (revenue, EBITDA, FCF, EV, per share ...) first; every tab gets an
    equal share of `budget` characters (a share a tab does not use passes to the next)."""
    from engines.forge.validate_xlsx import _period_header, _r1c1
    out = []
    calc = [ws for ws in wf.worksheets if not (INPUT_TABS.search(ws.title) or SKIP_CALC.match(ws.title))]
    spare = 0
    for i, ws in enumerate(calc):
        wsv = wv[ws.title]
        key, rest = [], []
        pcols = set(_period_header(ws)[1])             # consistency is judged across period columns only
        for r in range(1, ws.max_row + 1):
            cells = [ws.cell(r, c) for c in range(2, ws.max_column + 1)]
            fc = [c for c in cells if isinstance(c.value, str) and c.value.startswith("=")]
            if not fc:
                continue
            lab = _fmt(ws.cell(r, 1).value) or "(no label)"
            first, last = fc[0], fc[-1]
            line = f"  r{r} {lab[:50]}: {first.coordinate} {first.value[:55]} -> {_fv(first, wsv[first.coordinate].value)}"
            if last is not first:
                line += f" | {last.coordinate} {last.value[:55]} -> {_fv(last, wsv[last.coordinate].value)}"
                per = [c for c in fc if c.column in pcols]
                body = [_r1c1(c.value, c.row, c.column) for c in per[1:]]
                if len(per) >= 3 and len(set(body)) > 1:
                    odd = [c.coordinate for c, b in zip(per[1:], body) if body.count(b) < len(body) / 2]
                    line += (f" | INCONSISTENT across periods ({len(set(body))} patterns"
                             + (f"; odd: {', '.join(odd[:4])}" if odd else "") + ")")
            consts = [c for c in cells if isinstance(c.value, (int, float)) and not isinstance(c.value, bool)]
            if consts:
                line += f" | typed numbers in {', '.join(c.coordinate for c in consts[:4])}"
            (key if KEY_ROW.search(lab) else rest).append(line)
        rows = key + rest
        if not rows:
            continue
        share = (budget - sum(len(x) + 1 for x in out)) // (len(calc) - i)
        txt, _ = _fit(rows, max(300, share - len(ws.title) - 30))
        out.append(f"{ws.title} ({len(rows)} formula rows):")
        out += txt.split("\n")
    return out or ["- no calculation tabs"]


def _fit(lines: list[str], budget: int) -> tuple[str, int]:
    text, used = [], 0
    for i, ln in enumerate(lines):
        if used + len(ln) + 1 > budget:
            more = f"  ... {len(lines) - i} more lines omitted"
            return "\n".join(text + [more]), used + len(more)
        text.append(ln)
        used += len(ln) + 1
    return "\n".join(text), used


def digest(path: str, report: dict | None = None, budget: int = 25000, title: str = "") -> str:
    """The reviewer's view of the workbook at `path`, within `budget` characters. `report` is the finish gate's
    forge rule report ({"rules": {...}, "warnings": [...], "failures": [...]}), optional."""
    wf = load_workbook(path, data_only=False)
    wv, note = _values_book(path)
    names = _names(wf)
    order = [("outputs", "KEY OUTPUTS (named results, computed values)", lambda: outputs_section(wf, wv, names)),
             ("checks", "CHECKS TAB", lambda: checks_section(wf, wv)),
             ("validation", "FORGE WORKBOOK RULES (validate_xlsx X0-X14)", lambda: validation_section(report)),
             ("errors", "ERROR CELLS (#REF!, #DIV/0!, no value)", lambda: errors_section(wf, wv)),
             ("tabs", "TABS AND PURPOSE", lambda: tabs_section(wf)),
             ("assumptions", "ASSUMPTION BOOK (name = value; unit; range; source)",
              lambda: assumptions_section(wf, wv, names)),
             ("literals", "HARD-CODED NUMBERS INSIDE FORMULAS (outside the input tabs)", lambda: literals_section(wf)),
             ("formulas", "FORMULA SAMPLES PER CALCULATION TAB (first | last formula column of each row)", None)]
    head = (f"WORKBOOK DIGEST: {title or Path(path).name}; {len(wf.sheetnames)} tabs; {len(names)} named cells; "
            f"{note}.\n")
    body = budget - len(head)
    parts, spare = {}, 0
    for key, label, fn in order:                        # each section up to its share, unused share carried forward
        cap = int(SHARES[key] * body) + spare - len(label) - 8
        try:
            lines = fn() if fn else formulas_section(wf, wv, max(300, cap))
        except Exception as e:  # noqa: BLE001 - one section failing never costs the digest
            lines = [f"- section could not be built: {type(e).__name__}: {e}"]
        txt, used = _fit(lines, max(200, cap))
        parts[key] = f"## {label}\n{txt}"
        spare = max(0, cap - used)
    show = ["tabs", "outputs", "checks", "validation", "errors", "assumptions", "literals", "formulas"]
    text = head + "\n\n".join(parts[k] for k in show)
    return text[:budget]
