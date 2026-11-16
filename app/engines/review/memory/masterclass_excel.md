# The standard for decision-ready models

## How we read your work

We start with the first page. We want to know the decision, your answer, the binding constraint and what would change your answer. We then look at the base and downside results, the cash bridge and the master check. Only then do we follow the drivers into the model and the sources behind them.

We test the number most likely to change the decision: often the cash floor, payout policy, operating margin or investment plan. We change an input and expect the calculations, scenarios and outputs to move together. We compare the sensitivity grid’s centre with the base case. We look for the year of minimum headroom, not just cash at the end of the forecast.

We stop reading when the workbook has no live calculation chain, no assumption book or no meaningful checks. We also stop when the grid tells a different story from the model, a material input is presented as fact without support, or the output claims to answer a capital-allocation question while showing only a liquidity test.

A polished page cannot rescue an untrustworthy model. An accurate model does not excuse a page that leaves the decision to the reader.

## Structure and flow

- **Put the decision and answer on the first page.** Follow them with the binding constraint, a short financial bridge and comparable cases.  
  *Wrong:* A cover with only “Capital Allocation Model.”  
  *Right:* “Approve the plan subject to $XB minimum headroom; the downside requires a buyback suspension.”

- **Separate inputs, calculations, outputs and checks.** Give each a distinct tab; link the first-page status to the checks, not to a typed “OK.”  
  *Wrong:* Three nearly empty tabs with constants in Outputs.  
  *Right:* Cover, Inputs, Model, Outputs and Checks, connected by live formulas.

- **Build one readable driver chain and one cash engine.** The base, downside and sensitivities should use the same flow definitions and forecast periods.  
  *Wrong:* A multi-year base model beside a one-period sensitivity proxy.  
  *Right:* Revenue → operating cash flow → free cash flow → returns → closing cash, recalculated for each case.

- **Make the timeline agree everywhere.** Check the title, year columns, summary ranges and sensitivity horizon against the stated forecast.  
  *Wrong:* A forecast title promising X years while totals cover fewer.  
  *Right:* Every forecast year appears in the model and is included in the linked totals.

## Inputs and sourcing

- **Give every input a name, value, unit, source and basis; give uncertain drivers a range.** Use dated URLs and filing or page references where available. A fixed historical fact can have a fixed range; a forecast needs a rationale and a testable range.  
  *Wrong:* Revenue growth: X%, source “filing.”  
  *Right:* Revenue growth: X%, range X%–X%; historical filing reference, URL and explanation of the forward assumption.

- **Distinguish evidence from judgment.** Prefer primary sources for declared dividends, authorizations and company guidance. If a policy is not disclosed, say so and label your estimate as an estimate.  
  *Wrong:* “Board cash floor: $XB,” sourced to “assumption.”  
  *Right:* “Analyst cash-floor assumption: $XB, based on X months of operating needs plus a buffer”; test alternative floors.

- **Match the source period and instrument to the use.** Date market quotes, distinguish prior-year guidance from forecast-year spending, and use a cash-yield benchmark appropriate to the cash duration.  
  *Wrong:* A prior-year capex guide used as next year’s plan without explanation.  
  *Right:* Prior-year guidance identified as the starting point, with a bridge to forecast-year capex.

- **State capital-return policy rather than inferring it from an authorization.** Show amounts, duration, priority and whether reauthorization is assumed.  
  *Wrong:* An authorization of $XB becomes a recurring $XB annual buyback.  
  *Right:* A stated annual buyback assumption, reconciled to the remaining authorization and its expiry.

## Formulas and integrity

- **Use live formulas, named inputs and a consistent sign convention.** Keep editable assumptions in the designated input cells; do not bury commercial assumptions in formulas or plug the closing balance.  
  *Wrong:* Closing cash typed as $XB, or capex entered as an unexplained positive outflow.  
  *Right:* Opening cash + free cash flow + interest − distributions = closing cash, with outflows negative in the model.

- **Make timing conventions explicit and consistent.** Reflect a start-of-year special dividend before calculating the cash that earns interest; state whether dividends use opening or average shares. Use a non-circular approximation or a controlled, tested solution.  
  *Wrong:* Interest labelled “average cash” but calculated on opening cash before a large immediate payout.  
  *Right:* Interest calculated on documented average non-interest cash balances, with payout timing and approximation disclosed.

