# Forge pipeline spec: storyline first, design mock second, native file last

Each stage reads one file and writes one file in a shared workspace, so any stage can be rerun or swapped to another
model. In Meshpool Work (Cowork) the stages are tools on the `meshpool-office` MCP server. In the cloud they are the
same tools behind a job queue (§5).

```
brief + sources ─► 1 intake ─► facts.json ─► 2 storyline ─► storyline.json (ghost deck) ─► [approval]
   ─► 3 content ─► slides.json ─► 4 design mock (optional) ─► layout_spec.json
   ─► 5 native build ─► deck.pptx + provenance.json ─► 6 render + QA ─► qa.json + PNGs
   ─► 7 repair (≤3 rounds, content scope only) ─► 3 …  ─► final files + QA report + provenance map
```

## 1. Stage contracts

| Stage | Reads | Writes | Model | Deterministic checks at exit |
|---|---|---|---|---|
| 1 Intake | brief, PDFs, scans, decks, workbooks, transcripts | `facts.json`: `[{id, text, value, unit, period, source:{doc, page, span}}]`, `tables/*.csv` with page refs | none for parsing; optional small model for table repair | every fact has a source span; OCR confidence recorded |
| 2 Storyline | `facts.json`, brief | `storyline.json`: governing thought, structure (SCR or stated), sections, one action title per node, plus the evidence each node needs (fact IDs) | strongest available | titles read top to bottom carry the argument; no two titles >0.8 similar (P12); every node cites facts; slide count = brief (P16). Approved by a human or a second-model review before stage 3 |
| 3 Content | one storyline node + its facts | one slide JSON (`schemas/slide.schema.json`) | any | JSON Schema valid; every number has provenance (P13); archetype limits (P10) |
| 4 Design mock (off by default) | slide JSON + theme tokens | `mock/<id>.png`, then `layout_spec.json`: grid columns per element, hierarchy, chart type, emphasis | image model, then vision model | layout snapped to template grid and archetypes; anything not expressible is reported, never hacked in; numbers and text in the mock are ignored |
| 5 Native build | slide JSON, layout spec, template, rules | `deck.pptx`, `provenance.json`, `brief.json`, `storyline.json` | none | renderer contract (engine-spec §4) |
| 6 Render + QA | `deck.pptx` | `qa.json` (`validate_pptx`), `png/*.png`, contact sheet, vision diff vs mock | vision model (review only) | P1–P19 |
| 7 Repair | `qa.json` failures with `fix_scope = content` | revised slide JSON for failing slides only | same as stage 3 | ≤3 rounds, then remaining failures go into the notes as `[QA]` lines and the report |

Workbooks follow the same shape: intake → model block (`schemas/workbook_block.schema.json`) → builder → LibreOffice
recalc → `validate_xlsx` (X1–X13) → repair, with X7 fed by the builder's own Python recompute.

### What runs in Cowork today

| Tool (meshpool-office MCP) | Stage | Status |
|---|---|---|
| `read_document` | 1 intake (PDF, Office, Power BI, OCR) | shipped |
| `make_deck`, `make_spreadsheet`, `analyze_data` (and `office_task` in Meshpool Work) | 2–5, current engines | shipped; forge QA lines now appended to every job report |
| `check_deck` | 6 QA, P1–P19 | **new** (read-only, auto-allowed) |
| `check_workbook` | 6 QA, X1–X13 | **new** (read-only, auto-allowed) |
| `measure_office_file` | reverse-engineering a reference .pptx/.xlsx/.pdf | **new** (read-only, auto-allowed) |
| `python -m engines.forge.rebuild_eval` | eval: one-shot rebuild of a reference deck | **new** (CLI; spends model calls) |
| template-placeholder renderer, storyline approval gate, design-mock stage, logo fetch, PBIP export | 2, 4, 5 | **to build** (see open-questions.md) |

Meshpool Work's system prompt now tells it to run `check_deck`/`check_workbook` on every deliverable and fix the
content failures. It also writes memos and reports as .docx, which was handoff issue 2.

## 2. Out-of-the-box toolkit (chosen, pinned, licensed)

One stack per file type. Versions are the ones tested in this repo on 13 Nov 2026.

