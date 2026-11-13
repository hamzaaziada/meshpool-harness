# Open questions, Hebbia demo requests, and the step-by-step checklist

## What to request in the Hebbia demo

Use one brief for every tool (Hebbia, Gamma, Copilot, Claude, Perceptis) so the scorecard compares like with like.
Save every file exactly as exported. Don't open and re-save it in PowerPoint or Excel, because that rewrites the
generator string, the relationship IDs and the shape names.

**Brief to use (the same text everywhere):**

> Using the attached 10-K and earnings-call transcript for [a public company you choose], build a 10-slide board
> update: an executive summary, revenue and margin trends with charts, a revenue bridge (waterfall) from last year to
> this year, a peer comparison table against 3 named competitors (with their logos), a 2x2 of strategic options, a
> timeline of next steps, and an appendix with the detailed figures. Cite the source on every data slide. Use our
> template. Then build the supporting Excel model: 3 historical years plus 5 forecast years, with an income
> statement, balance sheet, cash flow, checks, and a dashboard tab.

**Files to export from the Hebbia demo:**

1. The deck as **.pptx**, built into **your template** (upload the client .potx).
2. The same deck built into **Hebbia's default template**, to tell template-driven output apart from its own design.
3. The workbook as **.xlsx**.
4. If offered, the same content as a **.docx** memo.
5. A **regenerated** version of the deck from the same prompt (a second run). The two runs show what is fixed
   (template, layouts) and what the model decides each time.
6. An **edited** version: ask Hebbia to "make slide 3 a waterfall and shorten every title". This shows whether edits
   are placeholder-level or regenerate the slide.
7. Optional: a **HAR or SAZ** capture of your own session while generating (browser DevTools → Network → Save all as
   HAR). It is inspected offline with `inspect_web_network_capture`; no URLs are fetched or replayed.

**Questions to ask the Hebbia team (or check in the UI):**

- Can it build into an uploaded .potx, and does it use the template's layouts and placeholders or draw over a blank
  layout?
- Are charts editable in PowerPoint (native), and does each chart carry its data?
- Where are citations: on the slide, in the notes, or in a side panel only?
- Does it cap slide count, words or bullets per slide, or shrink text to fit?
- For Excel: are forecasts formulas or values? Are there check rows? Does the balance sheet balance without a plug?

## Open questions

| # | Question | Answered by |
|---|---|---|
| Q1 | Does Hebbia fill template placeholders (H1) or draw freely? | demo files 1–2, `measure_pptx` |
| Q2 | Which library writes Hebbia's OOXML (H2)? | `app.xml`, shape names, member order in file 1 |
| Q3 | Are Hebbia charts native with embedded data (H3)? | file 1, P2 |
| Q4 | Does Hebbia cut text to fit or use autofit (H4)? | file 1, `autofit` and `overflow` |
| Q5 | Do Hebbia workbooks keep live formulas and checks? | file 3, `validate_xlsx` |
| Q6 | What is fixed vs model-decided across runs? | files 1 and 5 diffed (`rea compare-artifacts`) |
| Q7 | Should the 12 pt body / 9 pt footnote floors bend to 10 / 8 pt for exhibit labels, as every reference does? | owner decision; the config supports either |
| Q8 | Should P10's word caps stay per archetype (as now), or move to "words per square inch of free body area"? | owner decision after the next rebuild round |
| Q9 | Which OCR engine backs P3's text-in-image check: the bundled PaddleOCR, or Docling? | engineering; P3 reports `not_run` until then |
| Q10 | Which logo API (Logo.dev or Brandfetch), and whose key? | owner |
| Q11 | Power BI: are PBIP + Power BI Desktop save-as acceptable, or is a direct .pbix required? | owner; direct .pbix writing is unsafe (pipeline-spec §3) |
| Q12 | Which McKinsey decks form the fixed rebuild benchmark set? Suggest DC Transportation, Admin Simplification pp53–76 and USPS (vector exhibits, English, source lines) | owner |

## Next engineering steps, in order

1. **Template-placeholder renderer** (engine-spec §4). It fixes P6, P7, P15 and P19 by construction and is the
   largest gap against both Hebbia (if H1 holds) and McKinsey.
2. **Source line and exhibit subtitle** as required fields on data archetypes. Fixes the 0% source-line result.
3. **Archetype library** from engine-spec §3, starting with the five most common in McKinsey references:
   text_matrix, two_exhibits, table_highlight, waterfall_bridge, scatter_positioning.
4. **Repair loop on forge failures:** feed `validate_pptx` content-scope failures to stage 3 for up to 3 rounds,
   then freeze the rest into the notes.
5. **Rerun the McKinsey rebuild test** after each step. Pass bar: `mckinsey-rebuild-test.md`.
6. The workbook dashboard tab, then PBIP export.

## Checklist: brief steps 1–5

| Step | Item | Status | Where |
|---|---|---|---|
| 1 | Inventory each file (members, hashes, content types, generator, core props) | **Answered for our files and the 17 reference PDFs; unresolved for Hebbia** (no file) | REA Evidence ev_c358009f…, ev_932600e1…; `measure_pptx.inventory` |
| 2 | Masters/layouts, placeholder use | answered for ours; unresolved for Hebbia | findings M-1; anatomy doc |
| 2 | Theme, schemeClr vs srgbClr ratio | answered for ours; unresolved for Hebbia | M-2 |
| 2 | Fonts, embedding, size histogram by role | answered for ours and references; unresolved for Hebbia | M-3, R-5, R-6 |
| 2 | Grid (EMU positions, margins, columns, left edges, overlaps) | answered for ours; partly for references (PDF text left edges only); unresolved for Hebbia | P7/P8; `reference-measurements.csv` |
| 2 | Native charts vs pictures, series → cells, chart types | answered for ours; unresolved for Hebbia | M-4 |
| 2 | Tables native vs images | answered for ours; partly (no OCR) | P3 |
| 2 | Text density, titles (action vs label) | answered for ours and references; unresolved for Hebbia | R-1, R-3, R-9 |
| 2 | Source lines | answered for ours and references (lower bound for raster decks) | R-4, M-7 |
| 2 | Images and icons, icon-set consistency | answered for ours; unresolved for Hebbia | P9 |
| 2 | Text fit (autofit, overflow estimate) | answered for ours (metric fit + render check) | P1, P5 |
| 2 | Notes, alt text, slide numbers, sections, group depth | answered for ours; unresolved for Hebbia | P17, P18, summary |
| 3 | Workbook anatomy (roles, formula share, literals, links, names, cycles, R1C1, formats, checks) | answered for our two workbooks; unresolved for Hebbia | M-10, M-11, `validate_xlsx` stats |
| 4 | Infer the generation pipeline | **partly**: hypotheses H1–H6 with confirm/refute evidence; untested | `hebbia-deck-anatomy.md` |
| 5 | Spec and validators | **answered**: schemas, archetypes, renderer contract, rules config, `validate_pptx.py` (P1–P19), `validate_xlsx.py` (X1–X13), scorecard, pipeline spec | `engine-spec.md`, `pipeline-spec.md` |
| — | Verification: render every deck, score against the rubric, blind reviewer | answered for ours and 5 reference pages; Hebbia and comparators unresolved | `visual-rubric.csv` |
| — | Recalculate every workbook headless before scoring | answered (LibreOffice) | `validate_xlsx` |
