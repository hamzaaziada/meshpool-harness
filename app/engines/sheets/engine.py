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
5. You have a fixed turn budget, stated in the task. A checked workbook delivered on time beats a richer one that is
   still being refined when the budget runs out: once the workbook is complete and the checker passes, finish. Your
   build scripts are moved out of the output folder when the run ends; only the .xlsx stays there.

Workbook rules:
- Structure (banker style): wb = B.banker_workbook(title, purpose, author, date) gives Cover -> Inputs -> Calc ->
  Outputs -> Checks with colored tabs and a linked tab map. Put middle tabs in that order; add tabs only when needed
  (e.g. Historicals/Model/DCF/Valuation inputs, or Comps/Precedents), passing middle=(...). Use B.section(ws, row, "Revenue build")
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
  mark it as an illustrative assumption in Source and say who would confirm it; if it is unknown and cannot be
  assumed, leave the cell N/A and say so in your answer. Say "I don't know" rather than guess.
- Trading comps and precedents: 5-10 peers / deals on the same date basis, each row sourced; use the median and the
  interquartile range (25th-75th percentile) of the most relevant multiple, never the mean alone. Loss-making peers show
  "NM" and drop out of the statistics.
- DCF: first decide whether it fits and say why in your answer (cash flows tied to observable drivers: yes; otherwise
  rely on comps and precedents). A DCF is built on full statements, not a margin shortcut: spread the filed history
  (spreading module, SEC EDGAR), build the integrated income statement, balance sheet and cash flow with
  B.three_statement, and take unlevered free cash flow from them with B.dcf_from_statements. Show the terminal value
  share of EV and the exit-multiple cross-check.
- Revenue and cost of revenue come from operating drivers, each sourced with a range: units x price, capacity x
  utilisation x price per unit, customers x ARPU; COGS as units x unit cost or a sourced gross margin.
- Balance sheet assumptions: working-capital days (DSO, DIO, DPO) anchored on history (S.history_metrics), capex and
  PP&E roll-forward with depreciation from useful lives, debt schedule (draws, repayments, interest on the opening
  balance so there is no circularity), equity roll-forward (net income, dividends, buybacks), minimum cash with a
  revolver. The engine refuses an integrated model whose Checks tab lacks the balance-sheet-balances and cash-tie
  checks (three_statement adds both), or whose balance sheet does not balance in every year.
- Scenarios: every judgement driver gets base / bull / bear values in B.scenario_book behind one switch (scenario);
  B.scenario_summary shows the three cases side by side. The engine refuses a DCF or integrated model without them.
- Method choice and weighting: B.valuation_summary decides which methods fit the company (DCF only when cash flows tie
  to drivers; trading comps; precedents; sum of the parts for distinct segments; NAV for asset-heavy companies and
  financials; dividend discount / excess return for banks and insurers; LBO as a floor for sponsor-relevant targets)
  and weights them on data quality, peer comparability, the DCF's terminal-value share, stage and control vs minority
  context. Weights are editable inputs with a justification each and must sum to 100%. The engine refuses a workbook
  with two or more methods and no weighting. Explain the weights in your answer.
- When you value something, finish with a football field that brackets the answer (comps, precedents, DCF, the
  weighted value, and the offer or current price as a marker); valuation_summary draws it.

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
      # here "start" is the first FORECAST year's value (three_statement's "start" is the last actual)
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
  Name the tabs Comps, Precedents, DCF (not "Valuation" for a tab holding typed peer data).
