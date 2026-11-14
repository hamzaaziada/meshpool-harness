"""Banker pages straight from a finished workbook: the numbers on the slide are the workbook's computed outputs, never
retyped by a model. Reads the sheets engine's named outputs and tables (comps, precedents, DCF, three statements,
scenarios, method weighting, football field) after a LibreOffice recalculation, and returns slide specs for the forge
renderer plus facts (W1..) the narrative slides cite, each pointing at its sheet and cell.

    pages, facts = pages_from_workbook("model.xlsx")
    build(brief, out_dir, slides_wanted, fixed_pages=pages, extra_facts=facts)
"""
from __future__ import annotations

import re
from pathlib import Path

from openpyxl import load_workbook

from .render import fmt_value

FIRM_SRC = "Meshpool model"


def _recalc(path: str):
    from engines.sheets.qa import recalc
    try:
        return load_workbook(recalc(path), data_only=True), load_workbook(path)
    except RuntimeError:                                   # no LibreOffice: cached values (may be empty)
        return load_workbook(path, data_only=True), load_workbook(path)


def _names(wb) -> dict[str, tuple[str, str]]:
    out = {}
    for k, dn in wb.defined_names.items():
        dest = str(dn.attr_text)
        if "!" in dest and ":" not in dest:
            sh, _, cell = dest.rpartition("!")
            out[k.lower()] = (sh.strip("'"), cell.replace("$", ""))
    return out


class Book:
    def __init__(self, path: str):
        self.path = path
        self.v, self.f = _recalc(path)
        self.names = _names(self.f)
        self.facts: list[dict] = []

    def name(self, n: str):
        loc = self.names.get(n.lower())
        if not loc or loc[0] not in self.v.sheetnames:
            return None
        return self.v[loc[0]][loc[1]].value

    def fact(self, text: str, where: str) -> str:
        fid = f"W{len(self.facts) + 1}"
        self.facts.append({"id": fid, "text": text, "source": f"{FIRM_SRC}, {where}"})
        return fid

    def find(self, text: str, exact: bool = True):
        """(sheet, row, col) of the first cell whose value equals (or starts with) text."""
        for ws in self.v.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    if isinstance(c.value, str) and (c.value.strip() == text if exact else c.value.startswith(text)):
                        return ws, c.row, c.column
        return None

    def sources(self, ws, header_row: int) -> str:
        """Distinct texts of a table's Source column (for the slide's source line)."""
        hdr = [c.value for c in ws[header_row]]
        if "Source" not in hdr:
            return ""
        col = hdr.index("Source") + 1
        seen = []
        r = header_row + 1
        while ws.cell(row=r, column=1).value not in (None, "Mean"):
            s = ws.cell(row=r, column=col).value
            if s and s not in seen:
                seen.append(str(s))
            r += 1
        return "; ".join(seen)[:220]


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _x(v):
    return fmt_value(_num(v), "x") if _num(v) is not None else "NM"


def _pct(v):
    return fmt_value(_num(v) * 100, "%") if _num(v) is not None else "NM"


def _usd(v, d=0):
    v = _num(v)
    return "NM" if v is None else (f"({abs(v):,.{d}f})" if v < 0 else f"{v:,.{d}f}")


def _table_rows(ws, hdr_row: int, stop=("Mean",)) -> list[int]:
    rows, r = [], hdr_row + 1
    while ws.cell(row=r, column=1).value not in (None, *stop):
        rows.append(r)
        r += 1
    return rows


def _col(ws, hdr_row: int, label_start: str) -> int | None:
    for c in ws[hdr_row]:
        if isinstance(c.value, str) and c.value.startswith(label_start):
            return c.column
    return None