- **Check every material roll-forward and aggregate all checks.** Include cash, shares, outputs to calculations and grid centre to the comparable base result; test ranges and scenario logic. Show differences and tolerances, and make the master flag fail if any required check fails.  
  *Wrong:* A manually typed green “OK.”  
  *Right:* A live master status linked to named checks, including closing cash less its flows.

- **Make formulas robust across the timeline.** Check row patterns, links, error cells and division guards; investigate exceptions rather than hiding them.  
  *Wrong:* A coverage ratio returning `#DIV/0!`, or a summary omitting the final year.  
  *Right:* A labelled “NM” for a zero denominator and a total covering every forecast column.

## Scenarios and sensitivities

- **Model the choices the decision-maker controls.** Put relevant payout, buyback and reinvestment alternatives side by side, with cash, headroom, shares and returns on the same basis.  
  *Wrong:* Only growth and capex sensitivities for a decision about distributions.  
  *Right:* Current plan, reduced distribution and retained-cash cases, each run through the same engine.

- **Build downside from named drivers and test policy mechanics.** Separate demand and cost shocks where useful; show a combined severe case, its basis and the actions taken. Do not force a breach merely to make the case dramatic.  
  *Wrong:* A generic haircut to closing cash, or a mild case that never tests the payout rules.  
  *Right:* Sourced growth, margin and capex stresses, plus an explicit test case in which the relevant suspension rule fires.

- **Show the policy order and its consequences.** Test each discretionary payment including the proposed payment itself; recalculate case-specific interest and shares. Call a payment deferred only if it is subsequently paid.  
  *Wrong:* “Special dividend deferred” when it is never paid in the forecast.  
  *Right:* “Special dividend not paid in the forecast,” or a dated later payment with its cash impact.

- **Run sensitivities through the actual model.** Label both axes, units, periods and output definition; highlight the centre and tie it to the comparable base result. Show the grid, its low and high, and a break-even where decision-relevant.  
  *Wrong:* A one-period formula labelled as forecast closing cash.  
  *Right:* Full-horizon model reruns; centre equals base closing cash, or a labelled reconciliation explains any difference.

## Outputs and presentation

- **Write a claim, not a label.** Lead with the recommendation or conclusion, then show the decision numbers in a compact table. Keep a linked answer readable; do not hide the page in a long concatenated cell.  
  *Wrong:* “Answer,” followed by a truncated paragraph.  
  *Right:* “The plan is fundable, but downside headroom falls to $XB in Year X.”

- **Label every quantity with its unit and period.** Use “Yes/No” for a verdict, a year for timing and $XB, X% or X shares for quantities; do not attach a financial unit to a year or a verdict.  
  *Wrong:* “Closing shares: X” and “Lowest headroom: $XB.”  
  *Right:* “Closing shares: X million, Year X” and “Lowest headroom: $XB, Year X.”

- **Show the bridge and the pinch point.** Reconcile opening cash, free cash flow, interest and each use to closing cash. Show minimum headroom and its year separately from final-year cash.  
  *Wrong:* “Funded: Yes” alongside a distant closing balance.  
  *Right:* “Minimum headroom $XB in Year X; closing cash $XB in the final year,” with the bridge visible.

- **Tell the reader what changes the answer.** State the threshold, breach or policy response beside the scenario; distinguish a narrow cushion from comfortable funding.  
  *Wrong:* “Downside funded: Yes,” with no context.  
  *Right:* “Downside funded by $XB above the floor; a higher floor or lower margin changes the verdict.”

## Banker conventions

- **Use a clear input convention and readable formatting.** Identify blue cells as editable inputs, make labels and axes fully visible, and clear unit and formula warnings before delivery.  
  *Wrong:* Editable assumptions mixed with black formula cells and truncated grid labels.  
  *Right:* Blue inputs in one assumption book; readable, labelled outputs and a clean validation report.

- **Use economic definitions precisely.** State whether cash cover uses opening or closing cash, whether free cash flow includes the relevant lease payments, and whether figures are cumulative or annual.  
  *Wrong:* Closing cash divided by annual capex labelled simply “cash cover.”  
  *Right:* “Closing cash / annual capex (x),” with the accumulation effect disclosed.

