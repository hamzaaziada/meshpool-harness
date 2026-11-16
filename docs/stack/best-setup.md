# Best setup for decks and models (measured November 2026)

The settings below are the defaults after this change. Each line says what was measured on the same brief (a
5-slide Costco capital-allocation board briefing, and the matching Excel model) with the partner and MD reviewers.

| Setting | Default | Why |
|---|---|---|
| Writer model | DeepSeek v4.1 Flash | Best value. GPT 6.1 terra (about 3x the cost per deck) and Kimi K3 for the storyline did not score higher in the review loop. |
| Reviewer model | DeepSeek v4.1 Flash | Cheap enough for many rounds. A stronger reviewer (GPT 6.1 terra) is a more reliable judge for spot checks: it graded the published Goldman Sachs board book 6.8 (partner) / 6.15 (MD) and our decks 4.8-5.15. |
| Partner and MD masterclass | on (`MH_MEMORY=1`) | Distilled from 360 of their reviews; every writer prompt reads it. New lessons are committed after each review (`engines/review/memory/`). |
| Decision playbooks | on when the brief matches | Capital allocation: dated sources-and-uses liquidity, authorization is a ceiling, buybacks valued against intrinsic value. With it the partner signed off a deck for the first time (8.2). |
| Storyboard rounds | 4 | The storyboard score rose from about 4.6 to 6.8-7.4 before any slide was written. |
| Deck review rounds | 3 | Best-scoring round is delivered. |
| Workbook reviews | median of 3 per round | One reviewer reply moves by up to 1.5 points on the same workbook. |
| Checks before the reviewers see a deck | figures agree across slides (cheap model, fixed in code where mechanical), sums in prose add up, no earnings-yield-vs-cash comparison, type scale, reading order, chart labels | Each was a recurring MD or partner top issue. |
| Research facts computed in code | free cash flow, cover, market cap, scaling rule, earnings yield (EPS test only), implied buyback return, CAPM cost of equity | The writer no longer has to derive the valuation test. |

## Building on a good deck

`FORGE_START_FROM=<a previous run's out folder>` reuses that run's facts, storyline and slides and runs only the
render, checks and partner/MD revise loop, so a deck that already scored well is improved instead of rebuilt.

## Results so far (raw, no calibration)

- Best deck: partner 8.2 (signed off) with MD 6.2; best single run with both at 7+: partner 7.4, MD 7.1.
- Best model: partner 7.75, MD 7.35.
- No deck or model has yet had both reviewers sign off (overall 8+, no dimension under 7) in the same run.
