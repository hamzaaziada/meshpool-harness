# Rogo outputs: what is public and what it implies for forge and sheets

Read 2026-11-16 from rogo.com (home, platform, agents, customers, blog "What's new" Aug/Oct/Nov 2025, Agent
Library post), captured with a headless browser. Rogo's slide generation runs server-side; no client code or public
sample deck shows how slides are built, so this is what the public pages show, not their implementation.

## What the public pages show

- **No slide samples.** The only full output on the site is an LBO model sheet. Slides appear as an export target
  ("Export answer to PowerPoint Slide (.pptx)", PDF, Word) and as agent outputs.
- **Polish comes from the firm's own template.** Taureau: "a custom presentation template and firm style guide
  directly into the platform, automatically producing polished, on-brand materials". Harbor View: forward-deployed
  bankers help "build custom agents and slide formats"; over 1,000 slide exports. Agents take "your firm's
  precedents, benchmarks, and house formatting".
- **Variety comes from a catalogue of banker formats**, each an agent that returns a finished Excel, PowerPoint or Word
  file: value creation bridge, NAV decomposition, dividend recap, SOTP, model audit, liquidation waterfall, DCF
  model, earnings update, broker rating distribution, company screen, research digest, broker research, QoE buyer
  analysis, IRR decomposition, LBO debt schedule, bolt-on M&A, merger model, PIB builder, CIM draft, management
  presentation, comparable companies.
- **Every figure is traceable**: a source chip on each number ("Capital IQ · View source"), ownership donut with a
  highlighted share, tables with the key cell shaded.
- **Workbook house style** (LBO sheet): a dark green band for the case title; light teal bands for section headers
  (Consolidated P&L, Debt Schedule, Credit Metrics & Covenant Compliance); units line "($ in millions)"; columns
  2025A then 2026E-2030E; historical inputs in green; bold totals over a rule; margins and growth in italics;
  negatives in parentheses; covenant PASS row; tabs Assumptions / Base Case / Optimistic Case / Pessimistic Case /
  Sensitivity.

## What it means for us

| Rogo | Harness today | Next step |
|---|---|---|
| Firm template and style guide built in | Client templates (`template.py`, `brand.py`), themes | Ask for the firm's template up front; default look only when none is given |
| Catalogue of banker slide formats | ~20 archetypes (waterfall, football field, table, stats, matrix, grid parts) | Add formats as exhibit types: ownership donut + table, SOTP stacked bar, broker rating distribution, scenario table (base/upside/downside), debt schedule; one format per page, no two pages alike |
| Source chip on every number | Provenance map, source line per page | Superscript source refs per figure on exhibits |
| Workbook: scenario tabs, banded sections, covenant PASS rows | Cover/Inputs/Model/Outputs/Checks, one case plus a downside block | Scenario tabs (base, upside, downside) driven by one switch; section bands; covenant-style PASS rows on Checks |
