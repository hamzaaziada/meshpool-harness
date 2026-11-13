---
name: finance-buy-side-deal-analysis
description: Build a buy-side acquisition analysis (board or investment committee deck plus the Excel model) the way an investment bank or corporate development team does. Use when the user asks whether a company should buy another, what to pay, how to fund it, or for an acquisition, merger, takeover, buy-side or IC deck.
---

# Buy-side deal analysis

The deliverable answers four questions, in this order: **should we buy it, what is it worth, what should we pay and
how, and what could go wrong**. Every number carries a source; every assumption carries a reason.

## 1. Research first (before any slide or cell)

Collect and cite, with dates:

1. **Target and acquirer filings**: latest 10-K/10-Q or annual report, latest earnings release and call transcript,
   investor presentation. Pull revenue, growth, margins, EBITDA, capex, debt (by instrument), cash, share count
   (basic and diluted), backlog or RPO, guidance.
2. **Market data on a stated date**: share prices, market caps, 52-week range, unaffected price (before any rumour or
   results that moved it), 30-day VWAP.
3. **Recent news (last 3-6 months)**: commercial agreements between the parties, existing stakes, financing rounds or
   debt raises, management changes, regulatory events, analyst rating changes. Existing relationships change the
   deal (a stake already held, a customer contract, a supply agreement).
4. **Industry evidence**: market size and growth from public market reports, capacity and pricing data, the
   competitive set. Public bank and broker research (e.g. Goldman Sachs, Morgan Stanley, JPMorgan insights pages),
   consulting reports and reputable newsletters are acceptable sources when they are public; quote the figure and
   link the page.
5. **Comparables**: trading peers (skill `finance-trading-comps`) and precedent transactions (skill
   `finance-precedent-transactions`).

Record each fact once, in an assumption book (skill `finance-assumption-book`): value, unit, as-of date, source link,
and how it is used. Never invent a number; if a figure is not public, write a gap and say what would close it.

## 2. The model (Excel)

Tabs: Cover, Inputs (sourced), Comps, Precedents, DCF (when it applies, skill `finance-dcf-valuation`),
Deal (offer price, premium, equity value, enterprise value, sources and uses), Financing (cash, stock, new debt;
foregone interest; new shares), Pro forma (accretion/dilution for year 1-2, ownership, leverage), Sensitivities
(premium x consideration mix; WACC x terminal growth), Football field (valuation ranges by method), Checks.

Rules: losses and costs are negative or subtracted (never a positive loss added); every calculation is a live
formula; inputs are blue and sourced; one master check.

## 3. The deck (10-14 slides for a board; 20-30 for an IC)

1. Title.
2. **Executive summary**: the recommendation, the price range, the headline economics, the top risks, the ask.
3. Situation and recent developments (news, existing relationship, why now).
4. Target overview: business, customers, KPIs, financial profile (table plus a growth chart).
5. Strategic rationale: what the acquirer gains that it cannot get otherwise; quantify.
6. Market context: market size and growth, competitive position (sourced industry data).
7. **Valuation**: trading comps (table with median and quartiles), precedent transactions, DCF summary,
   then a football field that brackets the offer.
8. Offer and premium: offer price vs unaffected, 30-day VWAP, 52-week high; premium vs precedent premiums.
9. Structure and financing: consideration mix, sources and uses, pro forma leverage and ratings view.
10. Accretion/dilution and returns: EPS impact, ROIC vs WACC by year, synergy phasing.
11. Risks and mitigants: antitrust, customer and channel conflict, integration, financing, key people.
12. Diligence plan and next steps (closing slide): decisions requested, owners, dates.
Appendix: detailed comps, precedents, DCF, assumption book.

Every valuation slide states the method, the date and the sample. Every chart has a source line.

## 4. Before you deliver

- Recompute the headline numbers outside the model (offer x shares, EV bridge, EPS math) and confirm they match.
- Check the deck's numbers against the model's Outputs tab.
- Run check_workbook and check_deck; fix every failure.
- Say plainly which inputs are illustrative assumptions and which are sourced facts.
