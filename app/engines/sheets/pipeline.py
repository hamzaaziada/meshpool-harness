"""Fixed-pipeline models: one model call fills sourced inputs, code builds the whole workbook.

The agentic sheets engine asks the model to write and debug a Python build script over many turns. A small model
(DeepSeek flash) could not: one run wrote '=B{ocf_row}' into whole columns, the next a #VALUE! chain, the next
saved a skeleton with no formulas, and one repeated a refused command 40 times. For the request types below the
workbook's structure is known, so it is written in code (engines/sheets/builder.py) and the model only supplies the
inputs, each with its source fact and rationale, the way the credit engine works. Quality then does not depend on
which model is plugged in.

    if pipeline.applies(task):
        res = pipeline.build(task, ws, client, model, task_id)     # {"path", "inputs", "missing", "notes"}

Recipes: capital allocation (return cash vs fund growth, buybacks, special dividends, uses of cash).
MH_SHEETS_PIPELINE=0 sends every request to the agent instead.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

CAPITAL_ALLOCATION = re.compile(r"(?i)capital allocation|return (?:more )?cash|buy ?backs?|special dividend|"
                                r"uses of cash|cash return")

# name, label, number format, what the model must give (units)
CA_INPUTS = [
    ("revenue_base", "Revenue, last full fiscal year ($m)", "usd", "last reported annual revenue, $ millions"),
    ("rev_growth", "Revenue growth per year", "pct", "decimal, e.g. 0.06 for 6%"),
    ("ocf_margin", "Operating cash flow / revenue", "pct", "decimal: last year's operating cash flow / revenue"),
    ("capex_base", "Capital expenditure, first forecast year ($m)", "usd", "$ millions, the plan or last year"),
    ("capex_growth", "Capex growth per year", "pct", "decimal"),
    ("cash", "Cash and equivalents, opening ($m)", "usd", "$ millions at the last balance sheet"),
    ("min_cash", "Cash floor the board keeps ($m)", "usd", "$ millions; say if it is an assumption"),
    ("shares", "Shares outstanding (m)", "num", "millions of shares"),
    ("share_price", "Share price for buybacks ($)", "usd2", "$ per share, latest close"),
    ("dps", "Regular dividend per share, first forecast year ($)", "usd2", "$ per share per year"),
    ("dps_growth", "Regular dividend growth per year", "pct", "decimal"),
    ("special_dps", "Special dividend per share, first forecast year ($)", "usd2", "$ per share; 0 if none"),
    ("buyback", "Share buyback per year ($m)", "usd", "$ millions spent each year; 0 if none"),
    ("cash_yield", "Interest earned on cash", "pct", "decimal: the short-term rate (Treasury bill yield fact), "
                   "never the 10-year yield; the fact's value as stated"),
    ("down_growth", "Downside: revenue growth per year", "pct", "decimal: a weak-demand case anchored on the "
                    "history (e.g. half the 3-year growth); say why"),
    ("down_margin_cut", "Downside: cut to the operating cash flow margin", "pct", "decimal points taken off the "
                        "margin, e.g. 0.01; anchored on how far it moved in the history"),
    ("capex_overrun", "Downside: capex overrun", "pct", "decimal, e.g. 0.15 for 15% over plan"),
]


def applies(task: str) -> str | None:
    """The recipe for this request, or None (the agent builds it)."""
    if os.environ.get("MH_SHEETS_PIPELINE", "1") == "0":
        return None
    return "capital_allocation" if CAPITAL_ALLOCATION.search(task or "") else None


PROMPT = """You fill the inputs of a capital-allocation model for a finance professional. Use ONLY the facts and
documents given: each value comes from a fact (cite its id, e.g. "F7") or is a labelled assumption with its
rationale. Growth rates are anchored on the company's own history ("grew x% a year over three years" facts) or its
guidance: cite that fact in the source and say in the rationale whether you keep, fade or cut the historical rate
and why. Only a floor the board would keep and similar policy choices are plain assumptions. Never invent a
reported figure. Units are fixed: money in $ millions (10-K figures in billions times 1000),
per-share amounts in $, rates as decimals (6% = 0.06), shares in millions.
For rev_growth, capex_growth, min_cash, buyback and down_growth also give "low" and "high": the range a committee
would test, with the reason in the rationale. When a value departs from the history it cites (e.g. 6% growth against
a 7.8% three-year history), the rationale says why (guidance, base effects, the plan).
Return ONLY JSON:
{"company": "short company name", "title": "workbook title", "first_year": 2027, "years": 5,
 "inputs": {"<name>": {"value": number, "low": number, "high": number, "source": "F12 10-K FY2026 ..." or
 "assumption", "rationale": "one line"}}}