# ------------------------------------------------------------------------------------------------ pages
def football_field(bk: Book) -> dict | None:
    hit = bk.find("Low: ", exact=False)
    if not hit:
        return None
    ws, hr, _ = hit
    rows = _table_rows(ws, hr, stop=())
    cats = [str(ws.cell(row=r, column=1).value) for r in rows]
    lows = [_num(ws.cell(row=r, column=2).value) for r in rows]
    highs = [_num(ws.cell(row=r, column=3).value) for r in rows]
    if not cats or any(v is None for v in lows + highs):
        return None
    mk_row = rows[-1] + 2
    mk_lab, mk_val = ws.cell(row=mk_row, column=1).value, _num(ws.cell(row=mk_row, column=2).value)
    marker = {"label": re.sub(r"\s*\(.*\)$", "", str(mk_lab)), "value": mk_val} if mk_lab and mk_val is not None else None
    prov = [bk.fact(f"{c}: {fmt_value(lo, '$')} to {fmt_value(hi, '$')} per share", f"{ws.title}!B{r}:C{r}")
            for c, lo, hi, r in zip(cats, lows, highs, rows)]
    lo, hi = min(lows), max(highs)
    wv = bk.name("weighted_value_per_share")
    if _num(wv) is not None:
        title = f"Methods put the shares at {fmt_value(lo, '$')}-{fmt_value(hi, '$')}; the weighted value is " \
                f"{fmt_value(wv, '$')}"
        prov.append(bk.fact(f"Weighted value per share: {fmt_value(wv, '$')}", "weighted_value_per_share"))
    else:
        title = f"Valuation methods bracket the shares at {fmt_value(lo, '$')} to {fmt_value(hi, '$')}"
    bullets = []
    if marker:
        prov.append(bk.fact(f"{marker['label']}: {fmt_value(marker['value'], '$')}", f"{ws.title}!B{mk_row}"))
        short = lambda c: re.sub(r"\s*\(.*?\)", "", c).strip()  # noqa: E731
        above = [short(c) for c, l_ in zip(cats, lows) if l_ > marker["value"]]
        below = [short(c) for c, h_ in zip(cats, highs) if h_ < marker["value"]]
        mlab, mval = marker["label"].lower(), fmt_value(marker["value"], "$")
        if len(above) == len(cats):
            bullets.append({"text": f"Every method sits above the {mlab} of {mval}"})
        elif len(below) == len(cats):
            bullets.append({"text": f"Every method sits below the {mlab} of {mval}"})
        else:
            if above:
                bullets.append({"text": f"Above the {mlab}: {', '.join(above).lower()}"})
            if below:
                bullets.append({"text": f"Below the {mlab}: {', '.join(below).lower()}"})
    widest = max(zip(cats, lows, highs), key=lambda t: t[2] - t[1])
    bullets.append({"text": f"Widest range: {widest[0]} ({fmt_value(widest[1], '$')}-{fmt_value(widest[2], '$')})"})
    return {"archetype": "football_field", "fixed": True, "title": title, "title_prov": prov[-1:],
            "chart": {"kind": "range", "categories": cats, "unit": "$", "decimals": 2,
                      "series": [{"name": "Low", "values": lows}, {"name": "High", "values": highs}],
                      "marker": marker, "highlight_categories": [c for c in cats if c.lower().startswith("weighted")]},
            "takeaway": {"text": "Value per share by method, low to high"}, "bullets": bullets[:4],
            "source": {"text": f"{FIRM_SRC} ({ws.title} tab); ranges are 25th-75th percentiles for multiples and the "
                               "Gordon / exit-multiple pair for the DCF", "prov": prov}}


