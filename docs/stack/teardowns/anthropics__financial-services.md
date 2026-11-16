# Teardown: anthropics/financial-services

Commit `3e32c2287dcb637c184c01cc0ad82d6af7e9bb0f` (2026-11-12). Apache-2.0. Claude plugins (skills + agents) for
finance work. Read in full: `plugins/agent-plugins/model-builder/skills/` `3-statement-model/SKILL.md` (+
`references/formulas.md`, `formatting.md`, `sec-filings.md`), `dcf-model/SKILL.md` (+ `TROUBLESHOOTING.md`,
`scripts/validate_dcf.py`), `lbo-model/SKILL.md`, `comps-analysis/SKILL.md`, `audit-xls/SKILL.md`,
`xlsx-author/SKILL.md`, `agents/model-builder.md`; plus `vertical-plugins/equity-research/skills/initiating-coverage/
assets/quality-checklist.md`, `.../model-update/SKILL.md` and `pitch-agent/skills/ib-check-deck/SKILL.md`. The four
model skills are byte-identical copies in `pitch-agent/`. [observation]
Labels: [observation] seen in the files; [inference] my reading; [unknown] not established.

## Tested setup

Not run: these are prompt recipes for an LLM driving Excel (Office JS) or openpyxl, and this task makes no LLM calls.
The only code is `dcf-model/scripts/validate_dcf.py` (292 lines), read, not executed; it scans for labels such as
"terminal growth" and takes the adjacent number. Their recalculation step points to `recalc.py` from
`anthropics/skills` (`skills/xlsx/scripts/recalc.py`), which runs LibreOffice `calculateAll()` + `store()` in a
dedicated profile. [observation]

## What it does well

- **Formulas over hardcodes, stated as non-negotiable**, with the allowed hardcodes listed (historicals, assumption
  drivers, current market data) — the same rule as our M3. [observation]
- **Sensitivity-table rules that are checkable:** odd dimensions (5x5 / 7x7), axis symmetric around the base case,
  centre cell must equal the model's live output and is highlighted, every cell a full recalculation (no linear
  approximation), every cell different, values move in the expected direction; three DCF tables (WACC x g, revenue
  growth x EBIT margin, beta x risk-free rate). [observation]
- **Check catalogue for 3-statement models** (`3-statement-model/SKILL.md`, "Check Categories"): currency
  consistency; BS balance; cash tie-out; NI link; RE roll-forward (prior RE + NI + SBC − dividends); equity-raise
  tie-out (ΔAPIC = CFF issuance); D&A and capex tie to schedules; ΔAR/ΔInventory/ΔAP tie to the WC schedule;
  DSO/DIO/DPO reasonability; debt ties; NOL schedule (NOL only from losses, utilisation ≤ 80% of EBT, DTA ties, tax = 0
  when taxable income ≤ 0); scenario hierarchy (upside > base > downside for NI, EBITDA, FCF and margins; inverted for
  leverage); credit metrics with covenant thresholds (green/yellow/red); a master check. [observation]
- **Audit recipe** (`audit-xls`) with model-type bug lists: DCF (mid-year vs end-year mismatch, TV not discounted,
  book-value WACC weights, FCF that includes interest, double-counted tax shield), LBO (sweep vs paydown mismatch, PIK
  not accruing, management rollover missing, exit multiple on the wrong EBITDA, fees not deducted from day-1 equity),
  merger (pre- vs post-deal share count, unphased synergies, PPA not balancing, foregone interest on cash, fees missing
  from S&U), 3-statement (WC sign, depreciation vs PP&E schedule, maturities vs principal, dividends > NI). Plus
  generic checks: off-by-one SUM ranges, pasted-over formulas, hidden rows/tabs, unit/scale mismatch (% stored as whole
  numbers), edge cases (0% or negative growth, negative EBITDA, leverage going negative). Report: findings table with
  Critical / Warning / Info and a one-line verdict. [observation]
