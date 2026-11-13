"""meshpool-sheets: the model writes a build script using engines.sheets.builder; deterministic QA gates finish."""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from meshpool_harness.core.client import LiveClient  # noqa: E402
from meshpool_harness.core.loop import Caps, run  # noqa: E402
from meshpool_harness.core.tools import ToolError, Workspace  # noqa: E402

SYSTEM = f"""You build Excel workbooks that a finance professional would accept without edits.

Work only in the current folder, which is your output folder: write every file there and run every command from there.
The helper library below is complete and documented here: do NOT read, grep, list or print Meshpool's engine or
library source (those reads are refused and waste turns). Build first, then refine.

How you work:
1. Read any attached input files first (inspect with a short pandas script: sheets, columns, row counts).
2. Write ONE Python build script (build_workbook.py) using the helper library below, then run it with python.
   Save a first complete version early; improve it afterwards.
3. Run the checker: python -m engines.sheets.check <file.xlsx>. Fix every issue it reports and rerun until it prints PASS.
4. Finish with a short answer (what the workbook contains, key outputs with their cell references) and cite the
   checker command as evidence. List the .xlsx in artifacts.

Workbook rules:
- Structure (banker style): wb = B.banker_workbook(title, purpose, author, date) gives Cover -> Inputs -> Calc ->
  Outputs -> Checks with colored tabs and a linked tab map. Put middle tabs in that order; add tabs only when needed
  (e.g. one per scenario, or Comps/Precedents/DCF), passing middle=(...). Use B.section(ws, row, "Revenue build")
  bands to group rows.
- Add at least one tie-out check with B.add_check(wb, "Summary ties to Calc", "=Outputs!B4-Calc!D5"); every check
  must show OK.
- Labels in plain words with units ("Revenue ($K)", "Growth per year (%)"). No jargon.
- Every computed number is a live Excel formula. Never type a computed value. Never hardcode a number in Calc or
  Outputs; put it in Inputs and reference it (named ranges for key assumptions).
- Units in labels ("Revenue ($K)"), consistent number formats, years/periods across columns, totals with SUM.
- No circular references. Guard divisions with IF(denominator=0, 0, ...).
- Losses, costs, capex and outflows are negative numbers (say "negative" in the label) or subtracted; never add a
  positive loss.

Valuation and deal work (investment-bank standard):
- Every input goes in the assumption book on Inputs with its Source (document, page, date), Evidence / rationale (the
  fact it rests on and why this value) and a Low / High range. The engine refuses a valuation workbook (a Comps,
  Precedents, DCF or Valuation tab) while any input has an empty Source. Never invent a figure: if it is not public,
  mark it as an illustrative assumption in Source and say who would confirm it.
- Trading comps and precedents: 5-10 peers / deals on the same date basis, each row sourced; use the median and the
  interquartile range (25th-75th percentile) of the most relevant multiple, never the mean alone. Loss-making peers show
  "NM" and drop out of the statistics.
- DCF: first decide whether it fits and say why in your answer (cash flows tied to observable drivers: yes; otherwise
  rely on comps and precedents). Drive revenue from operating drivers, not a flat growth rate. Show the terminal value
  share of EV and the exit-multiple cross-check.
- When you value something, finish with a football field that brackets the answer (comps, precedents, DCF, and the
  offer or current price as a marker).

Helper library (import from engines.sheets.builder as B; it is importable from the working folder; if python cannot
find it, start the script with: import sys; sys.path.insert(0, r"{ROOT}")):
  wb = B.new_workbook(("Inputs","Calc","Outputs"))   B.title(ws, text)   B.header(ws, row, labels, col=1)
  B.input_cell(ws, "B3", 0.1, "pct", name="Growth")  B.formula(ws, "C5", "=B5*(1+Growth)", "usd", total=False)
  B.section(ws, row, text, ncols)   B.add_check(wb, label, "=diff formula")
  B.widths(ws)   B.chart(ws, "bar"|"line"|"pie", (min_col,min_row,max_col,max_row), cats_ref, "E3", "Title", "usd")
  formats: usd, usd2, pct, int, x, num, or any Excel format string. wb.save(path).
Finance kit (all live formulas; raw numbers are blue inputs; results are workbook names you can link to):
  book = B.assumption_book(wb["Inputs"], row=3)        # Assumption | Value | Source | Evidence / rationale | Low | High
  book.section("WACC")
  book.add("Risk-free rate (%)", 0.042, "pct", name="risk_free", source="US 10Y Treasury, 2026-11-12, treasury.gov",
           rationale="Current 10-year yield", low=0.038, high=0.046)   # also names risk_free_low / risk_free_high
  t = B.comps_table(wb["Comps"], 3, peers)   peers: [{{"name","source","price","shares","debt","cash","revenue",
      "ebitda"}}]; computes equity value, EV, EV/Revenue, EV/EBITDA, EBITDA margin; Mean, Median, 25th, 75th
      percentile rows named comps_<col>_mean|median|p25|p75 (e.g. comps_ev_ebitda_median). Custom columns:
      inputs=[(key,label,fmt)], calcs=[(key,label,'IF({{b}}<=0,"NM",{{a}}/{{b}})',fmt)].
  t = B.precedents_table(wb["Precedents"], 3, deals)   deals: [{{"date","acquirer","name","consideration","ev",
      "revenue","ebitda","offer","unaffected","source"}}]; EV/Revenue, EV/EBITDA, premium to unaffected; names
      prec_ev_ebitda_median, prec_premium_p25, ...
  d = B.dcf_block(wb["DCF"], 3, [2027, 2028, 2029, 2030, 2031],
        [{{"key":"mw","label":"Active capacity (MW)","start":"mw_start","growth":"mw_growth"}},
         {{"key":"rev_mw","label":"Revenue per MW ($m)","start":"rev_per_mw","growth":"price_growth","fmt":"usd2"}}],
        revenue="{{mw}}*{{rev_mw}}", margin=("margin_start","margin_end"), names={{}})
      Needs these named inputs (rename via names={{role: your_name}}): risk_free, beta_unlevered,
      equity_risk_premium, cost_of_debt, tax_rate, debt_weight, terminal_growth, exit_multiple, da_pct, capex_pct,
      nwc_pct, revenue_base (last actual revenue), mid_year (1 = mid-year discounting), total_debt, cash,
      diluted_shares, wacc_step, g_step; optional other_claims, size_premium. Builds revenue from drivers, EBITDA,
      D&A/tax/capex/working capital (negative), unlevered FCF, WACC build-up (relevered beta, CAPM), Gordon terminal
      value with exit-multiple cross-check, EV -> equity -> value per share, a WACC x g grid, and a check. Names:
      dcf_wacc, dcf_ev, dcf_ev_exit, dcf_tv_share, dcf_equity, dcf_per_share, dcf_per_share_exit.
  B.football_field(wb["Outputs"], 3, [{{"label":"Trading comps, EV/EBITDA 25th-75th","low":"=(comps_ev_ebitda_p25*
      ebitda_ltm-total_debt+cash)/diluted_shares","high":"=..."}}, ...], marker={{"label":"Offer price ($/share)",
      "value":"=offer_price"}})   # table + floating-bar chart + a low <= high check
  Name the tabs Comps, Precedents, DCF (not "Valuation" for a tab holding typed peer data)."""