def comps(bk: Book, prefix: str = "comps_", kind: str = "Trading comparables", style: str = "benchmark",
          logos: dict | None = None) -> dict | None:
    med = bk.names.get(f"{prefix}ev_ebitda_median")
    if not med:
        return None
    ws = bk.v[med[0]]
    hit = None
    for r in range(1, ws.max_row + 1):
        if ws.cell(row=r, column=1).value in ("Company", "Announced"):
            hit = r
            break
    if hit is None:
        return None
    hr = hit
    rows = _table_rows(ws, hr)
    is_prec = prefix.startswith("prec")
    want = ([("Target", "Target"), ("Acquirer", "Acquirer"), ("Announced", "Date")] if is_prec else
            [("Company", "Company")])
    want += [("Enterprise value", "EV ($m)"), ("EV / Revenue", "EV / Revenue"), ("EV / EBITDA", "EV / EBITDA")]
    want += [("Premium", "Premium")] if is_prec else [("EBITDA margin", "EBITDA margin")]
    cols = [(lab, _col(ws, hr, key) or (1 if key == "Company" else None)) for key, lab in want]
    cols = [(lab, c) for lab, c in cols if c]
    fmt = lambda lab, v: (_x(v) if "EV /" in lab else _pct(v) if lab in ("EBITDA margin", "Premium") else  # noqa: E731
                          _usd(v) if lab.startswith("EV (") else str(v or ""))
    table_rows = [[fmt(lab, ws.cell(row=r, column=c).value) for lab, c in cols] for r in rows]
    stats = []
    for sk, sl in (("median", "Median"), ("p25", "25th percentile"), ("p75", "75th percentile")):
        row = [sl] + [""] * (len(cols) - 1)
        for i, (lab, c) in enumerate(cols):
            key = {"EV / Revenue": "ev_rev", "EV / EBITDA": "ev_ebitda", "EBITDA margin": "margin",
                   "Premium": "premium"}.get(lab)
            if key and i > 0:
                row[i] = fmt(lab, bk.name(f"{prefix}{key}_{sk}"))
        stats.append(row)
    m, p25, p75 = (bk.name(f"{prefix}ev_ebitda_{k}") for k in ("median", "p25", "p75"))
    what = "Precedent deals paid" if is_prec else "Peers are valued at"
    title = f"{what} a median {_x(m)} EV/EBITDA, with the middle half at {_x(p25)}-{_x(p75)}"
    prov = [bk.fact(f"{kind} EV/EBITDA median {_x(m)}, 25th percentile {_x(p25)}, 75th percentile {_x(p75)}",
                    f"{prefix}ev_ebitda_median / p25 / p75")]
    ev_i = next((i for i, (lab, _) in enumerate(cols) if lab == "EV / EBITDA"), None)
    bullets = []
    if ev_i is not None:
        pts = [(r[0], ws.cell(row=rr, column=cols[ev_i][1]).value) for r, rr in zip(table_rows, rows)]
        pts = [(nme, v) for nme, v in pts if _num(v) is not None]
        if len(pts) >= 2:
            hi_, lo_ = max(pts, key=lambda t: t[1]), min(pts, key=lambda t: t[1])
            bullets.append({"text": f"Highest: {hi_[0]} at {_x(hi_[1])}; lowest: {lo_[0]} at {_x(lo_[1])}"})
        bullets.append({"text": f"The 25th-75th percentile range ({_x(p25)}-{_x(p75)}) sets the valuation range"})
        mr, mm = bk.name(f"{prefix}ev_rev_median"), bk.name(f"{prefix}{'premium' if is_prec else 'margin'}_median")
        if _num(mr) is not None and _num(mm) is not None:
            bullets.append({"text": f"Median EV / Revenue of {_x(mr)}; median "
                                    f"{'premium to the unaffected price' if is_prec else 'EBITDA margin'} of {_pct(mm)}"})
    nm = sum(1 for r in table_rows for v in r if v == "NM")
    if nm:
        bullets.append({"text": "Loss-making rows show NM and drop out of the statistics"})
    notes = ["NM: not meaningful (negative or zero denominator); excluded from the statistics"] if nm else []
    first = len(table_rows)
    if style == "benchmark":                            # peers lined up, one panel per multiple, median lines
        def raw(lab):
            c = dict(cols).get(lab)
            return [(_num(ws.cell(row=r, column=c).value) if c else None) for r in rows]
        if is_prec:
            yr = dict(cols).get("Date")
            names = [f"{ws.cell(row=r, column=dict(cols)['Target']).value} ({ws.cell(row=r, column=dict(cols)['Acquirer']).value}"
                     + (f", {str(ws.cell(row=r, column=yr).value)[:4]})" if yr else ")") for r in rows]
            spec = [("EV / EBITDA", "ev_ebitda", "x"), ("EV / Revenue", "ev_rev", "x"), ("Premium", "premium", "%")]
        else:
            names = [str(ws.cell(row=r, column=1).value) for r in rows]
            spec = [("EV / EBITDA", "ev_ebitda", "x"), ("EV / Revenue", "ev_rev", "x"), ("EBITDA margin", "margin", "%")]
        panels = []
        for lab, key, unit in spec:
            vals = raw(lab)
            k_ = 100.0 if unit == "%" else 1.0
            st = {q: _num(bk.name(f"{prefix}{key}_{q}")) for q in ("median", "p25", "p75")}
            panels.append({"title": {"Premium": "Premium paid (%)", "EBITDA margin": "EBITDA margin (%)"}.get(
                lab, f"{lab} (x)"), "unit": unit, "decimals": 1,
                "values": [None if v is None else v * k_ for v in vals],
                **{q: (None if v is None else v * k_) for q, v in st.items()}})
        bullets = [b for b in bullets if not b["text"].startswith("Loss-making")]
        if nm:
            bullets.append({"text": "NM: not meaningful (a loss); the company drops out of that median"})
        return {"archetype": "benchmark", "fixed": True, "title": title, "title_prov": prov,
                "benchmark": {"companies": names, "panels": panels, "logos": logos or {}},
                "takeaway": {"text": "Dashed line: median; dotted lines: 25th and 75th percentiles"},
                "bullets": bullets[:4], "source": {"text": bk.sources(ws, hr) or f"{FIRM_SRC} ({ws.title} tab)",
                                                   "prov": prov}}
    return {"archetype": "table_focus", "fixed": True, "title": title, "title_prov": prov,
            "bullets": bullets,
            "table": {"columns": [lab for lab, _ in cols], "rows": table_rows + stats,
                      "highlight_rows": [first], "italic_rows": [first + 1, first + 2]},
            "source": {"text": bk.sources(ws, hr) or f"{FIRM_SRC} ({ws.title} tab)", "prov": prov},
            "footnotes": notes}


