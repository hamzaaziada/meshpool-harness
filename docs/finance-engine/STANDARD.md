# Meshpool finance engine: the standard

The target is the work product of a 2–4 year investment-banking analyst / associate (models, valuation, pitch books)
and of a McKinsey business analyst / associate (problem solving, research, decks), with the research depth of
Hebbia / AlphaSense. This file sets the rules the engines follow, says where each rule is enforced today, and lists
what is still missing, in priority order.

It was compiled in November 2026 from public practitioner sources, read in full where free: FAST Standard 02c, the
ICAEW Financial Modelling Code and "How to review a spreadsheet", the CFI Financial Modeling Guidelines (94 pp.),
plus many articles from Mergers & Inquisitions, Breaking Into Wall Street, Wall Street Prep, Macabacus, CFI and
Damodaran. Wall Street Oasis pages refuse automated reading (HTTP 403), so WSO points come from search excerpts
only. The books listed at the end are copyrighted and were not read in full; what they contribute is taken from
publisher descriptions, tables of contents and excerpts. Neither Goldman Sachs nor McKinsey publishes an internal
style guide, so everything here is public convention, not firm policy.

Status key: **Enforced** (a deterministic check refuses the output), **Built** (a builder produces it, nothing
refuses its absence), **Gap** (not built).

---

## 1. Models (sheets engine)

| # | Rule | Source | Status |
|---|---|---|---|
| M1 | Inputs, calculations and outputs are separated; a cover page carries a tab map and the check summary | ICAEW; FAST 1.01; CFI | Enforced (`banker_workbook`, finish_check needs a Cover tab) |
| M2 | Inputs are blue on a fill; formulas black; cross-sheet links green | WSP; CFI; ICAEW | Enforced (forge X10) |
| M3 | No typed number in a calculation; only historicals and inputs are hardcoded; no constants inside formulas | CFI; FAST 3.04; BIWS | Enforced (QA hardcode scan, X1/X2) |
| M4 | One formula per row across forecast periods | WSP; FAST 3.02 | Enforced (X8) |
| M5 | Every input has a source, evidence and a range | assumption-book practice | Enforced (`missing_sources`) |
| M6 | Revenue and cost of revenue come from operating drivers (volume × price, capacity × utilisation × price, customers × ARPU, stores × sales per store), 2–3 drivers beyond growth rates | BIWS; WSP; CFI | Built (`three_statement` segments); Gap: a check that refuses a bare growth-rate revenue line |
| M7 | Working capital from days; PP&E roll-forward; depreciation from useful lives; debt schedule; revolver to minimum cash; equity roll-forward | Macabacus; WSP; BIWS | Built (`three_statement`) |
| M8 | Never calculate on the balance sheet: it links to schedules (corkscrews) | WSP; FAST 2.02-05 | Built |
| M9 | The balance sheet balances every year, cash ties, net income ties, revolver and PP&E never negative; a master check | WSP; ICAEW; FAST 2.03-05 | Enforced (`model_gaps`, X3, X12) |
| M10 | No plugs | CFI; ICAEW review guide | Enforced (X3 plug labels) |
| M11 | Circularity: none by default (interest on opening balances); average balances only behind a circuit-breaker switch | FAST 1.01-11 vs WSP/BIWS | Built (opening balances); Gap: the average-balance mode with a `circ` switch |
| M12 | Scenarios: stacked base/bull/bear rows, one live switch, never Excel Scenario Manager | CFI | Enforced (`model_gaps`), Built (`scenario_book`, `scenario_summary`) |
| M13 | Sensitivities: one- and two-way tables on the key outputs | CFI | Built for DCF value (WACC × g, WACC × exit); Gap: general driver tables (IRR, EPS, leverage) |
| M14 | Historicals sourced to the filing; restatements flagged; gaps left empty | M&I; AlphaSense Table Explorer | Built (`spreading.py`, EDGAR XBRL with accession per cell) |
| M15 | Build at the shortest period and roll up; quarters sum to the year; LTM = FY + YTD − prior YTD; calendarisation | WSP; BIWS | Gap (annual only) |
| M16 | Share count: treasury stock method for options/RSUs, if-converted for convertibles | Macabacus; WSP | Gap |
| M17 | SBC not added back in FCF unless dilution is modelled; leases (IFRS 16 / ASC 842) treated consistently in EBITDA, EV and FCF | BIWS; M&I | Gap |
| M18 | Deferred taxes and NOLs; intangibles amortisation; minority interest and equity-method investments | Macabacus; M&I | Gap |
| M19 | Sanity checks: growth declines over time; margins and growth rarely rise together; capex ≥ D&A for growers; ΔNWC within history; FCF conversion reasonable; output vs consensus | BIWS; ICAEW review | Gap (alerts tab) |
| M20 | Cover lists qualifications and weaknesses; version log; every schedule printable | FAST 2.06; CFI | Gap |
| M21 | Independent recompute of key outputs | ICAEW (self-review catches 34–69% of errors) | Built in tests; Gap: the engine writes no X7 recompute sidecar |

