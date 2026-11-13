---
name: finance-valuation-method-selection
description: Decide which valuation methods fit a company (DCF, trading comps, precedent transactions, sum of the parts, NAV, dividend discount, excess return, LBO floor) and weight them explicitly on data quality, peer comparability, terminal-value dependence, stage and control vs minority context, with editable weights, a weighted value per share and a football field. Use whenever a valuation uses more than one method, or someone asks which method to use.
---

# Choosing and weighting valuation methods

Different methods answer different questions. A DCF says what the cash flows are worth if the forecast holds;
trading comps say what investors pay today for similar minority stakes; precedents say what buyers paid for control;
an LBO says what a financial sponsor could afford. Pick the ones that fit the company and the question, say why the
others do not, and weight the survivors with reasons a reviewer can challenge.

## Which methods fit

| Method | Use it when | Do not use it when |
|---|---|---|
| DCF (from integrated statements) | cash flows tie to observable drivers (volumes, capacity, prices, customers, contracts) | the forecast would rest on a growth-rate guess; banks and insurers (debt is an operating input, so FCFF and WACC lose meaning) |
| Trading comparables | at least 3, ideally 5-10, listed peers with similar business mix, growth and margins | peers are few or structurally different; use EV / revenue only when EBITDA is negative or not yet representative |
| Precedent transactions | at least 3 comparable deals, mostly recent | deals are old, in a different cycle or poorly disclosed; for a minority stake they are a cross-check only (control premium) |
| Sum of the parts | two or more segments with different growth, margins and peer multiples | one business line |
| NAV / asset-based | value sits in separable assets with observable values: REITs, resources, shipping, holding companies, financials (tangible book), distressed situations (recovery) | value comes from operating cash flows |
| Dividend discount | banks and insurers paying regular dividends; capital rules cap the payout | companies whose payout is a policy choice unrelated to capacity |
| Excess return (residual income) | banks and insurers: value = book equity + PV of (ROE - cost of equity) x book equity | non-financial companies, where book equity is not the operating capital |
| LBO | a sponsor could realistically buy it (stable cash flow, debt capacity, size) | financials; companies a sponsor could not lever. Show it as a floor in a sale; do not weight it |

Record the reason for each "no" as well as each "yes".

## How to weight

Score each method that fits on five factors (0 to 1) and weight by the normalised product. Each score carries a
written reason; the weights used are editable inputs, pre-filled with the rule-based result and rounded to 5%.

1. **Data quality**: how good are the inputs? DCF: drivers sourced vs judgement. Comps: number of peers (6 or more
   scores fully). Precedents: number of deals and recency (older than about five years scores lower).
2. **Comparability**: how alike are the peers or targets (business mix, growth, margins, size, geography)?
3. **Terminal-value dependence** (DCF): when the terminal value is more than about 60% of enterprise value, the
   answer mostly reflects the long-run growth or exit assumption; reduce the DCF weight as that share rises (to a
   floor, while the drivers stay sourced). This factor reads the model's terminal value share live.
4. **Stage**: early-stage companies lean on revenue multiples and scenarios; mature companies support every method;
   distressed companies lean on asset and recovery values.
5. **Context**: for control (a sale, a takeover) precedents and the LBO floor matter and trading prices need a
   control premium; for a minority stake precedents carry a premium the holder does not get.

Then:

- Show low, high and mid for each method in value per share, the weights, the weighted low / mid / high, and the
  largest gap between the weights used and the rule-based weights. Weights must sum to 100% (a check).
- If the methods disagree widely, explain why (different questions, stale peers, an aggressive forecast) before
  averaging. A weighted value inside a range nobody believes is not an answer.
- Finish with a football field: every method's range, the weighted range, and the current price or offer as a
  marker. Compare the weighted value with the LBO floor in a sale.

Kit: `B.select_methods(profile)` (the rules), `B.valuation_summary(ws, row, methods, profile, marker=...)` on a tab
whose name contains "Inputs" (the factor scores and weights are inputs).

## Sources (methodology)

- IFRS 13 Fair Value Measurement, paragraphs 61-66, and FASB ASC 820-10-35-24B: when several valuation techniques
  are used, evaluate the range of results and select the point most representative of value in the circumstances.
- International Valuation Standards Council, IVS 105 Valuation Approaches and Methods: choosing among market, income
  and cost approaches and reconciling several.
- AICPA, Accounting and Valuation Guide: Valuation of Portfolio Company Investments of Venture Capital and Private
  Equity Funds and Other Investment Companies: weighting and calibrating multiple techniques by company stage.
- A. Damodaran, Investment Valuation (Wiley) and "Valuing Financial Service Firms" (NYU Stern working paper):
  choosing among DCF, relative and asset-based valuation; dividend discount and excess return models for banks
  and insurers.
- CFA Institute, CFA Program curriculum, Equity Valuation: Discounted Dividend Valuation, Residual Income Valuation,
  Market-Based Valuation.
- T. Koller, M. Goedhart, D. Wessels (McKinsey & Company), Valuation, Wiley: continuing value and its share of
  total value; multiples as a cross-check.
- J. Rosenbaum, J. Pearl, Investment Banking, Wiley: trading comps, precedents, LBO analysis and the football field.