def dcf_outputs(bk: Book, prefix: str = "dcf_") -> dict | None:
    ps = bk.name(f"{prefix}per_share")
    if _num(ps) is None:
        return None
    g = {k: bk.name(f"{prefix}{k}") for k in ("wacc", "ev", "ev_exit", "tv_share", "equity", "per_share_exit")}
    loc = bk.names.get(f"{prefix}per_share")
    ws = bk.v[loc[0]]
    lab = {}
    for r in range(1, ws.max_row + 1):
        v = ws.cell(row=r, column=1).value
        if isinstance(v, str) and v not in lab:
            lab[v] = _num(ws.cell(row=r, column=2).value)
    bridge = [("Sum of PV of free cash flow ($m)", "PV of free cash flow, forecast years", _usd),
              ("PV of terminal value ($m)", "PV of terminal value (Gordon growth)", _usd),
              ("Enterprise value ($m)", "Enterprise value", _usd),
              ("Less: total debt ($m)", "Less: debt", _usd), ("Plus: cash ($m)", "Plus: cash", _usd),
              ("Less: minorities and preferred ($m)", "Less: minorities and preferred", _usd),
              ("Equity value ($m)", "Equity value", _usd), ("Diluted shares (m)", "Diluted shares (m)",
                                                            lambda v: _usd(v, 1)),
              ("Value per share ($)", "Value per share ($)", lambda v: fmt_value(v, "$")),
              ("Value per share, exit multiple ($)", "Cross-check: exit multiple ($ per share)",
               lambda v: fmt_value(v, "$")),
              ("Implied exit multiple from Gordon value (x)", "Implied EV / EBITDA at exit (x)", _x)]
    rows = [[short, f(lab[k])] for k, short, f in bridge if k in lab and lab[k] is not None]
    steps = [("Sum of PV of free cash flow ($m)", "PV of free cash flow"), ("PV of terminal value ($m)",
             "PV of terminal value"), ("Less: total debt ($m)", "Debt"), ("Plus: cash ($m)", "Cash")]
    steps += [(k, k.split(": ", 1)[1].replace(" ($m)", "").capitalize()) for k in lab
              if k.startswith(("Less: minorit", "Less: preferred", "Less: lease", "Less: unfunded", "Plus: equity inv"))]
    cats = [c_ for k, c_ in steps if lab.get(k) is not None] + ["Equity value"]
    vals = [lab[k] for k, _ in steps if lab.get(k) is not None] + [lab.get("Equity value ($m)")]
    prov = [bk.fact(f"DCF value per share {fmt_value(ps, '$')} (Gordon growth) and "
                    f"{fmt_value(_num(g['per_share_exit']), '$')} (exit multiple) at a {_pct(g['wacc'])} WACC; "
                    f"terminal value {_pct(g['tv_share'])} of EV", f"{prefix}per_share, {prefix}per_share_exit")]
    if None in vals:
        return None
    return {"archetype": "waterfall_bridge", "fixed": True,
            "title": f"The DCF puts the shares at {fmt_value(ps, '$')} at a {_pct(g['wacc'])} WACC",
            "title_prov": prov,
            "chart": {"kind": "waterfall", "categories": cats, "unit": "", "decimals": 0,
                      "series": [{"name": "$m", "values": vals}]},
            "subtitle": "From discounted cash flows to equity value, $m",
            "takeaway": {"text": f"{fmt_value(ps, '$')} per share on {_usd(lab.get('Diluted shares (m)'), 1)}m diluted shares"},
            "bullets": [{"text": f"Terminal value is {_pct(g['tv_share'])} of enterprise value"},
                        {"text": f"Exit-multiple cross-check: {fmt_value(_num(g['per_share_exit']), '$')} per share"},
                        {"text": f"WACC {_pct(g['wacc'])}; free cash flow from the integrated statements"}],
            "source": {"text": f"{FIRM_SRC} (DCF tab)", "prov": prov}}


def dcf_sensitivity(bk: Book, prefix: str = "dcf_") -> dict | None:
    hit = bk.find("WACC (%) \\ terminal growth (%)")
    ps = bk.name(f"{prefix}per_share")
    if not hit or _num(ps) is None:
        return None
    ws, gr, _ = hit
    cols = ["WACC \\ growth"] + [_pct(ws.cell(row=gr, column=c).value) for c in range(2, 7)]
    vals = [[_num(ws.cell(row=r, column=c).value) for c in range(2, 7)] for r in range(gr + 1, gr + 6)]
    rows = [[_pct(ws.cell(row=gr + 1 + i, column=1).value)] + [fmt_value(v, "$") if v is not None else "NM"
                                                                for v in vals[i]] for i in range(5)]
    xh = bk.find("WACC (%) \\ exit multiple (x)")
    if xh:                                                     # the exit-multiple grid stacked under the first
        wx, xr, _ = xh
        rows.append(["WACC \\ exit"] + [_x(wx.cell(row=xr, column=c).value) for c in range(2, 7)])
        for r in range(xr + 1, xr + 6):
            rows.append([_pct(wx.cell(row=r, column=1).value)] +
                        [fmt_value(_num(wx.cell(row=r, column=c).value), "$") if _num(wx.cell(row=r, column=c).value)
                         is not None else "NM" for c in range(2, 7)])
    flat = [v for row in vals for v in row if v is not None]
    lo, hi = min(flat), max(flat)
    prov = [bk.fact(f"DCF sensitivity: {fmt_value(lo, '$')} to {fmt_value(hi, '$')} per share across WACC and "
                    "terminal growth", f"{ws.title}!B{gr + 1}:F{gr + 5}")]
    return {"archetype": "table_focus", "fixed": True,
            "title": f"Value per share runs {fmt_value(lo, '$')}-{fmt_value(hi, '$')} across WACC and growth",
            "title_prov": prov, "table": {"columns": cols, "rows": rows, "italic_rows": [5] if xh else [], "keep_table": True,
                                          "heat_grid": [{"rows": list(range(5)), "center": ps, "base": [2, 3]}] + (
                                              [{"rows": list(range(6, 11)), "center": _num(bk.name(f"{prefix}per_share_exit")),
                                                "base": [8, 3]}] if xh else [])},
            "takeaway": {"text": "Heat map around the base case (boxed): darker = further from it"},
            "bullets": [{"text": f"Base case in the centre: {fmt_value(vals[2][2], '$')}"},
                        {"text": f"Highest WACC and lowest growth: {fmt_value(vals[4][0], '$')}"},
                        {"text": f"Lowest WACC and highest growth: {fmt_value(vals[0][4], '$')}"}],
            "source": {"text": f"{FIRM_SRC} (DCF tab sensitivity grid)", "prov": prov}}


