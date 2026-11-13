---
name: finance-assumption-book
description: Keep every model assumption fact-based: value, unit, as-of date, source link, the evidence it rests on (industry data, filings, guidance, peers), why it was chosen and its low/high range. Use whenever a financial model, valuation, forecast or deal analysis needs assumptions.
---

# Assumption book

An assumption is only as good as the evidence behind it. Each row on the model's Inputs tab carries:

| Field | What goes in it |
|---|---|
| Value and unit | the number used in the base case |
| As of | the date the value refers to |
| Source | link or document and page (filing, earnings release, transcript, market report, bank research, data provider) |
| Evidence | the fact(s) the value rests on, e.g. "Q3 2026 revenue $3.66B, +168% y/y; FY26 guidance $13.0-13.3B" |
| Rationale | why this value: guidance midpoint, peer median, industry forecast, contract terms |
| Low / High | the range used in sensitivities, with the reason for each end |
| Type | sourced fact, derived (formula from facts) or judgement (state who would confirm it) |

Rules:

- Prefer, in order: company disclosures and contracts > guidance > consensus > industry reports and public bank
  research > peer medians > judgement. Say which tier each assumption uses.
- Tie each forecast assumption to a driver and to industry evidence (market growth, capacity additions, pricing
  trends, peer margins). Do not use a generic growth rate when a driver exists.
- Mark illustrative deal terms (premium, consideration mix, synergies) as such until they are negotiated.
- A judgement assumption gets a sensitivity: show what the answer is at its low and high.
- When two sources disagree (e.g. share counts), say so, pick one with a reason and show the effect of the other.
