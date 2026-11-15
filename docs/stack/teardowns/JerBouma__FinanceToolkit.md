# Teardown: JerBouma/FinanceToolkit

Commit `f7ca32720377b79c504e0e13942948a8465ced58` (pinned clone), package `financetoolkit 2.3.4`. MIT
(`LICENSE.txt`). 150+ ratios, models (DuPont, WACC, intrinsic value, EV breakdown), risk and technicals, all as
transparent pandas formulas.
Labels: [observation] seen in a test run or in the code; [inference] my reading; [unknown] not established.

## Tested setup

- Same isolated venv as edgartools (installed from both pinned clones). No FMP key set
  (`FINANCIAL_MODELING_PREP_API_KEY` removed from the environment). Script: scratchpad `td/ft/ft1.py`. [observation]
- Statements for COST and NVDA (4 fiscal years each) built from **edgartools EntityFacts** (SEC companyfacts) with a
  ~45-line concept map to FinanceToolkit's generic items (`normalization/balance.csv`, `income.csv`, `cash.csv`), and
  passed as `Toolkit(..., balance=, income=, cash=)`: a DataFrame indexed by (ticker, item) with annual `pd.Period`
  columns, the format of the repo's own test datasets. Prices and treasury yields left to the Toolkit (Yahoo Finance,
  since there is no key). [observation]
- Computed: gross, operating and net margin; ROE, ROA, ROIC; current and quick ratio; debt-to-equity; interest
  coverage; asset and inventory turnover; DSO; P/E; EV/EBITDA; FCF yield; WACC; intrinsic value (FCFF DCF, 8% growth,
  2.5% perpetual, 9% WACC); EV breakdown. [observation]

## What it does well

- **Works without a paid key when we supply the statements.** Custom statements are used as given and "never replaced
  by downloaded ones" (`toolkit_controller.py`); only prices and rates come from Yahoo Finance. Data build 19 s (SEC
  facts download), Toolkit run 2.4 s. [observation]
- **Ratio arithmetic is right on spot checks.** COST FY2026 gross margin 12.82% = (303,154 − 264,279) / 303,154;
  interest coverage 80.59 = 11,685 / 145; NVDA FY2026 operating margin 60.4%. [observation]
- **Transparent formulas** in `financetoolkit/ratios/*_model.py`: each ratio is a short pandas expression with a
  docstring, easy to port into Excel formulas for our own cells. Averages of opening and closing balances for
  return and turnover ratios (first year therefore blank). [observation]
- Clear logging when an input row is missing: "get_enterprise_value could not be calculated ... because the item
  'Preferred Stock' is missing ... Fill this row". [observation]

## Where it breaks

- **Valuation ratios misalign for non-December fiscal years.** Periods are calendar years (`Y-DEC`); NVDA's FY ending
  2023-01-29 was priced at the December-2023 close ($49.52), giving P/E 284x and EV/EBITDA 215x for "2023".
  [observation] Any company whose year does not end in December gets a price up to 11 months off. [inference]
- **WACC output is not usable as is:** COST 16.4%, 17.2%, 11.4%, **1.46%** (2023–2026); NVDA 33–46%, with warnings
  "no data found for ^TNX / ^FVX / ^TYX / ^IRX" and for the tickers over part of the window. [observation] It depends
  on Yahoo for the risk-free rate and beta; the cause of the 1.46% is [unknown].
- **Silent gaps from missing rows.** Without 'Preferred Stock' and 'Change in Working Capital' rows, EV, FCFF and the
  intrinsic valuation were skipped with an ERROR log line, and `get_intrinsic_valuation` raised a `TypeError (Index
  must be a MultiIndex)`, which the library itself labels "a defect rather than missing data". After filling the rows
  (CWC approximated as CFO − NI − D&A − SBC) it ran: COST $394/share, NVDA $65.8/share. [observation]
- **Garbage in, garbage out on the statements:** NVDA FY2026 marketable securities are tagged with a company
  extension and fell out of the us-gaap mapping, so its FY2026 quick ratio dropped to 1.53 from 3.67. The Toolkit has
  no balance check on supplied data. [observation]
- Default source with a key is Financial Modeling Prep (paid plans for full history); without a key it falls back to
  Yahoo Finance, whose terms limit use. Not tested with FMP. [observation for the code path; FMP behaviour unknown]
- Rounds to 4 decimals by default (`rounding=4`). [observation]

## What we take

1. **Ratio definitions, not the runtime.** Port the formulas we lack into Excel cells built by
   `app/engines/sheets/builder.py` (a ratios block on the model: ROIC with average invested capital, DSO/DIO/DPO,
   interest coverage, FCF conversion, DuPont), citing the FinanceToolkit function name in the cell comment. MIT permits
   copying; keep the attribution. [inference]
2. **A recompute oracle for tests.** Feed `spreading.Spread` (or the model's historical block) to FinanceToolkit in
   `app/tests/` and assert our workbook's ratio cells match within 1e-6 (the X7 "independent recompute" idea in
   STANDARD.md M21). [inference]
3. Do **not** use its WACC, prices or intrinsic value in deliverables: `app/engines/sheets/market.py` already builds
   WACC from the US Treasury XML and Damodaran with sources; FinanceToolkit's version is unsourced and misbehaved here.

## Licence note

MIT at the pinned commit. Data: FMP is a paid commercial API; the fallback is Yahoo Finance via `yfinance`
(Apache-2.0 library, but Yahoo's terms of use restrict the data; whether our use is permitted is [unknown]). With
our own SEC-sourced statements the statement data has no licence issue. Ledger row matches. [observation]