SUMMARY_LINES = [("Revenue ($m)", "Revenue", "usd"), ("Revenue growth (%)", "Growth", "pct"),
                 ("Gross margin (%)", "Gross margin", "pct"), ("EBITDA ($m)", "EBITDA", "usd"),
                 ("EBITDA margin (%)", "EBITDA margin", "pct"), ("Net income ($m)", "Net income", "usd"),
                 ("Cash from operating activities ($m)", "Operating cash flow", "usd"),
                 ("Capital expenditure ($m, negative)", "Capex", "usd"), ("Dividends paid ($m, negative)", "Dividends",
                                                                           "usd"),
                 ("Cash ($m)", "Cash", "usd"), ("Term debt ($m)", "Term debt", "usd"),
                 ("Total assets", "Total assets", "usd"), ("Total equity", "Total equity", "usd")]


def financial_summary(bk: Book, sheet: str = "Model") -> dict | None:
    if sheet not in bk.v.sheetnames:
        return None
    ws = bk.v[sheet]
    hdr = next((r for r in range(1, 12) if ws.cell(row=r, column=1).value == "Line item"), None)
    if hdr is None:
        return None
    years = [ws.cell(row=hdr, column=c).value for c in range(2, ws.max_column + 1)]
    years = [y for y in years if y]
    where = {}
    for r in range(hdr + 1, ws.max_row + 1):
        lab = ws.cell(row=r, column=1).value
        if isinstance(lab, str) and lab not in where:
            where[lab] = r
    rows = []
    for lab, short, kind in SUMMARY_LINES:
        r = where.get(lab)
        if r is None:
            continue
        vals = [ws.cell(row=r, column=c).value for c in range(2, 2 + len(years))]
        rows.append([short] + [("" if _num(v) is None else _pct(v) if kind == "pct" else _usd(v)) for v in vals])
    if len(rows) < 3:
        return None
    rr, er = where.get("Revenue ($m)"), where.get("EBITDA ($m)")
    first_f = 3                                                # column C: first forecast year
    last = 1 + len(years)
    r0, r1 = _num(ws.cell(row=rr, column=2).value), _num(ws.cell(row=rr, column=last).value)
    n = len(years) - 1
    cagr = (r1 / r0) ** (1 / n) - 1 if r0 and r1 and r0 > 0 and n > 0 else None
    m0, m1 = _num(ws.cell(row=er, column=first_f).value), _num(ws.cell(row=er, column=last).value)
    prov = [bk.fact(f"Revenue grows from ${_usd(r0)}m ({years[0]}) to ${_usd(r1)}m ({years[-1]}), "
                    f"{_pct(cagr)} a year", f"{sheet} revenue row")]
    title = f"Revenue compounds {_pct(cagr)} a year to ${_usd(r1)}m by {years[-1]}" if cagr is not None else \
        "The integrated model in summary"
    bullets = [{"text": f"Revenue reaches ${_usd(r1)}m in {years[-1]}, from ${_usd(r0)}m in {years[0]}"}]
    mr = where.get("EBITDA margin (%)")
    if mr:
        a, b = _num(ws.cell(row=mr, column=first_f).value), _num(ws.cell(row=mr, column=last).value)
        if a is not None and b is not None:
            bullets.append({"text": f"EBITDA margin moves from {_pct(a)} to {_pct(b)}"})
    for lab, txt in (("Cash ($m)", "Cash"), ("Term debt ($m)", "Term debt")):
        r = where.get(lab)
        if r:
            a, b = _num(ws.cell(row=r, column=2).value), _num(ws.cell(row=r, column=last).value)
            if a is not None and b is not None:
                bullets.append({"text": f"{txt} goes from ${_usd(a)}m to ${_usd(b)}m"})
    keyl = ("Revenue", "Growth", "EBITDA", "EBITDA margin", "Net income", "Operating cash flow")
    rev = [_num(ws.cell(row=where["Revenue ($m)"], column=c).value) for c in range(2, 2 + len(years))]
    ebd = [_num(ws.cell(row=where["EBITDA ($m)"], column=c).value) for c in range(2, 2 + len(years))] \
        if "EBITDA ($m)" in where else []
    yrs, rv, eb = zip(*[(str(y), a, b) for y, a, b in zip(years, rev, ebd or [None] * len(years))
                        if a is not None and b is not None]) if ebd else ([], [], [])
    parts = []
    if len(yrs) >= 3:
        parts.append({"type": "chart", "heading": "Revenue and EBITDA, $m",
                      "chart": {"kind": "column", "categories": list(yrs), "unit": "", "decimals": 0,
                                "series": [{"name": "Revenue", "values": [round(v) for v in rv]},
                                           {"name": "EBITDA", "values": [round(v) for v in eb]}]}})
    parts.append({"type": "table", "heading": "Key lines, $m",
                  "table": {"columns": ["$m"] + [str(y) for y in years], "rows": [r_ for r_ in rows if r_[0] in keyl]}})
    return {"archetype": "grid", "fixed": True, "layout": "halves_v" if len(parts) == 2 else "full",
            "title": title, "title_prov": prov, "parts": parts,
            "footnotes": [b["text"] for b in bullets],
            "source": {"text": f"{FIRM_SRC} ({sheet} tab: integrated income statement, balance sheet and cash flow); "
                               "historical year from company filings", "prov": prov}}


