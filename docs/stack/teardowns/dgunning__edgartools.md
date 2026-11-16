# Teardown: dgunning/edgartools

Commit `574c2bd15f6c629bceef03b6550ea8ac8abf3473` (pinned clone), package `edgartools 6.2.0` (import name `edgar`,
Python >= 3.11). MIT (`LICENSE.txt`).
Labels: [observation] seen in a test run or in the code; [inference] my reading; [unknown] not established.

## Tested setup

- Isolated venv (Python 3.12) with edgartools and FinanceToolkit installed from the pinned clones;
  `EDGAR_LOCAL_DATA_DIR` set to the scratchpad; identity `set_identity("Teardown Research research-bot@example.com")`
  (a generic contact, not the project's). Scripts: scratchpad `td/edgar/e1.py`, `e2.py`, `e3.py`. [observation]
- Pulled for COST and NVDA: annual income statement, balance sheet and cash flow for 4 fiscal years
  (`Company(t).income_statement(periods=4)` etc., built from SEC companyfacts), the latest 10-K (`latest("10-K").obj()`:
  item list and Item 7 / 1A text), the 10-K's own XBRL statements (`filing.xbrl().statements`), and the latest 8-K
  with Item 2.02 and its EX-99.1 text. [observation]
- Compared every value our `app/engines/sheets/spreading.py` spreads (`S.spread(facts, years=4)`, imported read-only
  from the harness) with edgartools' value for the **same us-gaap tag and fiscal year**. [observation]
- Non-US: TSM, SAP, BABA (20-F), CNQ (40-F), and CLHO.CA (Cleopatra Hospitals, Egyptian Exchange). [observation]

## What it does well

- **Same numbers as our spreading where both have the tag.** COST: 146 values agree; NVDA: 144 agree (exact to
  $0.5m). The "disagreements" below are explained, not data errors on either side. [observation]
- **Filings, not just facts.** For both companies the latest 10-K parses into 23 items (Item 1 ... Item 16); Item 7
  text 28.8k / 34.2k characters, Item 1A 44k / 115k. Latest Item 2.02 8-Ks found and their press releases extracted
  as text: COST `0000909832-26-000084` (2026-09-24), EX-99.1 18,167 chars; NVDA `0001045810-26-000073` (2026-08-26),
  EX-99.1 46,806 chars, plus the CFO commentary as EX-99.2. 0.5 s per 8-K, 2–3 s per 10-K. [observation]
- **The filing's own XBRL statements keep the company's presentation and extension tags.** NVDA's FY2026 10-K balance
  sheet shows `nvda_MarketableSecuritiesAndEquitySecuritiesFVNI` = $51,951m ("Marketable securities"). Our spreading
  (us-gaap companyfacts only) does not see it and puts it in its derived "Other current assets" line (55,131m, of
  which 51,951m are marketable securities), which understates net cash in an EV bridge by ~$52bn. [observation]
- **Foreign private issuers work through the filing XBRL.** TSM's latest 20-F (filed 2026-04-16) gives an IFRS income
  statement in TWD for FY2025 (revenue TWD 3,809bn); SAP and BABA 20-F and CNQ 40-F statements parse too. So
  edgartools does handle non-US filers **that file with the SEC**; it does not handle companies outside EDGAR:
  `Company("CLHO.CA")` and `Company("CLHO")` raise `CompanyNotFoundError`. [observation] (The brief expected no
  non-US support; that is only true for non-SEC registrants.)
- **SEC etiquette built in.** No key. Without an identity any request raises `IdentityNotSetError` (ticker-to-CIK
  works offline from bundled data). A shared limiter (`pyrate_limiter`) keeps to 9 requests/s by default
  (`EDGAR_RATE_LIMIT_PER_SEC`; SEC's fair-access limit is 10/s); responses are cached on disk (17 MB after this
  session). Whole COST + NVDA statement pull: 6.3 s wall including import. [observation]
- Also present, not tested here: full-text search, quarterly / TTM statements, 13F, Form 4, N-PORT, an MCP server.
  [observation from README]

## Where it breaks

- **The standardized multi-period statements drop lines.** Present in companyfacts and in our spread, absent from
  edgartools' statements: NVDA `MarketableSecuritiesCurrent` (9,907 / 18,704 / 34,621m for FY2023–25), NVDA capex
  (`PaymentsToAcquireProductiveAssets`, all four years), NVDA dividends (`PaymentsOfDividends`), COST
  `ReceivablesNetCurrent` (all four years), COST total equity incl. NCI, COST FY2026 revenue under
  `RevenueFromContractWithCustomerExcludingAssessedTax` (shown under `Revenues` instead). The NVDA balance sheet's
  current-asset lines then do not add up to total current assets. [observation]
- **Split-unadjusted history mixed with adjusted.** NVDA FY2023–24 diluted shares 2,507m / 2,494m and EPS 1.74 /
  11.93 in edgartools versus 25,070m / 24,940m and 0.17 / 1.19 in our spread (latest filed, split-adjusted, matching
  FY2025–26). [observation] A per-share series from edgartools would jump 10x at the 2024 split. [inference]
- **Foreign filers through companyfacts are unsafe.** TSM's multi-period income statement ends at FY2024 although a
  FY2025 20-F exists, and mixes units in one column: revenue 88.27bn and EPS 1.36 are the USD convenience translation,
  current tax 275.7bn is TWD (companyfacts has both `TWD` and `USD` units for revenue). [observation]
- No restatement flag or accession per value in the DataFrame output (columns: label, depth, is_abstract, is_total,
  section, confidence, periods); concepts are on the `MultiPeriodItem` objects only. [observation] Our spreading keeps
  accession, form, filing date and a `restated` flag per cell. [observation]
- Labels in the cash flow statement include stray items (Goodwill, Dividends payable under "Additional Financial
  Items"). [observation]

## What we take

1. **Filings and text, now.** `app/engines/research/library.py` already has EDGAR full-text search; add an
   `edgar_filing(ticker, form)` reader on edgartools for 10-K item text (Item 1, 1A, 7, 7A) and 8-K Item 2.02 EX-99
   press releases, passing passage ids into the corpus. Earnings releases are listed in STANDARD.md section 5 as a
   free source but there is no fetcher for them today. [observation]
2. **Plug-line audit with the filing XBRL.** In `app/engines/sheets/spreading.py:spread`, when a derived "other ..."
   line exceeds ~10% of its total (NVDA FY2026: 55,131m of 125,605m), read the latest 10-K's presentation statement via
   `filing.xbrl().statements.balance_sheet()` and report the extension-tagged lines behind it (with labels) as a gap,
   or map known extensions (marketable securities, short-term investments) into the right line.
3. **20-F / 40-F spreading.** `spreading.py` refuses IFRS today ("spread them from the 20-F instead"). Use
   `filing.xbrl()` on the 20-F for IFRS filers, pick the reporting-currency unit explicitly and never mix units in a
   column; never use companyfacts for them.
4. Keep our own companyfacts spreading as the source of record for US filers (it keeps provenance per cell and
   split-adjusts by taking the latest filed value). Do not switch to edgartools' standardized statements.

## Licence note

MIT at the pinned commit (`LICENSE.txt`). Data is SEC public data; SEC asks for a declared User-Agent with contact
details and at most 10 requests/s. Ledger row matches. [observation]