Research hand-off: when the folder has report.md and sources.json from a research step, take each assumption's
Source and Evidence from the passage behind it: from engines.research.library import Corpus;
src = Corpus(".").assumption_source("S3.p12") -> {{"source": ..., "evidence": ...}} (pass them to book.add).
Integrated model kit (spreading -> statements -> DCF -> scenarios -> method weighting):
  from engines.sheets import spreading as S
  facts = S.fetch_companyfacts(S.cik_for_ticker("ACME"))      # SEC EDGAR XBRL; User-Agent from $SEC_USER_AGENT
  sp = S.spread(facts, years=3)    # standard IS/BS/CF, each cell with tag, accession, filing date; sp.gaps (left
      empty, never estimated), sp.checks (balance sheet balances, cash flow ties, net income ties); report both
  from engines.sheets import market as MK
  MK.add_wacc_inputs(book, industry="Semiconductor")   # live, sourced: Treasury 10Y, Damodaran implied ERP (its
      published variants as the range), industry unlevered beta corrected for cash; MK.industries() lists names
  S.history_metrics(sp)            # growth, gross margin, SG&A %, DSO/DIO/DPO, capex %, D&A %, tax rate, payout
  q = S.spread_quarters(facts, 8)  # fiscal quarters from 10-Q/10-K (Q4 = year less Q1-Q3; cash flow from YTD)
  S.ltm(q, "revenue"); S.calendarize({{"2025-06-30": 100, "2026-06-30": 120}}, 2025)   # LTM; calendar-year peers
  h = S.write_spread(wb["Historicals"], 4, sp)       # tab "Historicals" before Inputs; sourced blue inputs, names
      h_<line>_<fy> (h_revenue_fy2025)
  sb = B.scenario_book(wb["Inputs"], 3)    # Assumption | Selected case | Base | Bull | Bear | Source | Evidence
  sb.section("Revenue drivers")
  sb.add("Units sold, last actual (m)", 110, fmt="num", name="units_start", source="10-K FY25 p.32", rationale=...)
  sb.add("Unit growth per year (%)", 0.06, 0.09, 0.02, "pct", "units_growth", source=..., rationale=...)
      # base, bull, bear; names units_growth (selected), units_growth_base / _bull / _bear, _low / _high
  sb.link("Cash, opening ($m)", "=h_cash_fy2025", "usd", name="cash_open", source="Historicals tab")
  S.link_opening_balances(sb, sp)     # all opening balance sheet names below, linked to Historicals
  m = B.three_statement(wb["Model"], 3, [2026, 2027, 2028, 2029, 2030],
        [{{"name": "Devices", "drivers": [
            {{"key": "units", "label": "Units sold (m)", "start": "units_start", "growth": "units_growth"}},
            {{"key": "price", "label": "Price per unit ($)", "start": "price_start", "growth": "price_growth",
             "fmt": "usd2"}},
            {{"key": "ucost", "label": "Unit cost ($)", "start": "unit_cost_start", "growth": "unit_cost_growth",
             "fmt": "usd2"}}], "revenue": "{{units}}*{{price}}", "cogs": "{{units}}*{{ucost}}"}},
         {{"name": "Subscriptions", "drivers": [{{"key": "subs", ...}}, {{"key": "arpu", ...}}],
          "revenue": "{{subs}}*{{arpu}}", "gross_margin": "subs_gross_margin"}}],
        opex=[{{"key": "sga", "label": "SG&A ($m, negative)", "pct": "sga_pct"}},
              {{"key": "rnd", "label": "R&D ($m, negative)", "pct": "rnd_pct"}}],
        capex_growth="MAX(0,{{units}}-{{units@prev}})*capex_per_unit")
      # growth may be a name or (start_name, end_name) for a linear fade; "step" adds; "formula" for derived drivers
      # ("{{capacity}}*{{utilisation}}"). Column B = last actual (opening balance sheet); forecast columns after.
      Needs names: tax_rate, dso, dio, dpo, capex_pct, useful_life_new, remaining_life_existing, term_rate,
      revolver_rate, cash_rate, min_cash, revolver_capacity, term_draw, term_amort, payout_ratio, buyback, and the
      opening balance sheet cash_open, ar_open, inv_open, other_ca_open, ppe_open, other_nca_open, ap_open,
      other_cl_open, revolver_open, term_debt_open, other_ncl_open, equity_open (override with names={{...}}).
      Adds the checks: balance sheet balances every year, closing cash equals balance sheet cash, net income ties,
      revolver and PP&E never negative.
  SBC: names={{"sbc": "sbc_pct"}} in three_statement adds share-based compensation as an expense in EBITDA, adds it
      back in operating cash flow and credits equity (not added back in unlevered FCF).
  B.diluted_shares(ws, row, basic="basic_shares", price="share_price", options=[("opt_n", "opt_strike")],
      rsus="rsus", converts=[("conv_shares", "conv_price")])     # treasury stock method; defines diluted_shares
  lb = B.lbo(wb["LBO"], 3, m, [{{"label": "Term Loan A", "multiple": "tla_x", "rate": "tla_rate", "amort": "tla_amort",
      "sweep": True}}, {{"label": "Term Loan B", ...}}, {{"label": "Senior notes", "multiple": "notes_x",
      "rate": "notes_rate"}}])   # sources & uses, sweep, IRR / MOIC, and lbo_floor_low / lbo_floor_high ($ per share
      a sponsor could pay at lbo_irr_high / lbo_irr_low): use them as the LBO bar in valuation_summary
      (needs lbo_entry_multiple, lbo_ltm_ebitda, lbo_fees_pct, lbo_exit_multiple, lbo_irr_low, lbo_irr_high)
  ca = B.capital_allocation(wb["Model"], 3, [2027, 2028, 2029, 2030, 2031])   # cash generated and its uses (return
      cash vs fund growth, buybacks, special dividends): revenue -> operating cash flow -> capex -> FCF; opening cash,
      interest, regular dividends, special dividend, buyback, closing cash; shares retired; headroom over the cash
      floor; a growth x capex grid of closing cash; checks that tie. Put these names in the assumption book first:
      revenue_base, rev_growth, ocf_margin, capex_base, capex_growth, cash, min_cash, shares, share_price, dps,
      dps_growth, special_dps, buyback, cash_yield (override with names={{...}}). Outputs: ca_fcf_total, ca_cash_end,
      ca_min_headroom, ca_uses_total, ca_shares_end. Use it instead of writing these rows by hand.
  B.alerts(wb, m, d)               # Alerts tab: growth fades, capex vs depreciation, growth and margin both rising,
      cash shortfall, terminal value share, terminal growth vs risk-free; explain every REVIEW in your answer
  d = B.dcf_from_statements(wb["DCF"], 3, m)   # names risk_free, beta_unlevered, equity_risk_premium, cost_of_debt,
      debt_weight, terminal_growth, exit_multiple, mid_year, diluted_shares, wacc_step, g_step (exit_step adds a WACC x
      exit multiple grid); EV bridge items by name: minorities, preferred, leases, pensions, investments;
      dcf_* names as dcf_block. A deck built after the model takes its football field, comps, precedents, DCF
      bridge and sensitivities, scenarios and statement summary straight from the workbook.
  B.scenario_summary(wb["Outputs"], 3, dict(years=..., segments=..., opex=..., capex_growth=...))   # same args as
      three_statement: pinned Base/Bull/Bear case tabs + table + a live-vs-pinned check
  v = B.valuation_summary(wb["Valuation inputs"], 3,
        [{{"key": "dcf", "low": "=MIN(dcf_per_share,dcf_per_share_exit)", "high": "=MAX(dcf_per_share,dcf_per_share_exit)"}},
         {{"key": "trading_comps", "low": "=...", "high": "=..."}}, {{"key": "precedents", ...}}, {{"key": "lbo", ...}}],
        {{"sector": "general", "stage": "mature", "context": "control", "drivers_traceable": True, "n_peers": 7,
         "peer_comparability": "medium", "n_precedents": 5, "sponsor_relevant": True}},
        marker={{"label": "Current price ($/share)", "value": "=share_price"}})
      # keys: dcf trading_comps precedents sotp nav ddm excess_return lbo. Put it on a tab whose name contains
      # "Inputs" (the factor scores and weights are inputs). B.select_methods(profile) shows the rules in Python."""