def scenario_chart(bk: Book) -> dict | None:
    hit = bk.find("Case")
    if not hit:
        return None
    ws, hr, _ = hit
    rows = _table_rows(ws, hr, stop=("Selected case in the live model",))
    vals = {ws.cell(row=r, column=1).value: _num(ws.cell(row=r, column=6).value) for r in rows}
    b, u, d = vals.get("Base case"), vals.get("Bull case"), vals.get("Bear case")
    if None in (b, u, d):
        return None
    prov = [bk.fact(f"Value per share: bear {fmt_value(d, '$')}, base {fmt_value(b, '$')}, bull {fmt_value(u, '$')}",
                    f"{ws.title} scenario table")]
    return {"archetype": "bar_takeaway", "fixed": True,
            "title": f"Cases span {fmt_value(d, '$')} to {fmt_value(u, '$')} per share around a "
                     f"{fmt_value(b, '$')} base", "title_prov": prov,
            "chart": {"kind": "column", "categories": ["Bear case", "Base case", "Bull case"], "unit": "",
                      "decimals": 2, "series": [{"name": "Value per share ($)", "values": [d, b, u]}],
                      "highlight_categories": ["Base case"]},
            "takeaway": {"text": "DCF value per share by case ($)"},
            "bullets": [{"text": f"The bull case adds {fmt_value(u - b, '$')} per share to the base"},
                        {"text": f"The bear case takes {fmt_value(b - d, '$')} off it"},
                        {"text": "Each case moves every driver to the end of its evidenced range"}],
            "source": {"text": f"{FIRM_SRC} (pinned Base / Bull / Bear case tabs)", "prov": prov}}


def weighting_table(bk: Book) -> dict | None:
    hit = bk.find("Weight used (%)")
    if not hit:
        return None
    ws, hr, _ = hit
    rows = _table_rows(ws, hr, stop=("Total",))
    out = []
    for r in rows:
        out.append([str(ws.cell(row=r, column=1).value), fmt_value(_num(ws.cell(row=r, column=2).value), "$"),
                    fmt_value(_num(ws.cell(row=r, column=3).value), "$"), _pct(ws.cell(row=r, column=11).value),
                    _pct(ws.cell(row=r, column=12).value)])
    wv = bk.name("weighted_value_per_share")
    if _num(wv) is None or len(out) < 2:
        return None
    lead = max(rows, key=lambda r: _num(ws.cell(row=r, column=12).value) or 0)
    prov = [bk.fact(f"Weighted value per share {fmt_value(wv, '$')}", "weighted_value_per_share")]
    short = lambda r: str(ws.cell(row=r, column=1).value).split(" (")[0]  # noqa: E731
    harvey = [{"label": short(r), "scores": [round((_num(ws.cell(row=r, column=c).value) or 0) * 4, 2)
                                             for c in range(5, 10)]} for r in rows]
    weights = [round((_num(ws.cell(row=r, column=12).value) or 0) * 100, 1) for r in rows]
    return {"archetype": "grid", "fixed": True, "layout": "halves",
            "title": f"Weighting the methods gives {fmt_value(wv, '$')} per share, led by {short(lead).lower()}",
            "title_prov": prov,
            "parts": [{"type": "harvey", "heading": "How well each method fits (full ball = strong)",
                       "criteria": ["Data quality", "Peer fit", "Terminal value", "Stage", "Deal context"],
                       "rows": harvey},
                      {"type": "chart", "heading": "Weight used (%)",
                       "chart": {"kind": "bar", "categories": [short(r) for r in rows], "unit": "%", "decimals": 0,
                                 "series": [{"name": "Weight used (%)", "values": weights}],
                                 "highlight_categories": [short(lead)]}}],
            "footnotes": ["Scores 0-1 per factor (quarter balls); weight = normalised product, rounded to 5%; "
                          "the reason for each score is in the model"] + [x["text"] for x in _not_used(ws, hr)],
            "source": {"text": f"{FIRM_SRC} ({ws.title} tab)", "prov": prov}}