- **DCF sanity thresholds:** g < WACC (critical), WACC outside 5–20% flagged, terminal value > 75–80% of EV flagged,
  < 40% questioned; g not above risk-free / long-run GDP. [observation]
- **Comps:** header block with "As of" date and units; statistics max / 75th / median / 25th / min; notes and
  methodology section; red flags (different fiscal year ends, > 10% variance between sources, EV/EBITDA on negative
  EBITDA, P/E > 100x without a growth story); "better 3 perfect comps than 6 questionable". [observation]
- **Provenance on inputs:** every hardcoded input gets a cell comment "Source: [system/document], [date],
  [reference], [URL]", written when the value is entered; hyperlinks to EDGAR. [observation]
- **Model update after earnings** (`equity-research/skills/model-update`): actual vs prior estimate vs delta table,
  old vs new estimates for this and next fiscal year, valuation impact per method, price-target change. [observation]
- Human checkpoints after each statement / section (show the BS check before building the CF, etc.). [observation]

## Where it breaks

- **Nothing is enforced.** The rules live in prompts; the one script (`validate_dcf.py`) is a label-adjacency
  heuristic that checks three things (g < WACC, WACC range, TV share) and finds them only if the labels match.
  [observation] Compliance depends on the model following the text. [inference]
- **Data assumptions:** comps and the agent assume S&P Capital IQ / FactSet / Daloopa MCPs; without them they fall
  back to "SEC EDGAR or other institutional sources" with no fetcher. [observation]
- **Contestable defaults:** WACC weights use **net** debt ("Debt Weight may be negative" when cash > debt), where
  common practice and our STANDARD V3 use gross debt or a target structure; the 3-statement recipe enables iterative
  calculation for interest while the LBO recipe says to use beginning balances (our default, FAST/ICAEW); the DCF
  recipe discounts the perpetuity TV at N − 0.5 under mid-year convention without separating the exit-multiple case.
  [observation; "contestable" is inference]