def finish_check(answer, artifacts, ws):
    from engines.sheets.qa import check
    xl = [a for a in artifacts if a.endswith((".xlsx", ".xlsm"))]
    if not xl:
        return "ENGINE CHECK: list the produced .xlsx file in artifacts."
    for a in xl:
        p = a if os.path.isabs(a) else str(ws.root / a)
        if not os.path.exists(p):
            return f"ENGINE CHECK: artifact not found: {a}"
        r = check(p)
        from engines.sheets.qa import value
        if "Checks" in r["stats"]["sheets"]:
            try:
                status = value(p, "Checks!F3")
            except Exception:
                status = None
            if status not in (None, "OK"):
                r["issues"].append({"check": "checks_tab", "cell": "Checks!F3", "detail": f"shows {status}"})
        if "Cover" not in r["stats"]["sheets"]:
            r["issues"].append({"check": "structure", "cell": "", "detail": "no Cover tab; use B.banker_workbook"})
        try:                                   # the forge workbook rules: logic errors QA cannot see (signs, plugs,
            from engines.forge.validate_xlsx import validate as forge_validate     # literals, tie-outs, master check)
            fr = forge_validate(p)
            r["issues"] += [{"check": f["rule"], "cell": f.get("cell") or f.get("sheet") or "", "detail": f["message"]}
                            for f in fr["failures"] if f["status"] == "fail" and f["severity"] == "error"][:10]
        except Exception:
            pass
        from engines.sheets.qa import missing_sources          # valuation workbooks: every input carries a source
        gaps = missing_sources(p)
        if gaps:
            r["issues"].insert(0, {"check": "source_missing", "cell": "", "detail": (
                f"{len(gaps)} inputs have no Source. Fill the Source column (and Evidence / rationale, Low, High) "
                "for: " + "; ".join(gaps[:30]) + (f"; and {len(gaps) - 30} more" if len(gaps) > 30 else ""))})
        if r["issues"]:
            return "ENGINE CHECK: workbook QA failed:\n" + "\n".join(
                f"- {i['check']} {i['cell']}: {i['detail']}" for i in r["issues"][:15])
    return None