def _not_used(ws, weight_hdr: int) -> list[dict]:
    """One bullet naming the methods the rules excluded (from the selection table above the weights)."""
    names = []
    for r in range(1, weight_hdr):
        if ws.cell(row=r, column=2).value == "No":
            names.append(str(ws.cell(row=r, column=1).value).split(" (")[0].split(" / ")[0].lower())
    return [{"text": "Not used, as they do not fit this company: " + ", ".join(names[:5])}] if names else []


def _why(bk: Book, ws, row: int) -> str:
    """The method's fit rationale from the selection table, without clauses carrying numbers the slide lacks."""
    name = str(ws.cell(row=row, column=1).value)
    for r in range(1, row):
        if ws.cell(row=r, column=1).value == name and isinstance(ws.cell(row=r, column=4).value, str):
            parts = [p.strip() for p in re.split(r"[;.]", ws.cell(row=r, column=4).value) if p.strip()]
            keep = [p for p in parts if not re.search(r"\d", p)]
            return (keep[0][0].lower() + keep[0][1:]) if keep else ""
    return ""


def lbo_page(bk: Book) -> dict | None:
    irr, moic = bk.name("lbo_irr"), bk.name("lbo_moic")
    lo, hi = bk.name("lbo_floor_low"), bk.name("lbo_floor_high")
    hit = bk.find("Purchase enterprise value ($m)")
    if _num(irr) is None or not hit:
        return None
    ws, r0, _ = hit
    rows = []
    r = r0
    while ws.cell(row=r, column=1).value and not str(ws.cell(row=r, column=1).value).startswith("Total debt /"):
        lab, v = str(ws.cell(row=r, column=1).value), _num(ws.cell(row=r, column=2).value)
        rows.append([lab.replace(" ($m)", ""), _usd(v)])
        r += 1
    lev = _num(ws.cell(row=r, column=2).value)
    rows.append(["Total debt / LTM EBITDA", _x(lev)])
    rows.append(["IRR / money multiple", f"{_pct(irr)} / {_x(moic)}"])
    rows.append(["Price a sponsor could pay ($ per share)", f"{fmt_value(_num(lo), '$')}-{fmt_value(_num(hi), '$')}"])
    prov = [bk.fact(f"LBO: IRR {_pct(irr)}, money multiple {_x(moic)}; a sponsor could pay "
                    f"{fmt_value(_num(lo), '$')}-{fmt_value(_num(hi), '$')} per share", "lbo_irr, lbo_floor_low/high")]
    hdr = next((rr for rr in range(r, ws.max_row + 1) if ws.cell(row=rr, column=2).value == "Entry"), None)
    series = []
    if hdr:
        years = ["Entry"] + [str(ws.cell(row=hdr, column=c).value) for c in range(3, ws.max_column + 1)
                             if ws.cell(row=hdr, column=c).value]
        for rr in range(hdr + 1, ws.max_row + 1):
            lab = ws.cell(row=rr, column=1).value
            if isinstance(lab, str) and lab.endswith(": closing balance ($m)"):
                tr = lab.split(":")[0]
                entry = next((_num(ws.cell(row=q, column=2).value) for q in range(r0, hdr)
                              if ws.cell(row=q, column=1).value == f"{tr} ($m)"), None)
                vals = [entry] + [_num(ws.cell(row=rr, column=c).value) for c in range(3, 3 + len(years) - 1)]
                if None not in vals:
                    series.append({"name": tr, "values": [round(v, 1) for v in vals]})
    uses = [x for x in rows if x[0] in ("Purchase enterprise value", "Transaction and financing fees", "Total uses")]
    srcs = [x for x in rows if x not in uses and x[0] not in ("Total sources",)]
    tiles = [{"value": _pct(irr), "label": "Sponsor IRR"}, {"value": _x(moic), "label": "Money multiple"},
             {"value": _x(lev), "label": "Entry debt / LTM EBITDA"},
             {"value": f"${_num(lo):,.0f}-{_num(hi):,.0f}", "label": "Price a sponsor could pay ($)"}]
    parts = []
    if len(series) >= 1 and len(series[0]["values"]) >= 3:
        parts.append({"type": "chart", "heading": "Debt by tranche, $m: paid down from cash flow",
                      "chart": {"kind": "stacked_column", "categories": years, "unit": "", "decimals": 0,
                                "series": series}})
    parts.append({"type": "stats", "heading": "Returns and the floor", "items": tiles})
    parts.append({"type": "table", "heading": "Sources and uses, $m",
                  "table": {"columns": ["Item", "$m"], "rows": [[a.replace("Purchase enterprise value", "Purchase EV")
                                                                .replace("Transaction and financing fees", "Fees"), b]
                                                               for a, b in uses + srcs]}})
    return {"archetype": "grid", "fixed": True, "layout": "main_stack" if len(parts) == 3 else "halves",
            "title": f"A sponsor earns {_pct(irr)} at this price and could pay up to {fmt_value(_num(hi), '$')} per share",
            "title_prov": prov, "parts": parts,
            "source": {"text": f"{FIRM_SRC} (LBO tab, operating forecast from the integrated statements)", "prov": prov}}