- **Recalc step inherits LibreOffice's gaps:** `recalc.py` cannot evaluate XLOOKUP / LET / dynamic arrays on
  LibreOffice 24.2 and rewrites native data tables as `TABLE()` formulas in place (tested on the same LibreOffice in
  the headless-excel teardown); the recipes avoid data tables for that reason ("Excel's DATA TABLE function may not
  work with openpyxl"). [observation]
- The equity-research QC checklist measures volume (≥ 30 pages, ≥ 25 charts, ≥ 10,000 words) rather than
  correctness. [observation]

## What we lack (against STANDARD.md and the engine as it is)

Each item: the recipe rule, our status, and where it would go.

1. **Sensitivity-table integrity** (centre = live base output, odd grid, every cell differs, monotonic). Our DCF
   builder already writes 5x5 grids with a centre == base-case check row (`builder.py:_dcf_tail`, returns `grid_*`
   and `grid_*_centre` refs) [observation]; what we lack is a validator rule for any grid, including user-supplied
   models: "all cells distinct" and "monotonic along each axis". Add an X-rule in
   `app/engines/forge/validate_xlsx.py`.
2. **Operating sensitivities** (revenue growth x EBIT margin; beta x rf) and IRR / EPS grids: STANDARD M13 gap.
   `builder.py` sensitivity helpers.
3. **DCF threshold checks:** our `builder.py:alerts` already warns on TV share > 75% and on g above the risk-free
   rate (so STANDARD V4's "Gap: a check g ≤ risk-free" is out of date) [observation]. Missing: WACC outside 5–20%,
   TV share < 40%, and a hard refusal (not an alert) when g ≥ WACC in a user-supplied model. Add to
   `app/engines/sheets/qa.py:model_gaps` and as alert rows in `alerts`.
4. **RE roll-forward, CFO + CFI + CFF = Δcash, equity-raise tie-out, D&A/capex to schedule** as explicit check rows:
   our M9 enforces BS balance, cash tie and NI tie; M7 builds the equity roll-forward. `builder.py` has no
   "retained earnings" check row (string not found) [observation]. Add the missing rows to the Checks block so X12
   (master flag) covers them.
5. **NOL / DTA schedule** with the 80% limitation and tax = 0 on losses: STANDARD M18 gap (deferred taxes and NOLs).
   `builder.py:three_statement` tax block.
6. **Scenario hierarchy check** (upside > base > downside for NI, EBITDA, FCF, margins; leverage inverted): we have
   scenarios (M12, `scenario_book`) but no hierarchy test. `qa.py:model_gaps`, reading `scenario_summary`.
7. **Credit metrics with covenant thresholds** (Debt/EBITDA, Net debt/EBITDA, coverage, G/Y/R): roadmap item 1
   (lenders); not built.
8. **Currency section** (reporting currency identified, one currency and scale per model, units row matches):
   we have units checks (X10 `no_unit`) but no currency field; `spreading.py` refuses IFRS / non-USD filers. Needed
   before any 20-F or EGX model (see the edgartools and OpenBB notes: mixed-unit columns are a real failure mode).
9. **Audit generics we do not check:** hidden rows / hidden sheets, off-by-one SUM ranges (a SUM over a block that
   stops one row short of its neighbours'), % stored as whole numbers, and the edge-case flex test (0% growth,
   negative EBITDA). `validate_xlsx.py` new rules; the flex test needs fast in-process recalc (formulon).
10. **Model-type bug list as checks:** of the DCF/LBO/merger lists, deterministic ones we can add: mid-year factor vs
    TV period consistency, TV discounted, UFCF excludes interest, LBO fees deducted from day-1 equity, PIK accrues,
    S&U balance, merger PPA balances, dividends > NI flag. Which of these `builder.py:lbo` / `merger` already
    guarantee by construction is [unknown] without a line-by-line review.
11. **Comps hygiene:** fiscal-year-end mismatch flag (STANDARD V6 gap: calendarisation), > 10% source-variance flag
    when two sources give a figure (R7 gap: conflict detection), max/75th/median/25th/min (we show median and IQR;
    add max/min), "As of" stamp on the sheet. `builder.py:comps_table`.
12. **Input provenance format:** our M5 / X4 require a source per input or historical (comment or Source column);
    the recipe also asks for the date and URL in every comment and hyperlinks to EDGAR. `spreading.Cell` already holds
    the filing URL; write it as a hyperlink in `builder.py`.
13. **Model update after earnings** (actual vs estimate vs delta; old vs new estimates; valuation impact): we only
    build new models. A `model_update` flow in `app/engines/sheets/` fed by the 8-K Item 2.02 exhibit and the 10-Q
    XBRL (edgartools) would cover it.
14. **Four-colour fonts** (purple for same-sheet links): our M2 is three colours; style choice, low priority.
15. **Findings report format** (Critical / Warning / Info + one-line verdict): `validate_xlsx.report_text` has
    severities; add the verdict line. Low priority.

Where we are ahead: rules are enforced by code (X0–X14, `qa.check`, `model_gaps`), historicals come from SEC XBRL
with accession per cell and restatement flags, WACC inputs are fetched with sources (`market.py`), default
no-circularity, and a cover/tab map is required. [observation]

## What we take

The check catalogue above, turned into deterministic rules (items 1, 3, 4, 6, 9 first: they are cheap and catch
silent errors), and the sensitivity-table conventions in `builder.py`. Prompt text is not copied into our prompts
wholesale; where a rule is copied into docs, cite the file. [inference]

## Licence note

Apache-2.0 at the pinned commit (`LICENSE`). Copying text or the validation script is allowed with the licence
notice; the example workbooks the skills mention (`examples/LBO_Model.xlsx`, `examples/comps_example.xlsx`) were not
present in the clone (not checked further: [unknown] whether they ship elsewhere). Ledger row matches.