## 2. Valuation and transactions

| # | Rule | Source | Status |
|---|---|---|---|
| V1 | Decide which methods fit before building: DCF only when cash flows tie to drivers; comps, precedents, SOTP, NAV, DDM / excess return for banks and insurers, LBO as a floor | IVS 105; Damodaran; M&I | Enforced (`model_gaps`), Built (`select_methods`) |
| V2 | DCF from integrated statements; UFCF = EBIT(1−t) + D&A − capex − ΔNWC; mid-year; Gordon primary, exit multiple cross-check with implied multiple and implied g | M&I; WSP; BIWS | Enforced / Built |
| V3 | WACC: target or current structure held constant; unlever peer betas (median), relever; risk-free matched to duration; ERP and country risk from Damodaran's latest files; size premium only with a source; cost of debt at current yield | WSP; Damodaran | Built (build-up); Gap: live pulls of Damodaran ERP/betas and Treasury yields |
| V4 | Terminal growth ≤ long-run nominal GDP (Damodaran: ≈ risk-free); show the terminal-value share of EV | Damodaran; WSP | Built (share shown); Gap: a check g ≤ risk-free |
| V5 | EV → equity bridge: debt, preferred, NCI, leases, pensions; cash and non-operating assets; consistency between the multiple's denominator and the bridge | M&I | Partly built (debt, cash, one `other_claims`); Gap: itemised bridge |
| V6 | Comps: 5–10 peers screened on business, financials and risk; LTM and NTM; calendarised; scrubbed of one-offs; median and interquartile range; NM below 0x or above 100x | WSP; BIWS | Built (`comps_table`); Gap: NTM/consensus, calendarisation, live data |
| V7 | Precedents: screen on the target; LTM at announcement; unaffected date rules; premiums over 1/7/30/90-day windows; options outstanding at the offer price | Macabacus; BIWS | Built (`precedents_table`); Gap: premium windows, unaffected-date logic |
| V8 | LBO: sources & uses, tranches (revolver, TLA, TLB, notes), cash sweep in seniority order, IRR/MOIC, exit = entry multiple, ability-to-pay back-solve as the floor | Macabacus; BIWS; WSP | Gap (biggest valuation gap) |
| V9 | Merger model: consideration mix, financing, PPA with goodwill and DTL, synergies net of costs, accretion/dilution, contribution analysis | WSP; BIWS; Macabacus | Gap |
| V10 | Banks: no EV/UFCF; DDM from regulatory capital; excess return; P/TBV vs ROTE. Insurers: DDM, P/BV, embedded value | M&I; Damodaran | Selected by rules (V1); Gap: builders |
| V11 | Combining methods is a reconciliation, not an average: investigate wide divergence before weighting; football field with 25th–75th ranges, the 52-week range and the current price | IVS 105 + glossary; BIWS | Built (weights with justification, football field); Gap: a divergence check that forces an explanation |

## 3. Decks (forge engine)