REFUSED = ("REFUSED: Meshpool's engine and library source is off limits here. The helper API in your instructions "
           "is complete. Write build_workbook.py with it now, run it, then run the checker. (Files in the working "
           "folder and the input files you were given are readable.)")
_SOURCE = re.compile(r"engines[/\\]|meshpool_harness|builder\.py|validate_xlsx|\bqa\.py|site-packages|dist-packages|"
                     + re.escape(str(ROOT)))
_READER = re.compile(r"(^|[\s;&|(`$])(cat|head|tail|less|more|sed|awk|grep|egrep|rg|find|ls|tree|nl|wc|strings|"
                     r"type|dir|cp|copy|Get-Content|gc|Get-ChildItem|Select-String)\b|open\(|read_text|\.read\(")
_INTROSPECT = re.compile(r"\binspect\.|getsource|\bhelp\(|__file__|\bdir\(\s*B\b|pydoc")


class SheetsWorkspace(Workspace):
    """The sheets engine's workspace: the model builds in its folder and may read the inputs it was given, but not
    Meshpool's own source (a live run spent its whole budget reading the engine instead of building)."""

    def __init__(self, workdir: str, run_dir: str, inputs=None, **kw):
        super().__init__(workdir, run_dir, **kw)
        self.inputs = {Path(f).resolve() for f in inputs or []}
        self.started = time.time()
        self.nudged = False

    def _allowed(self, q: Path) -> bool:
        return q == self.root or self.root in q.parents or q in self.inputs

    def _guard(self, path) -> None:
        if not self._allowed(self._path(str(path or "."))):
            raise ToolError(REFUSED)

    def read_file(self, path, start=None, end=None):
        self._guard(path)
        return super().read_file(path, start, end)

    def grep(self, pattern, path=".", glob=None):
        self._guard(path)
        return super().grep(pattern, path, glob)

    def glob(self, pattern):
        if ".." in Path(pattern).parts or os.path.isabs(pattern):
            raise ToolError(REFUSED)
        return super().glob(pattern)

    def _run(self, command, timeout_s, kind, record=True, raw=False):
        bare = command.replace(str(self.root), ".")                # the workdir may itself sit under ROOT
        if _INTROSPECT.search(bare) or (_SOURCE.search(bare) and _READER.search(bare)):
            raise ToolError(REFUSED)
        return super()._run(command, timeout_s, kind, record, raw)

    def saved_workbook(self) -> bool:
        """An .xlsx written in this run (input workbooks copied in beforehand do not count)."""
        for p in self.root.rglob("*.xlsx"):
            if ".run" in p.parts or p.name.startswith("~$"):
                continue
            try:
                if p.stat().st_mtime >= self.started - 1 and p.resolve() not in self.inputs:
                    return True
            except OSError:
                continue
        return False


def build_first(turn: int, max_turns: int, ws) -> str | None:
    """At ~60% of the turn budget with no workbook saved yet: tell the model to save a first complete version now."""
    if getattr(ws, "nudged", True) or turn < max(2, int(0.6 * max_turns)) or ws.saved_workbook():
        return None
    ws.nudged = True
    return (f"BUILD NOW: {turn} of {max_turns} turns used and no .xlsx has been saved. Stop exploring. Write "
            "build_workbook.py with the helper library from your instructions, run it to save a first complete "
            "workbook now, then run python -m engines.sheets.check on it. Refine only after the file exists.")


def solve(task: str, *, workdir: str, task_id: str, max_turns: int | None = None, model: str = "deepseek-v4.1-flash",
          client=None, phase: str = "phase_2_build", files=None) -> dict:
    ws = SheetsWorkspace(workdir, os.path.join(workdir, ".run"), inputs=files)
    client = client or LiveClient(phase)
    ctx = {"task_id": task_id, "model": model, "profile": "deepseek", "role": "worker"}
    r = run(task, client=client, model=model, profile=None, ws=ws, ctx=ctx, system_text=SYSTEM,
            caps=Caps(max_turns=max_turns or int(os.environ.get("MH_SHEETS_MAX_TURNS", "50"))), temperature=0.0, finish_check=finish_check, turn_hook=build_first)
    return {"ok": r.ok, "answer": r.answer, "artifacts": r.artifacts, "evidence": r.evidence, "turns": r.turns,
            "reason": r.reason, "usage": r.usage}
