# Excel calculation and financial data: teardown summary

Six repos tested on 2026-11-15 at the commits in `docs/stack/license-and-repo-ledger.csv`, each in its own venv under
the session scratchpad, no LLM calls, nothing installed into the harness or changed under `app/`. Per-repo notes:
`libraz__formulon.md`, `hewliyang__headless-excel.md`, `dgunning__edgartools.md`, `JerBouma__FinanceToolkit.md`,
`OpenBB-finance__OpenBB.md`, `anthropics__financial-services.md`.
Labels: [observation] seen in a test run or in the files; [inference] my reading; [unknown] not established.

## Do first: our LibreOffice recalc can report stale values

`app/engines/sheets/qa.py:recalc` and `app/engines/forge/validate_xlsx.py:recalc` (through
`app/engines/forge/common.py:soffice_convert`) run `soffice --convert-to xlsx` with a fresh profile. On LibreOffice
24.2 that **does not recompute formulas that already carry cached values**, even with `fullCalcOnLoad="1"`: after
changing an input (growth 8% -> 12%) in a file with cached results, `Model!G2` stayed 1,469.33; the correct value is
1,762.34 (formulon and LibreOffice `calculateAll()` both gave it). It also leaves native Excel data tables empty.
[observation] Workbooks our builder writes through openpyxl have no cached values, so they are computed correctly
today; the exposure is any workbook that arrives with cached values (user-supplied models, the X11 "before" file,
files re-edited after a recalc), where the checks would read Excel's or an earlier run's numbers and verify nothing.
[inference]

Fix (tested both): write `user/registrymodifications.xcu` into the throwaway profile with
`/org.openoffice.Office.Calc/Formula/Load` `OOXMLRecalcMode = 0` (and `ODFRecalcMode = 0`) before converting; that
recomputed the stale cell but evaluated the data table wrongly (all 12 cells = base EV). A `calculateAll()` call
(Basic macro in the temp profile, as `anthropics/skills` `xlsx/scripts/recalc.py` and headless-excel do) got both
right. `soffice_convert` already prepares its temp profile in `_profile_fonts`, so either goes there. [observation]

## Ranked adoption list