| # | Rule | Source | Status |
|---|---|---|---|
| D1 | Action titles: one full-sentence message per slide; the titles read as the storyline | Minto; consulting practice | Enforced (P11, P12) |
| D2 | Ghost deck first: storyline of titles before slides | Conn & McLean; consulting practice | Built (storyline pass) |
| D3 | Every number on a slide traces to a fact; chart data matches text | Macabacus Deck Check; WSO | Enforced (P13, P14) |
| D4 | Source line with as-of date on every quantitative slide | IB and consulting practice | Enforced (P13); Gap: sources are free text, not linked to research passages or workbook cells |
| D5 | Chart form follows the comparison (component, item, time series, frequency, correlation) | Zelazny | Partly (chart checks) |
| D6 | Units consistent ($mm vs $bn), multiples as 12.5x, NM, dashes for missing data, rounded valuation outputs ($24–25, not $24.37) | IB practice | Gap |
| D7 | IB page types: situation overview, company profile (strip), comps table, precedents table, football field, DCF output, sources & uses, accretion/dilution, ownership, share price performance | M&I; CFI; WSP | Gap (no IB archetypes; no floating-bar chart) |
| D8 | Same metric identical on every page; executive summary matches the body | WSO; Macabacus | Gap: deck-wide consistency check |
| D9 | Deck numbers come from the workbook's named outputs, not re-typed | IB practice | Gap |

## 4. Research (research engine)

| # | Rule | Source | Status |
|---|---|---|---|
| R1 | Issue tree and hypotheses first; a "one-day answer" | Conn & McLean; Rasiel | Built (engine method) |
| R2 | Start from existing reports (equity research, industry reports, investor presentations; `filetype:pdf`, `report.pdf`) and treat their conclusions as hypotheses | analyst practice | Built (`find_reports`) |
| R3 | Primary sources first (filings, releases, regulators), then reputable secondary | AlphaSense / Hebbia practice | Built (tiers, EDGAR full-text search) |
| R4 | Passage-level citations for every fact; numbers verbatim or shown as arithmetic; quotes verbatim | Hebbia, AlphaSense, Rogo | Enforced (verify R1–R3) |
| R5 | Independent sources (syndicated copies count once); headline numbers verified by a primary source or two independent sources; refuse rather than guess | Rogo ("no source, no answer") | Enforced (R4, R5, R7) |
| R6 | Driver tree: what drives the market, revenue and costs; which 2–3 drivers matter most | consulting practice; Damodaran *Narrative and Numbers* | Built (engine method); Gap: a check that the report has the section |
| R7 | Conflicts between sources shown as a range with the choice explained | consulting practice | Built (method); Gap: automatic conflict detection per metric |
| R8 | Hand-off: research facts flow into decks and assumption books with their passage ids | Hebbia Matrix 2.0 | Built (report to the next engine, `assumption_source`, `facts`); Gap: forge source lines linked to passages |
| R9 | Grid extraction: the same questions across many documents (CIM screening, diligence trackers) | Hebbia Matrix; AlphaSense Generative Grid | Gap |
| R10 | Table stitching across filings with restatement flags | AlphaSense Table Explorer | Partly (XBRL spreading) |

## 5. Data we can use without a licence

SEC EDGAR (XBRL companyfacts and frames, submissions, full-text search since 2001; 10 requests/s with a declared
User-Agent), SEC Financial Statement Data Sets, FRED (key; copyright series excluded), US Treasury yield XML and
Fiscal Data API, Damodaran's datasets (ERP, betas, margins, multiples; free use with attribution; ask before
commercial redistribution), World Bank (CC BY 4.0), OECD (CC BY 4.0 from July 2024), ECB (free with attribution),
UK Companies House (600 requests / 5 min), EU ESEF via filings.xbrl.org, Japan EDINET. Earnings releases come as
8-K Item 2.02 exhibits.

These need a licence: broker research, expert-call transcripts, transcripts at scale, consensus estimates,
redistributable prices, private-company and deal databases (PitchBook, Capital IQ, Mergermarket), ratings data. The
engines take them as provided files or connectors when the user has access.

## 6. Roadmap, in priority order

1. **Cash flow and credit analysis for lenders**, built to run on small local models: deterministic spreading,
   the cash flow analysis and covenant tests in code, with the model only choosing among structured options. Scope
   to be confirmed.