- **Do not present a cash model as a valuation.** If the brief requires a value recommendation, build and source the cost of capital, valuation cash flows, terminal value, enterprise-to-equity bridge and per-share result; cross-check the valuation. Otherwise say the work addresses funding capacity only.  
  *Wrong:* “The buyback creates value” based solely on a positive cash balance.  
  *Right:* A value comparison against a no-buyback case, or a clearly limited liquidity conclusion.

- **Show share and price assumptions.** Tie share retirements to buyback dollars and an explicit price path or sensitivity; use a consistent diluted-share basis for per-share value.  
  *Wrong:* The same shares retired every year at an undated spot price.  
  *Right:* A dated starting quote, stated price convention and sensitivity for shares retired.

## The mistakes you keep making

The ordering below is a practical reading of the recurring issues in our reviews, not a statistical count.

1. **The grid is not the model.** Replace the shortcut with full-horizon reruns; reconcile the centre and check the low and high against the base where appropriate.
2. **The cash floor has no defensible basis.** Cite policy if disclosed; otherwise build an operating rationale, label it as judgment and test a range.
3. **The first page reports numbers but not the decision.** State the action, constraint, downside and what changes the answer; put the bridge immediately below.
4. **The downside is too soft, unexplained or irrelevant.** Anchor shocks to evidence and decision risks; separate cases and test the mechanism without inventing certainty.
5. **Forecast assumptions borrow credibility from historical sources.** Explain the bridge from history or guidance to forecast, and give each material judgment a range.
6. **The buyback authorization is treated as an annual plan.** Show the proposed pace, remaining authority, expiry and any assumed reauthorization.
7. **Sensitivity axes miss the decision levers.** Test margin, floor and payout choices when they drive the answer, not just convenient growth and capex inputs.
8. **Interest timing and labels disagree.** State the convention, account for immediate outflows, avoid uncontrolled circularity and test the result under stress.
9. **A “Yes” hides a thin cushion or an earlier breach.** Show minimum headroom, its year, the floor and the consequence beside the verdict.
10. **Special dividends and buybacks lack policy logic.** Identify committed versus proposed actions, payment timing and priority; quantify any suspension or unpaid amount.
11. **Units, dates and labels are missing.** Fix every warning and make each output’s definition and period visible on the page.
12. **The share count assumes an unchanging price without disclosure.** Model a price path or show the effect of alternative prices on retirement and closing shares.
13. **The model stops at liquidity but implies a value recommendation.** Add the valuation and return comparison if required; otherwise narrow the claim.
14. **Checks are absent, incomplete or cosmetic.** Build independent live checks, aggregate every required status and run the full validation suite.

## What an 8 looks like

The first page answers the actual question in plain English. It shows the base and downside on the same definitions, a cash bridge that ties, the minimum cushion and its year, and the decision levers that could change the result. The sensitivity is readable, runs the full model and reconciles at its centre.

Behind it is a clean driver chain, a sourced and ranged assumption book, explicit payout and timing conventions, and a live checks tab with an aggregate status. A reviewer can change a blue input, follow the calculation and see the answer update. The page can stand without a voice-over; the workbook can withstand a hostile question.

## Before you send it

Answer **yes** to each question:

- [ ] Does the first page state the decision and your answer?
- [ ] Are the binding constraint, minimum headroom and its year visible?
- [ ] Does the cash bridge reconcile opening to closing cash, including interest and all uses?
- [ ] Are base, downside and relevant policy alternatives presented on the same basis?
- [ ] Are all figures labelled with the appropriate unit and period?
- [ ] Does every material input have a named source, date or period, and rationale?
- [ ] Are uncertain inputs ranged, with base values inside their stated ranges?
- [ ] Are the cash floor and capital-return assumptions defensible and separately identified as policy or judgment?
- [ ] Do live formulas connect inputs, calculations, scenarios and outputs?
- [ ] Do forecast columns and summary formulas cover the stated horizon?
- [ ] Are cash, share and payment timing conventions consistent and disclosed?
- [ ] Does each downside use case-specific flows and correctly apply the stated payout priorities?
- [ ] Does the sensitivity centre tie to the comparable base model, with axes and horizon labelled?
- [ ] Are thresholds, breaches and any suspensions or unpaid distributions quantified?
- [ ] Do all required checks pass, with a live master flag and no unexplained errors or warnings?
- [ ] Is the conclusion limited to funding capacity unless the required valuation work has been done?