def finish_check(answer, artifacts, ws, report=None):
    """The engine's deterministic finish gate: None when every listed workbook passes, else the issues. report (a
    dict), when given, collects the forge rule statuses ("rules") and warning-level findings ("warnings")."""
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
            if report is not None:
                report["rules"] = dict(fr.get("rules") or {})
                report.setdefault("warnings", []).extend(
                    f"{f['rule']} {f.get('cell') or f.get('sheet') or ''}: {f['message']}".replace("  ", " ")
                    for f in fr["failures"] if f["status"] == "fail" and f["severity"] == "warning")
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
        from engines.sheets.qa import model_gaps           # integrated statements, scenarios, method weighting
        at = 1 if gaps else 0                              # right after the source gate, ahead of cosmetic issues
        r["issues"][at:at] = [{"check": "valuation_model", "cell": "", "detail": g} for g in model_gaps(p)]
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
        self.wrapped = False                  # the ~80% "wrap up and finish" note was sent
        self.turn = 0                         # last turn whose tools ran (set by turn_budget)
        self.before = set(_files(self.root))  # files already in the folder: never moved or deleted by the tidy-up

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
            raise ToolError(self._refusal(command))
        return super()._run(command, timeout_s, kind, record, raw)

    def _refusal(self, command: str) -> str:
        """The refusal says what to run instead, and gets firmer when the same command comes back: a DeepSeek run
        sent one refused command (cd into the engine folder, ls, python -c importing the builder) 40 times in a row."""
        self.refused = getattr(self, "refused", {})
        key = re.sub(r"\s+", " ", command.strip())[:300]
        self.refused[key] = self.refused.get(key, 0) + 1
        scripts = sorted(p.name for p in self.root.glob("*.py"))
        run = f"python {scripts[0]}" if scripts else "write build_workbook.py, then run: python build_workbook.py"
        msg = (REFUSED + f" Run your code from the working folder instead, with no cd and no listing of the engine "
               f"folder: {run}. Import the helpers in the script with "
               f"`import sys; sys.path.insert(0, r\"{ROOT}\"); from engines.sheets import builder as B` as your "
               "instructions show.")
        if self.refused[key] >= 2:
            msg = (f"REFUSED again ({self.refused[key]} times): this exact command will always be refused. Do not send "
                   f"it again. Your next call must be: {run}")
        return msg

    def saved_workbooks(self) -> list[Path]:
        """.xlsx files written in this run (input workbooks copied in beforehand do not count): top-level files first,
        then the most recently saved."""
        out = []
        for p in self.root.rglob("*.xlsx"):
            rel = p.relative_to(self.root).parts
            if ".run" in rel or p.name.startswith(("~$", ".~lock")):
                continue
            try:
                m = p.stat().st_mtime
                if m >= self.started - 1 and p.resolve() not in self.inputs:
                    out.append((len(rel) > 1, -m, p))
            except OSError:
                continue
        return [p for *_, p in sorted(out)]

    def saved_workbook(self) -> bool:
        """An .xlsx written in this run (input workbooks copied in beforehand do not count)."""
        return bool(self.saved_workbooks())


_SKIP_DIRS = {".run", ".git", "node_modules", "__pycache__", ".venv", "venv"}


def _files(root: Path, limit: int = 20000) -> list[Path]:
    """Files under root, outside .run and other tool folders (bounded: the folder may be a large user folder)."""
    out = []
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x not in _SKIP_DIRS]
        out += [Path(d) / n for n in names]
        if len(out) >= limit:
            break
    return out


def build_first(turn: int, max_turns: int, ws) -> str | None:
    """At ~60% of the turn budget with no workbook saved yet: tell the model to save a first complete version now."""
    if getattr(ws, "nudged", True) or turn < max(2, int(0.6 * max_turns)) or ws.saved_workbook():
        return None
    ws.nudged = True
    return (f"BUILD NOW: {turn} of {max_turns} turns used and no .xlsx has been saved. Stop exploring. Write "
            "build_workbook.py with the helper library from your instructions, run it to save a first complete "
            "workbook now, then run python -m engines.sheets.check on it. Refine only after the file exists.")


def turn_budget(turn: int, max_turns: int, ws) -> str | None:
    """Notes injected after a turn's tools ran (the model reads them on the next turn). A live run built a passing
    17-tab model, kept refining it and hit the turn cap without calling finish; these make it land the work:
    - at ~60% with no workbook: BUILD NOW (build_first);
    - at ~80%: stop adding features, run the checks, fix only failures, finish;
    - on the last two turns: finish now."""
    ws.turn = turn                                    # last completed turn (reported if the loop raises)
    left = max_turns - turn                           # turns still to come after this note
    notes = [n for n in [build_first(turn, max_turns, ws)] if n]
    if left <= 0:
        return "\n".join(notes) or None
    if left <= 2:
        ws.wrapped = True
        notes.append(f"FINISH NOW: {turn} of {max_turns} turns used; {left} turn{'s' if left != 1 else ''} left. "
                     "Call finish on this turn with the saved .xlsx in artifacts and the checker command as evidence. "
                     "Do not change the workbook any more. If the run ends without finish, the last saved workbook "
                     "is checked and delivered as it is.")
    elif not getattr(ws, "wrapped", True) and turn >= max(2, int(0.8 * max_turns)):
        ws.wrapped = True
        if not ws.saved_workbook():
            notes.append(f"WRAP UP: {turn} of {max_turns} turns used and still no .xlsx. Save a complete workbook "
                         "with what you have now, run python -m engines.sheets.check on it, fix only what fails, "
                         "then call finish.")
        else:
            notes.append(f"WRAP UP: {turn} of {max_turns} turns used; {left} left. Stop adding features or tabs. "
                         "Run python -m engines.sheets.check on the saved workbook, fix only what fails (and any "
                         "Checks row not OK), then call finish now with the .xlsx in artifacts. A finished, checked "
                         "workbook is the goal; polish is not.")
    return "\n".join(notes) or None


CAPS = ("turn cap", "wall-clock cap", "task budget")