2. **LBO builder** (V8) and **paper LBO / ability to pay**, so the LBO floor is computed rather than typed.
3. **Quarterly model, LTM and calendarisation** (M15) from 10-Q XBRL, feeding comps and the model.
4. **Share count (TSM, convertibles), SBC and leases, itemised EV bridge** (M16, M17, V5).
5. **IB deck archetypes** (D7) with the football field as a floating-bar chart, number conventions (D6), and deck
   numbers read from workbook names (D9) with sources linked to research passages (D4, R8).
6. **Merger model** (V9) and **bank / insurer builders** (V10).
7. **Live market inputs**: Treasury yields, Damodaran ERP and industry betas, EDGAR frames for peer fundamentals.
8. **Sanity alerts** (M19), divergence check (V11), circularity switch (M11), general sensitivity tables (M13),
   the engine's own X7 recompute sidecar (M21).
9. **Grid extraction** over many documents (R9) and per-metric conflict detection (R7).

## 7. Reading list (what each informs: M model, D deck, R research)

Financial modelling: Pignataro, *Financial Modeling and Valuation* 2e (Wiley 2022) M; Benninga & Mofkadi,
*Financial Modeling* 5e (MIT 2022) M; Tjia, *Building Financial Models* 3e (McGraw Hill 2018) M; Day, *Mastering
Financial Modelling in Excel* 3e (FT/Pearson 2012) M; Rees, *Principles of Financial Modelling* (Wiley 2018) M.
Standards: FAST Standard 02c; ICAEW Financial Modelling Code; ICAEW "How to review a spreadsheet".

Valuation: Rosenbaum & Pearl, *Investment Banking* 3e (Wiley 2020) M D; Koller, Goedhart & Wessels, *Valuation* 8e
(Wiley 2025) M R; Damodaran, *Investment Valuation* 4e (Wiley 2024) M R, *Narrative and Numbers* (Columbia 2017)
R D, *The Little Book of Valuation* (Wiley 2024) R, "Valuing Financial Service Firms" (NYU 2009) M; Pratt / ASA,
*Valuing a Business* 6e (McGraw Hill 2022) M R. Standards: IVS 105; IFRS 13 / ASC 820; AICPA PE/VC valuation guide.

M&A and LBO: Bruner, *Applied Mergers and Acquisitions* (Wiley 2004) M R; DePamphilis, *Mergers, Acquisitions,
and Other Restructuring Activities* 12e (Elsevier 2025) R; Pignataro, *Leveraged Buyouts* (Wiley 2013) M.

Accounting and red flags: Fridson & Alvarez, *Financial Statement Analysis* 5e (Wiley 2022) M R; Schilit, Perler &
Engelhart, *Financial Shenanigans* 4e (McGraw Hill 2018) R; O'glove, *Quality of Earnings* (Free Press 1987) R.

Consulting and communication: Minto, *The Pyramid Principle* (FT/Pearson) D R; Rasiel, *The McKinsey Way* (McGraw
Hill 1999) D R; Conn & McLean, *Bulletproof Problem Solving* (Wiley 2019) R D; Zelazny, *Say It With Charts* 4e
(McGraw Hill 2001) D; Knaflic, *Storytelling with Data* (Wiley 2015) D; Duarte, *slide:ology* (O'Reilly 2008) D.

Equity research: Valentine, *Best Practices for Equity Research Analysts* (McGraw Hill 2011) R M; Graham & Dodd,
*Security Analysis* 7e, ed. Klarman (McGraw Hill 2023) R.

## 8. Where practitioners disagree (the engine's default, configurable)

| Topic | Default | Alternative |
|---|---|---|
| Circularity | none: interest on opening balances (FAST, ICAEW) | average balances behind a switch (WSP, BIWS, CFI) |
| Statements | on one tab with schedules beneath (BIWS, CFI, WSP) | separate sheets by type (FAST) |
| Signs | costs negative, label says so (BIWS, WSP) | normally positive (FAST workings) |
| Comps statistic | median and interquartile range | mean with fewer than ~5 peers |
| Options in share count | exercisable for trading comps | outstanding at offer price for transactions |
| Terminal growth | ≤ risk-free rate (Damodaran) | 1–3% / 2–4% bands (BIWS / WSP) |
| Sponsor hurdle | 20–25% IRR | 15–30% by market |