| # | Repo | Verdict | Integration pointer |
|---|---|---|---|
| 1 | dgunning/edgartools (MIT) | **Adopt** as a library for filings and foreign filers; keep our spreading as the source of record for US statements | `engines/research/library.py`: 10-K item text and 8-K Item 2.02 EX-99 press releases as corpus passages. `engines/sheets/spreading.py:spread`: when a derived "other ..." line is > ~10% of its total, read `filing.xbrl().statements` and surface the extension-tagged lines (NVDA FY2026: $51,951m marketable securities under `nvda:` sit in our "other current assets"). 20-F / 40-F via `filing.xbrl()` with one reporting currency per column. |
| 2 | libraz/formulon (Apache-2.0) | **Adopt** as a second calc engine and fast in-process recalc, not as the only engine | `engines/forge/validate_xlsx.py`: X5b "engines disagree" (formulon vs LibreOffice, 1e-6); new X15 stress test (set inputs to 0% growth / negative EBITDA / WACC +200 bp, `recalc()`, checks must hold; 0.03–0.3 s per recalc on our models); refuse defined names equal to function names (formulon shadows `RATE(` with a name `rate`); check `_xlfn.` prefixes statically (formulon tolerates their absence, Excel does not). `engines/sheets/qa.py:check`: use formulon values where LibreOffice returns `#NAME?` for functions it lacks (XLOOKUP, LET, dynamic arrays). |
| 3 | anthropics/financial-services (Apache-2.0) | **Adopt the check catalogue** as deterministic rules; not the prompts | See "Gaps against the recipes" below; first: sensitivity grids distinct + monotonic (validate_xlsx), WACC 5–20% / TV < 40% / g ≥ WACC refusal (`qa.model_gaps`, `builder.alerts`), RE roll-forward and CFO + CFI + CFF = Δcash rows (`builder.py` Checks), scenario hierarchy (`qa.model_gaps` over `scenario_summary`), hidden rows/sheets and off-by-one SUM (validate_xlsx). |
| 4 | hewliyang/headless-excel (Apache-2.0) | **Take the idea** (hard recalc, warm daemon, error details with input values); **not the package** | The fix above in `common.soffice_convert`; optionally one warm soffice per worker with its own profile and UNO port for the agent loop in `engines/sheets/engine.py` (~1 s saved per recalc); add formula + precedent values to `qa.check` messages. Package rejected: overwrites the user's file through LibreOffice (data tables become invalid `TABLE()` formulas, 2,749 number formats rewritten), writes a macro into the global LibreOffice profile, fixed UNO port 2002, and returned 164 empty cells **without an error** when its daemon and a cold call shared a profile. |
| 5 | JerBouma/FinanceToolkit (MIT) | **Borrow formulas and use as a test oracle**; do not ship its WACC, prices or valuations | Port ratio definitions (ROIC on average capital, DSO/DIO/DPO, coverage, DuPont) into a ratios block in `engines/sheets/builder.py`; in `app/tests/` feed `spreading.Spread` to it and assert our ratio cells match (STANDARD M21). Works with no key when we supply statements (tested with edgartools data); its WACC came out 1.46%–46% and non-December fiscal years get prices up to 11 months off. |
| 6 | OpenBB-finance/OpenBB (Apache-2.0 monorepo) | **Do not adopt the platform.** Take the Fed H.15 / FX ideas; non-US prices only via `yfinance` directly and only after a terms-of-use decision | `engines/sheets/market.py`: second Treasury source (Fed H.15) beside the Treasury XML; `yahoo_history` / `yahoo_statements` for non-EDGAR tickers (CLHO.CA worked: 252 days, 4–5 years of statements in EGP) labelled "Yahoo Finance, unaudited". The provider that served all of that, `openbb-yfinance 2.1.0`, is **AGPL-3.0-only** and lives outside the pinned monorepo; `pip install openbb` 5.0 also ships without the equity router. |

## What the tests showed (headline numbers)

- **Calc engines on our own workbooks:** formulon and LibreOffice agree on 4,098 / 4,098 formula cells across four
  engine-built workbooks and 49,802 / 49,802 on a 50k-formula stress file. [observation]
- **Function coverage:** of 110 probe functions, LibreOffice 24.2 returned `#NAME?` for 30 (the post-2019 family:
  XLOOKUP, LET, FILTER, SORT, UNIQUE, SEQUENCE, LAMBDA helpers, TEXTSPLIT, VSTACK...); formulon computed all but the
  cloud functions. DURATION/MDURATION: LibreOffice 5.99195 vs Excel's documented 5.99377 (formulon matches Excel).
  Native data table: formulon correct and written back as an Excel data table; LibreOffice `--convert-to` empty;
  LibreOffice `calculateAll()` correct but rewritten as non-Excel `TABLE()` formulas. Iterative circularity: engines
  agree to 1.5e-9. [observation]
- **Speed:** cold LibreOffice 1.3–2.0 s per small workbook, 2.7–3.5 s at 50k formulas; warm LibreOffice daemon
  0.4–1.4 s; formulon 0.03–0.3 s per small workbook after a one-off 1.6–3.3 s WASM start, but ~5.5 s at 50k formulas
  (WASM load/save dominate). [observation]
- **SEC data:** our spreading and edgartools agree on 290 tag-year values for COST and NVDA. edgartools' standardized
  statements drop lines (NVDA marketable securities, capex, dividends; COST receivables) and mix pre- and post-split
  NVDA per-share data; both tools miss NVDA's FY2026 extension-tagged marketable securities ($52bn), which only the
  filing's presentation XBRL shows. [observation]