def salvage(r, ws, max_turns: int) -> dict:
    """The run hit a cap without an accepted finish. Do not throw the work away: check the last saved workbook with
    the same gate finish uses. Passing -> ok with the file; failing -> not ok, but with the file and the failures, so
    the caller can still deliver or repair it. The answer is never empty."""
    books = ws.saved_workbooks()
    if not books:
        return {"ok": False, "artifacts": [], "evidence": [], "checks": [],
                "answer": (f"Stopped at the {r.reason} before any workbook was saved; nothing to deliver. Run again "
                           "with a larger turn budget (MH_SHEETS_MAX_TURNS) or a narrower request."),
                "reason": f"{r.reason}; no workbook saved"}
    book = books[0]
    rel = str(book.relative_to(ws.root))
    report: dict = {}
    try:
        msg = finish_check("", [str(book)], ws, report=report)
    except Exception as e:                     # noqa: BLE001 - a crashing checker must not lose the file either
        msg = f"ENGINE CHECK: the checker crashed: {type(e).__name__}: {e}"
    failures = [ln[2:] for ln in (msg or "").splitlines() if ln.startswith("- ")] or ([msg] if msg else [])
    rules = report.get("rules") or {}
    rule_line = " ".join(f"{k}={v}" for k, v in rules.items())
    warnings = report.get("warnings") or []
    tabs = []
    try:
        from openpyxl import load_workbook
        wb = load_workbook(book, read_only=True)
        tabs = list(wb.sheetnames)
        wb.close()
    except Exception:                          # noqa: BLE001
        pass
    head = (f"Stopped at the turn limit ({max_turns} turns) before the model called finish."
            if r.reason.startswith("turn cap") else f"Stopped at the {r.reason} before the model called finish."
            if r.reason.startswith(CAPS) else f"The run stopped on an error before the model called finish ({r.reason}).")
    what = f"{rel}" + (f" ({len(tabs)} tabs: {', '.join(tabs)})" if tabs else "")
    lines = [head]
    if not msg:
        lines.append(f"Delivered the last saved, checked version: {what}. It passes the engine's finish checks "
                     "(workbook QA, Checks tab, forge workbook rules, input sources, model completeness).")
    else:
        lines.append(f"The last saved version is {what}. It does NOT pass the engine's finish checks; "
                     f"{len(failures)} failing:")
        lines += [f"- {f}" for f in failures]
    if rule_line:
        lines.append(f"Forge workbook rules: {rule_line}")
    if warnings:
        lines.append(f"Remaining warnings ({len(warnings)}):")
        lines += [f"- {w}" for w in warnings[:10]] + ([f"- and {len(warnings) - 10} more"] if len(warnings) > 10 else [])
    elif not msg:
        lines.append("Remaining warnings: none.")
    evidence = [f"engine finish check on {rel}: " + ("pass" if not msg else f"{len(failures)} failing")]
    if rule_line:
        evidence.append(f"forge validate_xlsx: {rule_line}")
    return {"ok": not msg, "answer": "\n".join(lines), "artifacts": [str(book)], "evidence": evidence,
            "checks": failures, "warnings": warnings, "rules": rules,
            "reason": f"{r.reason}; delivered the last saved workbook, checks pass" if not msg else
                      f"{r.reason}; saved workbook fails {len(failures)} checks: " + "; ".join(failures)[:600]}


def tidy(ws, artifacts: list) -> list:
    """Keep the output folder to the deliverable: build scripts (and any helper .py) the model wrote move to .run/,
    and extra .xlsx copies made this run (LibreOffice conversions such as conv/*.xlsx, earlier versions) move to
    .run/scratch/, as do lock files and __pycache__. Files that were in the folder before the run are never touched.
    Returns the artifacts with moved paths rewritten."""
    import shutil
    keep = {Path(a if os.path.isabs(a) else ws.root / a).resolve() for a in artifacts}
    keep_xl = any(k.suffix.lower() in (".xlsx", ".xlsm") for k in keep)
    moved: dict[Path, Path] = {}
    for p in _files(ws.root):
        try:
            q = p.resolve()
        except OSError:
            continue
        if p in ws.before or q in ws.inputs:
            continue
        rel = p.relative_to(ws.root)
        name, suf = p.name, p.suffix.lower()
        if suf in (".py", ".pyc"):
            dst = ws.run_dir / "scripts" / rel
        elif name.startswith((".~lock", "~$")) or (suf in (".xlsx", ".xlsm") and keep_xl and q not in keep):
            dst = ws.run_dir / "scratch" / rel
        else:
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                dst.unlink()
            shutil.move(str(p), str(dst))
            moved[q] = dst
        except OSError:
            continue
    for src in {q.parent for q, dst in moved.items() if dst.suffix == ".py"}:   # the model's helper modules' cache
        shutil.rmtree(src / "__pycache__", ignore_errors=True)
    emptied = {q.parent for q in moved}                  # folders the moves emptied (conv/), deepest first
    for dp in sorted({a for e in emptied for a in [e, *e.parents] if ws.root in a.parents}, key=lambda x: -len(x.parts)):
        try:
            dp.rmdir()                                   # only succeeds when empty
        except OSError:
            pass
    return [str(moved.get(Path(a if os.path.isabs(a) else ws.root / a).resolve(), a)) for a in artifacts]


REVIEW_CHAIN = ("claude-sonnet-5.5", "gpt-6.1-terra")       # strong text models for the partner / MD panel
NAMES = {"md": "MD (bulge-bracket bank)", "partner": "senior partner (strategy firm)"}


def review_enabled(client, flag: bool | None = None) -> bool:
    """The partner / MD review of a finished workbook. Off by default in 0.0.5: one round took a 12-month budget from
    about 150 s to about 400 s with the same validator results, and no workbook has shown a measured gain yet.
    MH_MODEL_REVIEW=1 turns it on for live clients; stub clients only when the caller asks (review=True)."""
    if flag is not None:
        return bool(flag)
    if os.environ.get("MH_MODEL_REVIEW", "").strip().lower() not in ("1", "true", "on", "yes"):
        return False
    return isinstance(client, LiveClient)


def _sha(path) -> str:
    import hashlib
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _rscore(rnd: dict):
    """Comparable score of one review round: sign-off first, then how many signed off, then the mean score. None when
    no reviewer ran (such a round never counts as best)."""
    ran = [r for r in rnd.get("reviews") or [] if r.get("status") == "ok" and r.get("overall") is not None]
    if not ran:
        return None
    return (bool(rnd.get("signed_off")), sum(r["verdict"] == "sign_off" for r in ran),
            round(sum(r["overall"] for r in ran) / len(ran), 3))


