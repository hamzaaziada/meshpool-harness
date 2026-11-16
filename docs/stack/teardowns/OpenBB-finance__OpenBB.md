# Teardown: OpenBB-finance/OpenBB

Commit `fac2bd450fac5e0b69ca33f11c931f8b1b0b79fa` (pinned clone; the monorepo is Apache-2.0). Tested from PyPI, not
built from the clone: `openbb 5.0.0`, `openbb-core 2.0.1`, routers `openbb-equity / index / currency / fixedincome /
news 2.0.0`, providers `openbb-yfinance 2.1.0`, `openbb-sec 2.0.0`, `openbb-federal-reserve 2.0.0`, `yfinance 1.8.0`.
Labels: [observation] seen in a test run or in the code; [inference] my reading; [unknown] not established.

## Tested setup

- Two isolated venvs (Python 3.12) under the scratchpad, `HOME` pointed at scratch folders (OpenBB writes user
  settings there): (1) the `openbb` meta-package; (2) a lean set: core + the five routers + three providers above.
  No API keys. Script: scratchpad `td/obb/o1.py`. [observation]
- Calls: NVDA price history, quote, annual income (yfinance); CLHO.CA (Cleopatra Hospitals, Egyptian Exchange)
  history, quote, profile, income, balance, cash flow, key metrics, news; NVDA news; COST income and NVDA filings via
  the SEC provider; US Treasury curve via the Federal Reserve provider; USD/EGP; the EGX30 index. [observation]

## What it does well

- **No-key data that worked:** NVDA daily history (260 rows, 2.7 s), quote, annual income statement; CLHO.CA daily
  history (252 rows), quote (currency EGP), profile (market cap EGP 9.58bn, 1.449bn shares), annual income (4 years;
  FY2025 revenue EGP 7,227m), balance sheet (5), cash flow (5); NVDA company news (10 items with URL, source,
  summary); USD/EGP daily (33 rows); Treasury curve 1M–30Y (Federal Reserve H.15). [observation]
- **SEC provider agrees with SEC.** COST FY2026 income via `provider="sec"`: revenue 303,154m, cost of revenue
  264,279m, period end 2026-08-30, the same as our `spreading.py` and edgartools. NVDA filings list with 8-K items and
  accession numbers. [observation]
- One normalised interface (`obb.equity.price.historical(..., provider=...)`) over many sources, returning pydantic
  objects with `.to_dataframe()`; an MCP server and REST API come with it. [observation]
- Lean install imports in 5.6 s once built. [observation]

## Where it breaks

- **`pip install openbb` 5.0 has no equity router.** `obb.equity`, `obb.index`, `obb.currency`, `obb.fixedincome` do
  not exist; they are the `openbb[routers]` extra (or `openbb-equity` etc.). Only `obb.news` worked in the meta install.
  [observation]
- **First import builds the package in place** ("Extensions to add: ... Building..."): 71 s with the meta-package,
  writing into the venv; the `obb` object of that first process was unusable (`'App' object has no attribute ...`)
  until a restart. [observation]
- **Thin coverage for Egypt:** CLHO.CA key metrics empty, CLHO.CA news empty, `^CASE30` (EGX30) not found; Yahoo
  labels CLHO.CA `asset_type: MUTUALFUND`. Statement depth is Yahoo's: 4–5 years, Yahoo's own line names, no filing
  reference. [observation]
- **Dates normalised by Yahoo:** NVDA's fiscal year end shows as 2026-01-31 (SEC: 2026-01-25). [observation]
- Values from Yahoo carry no source document; for a cited model each number still needs a primary source.
  [inference]

## What we take

1. **Non-US market data for emerging-market models, behind a licence decision.** For tickers outside EDGAR (EGX,
   etc.) the only no-key route that worked is Yahoo. Call `yfinance` (Apache-2.0) directly from a small
   `app/engines/sheets/market.py:yahoo_history(symbol)` / `yahoo_statements(symbol)` rather than through
   `openbb-yfinance` (AGPL, see below), mark every such cell "Yahoo Finance, unaudited, as of <date>" and require
   a primary-source check for headline figures (STANDARD R5). Whether Yahoo's terms allow use in a paid product is
   [unknown]; get that answered before shipping.
2. **Federal Reserve H.15 curve** as a second source to `market.py:treasury_yield` (US Treasury XML): the
   `openbb-federal-reserve` provider is Apache-2.0, or call the Fed data download directly. [inference]
3. **FX for currency translation** of foreign spreads (USD/EGP worked), feeding the currency check that
   `anthropics/financial-services` lists and we lack (see summary). [inference]
4. Not taken: the OpenBB platform as a dependency. We use 3–4 endpoints; the platform brings 135 packages (lean
   install), an in-place build step and per-provider licences to track.

## Licence note

- Monorepo at the pinned commit: Apache-2.0, and every provider and router present in it declares Apache-2.0.
  [observation]
- **`openbb-yfinance 2.1.0` is `AGPL-3.0-only`** (its PyPI METADATA), authored and published from a separate
  repository (`github.com/deeleeramone/openbb-danglewood`); there is no `providers/yfinance` in the pinned monorepo.
  It is the provider that served every no-key price and non-US fundamentals call above. Using it in a network
  service would bring AGPL obligations; keep it out of the harness. [observation; legal consequence is inference]
- Used and Apache-2.0 / permissive (METADATA): `openbb-core`, `openbb-equity`, `openbb-index`, `openbb-currency`,
  `openbb-fixedincome`, `openbb-news`, `openbb-sec`, `openbb-federal-reserve`, `openbb-charting`,
  `openbb-platform-api`, `openbb-technical` (Apache-2.0); `yfinance` (Apache-2.0); `curl_cffi` (MIT); `pandas`
  (BSD-3-Clause); `fastapi` (MIT); `uvicorn` (BSD-3-Clause). [observation]
- Data terms are separate from code licences: Yahoo Finance terms [unknown for our use]; SEC and Federal Reserve
  data are public. The ledger row ("Apache-2.0 at this commit; confirm per-component licences before use") should be
  updated with the openbb-yfinance AGPL finding. [observation]