def merger_page(bk: Book) -> dict | None:
    acc, pf, sa = bk.name("ma_accretion"), bk.name("ma_pf_eps"), bk.name("ma_standalone_eps")
    be, own, con = bk.name("ma_breakeven_synergies"), bk.name("ma_pf_ownership"), bk.name("ma_ni_contribution")
    hit = bk.find("Premium (%) \\ stock (%)")
    if _num(acc) is None or not hit:
        return None
    ws, gh, _ = hit
    cols = ["Premium \\ stock"] + [_pct(ws.cell(row=gh, column=c).value) for c in range(2, 7)]
    rows = [[_pct(ws.cell(row=r, column=1).value)] + [_pct(ws.cell(row=r, column=c).value) for c in range(2, 7)]
            for r in range(gh + 1, gh + 6)]
    word = "accretive" if acc >= 0 else "dilutive"
    prov = [bk.fact(f"Year-one EPS {word} by {_pct(abs(acc))}; breakeven synergies ${_usd(be)}m; acquirer "
                    f"ownership {_pct(own)}", "ma_accretion, ma_breakeven_synergies, ma_pf_ownership")]
    tiles = [{"value": _pct(acc), "label": "Year-one EPS accretion / (dilution)"},
             {"value": f"${_num(pf):,.2f}", "label": f"Pro forma EPS (standalone ${_num(sa):,.2f})"},
             {"value": f"${_usd(be)}m", "label": "Pre-tax synergies to break even"},
             {"value": _pct(own), "label": f"Acquirer ownership (contributes {_pct(con)} of earnings)"}]
    return {"archetype": "grid", "fixed": True, "layout": "halves_v",
            "title": f"The deal is {word} by {_pct(abs(acc))} in year one; breakeven needs ${_usd(be)}m of synergies",
            "title_prov": prov,
            "parts": [{"type": "table", "heading": "EPS accretion (%) by premium and share paid in stock",
                       "table": {"columns": cols, "rows": rows, "keep_table": True,
                                 "heat_grid": {"center": 0.0, "base": [2, 3]}}},
                      {"type": "stats", "heading": "Base case", "items": tiles}],
            "footnotes": ["Heat map: blue accretive, orange dilutive, boxed cell = the proposed terms"],
            "source": {"text": f"{FIRM_SRC} ({ws.title} tab)", "prov": prov}}


def pages_from_workbook(path: str) -> tuple[list[dict], list[dict]]:
    """Banker pages in deck order (financial summary, football field, weighting, DCF outputs, DCF sensitivity,
    scenarios, comps, precedents)
    and the facts behind them. Pages whose inputs are not in the workbook are skipped."""
    bk = Book(path)
    pages = []
    for fn in (financial_summary, football_field, weighting_table, dcf_outputs, dcf_sensitivity, scenario_chart,
               comps, lambda b: comps(b, "prec_", "Precedent transactions"), lbo_page, merger_page):
        try:
            p = fn(bk)
        except Exception:                                      # noqa: BLE001 - one unreadable table skips its page
            p = None
        if p:
            p.setdefault("node", f"W{len(pages) + 1}")
            p.setdefault("section", "Valuation")
            pages.append(p)
    return pages, bk.facts


def finance_workbook(paths: list[str]) -> str | None:
    """The first workbook that carries the sheets engine's valuation or model outputs."""
    for p in paths:
        if not str(p).lower().endswith((".xlsx", ".xlsm")) or not Path(p).exists():
            continue
        try:
            names = {k.lower() for k in load_workbook(p, read_only=False).defined_names.keys()}
        except Exception:                                      # noqa: BLE001
            continue
        if names & {"dcf_per_share", "comps_ev_ebitda_median", "weighted_value_per_share", "ts_revenue_final",
                    "lbo_irr", "ma_accretion"}:
            return str(p)
    return None