- **Non-US:** edgartools reads 20-F / 40-F XBRL (TSM, SAP, BABA, CNQ) but its companyfacts-based statements for TSM
  are a year stale and mix USD convenience figures with TWD in one column; CLHO.CA is not on EDGAR
  (`CompanyNotFoundError`). OpenBB/Yahoo covers CLHO.CA prices and statements but not its metrics, news or the EGX30.
  [observation]
- **SEC etiquette:** edgartools refuses requests without an identity (`IdentityNotSetError`) and rate-limits to 9/s by
  default; SEC allows 10/s with a declared User-Agent. Our `spreading._get` also refuses a User-Agent without an
  e-mail, but has no rate limiter (one request per company, retries with back-off). [observation]

## Gaps against the anthropics/financial-services recipes

What the recipes ask for that our engine does not do or does not enforce (details and file pointers in
`anthropics__financial-services.md`):

1. Sensitivity grids: we build 5x5 WACC x g with a centre check; nothing verifies "every cell different" and
   "monotonic"; no revenue growth x EBIT margin or beta x rf grids; no IRR/EPS grids (M13 gap).
2. DCF thresholds: WACC outside 5–20%, TV share < 40%, hard refusal when g ≥ WACC (we already alert on TV > 75% and
   g > rf).
3. Check rows: RE roll-forward, CFO + CFI + CFF = Δcash, equity-raise tie-out, D&A and capex tied to schedules.
4. NOL / DTA schedule with the 80% limitation (M18 gap).
5. Scenario hierarchy: upside > base > downside for NI, EBITDA, FCF and margins; leverage inverted.
6. Credit metrics with covenant thresholds (roadmap item 1, not built).
7. Currency: reporting currency identified, one currency and scale per model; we refuse IFRS/non-USD filers.
8. Audit generics: hidden rows/sheets, off-by-one SUM ranges, % stored as whole numbers, edge-case flex tests.
9. Model-type bug lists turned into checks (TV discounted, UFCF excludes interest, LBO fees from day-1 equity, PIK
   accrual, S&U and PPA balance, dividends > NI); which of these our builders guarantee by construction is
   [unknown] without a review of `builder.py:lbo` and `merger`.
10. Comps: fiscal-year-end mismatch flag, > 10% source-variance flag, max/min beside median and IQR, "As of" stamp.
11. Input provenance with date and URL in each comment and EDGAR hyperlinks (we require a source; the URL is in
    `spreading.Cell.url` but not written as a link).
12. Model update after earnings: actual vs estimate vs delta, estimate revisions, valuation impact (we only build new
    models).
13. Minor: purple font for same-sheet links; Critical/Warning/Info verdict line in the validator report.

Where we are ahead of the recipes: the rules are enforced by code (forge X0–X14, `qa.check`, `model_gaps`), historicals
carry SEC accession numbers and restatement flags per cell, WACC inputs are fetched with sources, and the default
model has no circularity. [observation]

## Licence notes

All six repos' own code licences match the ledger: Apache-2.0 (formulon, headless-excel, financial-services, OpenBB
monorepo — 34 of 34 components declare Apache-2.0) and MIT (edgartools, FinanceToolkit). Two exceptions found in what
they pull in: **`openbb-yfinance 2.1.0` is AGPL-3.0-only** (published from `deeleeramone/openbb-danglewood`, not part
of the pinned monorepo), and Yahoo Finance data, used by FinanceToolkit and OpenBB when there is no paid key, has
terms of use whose fit with a paid product is [unknown]. formulon's runtime dependency wasmtime is
`Apache-2.0 WITH LLVM-exception`. Suggest updating the OpenBB row of `license-and-repo-ledger.csv` with the AGPL
finding (not edited here). [observation]

## Reproducing

Scripts and outputs are in the session scratchpad under `td/` (may be cleaned): `build_synth.py`, `build_probe.py`,
`run_lo.py`, `run_lo_always.py`, `run_formulon.py`, `run_hx.py`, `compare.py`, `bench.py`, `edgar/e1.py`–`e3.py`,
`ft/ft1.py`, `obb/o1.py`; comparison JSON in `td/out/cmp_*.json`.
