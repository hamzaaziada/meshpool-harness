# Teardown: libraz/formulon

Commit `636e32f1158f973b549b7cc6a309959716a444e5` (pinned clone). Apache-2.0 (`LICENSE`, `NOTICE`). C++17 core shipped
as WebAssembly; the Python wheel runs it under wasmtime. Tested the PyPI wheel `formulon 0.18.0`, which is the version
in the pinned clone's `packages/python/pyproject.toml`.
Labels: [observation] seen in a test run or in the code; [inference] my reading; [unknown] not established.

## Tested setup

- Isolated venv (Python 3.12, `uv`) under the scratchpad: `formulon 0.18.0`, `openpyxl 3.1.5`. Nothing installed into
  the harness's Python. [observation]
- Reference: LibreOffice 24.2.7.2, called exactly the way `app/engines/sheets/qa.py:recalc` and
  `app/engines/forge/validate_xlsx.py:recalc` call it (`soffice --headless --convert-to xlsx`, fresh profile per call).
  [observation]
- Corpus (scratchpad `td/corpus/`):
  - four workbooks our engine produced: `tv.xlsx` (226 formulas), the NVIDIA/CoreWeave IC model (436), the
    `polish/fin/model.xlsx` 3-statement model (2,836), the coffee-kiosk projection (600);
  - `synth.xlsx`, a 3-statement-style workbook built with openpyxl (`td/build_synth.py`): SUM, IF, INDEX/MATCH,
    XLOOKUP, VLOOKUP, SUMIFS, NPV, IRR, XNPV, XIRR, MIRR, PMT, RATE, OFFSET, INDIRECT, LET, IFS, SWITCH, FILTER / SORT /
    UNIQUE / SEQUENCE, a native 2-D Excel data table (`<f t="dataTable">`, EV by WACC x g) and a `circ` switch that
    makes interest use average debt. Variants: `synth_circ.xlsx` (switch on, iterative calculation on),
    `synth_circ_noiter.xlsx` (switch on, iteration off), `synth_noprefix.xlsx` (modern functions written without the
    `_xlfn.` prefix);
  - `probe.xlsx`: 110 functions, one per cell (41 post-2010 / dynamic-array / LAMBDA functions, 50+ finance, date and
    statistics functions);
  - `big50k.xlsx`: 49,802 chained formulas, for speed.
- Each workbook recalculated by formulon (`Workbook.load` -> `recalc()` -> `recalc_data_tables()` -> `save()`) and by
  LibreOffice; every formula cell, spill cell and data-table cell compared (`td/compare.py`, rel. tol. 1e-9).
  [observation]

## What it does well

- **Our engine's workbooks: 100% agreement with LibreOffice.** 4,098 of 4,098 formula cells on the four engine-built
  workbooks, and 49,802 of 49,802 on `big50k`. [observation]
- **Modern Excel functions LibreOffice 24.2 cannot evaluate.** In the probe, LibreOffice returned `#NAME?` for 30 of
  110 cells (XLOOKUP, XMATCH, LET, FILTER, SORT, SORTBY, SEQUENCE, TEXTBEFORE/AFTER/SPLIT, VSTACK, HSTACK, TAKE, DROP,
  CHOOSECOLS, TOCOL, WRAPROWS, BYROW, MAP, REDUCE, SCAN, ARRAYTOTEXT, VALUETOTEXT, REGEXTEST, PERCENTOF, TRIMRANGE,
  IMAGE, STOCKHISTORY, unprefixed AGGREGATE) and `#VALUE!` for LAMBDA; formulon computed all of them except the
  cloud functions (IMAGE, STOCKHISTORY, WEBSERVICE return errors by design). [observation] It registers 526 function
  names (`Workbook.function_count()`). [observation]
- **Excel data tables, natively.** It computes the 3 x 4 `{=TABLE()}` grid and writes it back as a real Excel data
  table (`<f t="dataTable" ref="I2:L4" ...>` plus cached values). LibreOffice `--convert-to` left all 12 cells empty;
  LibreOffice via UNO `calculateAll()` computed the same 12 numbers but rewrote them as per-cell
  `TABLE($H$1,$B$8,$H2,$B$9,I$1)` formulas, which are not valid Excel. [observation]
- **Iterative circularity.** With `iterate=1` in the file, formulon read the settings (100 iterations, 1e-6) and its
  converged interest agreed with LibreOffice to 1.5e-9 relative. [observation]
- **Recalculates stale cached values.** After editing an input in a file that already carried cached results,
  formulon recomputed (`Model!G2` = 1,762.34, correct); LibreOffice `--convert-to` kept the stale 1,469.33 (see the
  summary note: this is a defect in our current QA path). [observation]
- **Finance accuracy where LibreOffice is off.** DURATION(2008-01-01, 2016-01-01, 8%, 9%, 2, 1): formulon 5.993775
  (Microsoft's documented value, and the value in formulon's own unit test), LibreOffice 5.991950; MDURATION differs
  the same way. FORMULATEXT returns Excel syntax; LibreOffice returns its own (`=_xlfn.xmatch(4,$Data.A1:A10...`).
  [observation]