def _scores_line(rnd: dict) -> str:
    parts = [f"{NAMES.get(r['persona'], r['persona'])} {r['overall']}/10 "
             f"({'signs off' if r['verdict'] == 'sign_off' else 'revise'})"
             for r in rnd.get("reviews") or [] if r.get("status") == "ok" and r.get("overall") is not None]
    parts += [f"{NAMES.get(r['persona'], r['persona'])} reply unreadable (counted as revise)"
              for r in rnd.get("reviews") or [] if r.get("status") == "unparsed"]
    nr = [NAMES.get(r["persona"], r["persona"]) for r in rnd.get("reviews") or [] if r.get("status") == "not_run"]
    return "; ".join(parts + ([f"not run: {', '.join(nr)}"] if nr else [])) or "no reviewer ran"


def _issue_line(i: dict) -> str:
    return (f"[{i['severity']}] {i.get('where') or 'model'} ({i.get('persona', '')}; {i.get('scope', '')}): "
            f"{i['issue']}" + (f" Fix: {i['fix']}" if i.get("fix") else ""))


def _review_bar() -> dict:
    """The sign-off bar with the reviewer calibration (review.personas.bar in the forge rules; the per-persona offsets
    were measured on a published Goldman Sachs board book, docs/stack/reviewer-calibration.md)."""
    try:
        from engines.forge import critics
        from engines.forge.common import load_rules
        return critics.settings(load_rules())["bar"]
    except Exception:  # noqa: BLE001
        return {}


