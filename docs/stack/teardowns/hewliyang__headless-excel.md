# Teardown: hewliyang/headless-excel

Commit `294064fa25a132456ca505bf14296b8d2534fc29` (pinned clone), package `headless-excel 0.1.4`. Apache-2.0. A thin
Python wrapper around openpyxl plus LibreOffice: `ctx.sync()` saves with openpyxl, recalculates with LibreOffice and
reloads, like Office JS `context.sync()`.
Labels: [observation] seen in a test run or in the code; [inference] my reading; [unknown] not established.

## Tested setup

- Isolated venv (Python 3.12) with the package installed from the pinned clone; `HOME` pointed at a scratch folder so
  its macro and daemon files did not touch the real user profile. LibreOffice 24.2.7.2; the Linux daemon uses the
  system `/usr/bin/python3` with `python3-uno` (present). `headless-excel check` passed. [observation]
- Same corpus and comparison as the formulon teardown (`td/corpus/`, `td/compare.py`): four engine-built workbooks
  (4,098 formulas), `synth*.xlsx` (finance functions, native data table, `circ` switch), `probe.xlsx`, `big50k.xlsx`.
  Two modes: public API (`run(path)` + `ctx.sync()`, daemon) and the library's cold path (`libre._cold_recalc`).
  Reference: LibreOffice `--convert-to`, as our `qa.recalc` does it. [observation]

## What it does well

- **Same values as our LibreOffice path** on every engine-built workbook (4,098 / 4,098 cells, both modes). It is
  LibreOffice underneath, so this is expected. [observation]
- **Hard recalculation.** Both modes run `calculateAll()` before `store()` (Basic macro for cold runs,
  `daemon/linux_helper.py:handle_recalc` over UNO for the daemon). That fixes two defects of `--convert-to`:
  stale cached values are recomputed (`Model!G2` 1,762.34 after an input edit, where `--convert-to` kept 1,469.33),
  and Excel data tables get values (the 12-cell WACC x g grid, identical to formulon's numbers). [observation]
- **Warm daemon.** Raw daemon recalc: 0.4–0.7 s for `tv.xlsx`, 0.6–1.2 s for the 2,836-formula model, ~1.4 s for 50k
  formulas, against 1.3–2.0 s and 2.7–3.5 s for cold `--convert-to`. Through `ctx.sync()` add openpyxl load/save
  (fin3s 1.74 s end to end). The daemon auto-exits after 5 minutes idle. [observation]
- **Agent-friendly error report.** After `sync()` it scans for `#REF!`/`#NAME?`/... and prints each failing cell with
  its formula and the values of its inputs (`context.py:_build_error_details`), e.g.
  `Outputs!B40: #NAME? formula==let(x,B5,y,B1,x-y) inputs={Outputs!B5=2342.8, Outputs!B1=597.2}`. Pre/post-sync
  hooks (`hooks.py`) for house rules. [observation]

## Where it breaks

- **Overwrites the user's file in place, through LibreOffice.** `store()` writes the workbook back as LibreOffice
  exports it: native data tables become per-cell `TABLE($H$1,$B$8,$H2,$B$9,I$1)` formulas (not valid Excel), 39
  formulas were re-quoted and 2,749 number-format strings re-escaped on the 3-statement model. `sync()` also re-saves
  through openpyxl first (dropping anything openpyxl does not model). [observation] Our `qa.recalc` writes a separate
  copy and does not have this problem. [observation]
- **Global side effects.** The cold path writes a Basic macro into the user's LibreOffice profile
  (`~/.config/libreoffice/4/user/basic/Standard`, `libre.py:setup_libreoffice_macro`); the Linux daemon listens on a
  fixed UNO port 2002 and keeps one PID/port file pair in `~/.headless-excel/` (its own `docs/roadmap.md` lists
  multi-instance support as future work). [observation]
- **Silent wrong results when two LibreOffice instances share a profile.** With the daemon running, the cold path
  returned without an error but left 164 formula cells empty (`None`) in four workbooks; rerun with the daemon
  stopped, they were correct. [observation] LibreOffice hands the document to the already-running instance and the
  macro call exits early. [inference] Concurrent workers would hit this.
- **Error-detail bug:** for a cross-sheet range it attributes the range end to the host sheet
  (`inputs={Inputs!D2=Alpha, Outputs!D6=None}` for `Inputs!D2:D6`). [observation]
- It inherits every LibreOffice 24.2 gap: `#NAME?` for XLOOKUP / LET / FILTER / SORT / UNIQUE / SEQUENCE and the rest
  of the dynamic-array family; DURATION/MDURATION off Excel's value in the 4th significant figure. [observation]

## What we take

1. **The hard-recalc idea, not the package.** Fix `app/engines/sheets/qa.py:recalc` and
   `app/engines/forge/validate_xlsx.py:recalc` (which goes through `app/engines/forge/common.py:soffice_convert`; that
   function already writes into its throwaway profile via `_profile_fonts`) so LibreOffice recomputes every cell. Two tested
   options: (a) write `user/registrymodifications.xcu` into the fresh temp profile with
   `/org.openoffice.Office.Calc/Formula/Load: OOXMLRecalcMode = 0` (always recalculate) before `--convert-to`; tested,
   it fixed the stale value (1,762.34) but computed the native data table wrongly (all 12 cells = the base EV
   2,342.81); (b) a `calculateAll()` macro/UNO call into a temp profile (what this package and
   `anthropics/skills` `xlsx/scripts/recalc.py` do), which computed the data table correctly. Keep writing to a copy.
   [observation]
2. **A warm LibreOffice daemon with per-worker profiles** if recalc latency matters in the agent loop
   (`app/engines/sheets/engine.py` calls QA after each build): one soffice per worker, each with its own
   `-env:UserInstallation` and UNO port, never sharing a profile with cold calls. Saves ~1 s per recalc. [inference]
3. **Error details with input values** in our QA messages: `qa.check` reports the failing cell; adding the formula and
   its precedents' values (as `_build_error_details` does) gives the model what it needs to fix it in one turn.
   [inference]

Not taken: the package itself (in-place writes, global profile changes, profile collisions, single fixed port).

## Licence note

Apache-2.0 (`LICENSE`; PyPI `headless-excel 0.1.4`). It drives LibreOffice (MPL-2.0) as a separate process, so no
copyleft reaches our code. [inference] Ledger row matches. [observation]