| Need | Choice | Version | Licence | Notes |
|---|---|---|---|---|
| Decks | python-pptx + lxml | 1.0.2 / 6.1.3 | MIT / BSD-3 | lxml for theme refs, sections, chart XML |
| Workbooks | openpyxl | 3.1.5 | MIT | read + write; formula tokenizer for X2/X8 |
| Render + recalc | LibreOffice headless | 24.2 | MPL-2.0 | external process, throwaway profile per run |
| PDF text geometry (runtime) | pypdfium2 | 5.13 | BSD-3 / Apache-2.0 | already bundled; used by the P1 render check |
| PDF measurement (offline calibration only) | PyMuPDF | 2.1 | **AGPL-3.0** | dev-only (`measure_pdf`, `rebuild_eval`); must not ship in the Apache-licensed app |
| Font metrics | Pillow + metric-compatible fonts | 12.3 | MIT-CMU | Carlito = Calibri, Liberation = Arial/Times/Courier (`fonts.metric_files`) |
| Icons | Lucide (bundled, 1,540 SVGs) | — | ISC (licence file checked: `engines/slides/icons/LICENSE-lucide.txt`) | recolour through theme slots only; keyword index in `archetypes.py` |
| OCR | PaddleOCR PP-OCRv5/v6 (bundled) | — | Apache-2.0 | Docling (MIT) as a table-structure option; Tesseract (Apache-2.0) as a fallback |
| Logos | Logo.dev or Brandfetch | API | commercial API terms | the free Clearbit Logo API shut down on 8 Dec 2025, so don't build on it; cache SVG plus light/dark variants with source and fetch date; fall back to a text wordmark with a visible flag; use only to identify companies |
| Config | PyYAML | 6.0.3 | MIT | `rules.yaml` + client overrides |
| Artifact inventory | REA (`rea-agents`, npx) | 6.9.0 | see its repo | `inspect-artifact` for ZIP/OOXML inventory with Evidence IDs; `inspect-web-network-capture` for HAR/SAZ |

Chart library (all native, editable): bar, stacked, 100% stacked, line, combo; waterfall (stacked columns with
invisible base); Marimekko (stacked area or variable-width columns via a date axis); scatter and bubble; Gantt (stacked
bars); Harvey balls (pie shapes, grouped one level); 2x2 matrix (shapes on grid); tornado (clustered bar, negative
left). Each one gets direct data labels in place of a legend where there are ≤4 series, a highlight in `accent1`, the
rest in muted `tx2`, and a source line.

## 3. Excel → dashboard → Power BI

1. **Model.** The workbook builder creates Inputs/Calc/Outputs/Checks from the model block under X1–X13.
2. **Dashboard tab.** One message per page; 3–6 KPI cards, each with a comparison value (prior period, plan or first
   period); ≤4 native charts. Every value is a formula into Outputs/Calc (X9). Theme colours only, no 3D effects.
3. **Power BI Project (PBIP).** From the workbook's clean tables (one per Calc block, long format: period, line,
   value):
   - semantic model in TMDL: tables, relationships (period dimension), measures in DAX that mirror the Outputs
     formulas;
   - report in PBIR: one page per dashboard message, KPI cards with reference labels, the same chart choices;
   - validation of `definition.pbir`, `report.json` pages and visuals against Microsoft's published JSON schemas
     (the `$schema` URLs in each file), plus a DAX parse of each measure.
4. **Getting a .pbix.** The reliable path is to open the PBIP in Power BI Desktop and save it as .pbix, or publish
   with fabric-cicd. pbi-tools can compile report-only PBIX. Third-party tools that write full PBIX directly need
   testing first: the data model inside a PBIX is a binary format that isn't documented for programmatic writing.
   Microsoft's Power BI MCP servers and agent skills are an alternative builder; run them against the same rules.

Status: not built. `read_document` already reads .pbix/.pbit (pages, visuals, DAX, Power Query).

## 4. Running in a cloud workspace

- **Container:** Python 3.12+, LibreOffice 24.x, the metric-compatible fonts (Carlito, Caladea, Liberation) plus
  the client template fonts, the bundled OCR models, and pinned wheels for the toolkit above.
- **Job queue:** renders, recalcs and builds run as jobs (the office server already runs engines as background
  jobs with `office_job_status`). LibreOffice uses one throwaway profile per job, so jobs parallelise.
- **Tools over MCP:** each stage is a tool that reads and writes files in the shared workspace
  (`<project>/Meshpool outputs/<stamp>-<engine>/`).
- **Every run leaves:** the final files (.pptx, .xlsx, PBIP, and .pbix where possible), slide PNGs plus a contact
  sheet, `qa.json`, and `provenance.json`. A person or another agent can review the work without rerunning anything.

## 5. Evaluation harness

- `validate_pptx` / `validate_xlsx`: the rules (CI: `app/tests/test_forge.py`, 9 tests, good and bad fixtures).
- `scorecard.py`: every file × every rule → `docs/deck-engine/scorecard.csv`.
- `rebuild_eval.py`: **the McKinsey one-shot test.** Text-only brief from a reference PDF, one build, then the same
  measurements on both decks plus side-by-side contact sheets for a blind vision review.
- Blind reviewer: a separate agent that sees only PNGs scores hierarchy, white space, grid, chart clarity and
  "consultant-made" from 1 to 5 (`visual-rubric.csv`).
