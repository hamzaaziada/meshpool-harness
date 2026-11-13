---
name: finance-three-statement-model
description: Spread a company's filed financials (SEC EDGAR XBRL) and build an integrated income statement, balance sheet and cash flow driven by sourced operating drivers, with base/bull/bear cases, so a DCF and its assumptions can be stress-tested inside the statements. Use for three-statement models, financial spreading, operating models, integrated forecasts, LBO or DCF preparation.
---

# Three-statement model

A DCF is only as good as the statements behind it. Free cash flow should fall out of a forecast in which the
income statement, the balance sheet and the cash flow statement are linked, every driver has a source and a range,
and the balance sheet balances in every year. Changing one driver then moves revenue, margins, working capital,
capex, funding and value consistently.

## 1. Spread the history

- Pull the filed numbers, not a data vendor's restatement of them. For US filers the SEC's XBRL "company facts" API
  (`data.sec.gov/api/xbrl/companyfacts/CIK##########.json`) has every tagged figure with the filing it came from.
  Send a User-Agent naming the requester and stay within the SEC's fair-access rate.
- Map tags to a standard layout with an ordered fallback list per line (revenue may be `Revenues`,
  `RevenueFromContractWithCustomerExcludingAssessedTax`, ...). Record for each cell the tag, the accession number,
  the form and the filing date. Use annual figures from 10-K filings; when a later filing restates a year, use the
  latest value and say so.
- Never fill a gap with an estimate. A line no tag covers stays empty and is listed with the tags tried. A line the
  company simply does not have (no inventory, no minorities) is "not reported", which is different from "missing".
- Tie it out before forecasting: total assets = total liabilities + equity; operating + investing + financing + FX =
  change in cash; opening cash + change = closing cash; pre-tax income + tax = net income. A check that cannot run
  says why; it never passes by default.
- Compute the history of the ratios you will forecast: growth, gross margin, opex % of revenue, DSO / DIO / DPO,
  capex and D&A % of revenue, effective tax rate, payout.

Kit: `S.spread`, `S.history_metrics`, `S.write_spread` (a sourced Historicals tab with in-sheet checks),
`S.link_opening_balances` (the model's opening balance sheet as live links to the filing).

## 2. Drive revenue and cost of revenue from operations

Pick the driver the business is actually run on, and source the last actual value and the path:

| Business | Revenue | Cost of revenue |
|---|---|---|
| Manufacturing, devices | units x price | units x unit cost (materials, labour, freight) |
| Capacity businesses (data centres, plants, fleets, hotels) | capacity x utilisation x price per unit | fixed cost per unit of capacity + variable cost per unit sold |
| Subscriptions, software, telecoms | customers (or seats) x ARPU; churn and net adds if disclosed | gross margin by segment, or hosting / support cost per customer |
| Retail | stores x sales per store, or traffic x basket | merchandise margin |

Each driver gets: the last actual value (filing, page), the forecast path (guidance, contracts, industry forecasts,
peer evidence) and a bull and bear value at the ends of its evidenced range. Growth that fades toward a steady state
is a ramp from a start rate to an end rate, not a constant. Opex lines as % of revenue only after checking the
history supports it; split fixed and variable costs when operating leverage matters.

## 3. Balance sheet schedules

- **Working capital** from days: receivables = revenue x DSO / 365; inventory = cost of revenue x DIO / 365;
  payables = cost of revenue x DPO / 365. Anchor the days on history; explain any change.
- **PP&E roll-forward**: opening + additions - depreciation = closing. Additions = maintenance (% of revenue or of
  the asset base) + growth capex tied to the capacity driver (new units x cost per unit). Depreciation from useful
  lives: the opening base over its remaining life, each year's additions over the life of new assets. Never set
  depreciation as a % of revenue when the asset base is changing.
- **Debt schedule**: opening, draws, scheduled repayments, closing. Charge interest on the opening balance (or a
  prior-period average) so the model has no circular reference.
- **Revolver and minimum cash**: cash before the revolver = opening cash + operating + investing + term-debt flows +
  dividends + buybacks. The revolver draws only what restores minimum cash, repays when there is surplus, and is
  capped at its commitment; show any remaining shortfall rather than hiding it.
- **Equity roll-forward**: opening + net income - dividends - buybacks + issuance = closing.
- Lines without a driver (other assets and liabilities) are held flat and labelled so.

Then the balance sheet balances because every flow has two sides, not because of a plug. If it does not balance,
find the missing link; never add a balancing line.

## 4. Checks and scenarios

- Checks tab (all must be zero / OK): balance sheet balances in every year including the opening year; cash flow
  closing cash equals balance sheet cash; net income on the cash flow equals the income statement; revolver and PP&E
  never negative; scenario switch valid.
- Scenarios: one switch selects base / bull / bear for every judgement driver. Keep pinned copies of each case so the
  three can be compared side by side, with a check that the live model equals the pinned copy of the selected case.
- Stress-test inside the statements: a bear case that pushes the company onto its revolver should show interest,
  covenant headroom and any shortfall, not just a lower value.

## 5. Hand-off to valuation

Unlevered free cash flow = EBITDA - tax on EBIT (unlevered, none on losses) - capex - increase in working capital,
all read from the statements (`B.dcf_from_statements`). Net debt comes from the opening balance sheet. Then follow
`finance-dcf-valuation` and `finance-valuation-method-selection`.

## Sources (methodology)

- SEC, EDGAR application programming interfaces (XBRL frames, company concept and company facts APIs), and
  "Accessing EDGAR Data" (fair-access policy, User-Agent and rate limits), sec.gov.
- FASB, US GAAP Financial Reporting Taxonomy (element definitions used by the tag map).
- T. Koller, M. Goedhart, D. Wessels (McKinsey & Company), Valuation: Measuring and Managing the Value of Companies,
  Wiley: reorganising the statements, forecasting performance, invested capital.
- CFA Institute, CFA Program curriculum, Equity Valuation: Free Cash Flow Valuation (FCFF from forecast statements).
- J. Rosenbaum, J. Pearl, Investment Banking: Valuation, LBOs, M&A, and IPOs, Wiley: debt schedules, revolver
  mechanics and avoiding circular interest.