with exactly these input names:
"""


def ask_inputs(task: str, client, model: str, task_id: str) -> dict:
    spec = "\n".join(f'- "{n}": {lab} ({unit})' for n, lab, _, unit in CA_INPUTS)
    last = ""
    for budget in (4000, 8000):
        try:
            r = client.chat(model=model, messages=[{"role": "system", "content": PROMPT + spec},
                                                   {"role": "user", "content": task[:30000]}],
                            max_tokens=budget, temperature=0.0, task_id=f"{task_id}-inputs",
                            extra={"response_format": {"type": "json_object"}})
            txt = (((r or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            if "{" in txt:
                return json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
            last = "no JSON in the reply"
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
    raise RuntimeError(f"the model did not return the model inputs: {last}")


def _col(n_years: int) -> str:
    from openpyxl.utils import get_column_letter
    return get_column_letter(1 + n_years)


def _num(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(v or ""))
    return float(m.group(0).replace(",", "")) if m else None


def build(task: str, ws, client, model: str, task_id: str = "sheets") -> dict:
    """Write the workbook into ws.root. Returns {"path", "inputs", "missing", "notes"}; raises when inputs are missing."""
    from engines.sheets import builder as B
    got = ask_inputs(task, client, model, task_id)
    raw = got.get("inputs") or {}
    inputs, missing = {}, []
    for name, label, fmt, _ in CA_INPUTS:
        it = raw.get(name) if isinstance(raw.get(name), dict) else {"value": raw.get(name)}
        v = _num(it.get("value"))
        if v is None:
            missing.append(name)
            continue
        if fmt == "pct" and abs(v) > 1.5:             # 6 meant 6%
            v /= 100.0
        lo, hi = _num(it.get("low")), _num(it.get("high"))
        if fmt == "pct":
            lo = lo / 100.0 if lo is not None and abs(lo) > 1.5 else lo
            hi = hi / 100.0 if hi is not None and abs(hi) > 1.5 else hi
        if lo is not None and hi is not None and lo > hi:
            lo, hi = hi, lo
        inputs[name] = {"value": v, "source": str(it.get("source") or "assumption"),
                        "rationale": str(it.get("rationale") or ""), "low": lo, "high": hi}
    if missing:
        raise RuntimeError("the model left these inputs out: " + ", ".join(missing))
    first = int(_num(got.get("first_year")) or time.gmtime().tm_year + 1)
    n = max(3, min(10, int(_num(got.get("years")) or 5)))
    years = list(range(first, first + n))
    company = str(got.get("company") or "Company").strip()
    title = str(got.get("title") or f"{company} capital allocation model")
    wb = B.banker_workbook(title, "Cash generated and its uses: can the business fund growth and the cash returned?",
                           date=time.strftime("%Y-%m-%d"), middle=("Inputs", "Model", "Outputs"))
    B.title(wb["Inputs"], "Inputs: every number with its source")
    book = B.assumption_book(wb["Inputs"])
    sections = {"revenue_base": "Operations", "cash": "Balance sheet and shares", "dps": "Cash returned",
                "cash_yield": "Rates", "down_growth": "Downside case"}
    for name, label, fmt, _ in CA_INPUTS:
        if name in sections:
            book.section(sections[name])
        it = inputs[name]
        book.add(label, it["value"], fmt, name=name, source=it["source"], rationale=it["rationale"],
                 low=it.get("low"), high=it.get("high"))
    B.title(wb["Model"], f"{company}: cash generated and its uses, $m")
    ca = B.capital_allocation(wb["Model"], 3, years, names={"down_growth": "down_growth",
                                                             "down_margin_cut": "down_margin_cut",
                                                             "capex_overrun": "capex_overrun"})
    out = wb["Outputs"]
    B.title(out, "Outputs: the answer first")
    out["A3"], out["A4"] = "Question", "Answer"
    out["B3"] = (f"Can {company} fund its capital spending plan and the cash it returns over {years[0]}-{years[-1]} "
                 "without cash falling below the board's floor?")
    B._f(out, "B4", '="Over the forecast the business generates $"&TEXT(' + ca["fcf_total"] + ',"#,##0")&"m of free '
         'cash flow and returns $"&TEXT(' + ca["uses_total"] + ',"#,##0")&"m to shareholders; cash ends at $"&TEXT('
         + ca["cash_end"] + ',"#,##0")&"m and "&IF(' + ca["min_headroom"] + '>=0,"never falls below the $"&TEXT('
         'min_cash,"#,##0")&"m floor (lowest headroom $"&TEXT(' + ca["min_headroom"] + ',"#,##0")&"m).","falls $"&'
         'TEXT(-' + ca["min_headroom"] + ',"#,##0")&"m below the $"&TEXT(min_cash,"#,##0")&"m floor: the plan needs '
         'debt or a smaller return.")&" In the downside (lower growth, thinner margin, capex overrun) "&IF('
         + ca["down_suspended"] + '>0,"the buyback is suspended in "&' + ca["down_suspended"] + '&" year(s) and ","")'
         '&"cash "&IF(' + ca["down_min_headroom"] + '>=0,"stays above the floor (lowest headroom $"&TEXT('
         + ca["down_min_headroom"] + ',"#,##0")&"m).","still falls $"&TEXT(-' + ca["down_min_headroom"]
         + ',"#,##0")&"m below the floor: the special dividend or capex must give.")')
    for ref in ("A3", "A4"):
        out[ref].font = B.Font(name=B.FONT, bold=True)
    out["B3"].alignment = out["B4"].alignment = B.Alignment(wrap_text=False, vertical="top")   # runs across the
    # empty cells to its right (merged cells are not allowed in a data area, X10)
    model_ws, rws = wb["Model"], ca["rows"]
    out["A6"] = "Cash against the floor, $m"
    out["A6"].font = B.Font(name=B.FONT, bold=True)
    B.header(out, 7, ["", *[str(y) for y in years]])
    out["A8"], out["A9"] = "Closing cash", "Cash floor"
    from openpyxl.utils import get_column_letter as _gcl
    for i in range(len(years)):
        c = _gcl(2 + i)
        B._f(out, f"{c}8", f"='Model'!{c}{rws['close']}", "usd")
        B._f(out, f"{c}9", "=min_cash", "usd")
    B.chart(out, "line", (1, 8, 1 + len(years), 9), (2, 7, 1 + len(years), 7), "D12",
            "Closing cash against the board's floor ($m)", y_fmt="usd")
    from openpyxl.chart import Reference as _R  # the line chart reads its two series by row
    ch = out._charts[-1]
    ch.series.clear() if hasattr(ch.series, "clear") else None
    for rr in (8, 9):
        ch.add_data(_R(out, min_col=1, min_row=rr, max_col=1 + len(years), max_row=rr), from_rows=True,
                    titles_from_data=True)
    ch.set_categories(_R(out, min_col=2, min_row=7, max_col=1 + len(years), max_row=7))
    B.header(out, 11, ["Measure", "Value"])
    rows = [("Free cash flow, all forecast years ($m)", f"={ca['fcf_total']}", "usd"),
            ("Cash returned, all years ($m)", f"={ca['uses_total']}", "usd"),
            (f"Closing cash, {years[-1]} ($m)", f"={ca['cash_end']}", "usd"),
            ("Lowest headroom over the cash floor ($m)", f"={ca['min_headroom']}", "usd"),
            ("Closing shares (m)", f"={ca['shares_end']}", "num"),
            ("Funded without new debt?", f'=IF({ca["min_headroom"]}>=0,"Yes","No: cash falls below the floor")', None),
            ("Downside: closing cash ($m)", f"={ca['down_cash_end']}", "usd"),
            ("Downside: lowest headroom over the floor ($m)", f"={ca['down_min_headroom']}", "usd"),
            ("Funded in the downside?", f'=IF({ca["down_min_headroom"]}>=0,"Yes","No: in the downside cash falls below '
             'the floor")', None),
            ("Downside: years the buyback is suspended", f"={ca['down_suspended']}", "num"),
            ("Closing cash across growth +/-2pp and investment +/-20% ($m, low)", f"={ca['grid_low']}", "usd"),
            ("Closing cash across growth +/-2pp and investment +/-20% ($m, high)", f"={ca['grid_high']}", "usd"),
            ("Year of the lowest headroom, base", f"=INDEX($B$7:${_col(len(years))}$7,MATCH(MIN('Model'!$B${rws['head']}:"
             f"${_col(len(years))}${rws['head']}),'Model'!$B${rws['head']}:${_col(len(years))}${rws['head']},0))", None),
            ("Year of the lowest headroom, downside", f"=INDEX($B$7:${_col(len(years))}$7,MATCH(MIN('Model'!$B${rws['d_head']}:"
             f"${_col(len(years))}${rws['d_head']}),'Model'!$B${rws['d_head']}:${_col(len(years))}${rws['d_head']},0))", None)]
    for i, (lab, expr, fmt) in enumerate(rows, start=12):
        out.cell(row=i, column=1, value=lab)
        B._f(out, f"B{i}", expr, fmt)
    B.widths(out, {"A": 46, "B": 22, "C": 14, "D": 14, "E": 14, "F": 14, "G": 14})
    B.widths(wb["Model"], default=14, first=40)
    safe = re.sub(r"[^A-Za-z0-9]+", "_", company).strip("_") or "Company"
    path = Path(ws.root) / f"{safe}_capital_allocation_model.xlsx"
    wb.save(path)
    notes = [f"Built by the capital-allocation recipe in code: {len(CA_INPUTS)} sourced inputs, {n} forecast years "
             f"({years[0]}-{years[-1]}), every other number a formula."]
    assumed = [n for n, it in inputs.items() if it["source"].lower().startswith("assum")]
    if assumed:
        notes.append("Inputs that are assumptions (not reported figures): " + ", ".join(assumed) + ".")
    return {"path": str(path), "inputs": inputs, "missing": missing, "notes": notes, "years": years}