class WorkbookReview:
    """The partner and MD review the workbook each time it passes the engine's finish gate. Not signed off and budget
    left: their merged issues go back to the agent, which keeps working with its tools and calls finish again; the
    next passing finish is reviewed again. Stops when both sign off, after max_rounds, or when turns run out. Every
    reviewed version is copied aside and the best-scoring one is delivered; .run/review.json keeps every round."""

    def __init__(self, client, ws, max_turns: int, model: str | None = None, max_rounds: int | None = None,
                 min_turns: int | None = None, budget: int | None = None, task_id: str = "sheets"):
        self.client, self.ws, self.max_turns, self.task_id = client, ws, max_turns, task_id
        env = os.environ
        self.models = [m for m in dict.fromkeys([model, env.get("MH_SHEETS_REVIEW_MODEL"), env.get("MH_REVIEW_MODEL"),
                                                 *REVIEW_CHAIN]) if m]
        self.max_rounds = max(1, int(max_rounds or env.get("MH_REVIEW_ROUNDS") or 3))
        self.min_turns = int(min_turns if min_turns is not None else env.get("MH_REVIEW_MIN_TURNS") or 4)
        self.budget = int(budget or env.get("MH_REVIEW_DIGEST_CHARS") or 25000)
        self.rounds: list[dict] = []
        self.stopped = ""                       # why reviewing stopped early (reviewers could not run)
        self.notes: list[str] = []

    # ---- one round
    def run_round(self, path: str, report: dict | None, final: bool = False) -> dict:
        import shutil
        from engines.review.personas import panel
        from engines.sheets.review_digest import digest
        k = len(self.rounds) + 1
        p, root = Path(path), self.ws.root.resolve()
        rel = str(p.resolve().relative_to(root)) if root in p.resolve().parents else p.name
        box = self.ws.run_dir / "review" / f"round{k}"
        box.mkdir(parents=True, exist_ok=True)
        copy = box / p.name
        shutil.copy2(p, copy)
        try:
            text = digest(str(p), report=report, budget=self.budget, title=rel)
        except Exception as e:  # noqa: BLE001 - a digest failure is reported as a review that could not run
            text = ""
            err = f"digest failed: {type(e).__name__}: {e}"
        (box / "digest.txt").write_text(text)
        res, used, errors = None, None, []
        if text:
            material = (f"Workbook to review: {rel}. The digest below was built by the engine from the recalculated "
                        f"workbook.\n\n{text}")
            for m in self.models:
                res = panel(self.client, "workbook", material, model=m, task_id=f"{self.task_id}-r{k}",
                            bar=_review_bar())
                used = m
                errors = [f"{r['persona']}: {r.get('error')}" for r in res["reviews"] if r.get("status") == "not_run"]
                if len(res["not_run"]) < len(res["reviews"]) or any(x in " ".join(errors) for x in
                                                                     ("HTTP 401", "HTTP 403")):
                    break                       # someone answered, or the key itself was refused: no other model helps
            res = self._median_panel(res, used, material, k, panel) if res and used else res
        else:
            errors = [err]
        rnd = {"round": k, "turn": getattr(self.ws, "turn", 0) + 1, "final": final, "workbook": rel,
               "copy": str(copy), "sha256": _sha(p), "model": used, "digest_chars": len(text),
               "signed_off": bool(res and res["signed_off"]), "scores": (res or {}).get("scores", {}),
               "not_run": (res or {}).get("not_run", ["md", "partner"]), "errors": errors,
               "reviews": (res or {}).get("reviews", []), "issues": (res or {}).get("issues", []),
               "research_requests": (res or {}).get("research_requests", []),
               "samples": (res or {}).get("samples", [])}
        rnd["score"] = _rscore(rnd)
        self.rounds.append(rnd)
        if rnd["score"] is None and not any(r.get("status") == "unparsed" for r in rnd["reviews"]):
            self.stopped = "; ".join(errors)[:400] or "no reply"
        self.save()
        return rnd

    def _median_panel(self, first: dict, model: str, material: str, k: int, panel) -> dict:
        """A single reviewer reply moves by up to 1.5 points on the same workbook. With MH_REVIEW_SAMPLES=n (default
        1) the panel is asked n times and the panel whose mean score is the median is kept, so one generous or harsh
        reply neither signs a workbook off nor sinks it. All samples are kept in the round record."""
        n = max(1, int(os.environ.get("MH_REVIEW_SAMPLES") or 1))
        if n == 1 or first.get("not_run"):
            return first
        runs = [first] + [panel(self.client, "workbook", material, model=model, task_id=f"{self.task_id}-r{k}s{i}",
                                bar=_review_bar()) for i in range(1, n)]
        ok = [r for r in runs if not r.get("not_run") and r.get("scores")
              and all(v is not None for v in r["scores"].values())]
        if not ok:
            return first
        mean = lambda r: sum(r["scores"].values()) / len(r["scores"])     # noqa: E731
        ok.sort(key=mean)
        pick = dict(ok[len(ok) // 2])
        pick["samples"] = [r.get("scores", {}) for r in runs]
        return pick

    def best(self) -> dict | None:
        scored = [r for r in self.rounds if r["score"] is not None and r.get("passed_checks", True)]
        return max(scored, key=lambda r: (r["score"], r["round"])) if scored else None

    def feedback(self, rnd: dict, turns_left: int) -> str:
        lines = [f"Partner and MD review, round {rnd['round']} of {self.max_rounds} ({rnd['workbook']}): not signed "
                 f"off. {_scores_line(rnd)}."]
        for r in rnd["reviews"]:
            if r.get("status") == "ok" and r.get("headline"):
                lines.append(f"- {NAMES.get(r['persona'], r['persona'])}: \"{r['headline']}\"")
        lines.append("Sign-off bar: weighted score at least 8.0, no dimension below 7, no blocking issue.")
        from engines.review.personas import coaching
        coach = coaching(rnd.get("reviews") or [], NAMES)
        if coach:
            lines.append("Their coaching (keep what works; these changes would reach an 8):\n" + coach)
        b = self.best()
        if b and b["round"] != rnd["round"] and rnd["score"] is not None and b["score"] > rnd["score"]:
            lines.append(f"This version scores lower than round {b['round']} ({_scores_line(b)}); the engine keeps "
                         "and delivers the best-scoring version, so do not undo what the reviewers liked.")
        issues = rnd["issues"]
        top = [i for i in issues if i["severity"] in ("blocking", "major")]
        top += [i for i in issues if i["severity"] == "minor"][:max(0, 12 - len(top))]
        if top:
            lines.append("Issues (blocking first):")
            lines += [f"{n}. {_issue_line(i)}" for n, i in enumerate(top[:20], 1)]
        if rnd["research_requests"]:
            lines.append("Facts the reviewers say are missing (find them in the inputs you have, or mark the input as "
                         "an illustrative assumption with who would confirm it):")
            lines += [f"- {q.get('question')} ({q.get('why', '')})" for q in rnd["research_requests"][:6]]
        lines.append(f"Fix the blocking and major issues now: change build_workbook.py, rebuild the workbook, rerun "
                     f"python -m engines.sheets.check until it prints PASS, then call finish again (the reviewers will "
                     f"look again). Never invent a number to satisfy a reviewer: use figures already in the inputs, or "
                     f"label the input as an illustrative assumption. {turns_left} turns and "
                     f"{self.max_rounds - rnd['round']} review round(s) left.")
        return "\n".join(lines)

    # ---- the finish gate
    def gate(self, answer, artifacts, ws, report=None):
        rep: dict = {}
        try:
            msg = finish_check(answer, artifacts, ws, report=rep)
        except TypeError:                       # a checker without the report argument
            msg = finish_check(answer, artifacts, ws)
        if report is not None:
            report.update(rep)
        if msg or self.stopped:
            return msg
        xl = [a for a in artifacts if a.endswith((".xlsx", ".xlsm"))]
        path = Path(xl[0] if os.path.isabs(xl[0]) else ws.root / xl[0])
        last = self.rounds[-1] if self.rounds else None
        if last and last["sha256"] == _sha(path):
            return None                         # unchanged since the last review: accept, report that review
        if len(self.rounds) >= self.max_rounds:
            return None
        rnd = self.run_round(str(path), rep)
        turns_left = self.max_turns - (getattr(ws, "turn", 0) + 1)
        if (rnd["signed_off"] or rnd["score"] is None or len(self.rounds) >= self.max_rounds
                or turns_left < self.min_turns):
            return None
        ws.review_note = self.feedback(rnd, turns_left)
        return (f"REVIEW: the workbook passes the engine checks, but the partner and MD did not sign it off (review "
                f"round {rnd['round']} of {self.max_rounds}). Their review is in the next message: fix the blocking "
                "and major issues, then call finish again.")

    def hook(self, turn, max_turns, ws):
        note = turn_budget(turn, max_turns, ws)
        pending, ws.review_note = getattr(ws, "review_note", None), None
        return "\n".join(x for x in (pending, note) if x) or None

    # ---- delivery
    def finalize(self, out: dict, at_cap: bool) -> dict:
        import shutil
        xl = [a for a in out.get("artifacts") or [] if str(a).endswith((".xlsx", ".xlsm"))]
        cur = Path(xl[0] if os.path.isabs(xl[0]) else self.ws.root / xl[0]) if xl else None
        failing_only = at_cap and not out.get("ok") and not self.rounds   # nothing ever passed the engine checks
        if (at_cap and (out.get("ok") or failing_only) and cur and cur.exists() and not self.stopped
                and _sha(cur) not in {r["sha256"] for r in self.rounds}):
            rep = {"rules": out.get("rules") or {}, "warnings": out.get("warnings") or [],
                   "failures": out.get("checks") or out.get("failures") or []}
            rnd = self.run_round(str(cur), rep, final=True)     # the delivered version at the cap gets its review;
            if failing_only:                                    # one that fails the engine checks is still graded
                rnd["passed_checks"] = False                    # (the MD's job), but never delivered as "passed"
                self.notes.append("The workbook did not pass the engine checks; the partner and MD reviewed it anyway "
                                  "so their verdict and issues are known.")
                self.save()
        best = self.best()
        delivered = None
        if best:
            if not out.get("ok"):
                self.notes.append(f"The last saved workbook fails the engine checks, so the version reviewed in round "
                                  f"{best['round']} (which passed them) is delivered instead.")
            if not (cur and cur.exists() and out.get("ok") and _sha(cur) == best["sha256"]):
                dst = self.ws.root / best["workbook"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(best["copy"], dst)
                rest = [a for a in out.get("artifacts") or [] if not str(a).endswith((".xlsx", ".xlsm"))]
                out["artifacts"] = [str(dst)] + rest
                if out.get("ok"):
                    self.notes.append(f"Delivered the round {best['round']} version: it scored higher than the later "
                                      "version, which is kept in .run/review/.")
                out["ok"] = True
                out["reason"] = f"{out.get('reason', '')}; delivered the best reviewed workbook (round {best['round']})"
            delivered = best["round"]
        elif self.rounds:
            delivered = next((r["round"] for r in reversed(self.rounds) if cur and cur.exists()
                              and r["sha256"] == _sha(cur)), None)
        text = self.report(delivered)
        if text:
            out["answer"] = (out.get("answer") or "").rstrip() + "\n\n" + text
        out["review"] = {"rounds": [{k: r[k] for k in ("round", "scores", "signed_off", "not_run", "errors", "model")}
                                    for r in self.rounds], "delivered_round": delivered,
                         "signed_off": bool(best and best["signed_off"]), "stopped": self.stopped}
        self.save(delivered)
        return out

    def report(self, delivered) -> str:
        if not self.rounds:
            return ("Partner and MD review: not run (no workbook passed the engine checks, so there was nothing to "
                    "review).")
        best = self.best()
        graded = [r for r in self.rounds if r["score"] is not None]
        if best is None and graded:                    # only a workbook that fails the engine checks was graded
            r = graded[-1]
            names = {"partner": "senior partner", "md": "MD"}
            sc = "; ".join(f"{names.get(v['persona'], v['persona'])} {v['overall']}/10 ({'signs off' if v['verdict'] == 'sign_off' else 'revise'})"
                           for v in r["reviews"] if v.get("status") == "ok")
            top = "; ".join(f"{i['where']}: {i['issue']}" for i in r["issues"][:3])
            return (f"Partner and MD review of the workbook (it fails the engine checks): {sc}. Top issues: {top}")
        if best is None:
            why = self.stopped or "; ".join(self.rounds[-1]["errors"]) or "their replies could not be read"
            return (f"Partner and MD review: the reviewers could not run ({why[:300]}). No review scores are "
                    "reported; the workbook was not reviewed by them.")
        n = len(self.rounds)
        lines = [f"Partner and MD review ({n} round{'s' if n != 1 else ''}, reviewer model {best['model']}):"]
        for r in self.rounds:
            counts = {s: sum(i["severity"] == s for i in r["issues"]) for s in ("blocking", "major", "minor")}
            lines.append(f"- Round {r['round']}{' (at the turn cap)' if r['final'] else ''}: {_scores_line(r)}; "
                         f"{counts['blocking']} blocking, {counts['major']} major, {counts['minor']} minor issues.")
        lines += [f"- {x}" for x in self.notes]
        if best["signed_off"]:
            lines.append(f"Both reviewers signed off on the delivered version (round {best['round']}).")
        else:
            why = ("the review budget ran out" if len(self.rounds) >= self.max_rounds
                   else "the turn budget ran out" if not self.stopped else "a later review could not run")
            lines.append(f"Not signed off ({why}). Delivered the best-scoring version (round {best['round']}: "
                         f"{_scores_line(best)}). Remaining top issues:")
            lines += [f"- {_issue_line(i)}" for i in best["issues"][:6]]
        if self.stopped and best["round"] != self.rounds[-1]["round"]:
            lines.append(f"A later review could not run: {self.stopped}")
        return "\n".join(lines)

    def save(self, delivered=None) -> None:
        import json
        try:
            self.ws.run_dir.mkdir(parents=True, exist_ok=True)
            (self.ws.run_dir / "review.json").write_text(json.dumps({
                "models": self.models, "max_rounds": self.max_rounds, "stopped": self.stopped,
                "delivered_round": delivered, "best_round": (self.best() or {}).get("round"),
                "rounds": self.rounds}, indent=1, default=str))
        except OSError:
            pass


def dossier_brief(task: str, workdir: str, client, model: str, task_id: str) -> str:
    """Research the company before the first cell is written (engines/research/dossier.py): a model built from the
    statements alone misses what changed since them (an asset-light shift, a disposal, new guidance). Returns the text
    added to the task: the structural changes, the sourced findings and the open questions; '' when research is off
    (MH_DOSSIER=0, MH_RESEARCH=0, an offline client, no network) or found nothing."""
    if os.environ.get("MH_DOSSIER", "1") == "0":
        return ""
    try:
        from engines.forge import research as RS
        from engines.research.dossier import build_dossier, brief_block
        ok, _ = RS.allowed(client, None, task, None)
        if not ok:
            return ""
        d = build_dossier(task, [], client=client, models=[os.environ.get("MH_STORY_REVIEW_MODEL") or model],
                          out_dir=workdir, task_id=f"{task_id}-dossier")
    except Exception:  # noqa: BLE001 - research never costs the model
        return ""
    if not d.get("facts"):
        return ""
    found = "\n".join(f"- {f['id']}: {f['text']}" for f in d["facts"][:60])
    return ("\n\nResearch dossier (dossier.md in this folder; read it before you plan the model). Build the model "
            "for the business as it is NOW: where the findings show a structural change, the forecast follows it, "
            "not the history (an asset-light shift changes capex, depreciation, leases and margins). Cite a finding "
            "as each affected assumption's Source (its F id and URL); passages are in .research/ "
            "(Corpus('.research').assumption_source('S3.p12')).\n"
            + (brief_block(d) + "\n" if brief_block(d) else "") + "Findings:\n" + found)


def _pipeline(task: str, ws, client, model: str, task_id: str, rv) -> dict | None:
    """A request a recipe covers (engines/sheets/pipeline.py) is built in code: one model call for the sourced inputs,
    then the engine checks and the partner and MD review. Not signed off: their issues go back to the input call
    (re-source, re-anchor, never invent), the workbook is rebuilt and reviewed again, up to the review rounds; the
    best-scoring version that passes the checks is delivered. None when no recipe applies or the first build fails
    (the agent then builds it as before)."""
    import shutil
    from engines.sheets import pipeline as PL
    recipe = PL.applies(task)
    if not recipe:
        return None
    try:
        res = PL.build(task, ws, client, model, task_id)
    except Exception as e:  # noqa: BLE001 - the agent is the fallback
        ws.pipeline_note = f"The {recipe} recipe could not build it ({type(e).__name__}: {e}); the agent built it."
        return None
    path = res["path"]
    answer = [f"Built {Path(path).name}: " + " ".join(res["notes"])]
    rounds = max(1, int(os.environ.get("MH_REVIEW_ROUNDS") or 3)) if rv else 1
    msg, last = None, None
    for k in range(1, rounds + 1):
        rep: dict = {}
        msg = finish_check("", [path], ws, report=rep)
        if not rv:
            break
        try:
            last = rv.run_round(path, rep, final=True)
        except Exception as e:  # noqa: BLE001
            answer.append(f"Partner and MD review failed: {type(e).__name__}: {e}")
            break
        if msg:
            last["passed_checks"] = False
        if last["signed_off"] or last["score"] is None or k == rounds:
            break
        issues = [i for i in last["issues"] if i["severity"] in ("blocking", "major")][:12]
        notes = "\n".join(f"- [{i['severity']}, {i.get('persona')}] {i.get('where')}: {i['issue']} Fix: {i.get('fix', '')}"
                          for i in issues)
        from engines.review.personas import coaching
        coach = coaching(last.get("reviews") or [], NAMES)
        if coach:
            notes += "\n\nTheir coaching (keep what works; these changes would reach an 8):\n" + coach
        try:                                            # the inputs again, with the reviewers' issues
            res = PL.build(task + "\n\nThe partner and MD reviewed the model built from your last inputs. Revise the "
                           "inputs where their issues are about a value, its source or its anchor (cite the fact; "
                           "never invent a figure). Issues the inputs cannot fix are recorded in the review.\n" + notes,
                           ws, client, model, task_id)
            path = res["path"]
        except Exception as e:  # noqa: BLE001
            answer.append(f"Revision round {k + 1} could not rebuild ({type(e).__name__}: {e}).")
            break
    if rv and last:                                  # commit what this review taught to the team's memory
        from engines.review import memory as _MEM
        _MEM.learn("excel", last.get("reviews") or [], client, (getattr(rv, "models", None) or [model])[0],
                   f"{task_id}-memory")
    best = rv.best() if rv else None
    if best and best.get("copy") and Path(best["copy"]).exists():
        if Path(best["copy"]).resolve() != Path(path).resolve():
            shutil.copy2(best["copy"], path)           # deliver the best-scoring version that passed the checks
        msg = None
    answer.append("It passes the engine checks (recalculated, every check OK)." if not msg else "Engine checks: " + msg)
    out = {"ok": not msg, "answer": "", "artifacts": [path], "evidence": [f"engine finish check on {Path(path).name}: "
                                                                          + ("PASS" if not msg else "FAIL")],
           "turns": 1, "reason": None if not msg else "engine checks failed", "usage": {}, "recipe": recipe}
    if rv:
        try:
            answer.append(rv.report(best["round"] if best else (last or {}).get("round")))
        except Exception as e:  # noqa: BLE001
            answer.append(f"Partner and MD review report failed: {type(e).__name__}: {e}")
    out["answer"] = "\n\n".join(a for a in answer if a)
    return out


def solve(task: str, *, workdir: str, task_id: str, max_turns: int | None = None, model: str = "deepseek-v4.1-flash",
          client=None, phase: str = "phase_2_build", files=None, review: bool | None = None,
          review_model: str | None = None, review_rounds: int | None = None) -> dict:
    ws = SheetsWorkspace(workdir, os.path.join(workdir, ".run"), inputs=files)
    os.environ.setdefault("MH_DOOM", "1")     # the loop guard on: a cheap model repeated one refused call 40 times
    client = client or LiveClient(phase)
    ctx = {"task_id": task_id, "model": model, "profile": "deepseek", "role": "worker"}
    n = max_turns or int(os.environ.get("MH_SHEETS_MAX_TURNS", "50"))
    task = (f"{task}\n\nTurn budget: {n} turns (one turn = one reply with its tool calls). Save a first complete "
            f"workbook by about turn {max(2, int(0.4 * n))}, stop adding features by turn {max(2, int(0.8 * n))}, "
            f"and call finish before turn {n}; if the budget runs out first, the last saved workbook is checked and "
            "delivered as it is.")
    task += dossier_brief(task, workdir, client, model, task_id)
    rv = WorkbookReview(client, ws, n, model=review_model, max_rounds=review_rounds, task_id=task_id) \
        if review_enabled(client, review) else None
    if rv:
        task += ("\n\nA bank MD and a strategy-firm senior partner review the workbook each time it passes the engine "
                 "checks; if they do not sign off, their issues come back to you to fix before you finish again.")
    done = _pipeline(task, ws, client, model, task_id, rv)
    if done is not None:                      # a known model shape: built in code from one call's sourced inputs
        return done
    crashed = False
    try:
        r = run(task, client=client, model=model, profile=None, ws=ws, ctx=ctx, system_text=SYSTEM,
                caps=Caps(max_turns=n), temperature=0.0, finish_check=rv.gate if rv else finish_check,
                turn_hook=rv.hook if rv else turn_budget)
    except Exception as e:                     # noqa: BLE001 - e.g. a gateway 502 on turn 47 of a run whose
        if not ws.saved_workbook():            # workbook was already saved: check and deliver it, do not lose it
            raise
        from types import SimpleNamespace
        crashed = True
        r = SimpleNamespace(ok=False, answer="", artifacts=[], evidence=[], usage={}, turns=ws.turn + 1,
                            reason=f"{type(e).__name__}: {e}")
    out = {"ok": r.ok, "answer": r.answer, "artifacts": r.artifacts, "evidence": r.evidence, "turns": r.turns,
           "reason": r.reason, "usage": r.usage}
    at_cap = not r.ok and (crashed or r.reason.startswith(CAPS))
    if at_cap:
        out.update(salvage(r, ws, n))
    if rv and (r.ok or at_cap):
        try:
            out = rv.finalize(out, at_cap)
        except Exception as e:                 # noqa: BLE001 - the review never costs the delivered workbook
            out["answer"] = (out.get("answer") or "") + f"\n\nPartner and MD review failed: {type(e).__name__}: {e}"
    try:
        out["artifacts"] = tidy(ws, out["artifacts"])
    except Exception:                          # noqa: BLE001 - tidying never costs the result
        pass
    return out
