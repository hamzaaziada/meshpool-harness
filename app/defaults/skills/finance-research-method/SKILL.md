---
name: finance-research-method
description: Research a company, industry or market the way a senior banking or consulting analyst does - start from existing reports (equity research, industry reports, investor presentations found with filetype:pdf / report.pdf searches), verify every figure against filings and other independent sources, and work out what actually drives the market, revenue, costs and cash. Use for company and industry research, market sizing, due diligence, primers, deal background and any sourced brief.
---

# Research method

The goal is the answer a managing director would forward without edits: the few things that matter, each backed by
a source a reader can open, with the figures checked.

## 1. Frame before searching

- Write the decision the research serves and the question in one sentence.
- Break it into 3-6 sub-questions that do not overlap and together answer it (an issue tree). Put a hypothesis next
  to each. Keep a "one-day answer": your best answer right now, updated as evidence comes in.

## 2. Start from what others already wrote

Someone has usually written a report on the company or market already. These reports are the fastest route to the
structure of the problem: which drivers matter, how the market and revenue grow, how costs behave, what the bull and
bear debates are.

- Search for them directly: `<company> equity research report filetype:pdf`, `<company> initiating coverage pdf`,
  `<company> report.pdf`, `<company> investor presentation pdf`, `<industry> market report filetype:pdf`,
  `<industry> outlook 2026 pdf`. Banks, consultancies, trade bodies and the company itself publish many as PDFs.
- Read them for structure and hypotheses, not for answers. Do not copy their framing, and do not reuse a figure
  until you have checked it.
- Note where reports disagree: the disagreement usually marks the driver that matters.

Kit: `c.find_reports("<company or industry>")`.

## 3. Verify against primary sources

- The order is filings (10-K, 10-Q, 8-K earnings releases, proxies, S-1; EDGAR full-text search finds the exact
  passage), then issuer releases and investor-relations material, then regulators and statistics agencies, then
  reputable press and research, then everything else.
- For each figure you rely on, check:
  - period: fiscal vs calendar year, quarter, LTM;
  - scope: company vs segment, GAAP vs adjusted;
  - unit and currency;
  - date: is it still current?
- Every headline figure needs a primary or reputable source, or two independent ones. Syndicated copies of one
  article are one source. A number only one secondary source states is a hypothesis; say so.
- When sources conflict, show the range, pick one, and say why: primacy, recency, definition.

Kit: `c.edgar_search`, `c.add_edgar`, `c.corroborate("S4.p2")`, `c.numbers("<metric>")`.

## 4. Find what matters

Build the driver tree and test it with evidence.

- **Market:** who buys, how many, how much, at what price, and why. Look at growth by volume vs price, and at the
  constraints: capacity, regulation, supply.
- **Revenue:** the company's own build, e.g. segments x units x price, customers x ARPU, capacity x utilisation x
  price, stores x sales per store. Then its share of the market, and what moves that share.
- **Costs:**
  - fixed vs variable;
  - unit costs and their trend;
  - operating leverage;
  - capex intensity and maintenance vs growth capex;
  - working capital needs.
- **Cash:** how much of the profit turns into cash, and why. For lenders, the cash available for debt service.
- **So what:** say which 2-3 drivers explain most of the value or most of the change. Leave the rest out of the
  headline.

Market sizing is always two independent estimates: top-down (market x share) and bottom-up (units x price). Report
the gap between them and explain it.

## 5. Write it top-down

- **Answer first:** the governing thought in 2-4 sentences, then 3-5 findings, each a full-sentence takeaway with
  numbers.
- **One section per sub-question.** Each paragraph opens with its takeaway.
- **"What drives it":** the driver tree and the drivers that matter.
- **"Conflicts and open questions":** what you could not verify, and what to ask management or an expert.
- **Citations:** cite every fact at passage level. Write numbers as the source states them, or show the arithmetic.
- **Dates:** give the period of every figure.

## Sources (methodology)

- C. Conn, R. McLean, *Bulletproof Problem Solving* (Wiley, 2019): issue trees, hypotheses, the one-day answer.
- B. Minto, *The Pyramid Principle*: answer first, grouped and ordered support.
- A. Damodaran, *Narrative and Numbers* (Columbia Business School Publishing, 2017): tie the story to the drivers
  and test whether it is plausible.
- J. Valentine, *Best Practices for Equity Research Analysts* (McGraw Hill, 2011): forecasting from drivers,
  finding information edges.
- SEC, EDGAR full-text search and XBRL APIs (sec.gov): primary-source retrieval.
