# Rogo feature anatomy against the Meshpool harness

Source: rogo.com pages read on 2026-11-15 (platform, agents, intelligence, projects, watchtower, notes, integrations,
security, customers). Harness state: branch `claude/elegant-edison-1oey8d`, same date. Status per row: **have**
(works and is tested), **partial** (some of it exists), **missing**. Data rooms (VDRs) are deferred by decision.

## 1. Output: decks, models, memos

| Rogo capability | What Rogo says it does | Harness today | Status | Next step |
|---|---|---|---|---|
| Slide exports, firm template and style guide | On-brand decks from the firm's own template ("custom slide formats") | forge: native PPTX from client templates (`template.py`, `brand.py`), archetypes, validator P0-P20, repair loop, partner/MD review | have, quality not yet client-ready | WS1: close the spec's anti-slop and validator gaps; score 3 models |
| CIM draft, teaser, management presentation agents | Long-form banking documents from a template | Not as named workflows; forge builds decks from any brief | partial | Workflow presets (section 3) for CIM, teaser, management presentation |
| Excel exports, LBO model, comps, valuation | Model outputs as .xlsx | sheets engine (agentic, formula-driven), credit engine (fixed pipeline), `finance_pages.py` (DCF bridge, football field), `validate_xlsx.py` | partial | WS2: recipes from anthropics/financial-services (Apache-2.0); LibreOffice/formulon recalc cross-check |
| Earnings benchmarking (actuals vs model vs consensus) | Equity research workflow | Actuals from SEC XBRL; no consensus data | partial | Consensus needs a licensed source (FactSet/LSEG/CapIQ connector) or must be marked unavailable |

## 2. Research and data

| Rogo capability | What Rogo says | Harness today | Status | Next step |
|---|---|---|---|---|
| Deep company and market research, filing synthesis | Cited research across filings, news, internal docs | research engine (Corpus, tiered sources, passage citations, verifier), company dossier (new, local commit), XBRL facts, market data, computed economics | partial | WS4: search that works out of the box (ddgs added), non-US filings, earnings releases, news timeline |
| SEC filings search | Filings, exhibits, disclosures | EDGAR full-text search + XBRL company facts | have | edgartools for statements and 8-K exhibits (teardown running) |
| International filings (Companies House, EDINET, others) | Named connectors | None | missing | Companies House API (free key), EDINET API (free); MENA exchanges via IR sites |
| Transcripts, IR materials (Quartr) | Earnings calls and decks | 8-K exhibits only (US) | partial | Free: company IR pages and 8-K exhibits; licensed: Quartr connector |
| Fundamentals, estimates, prices (CapIQ, FactSet, LSEG, Daloopa) | Licensed data | SEC XBRL + Yahoo prices | partial | Connector slots for licensed data; OpenBB as the free layer (teardown running) |
| Private markets (PitchBook, Preqin, Crunchbase, Orbis) | Licensed data | None | missing | Connectors when a customer brings a licence |
| News (Dow Jones, MT Newswires) | Licensed newswires | Web search only | partial | Dossier news questions with recency; licensed feeds later |
| Public records (Data Commons, ClinicalTrials.gov, CourtListener) | Free public APIs | None | missing | Cheap to add: all free APIs |
| Every answer cites its source | "Checked line by line" | Passage-level citations, number-in-passage check, provenance.json for decks | have | Keep; extend to workbook inputs (Source column already required) |

## 3. Agents and workflows

| Rogo capability | What Rogo says | Harness today | Status | Next step |
|---|---|---|---|---|
| Agent library (hundreds of finance agents) | Pre-built workflows | Engines (forge, sheets, credit, research, data), skills (finance/legal/sales bundled) | partial | Named finance workflows on top of engines: comps, DCF, LBO, CIM, teaser, earnings note, buyer list |
| Agent builder (firm templates, conventions, sources) | Users build agents | Skills and plugins exist; no builder UI | partial | Workflow spec file (inputs, sources, output template) a user can save |
| Proactive agents (run on news, earnings, new files) | Event-triggered | Scheduled tasks exist in the host product; no finance triggers | missing | Earnings-date and filing watchers (SEC RSS, IR pages) that start a workflow |
| Prompt libraries | Shared prompts | Skills | have | |

## 4. Firm memory, projects, notes

| Rogo capability | What Rogo says | Harness today | Status | Next step |
|---|---|---|---|---|
| Intelligence (people, companies, deals, decisions memory; CRM writeback; MNPI wall) | Firm knowledge graph | memory folder, chat import; no entity memory | missing | Later: entity store (companies, people, deals) with permissions |
| Projects (deal workspace, synced folders, process types) | Deal team workspace | Project folders, checkpoints (per-turn undo) | partial | Process templates (sell-side, buy-side) as workflow bundles |
| Notes (meeting notetaker, compliance) | Meetings to notes and actions | None (Granola/Wispr connectors exist in host) | missing | Out of scope for the engine; use connectors |
| Data rooms | VDR ingestion | None | deferred | Deferred by decision |

## 5. Governance

| Rogo capability | What Rogo says | Harness today | Status | Next step |
|---|---|---|---|---|
| Model broker (route each task to the best model) | Quality/cost/latency routing | Meshpool Auto routing table, provider profiles, MH_MODEL_ALLOW guard | have | Per-step model choice in engines (writer vs reviewer vs extractor) already configurable |
| Credit center (budgets, caps) | Spend control | Gateway key budget; per-run spend checks in tests | partial | Per-run budget cap in engines (stop at a set spend) |
| Agent controls, audit trail | Who ran what, data touched | Checkpoints, run logs, provenance | partial | Run ledger: model calls, sources read, files written per run |
| Security (SOC 2, ISO 27001, no training on data) | Enterprise programme | Local-first app; not a certified SaaS | n/a | Not an engine task |

## What this means for the build order

1. **Research first** (the Cleopatra lesson): the dossier, out-of-the-box search and non-US sources. Everything else
   inherits the facts it finds.
2. **Deck engine to client-ready** on one template, measured on 3 models (WS1).
3. **Model engine** with recipes and a recalc cross-check (WS2).
4. **Named workflows** (comps, DCF, LBO, CIM, teaser, earnings note) that chain research, model and deck.
5. Later: proactive triggers, entity memory, licensed data connectors, data rooms.
