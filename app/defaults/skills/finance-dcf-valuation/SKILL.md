---
name: finance-dcf-valuation
description: Decide whether a DCF fits and build one whose every input is tied to a driver and a source (WACC build-up, driver-based forecast, terminal value, sensitivities). Use for intrinsic valuation, DCF, discounted cash flow, fair value or "what is it worth on fundamentals".
---

# DCF valuation

## Does a DCF fit?

Yes when future cash flows can be tied to observable drivers (contracted backlog, capacity, pricing, unit
economics), even if they are negative today. Say so if near-term free cash flow is negative and value sits in the
terminal period: show the share of value from the terminal value. If cash flows cannot be tied to drivers, rely on
comps and precedents and say why.

## Forecast: driver-based, not growth-rate-based

Build revenue from the drivers the business reports or that industry data supports (e.g. for compute
infrastructure: active power (MW) x utilisation x revenue per MW, cross-checked against backlog conversion; for
software: customers x ARPU; for manufacturing: units x price). For each driver give the starting value (sourced), the
path (with the reason: guidance, contracts, industry forecast) and a range.

Then margins (gross, EBITDA) from the company's trend and mature peers; capex as a function of capacity added
(cost per MW from filings or industry data); working capital from history; tax at the statutory rate with any NOLs;
depreciation from the asset base and useful lives in the filings.

Use 5-10 explicit years: long enough to reach a steady state (growth near the terminal rate, margins near mature
peers).

## WACC: build it, cite each piece

- Risk-free: current 10-year government yield (date and source).
- Equity risk premium: a published estimate (e.g. Damodaran's implied ERP, or a bank's published estimate), cited.
- Beta: median of peers' unlevered betas, relevered at the target's target capital structure.
- Cost of debt: yield on the company's traded debt or recent issuance; else interest expense / average debt; after tax.
- Weights: target or peer-median capital structure, not just today's.

## Terminal value

Gordon growth with g at or below long-run nominal growth (state it), cross-checked against an exit multiple from the
comps; show both. Use the mid-year convention.

## Output

EV, equity value (EV - net debt - minorities - preferred) and value per share; the share of EV from the terminal
value; sensitivity tables (WACC x g; a key driver x margin). Every input lives on the Inputs tab with its source and
rationale (skill `finance-assumption-book`).
