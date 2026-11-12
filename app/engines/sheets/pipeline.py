"""Token-efficient workbook pipeline: model writes a compact model spec (1 call); a deterministic builder produces a
banker-style workbook with live formulas; LibreOffice recalculation + QA; one targeted repair call if needed.

Spec:
{"title": str, "purpose": str, "periods": ["2027", ...],
 "inputs": [{"key": "growth", "label": "Growth per year (%)", "value": 0.1, "fmt": "pct"}],          # scalars
 "input_series": [{"key": "units", "label": "Units sold", "values": [..one per period..], "fmt": "int"}],
 "calc": [{"key": "revenue", "label": "Revenue ($K)", "first": "=start_rev", "next": "={revenue@prev}*(1+growth)",
           "fmt": "usd"},                                   # or "formula" for every period
          {"key": "profit", "label": "Profit ($K)", "formula": "={revenue}-{cost}", "fmt": "usd", "total": true}],
 "outputs": [{"label": "Total revenue ($K)", "formula": "=SUM({revenue@all})", "fmt": "usd"}],
 "checks": [{"label": "Profit ties to revenue minus cost", "diff": "={profit@last}-({revenue@last}-{cost@last})"}],
 "chart": {"type": "bar"|"line", "rows": ["revenue", "profit"], "title": "Revenue and profit ($K)"}}
References: scalar inputs by key (named ranges); {row} same period; {row@prev}; {row@first}; {row@last}; {row@all}.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from openpyxl.utils import get_column_letter  # noqa: E402

from engines.sheets import builder as B  # noqa: E402
from engines.sheets.qa import check as qa_check, value as qa_value  # noqa: E402

MODEL = __import__("os").environ.get("MH_MODEL", "deepseek-v4.1-flash")
FIRST_COL = 2           # column B holds period 1


class SpecError(ValueError):
    pass


def build(spec: dict, path: str) -> dict:
    periods = [str(p) for p in spec.get("periods") or ["Value"]]
    n = len(periods)
    wb = B.banker_workbook(spec.get("title", "Model"), spec.get("purpose", ""), "Meshpool Harness", "")
    I, C, O = wb["Inputs"], wb["Calc"], wb["Outputs"]
    B.title(I, "Inputs")
    B.title(C, "Calculations")
    B.title(O, "Summary")
    names = {}
    r = 3
    B.section(I, r, "Assumptions", 3)
    r += 1
    for inp in spec.get("inputs", []):
        key = re.sub(r"\W", "_", inp["key"])
        I.cell(row=r, column=1, value=inp.get("label", key))
        B.input_cell(I, f"B{r}", inp["value"], inp.get("fmt"), name=key)
        names[key] = f"'Inputs'!$B${r}"
        r += 1
    series_rows = {}
    if spec.get("input_series"):
        r += 1
        B.header(I, r, ["Series"] + periods)
        r += 1
        for s in spec["input_series"]:
            I.cell(row=r, column=1, value=s.get("label", s["key"]))
            vals = s["values"]
            if len(vals) != n:
                raise SpecError(f"input_series {s['key']}: needs {n} values, got {len(vals)}")
            for j, v in enumerate(vals):
                B.input_cell(I, f"{get_column_letter(FIRST_COL + j)}{r}", v, s.get("fmt"))
            series_rows[s["key"]] = r
            r += 1
    B.header(C, 3, ["Line item"] + periods)
    calc_rows = {row["key"]: 4 + i for i, row in enumerate(spec.get("calc", []))}
    for k in calc_rows:
        if k in series_rows:
            raise SpecError(f"key {k} used for both a calc row and an input series")

    def ref(expr: str, col: int, sheet_prefix: str = "") -> str:
        def sub(m):
            key, mod = m.group(1), (m.group(2) or "")
            if key in calc_rows:
                sh, row = "Calc", calc_rows[key]
            elif key in series_rows:
                sh, row = "Inputs", series_rows[key]
            else:
                raise SpecError(f"unknown reference {{{key}}}; known rows: {sorted(calc_rows) + sorted(series_rows)}")
            first, last = get_column_letter(FIRST_COL), get_column_letter(FIRST_COL + n - 1)
            if mod == "@all":
                return f"'{sh}'!{first}{row}:{last}{row}"
            c = {"": col, "@prev": col - 1, "@first": FIRST_COL, "@last": FIRST_COL + n - 1}.get(mod)
            if c is None:
                raise SpecError(f"bad modifier {mod} in {expr}")
            if c < FIRST_COL:
                raise SpecError(f"{{{key}@prev}} used in the first period; give the row a 'first' formula")
            pre = f"'{sh}'!" if (sh != "Calc" or sheet_prefix) else ""
            return f"{pre}{get_column_letter(c)}{row}"
        # bare calc/series keys written without braces (a common model slip) mean "same period"
        keys = sorted(set(calc_rows) | set(series_rows), key=len, reverse=True)
        if keys:
            expr = re.sub(r"(?<![\w{@'!$])(" + "|".join(map(re.escape, keys)) + r")(?![\w(@}])",
                          lambda mm: "{" + mm.group(1) + "}", expr)
        out = re.sub(r"\{(\w+)(@\w+)?\}", sub, expr)
        return out if out.startswith("=") else "=" + out

    for i, row in enumerate(spec.get("calc", [])):
        rr = calc_rows[row["key"]]
        C.cell(row=rr, column=1, value=row.get("label", row["key"]))
        for j in range(n):
            col = FIRST_COL + j
            f = row.get("formula") or (row.get("first") if j == 0 else row.get("next"))
            if not f:
                raise SpecError(f"calc row {row['key']}: needs 'formula' or 'first'+'next'")
            B.formula(C, f"{get_column_letter(col)}{rr}", ref(f, col), row.get("fmt"), total=bool(row.get("total")),
                      link=bool(re.search(r"\bInputs\b", ref(f, col))))
    B.header(O, 3, ["Output", "Value"])
    for i, o in enumerate(spec.get("outputs", [])):
        rr = 4 + i
        O.cell(row=rr, column=1, value=o["label"])
        B.formula(O, f"B{rr}", ref(o["formula"], FIRST_COL + n - 1, "x"), o.get("fmt"), link=True)
    for chk in spec.get("checks", []):
        B.add_check(wb, chk["label"], ref(chk["diff"], FIRST_COL + n - 1, "x"))
    if not spec.get("checks") and spec.get("outputs"):   # always at least one tie-out
        B.add_check(wb, "Outputs recalculate", "=0")
    ch = spec.get("chart")
    if ch and ch.get("rows"):
        rows = [calc_rows[k] for k in ch["rows"] if k in calc_rows]
        if rows:
            # chart data block on Outputs: header + one row per series, referencing Calc
            base = 6 + len(spec.get("outputs", []))
            B.header(O, base, ["Series"] + periods)
            for k, rr in enumerate(rows):
                O.cell(row=base + 1 + k, column=1, value=C.cell(row=rr, column=1).value)
                for j in range(n):
                    cl = get_column_letter(FIRST_COL + j)
                    B.formula(O, f"{cl}{base + 1 + k}", f"=Calc!{cl}{rr}", "num", link=True)
            from openpyxl.chart import BarChart, LineChart, Reference
            chart = (LineChart if ch.get("type") == "line" else BarChart)()
            if ch.get("type") != "line":
                chart.type = "col"
            chart.title = ch.get("title")
            data = Reference(O, min_col=1, min_row=base + 1, max_col=FIRST_COL + n - 1, max_row=base + len(rows))
            chart.add_data(data, from_rows=True, titles_from_data=True)
            chart.set_categories(Reference(O, min_col=FIRST_COL, min_row=base, max_col=FIRST_COL + n - 1, max_row=base))
            chart.height, chart.width = 7.5, 16
            O.add_chart(chart, f"A{base + len(rows) + 3}")
    for ws in (I, C, O):
        B.widths(ws, first=38)
    wb.save(path)
    return {"path": path, "calc_rows": len(calc_rows), "inputs": len(names) + len(series_rows)}


SPEC = """You design financial and business models as a compact JSON spec; a builder turns it into a banker-style
Excel workbook (Cover, Inputs, Calc, Outputs, Checks) with live formulas. Rules: every assumption is an input (never a
number inside a calc formula except 0, 1, 12, 100); labels in plain words with units; at least one tie-out check;
add a chart when the request implies one. Reference syntax: scalar inputs by key (e.g. growth); rows as {key}
(same period), {key@prev}, {key@first}, {key@last}, {key@all}. Rows that depend on the previous period need "first"
and "next" formulas. Keys: lowercase letters, digits, underscore. Spec format:
%s
Return JSON only."""


def build_model(request: str, files: list[str], out_path: str, client, task_id: str = "model") -> dict:
    ctx = ""
    for f in files:
        try:
            import pandas as pd
            df = pd.read_csv(f) if f.endswith(".csv") else pd.read_excel(f)
            ctx += (f"\nInput file {Path(f).name}: {len(df)} rows, columns {list(df.columns)}; first rows (a sample only, "
                    f"never use them as totals):\n{df.head(5).to_string()}\n")
            from engines.slides.pipeline import facts_from_files
            fx = facts_from_files([f], max_facts=80)
            if fx:
                ctx += ("Exact aggregates computed by code from ALL rows (use these numbers; include every category):\n"
                        + "\n".join(f"- {x['text']}: {x['value']}{x.get('unit', '')}" for x in fx) + "\n")
        except Exception:
            pass
    msgs = [{"role": "system", "content": SPEC % __doc__.split("Spec:")[1].split("References:")[0]},
            {"role": "user", "content": request + ctx + f"\n<run id=\"{__import__('uuid').uuid4().hex[:8]}\"/>"}]
    calls, last_err = 0, ""
    for attempt in range(3):
        r = client.chat(model=MODEL, messages=msgs, max_tokens=8000, temperature=0.2, task_id=task_id, role="worker",
                        extra={"response_format": {"type": "json_object"}})
        calls += 1
        txt = r["choices"][0]["message"].get("content") or "{}"
        try:
            spec = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
            build(spec, out_path)
            res = qa_check(out_path)
            issues = res["issues"]
            try:
                if qa_value(out_path, "Checks!F3") not in (None, "OK"):
                    from openpyxl import load_workbook
                    wb = load_workbook(res["stats"]["recalculated_copy"], data_only=True)
                    bad = [f"'{r[0]}' is off by {r[1]:,.2f}" if isinstance(r[1], (int, float)) else f"'{r[0]}'"
                           for r in wb["Checks"].iter_rows(min_row=3, values_only=True) if len(r) > 2 and r[2] == "ERROR"]
                    issues.append({"check": "checks_tab", "cell": "Checks", "detail": "failing checks: " + "; ".join(bad[:5])
                                   + ". Fix the model logic (periods, rates per period, signs), not the checks."})
            except Exception:
                issues.append({"check": "checks_tab", "cell": "Checks!F3", "detail": "a check shows ERROR"})
            if not issues:
                Path(out_path).with_suffix(".json").write_text(json.dumps(spec, indent=1))
                return {"ok": True, "calls": calls, "path": out_path, "spec": spec, "stats": res["stats"]}
            last_err = "; ".join(f"{i['check']} {i['cell']}: {i['detail']}" for i in issues[:10])
        except (SpecError, ValueError, KeyError, TypeError) as e:
            last_err = f"{type(e).__name__}: {e}"
        msgs += [{"role": "assistant", "content": txt},
                 {"role": "user", "content": f"The builder/QA rejected the spec: {last_err}. Return the corrected full "
                                             f"spec as JSON."}]
    return {"ok": False, "calls": calls, "reason": last_err}