- **Speed in a warm process.** WASM start-up 1.6–3.3 s once per process; then load + recalc + save: `tv.xlsx` 0.034 s,
  the 2,836-formula model 0.26–0.30 s, against 1.3–2.0 s for a cold LibreOffice call and 0.4–1.2 s for a warm
  LibreOffice daemon. [observation]
- **Dependency graph API.** `precedents()` / `dependents()` resolve defined names and cross-sheet references (for
  `Model!C6` it returned `Inputs!B12` (`circ`), `Inputs!B11` (`rate`) and the two debt cells). [observation] That is
  what `validate_xlsx.py` approximates with regexes (`_refs`, `_tarjan`). [inference]
- **File fidelity.** Formulas, 433/433 defined names, freeze panes, fills and number formats survive the round trip;
  it canonicalises `=3660.0` to `=3660` (15 cells) and writes an explicit black font colour where none was set (18–25
  cells). LibreOffice rewrote quoting in 39 formulas and re-escaped 2,749 number-format strings. [observation]

## Where it breaks

- **A defined name shadows a function of the same name.** In `synth.xlsx`, `=RATE(5,0,-1000,1500)` returned `#VALUE!`
  because the workbook defines a name `rate`; the same formula in a workbook without that name returns 0.084472
  (correct). Reproduced with `npv` / `NPV(...)`. [observation] Excel resolves `RATE(` as the function. [inference]
  None of our four engine workbooks has a defined name equal to a function name today (checked 834 names), but
  `builder.py` names cells freely (`tax`, `growth`, `term_rate`...). [observation]
- **Lenient about missing `_xlfn.` prefixes.** In `synth_noprefix.xlsx` it evaluated `XLOOKUP(`, `PERCENTILE.INC(`,
  `IFS(`, `STDEV.S(` etc. written without the prefix; LibreOffice returned `#NAME?`. [observation] Excel needs the
  prefix in the file (openpyxl documents this) and shows `#NAME?` otherwise. [inference] Used as a validator, formulon
  would pass a file that breaks in Excel.
- **Circular reference without iteration** gives `#REF!` (LibreOffice: `#VALUE!` / Err:522). Excel would show 0 and
  a warning. [inference] Any of these is fine for detection, but the error kind differs by engine. [observation]
- **Legacy-cell dynamic arrays:** `=SUM(_xlfn._xlws.FILTER(...))` in a plain (non-dynamic-array) cell returned
  `#VALUE!`. What Excel returns for that file is [unknown].
- **Large files are slow to load and save in WASM:** `big50k` took 2.5–2.8 s to load, 0.65 s to recalc, 2.0–2.4 s to
  save (about 5.5 s), against 1.4 s for a warm LibreOffice daemon and 2.7–3.5 s for cold LibreOffice. [observation]
- XIRR converges to a slightly different root than LibreOffice (1e-8 relative). Not material. [observation]
- The Python wheel is single-threaded (serial recalc only, per its docstring). [observation]

## What we take

1. **Second calc engine in the validator** (cross-check, not replacement): in `app/engines/forge/validate_xlsx.py`
   add an X5b "engines disagree" check: recalc with formulon as well as LibreOffice and report any formula cell whose
   values differ beyond 1e-6 relative (1e-6 absolute for iterative cells). On our own workbooks this should be silent
   (it was, on 4,098 cells); when it fires, it is either a LibreOffice gap (modern functions, data tables, DURATION) or a
   formulon bug (name shadowing), and both are worth a human look.
2. **Use formulon wherever LibreOffice cannot answer:** workbooks with XLOOKUP / LET / dynamic arrays / native data
   tables (user-supplied models), and the stale-cache case. In `app/engines/sheets/qa.py:check` fall back to formulon
   values for cells LibreOffice returns as `#NAME?` when the function is in `formulon.Workbook.function_name_at` list.
3. **Fast in-process flex tests** (what `audit-xls` calls edge cases): load the workbook once, `set_number` an input
   (growth 0%, negative EBITDA, WACC +200 bp), `recalc()`, read the checks row. At ~0.03–0.3 s per recalc this makes a
   "does every check still pass under stress" rule affordable in `validate_xlsx.py` (new X15) and in
   `qa.model_gaps`.
4. **Dependency graph** from `precedents()` / `dependents()` to replace the regex reference parser behind X1/X6/X7 in
   `validate_xlsx.py` (cycle detection that understands names and OFFSET/INDIRECT is [unknown]; test before swapping).
5. **Guard against its known bug in our own output:** add a rule to `validate_xlsx.py` X5 (`bad_name`) refusing a
   defined name equal to an Excel function name (case-insensitive), and keep writing `_xlfn.` prefixes; check the
   prefix statically rather than relying on any engine.

Not taken: formulon as the only engine. Its leniency on prefixes and the shadowing bug mean LibreOffice stays the
reference for "will this open cleanly", with formulon as the second opinion.

## Licence note

Apache-2.0 at the pinned commit (`LICENSE` + `NOTICE`). Its one runtime dependency is `wasmtime 49.0.0`, licensed
`Apache-2.0 WITH LLVM-exception` (its METADATA). [observation] Keep the NOTICE file if we vendor or redistribute.
Ledger row matches. [observation]
