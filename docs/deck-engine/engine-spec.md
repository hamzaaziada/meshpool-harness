# Forge engine spec: decks and workbooks with model-agnostic quality rules

Forge is the part of the Meshpool agent that builds and checks consulting-grade PowerPoint and Excel files. The visual design system is in `design-grammar.md`. This
spec defines what the model writes, what the renderer guarantees, and the rules that decide whether a file ships.
Hebbia's output is the bar to measure against. The hard rules below are the floor. Where a measured reference breaks
a rule, the rule stands, and the measurement goes into the findings ledger.

Code: `app/engines/forge/`. Rules: `app/engines/forge/rules.yaml`. Schemas: `app/engines/forge/schemas/`.

## 1. Why the result doesn't depend on the model

1. The model writes structured content only: one JSON object per slide (`schemas/slide.schema.json`) or one model
   block per workbook (`schemas/workbook_block.schema.json`). It never writes coordinates, colours, fonts or OOXML.
2. A deterministic renderer places that content into the client template's layouts and writes native OOXML
   directly. There's no HTML-to-PPTX conversion.
3. The validators (`validate_pptx.py`, `validate_xlsx.py`) run on the finished file. Each failure carries a
   `message` the model can act on without extra context, for example "Slide 4, Body 2: text overflows by 3 lines at
   14pt; cut to 45 words.", and a `fix_scope`:
   - `content`: goes back to the content model, which may change words, numbers, archetype choice and slide order.
   - `renderer`: a renderer or template defect. It is never sent to the model.
   - `template`: the client template or config must change.
4. The repair loop runs at most `repair.max_rounds` (3) times. Anything still failing is written to the QA report and
   into the speaker notes as `[QA]` lines. Nothing passes silently. A rule that could not be evaluated reports
   `not_run`, never `pass`.
5. Every threshold lives in `rules.yaml`. A client override file is deep-merged over it (`--rules client.yaml`), so a
   template can tighten or loosen any value.

## 2. Slide content schema (what the model writes)

Full JSON Schema: `app/engines/forge/schemas/slide.schema.json`. The essentials:

| Field | Meaning | Enforced by |
|---|---|---|
| `id`, `node` | slide ID; the storyline node it proves | P12 |
| `section` | section name; "Appendix" sends the slide to the appendix | P16 |
| `archetype` | a name from the archetype library (§3) | P15 |
| `title` | action title: one sentence with a verb, ≤15 words, ≤2 lines | P11, P19 |
| `subtitle` | exhibit subtitle: what is measured, the unit, the period ("Jobs, thousands, 2014–19") | P13 |
| `bullets[]` | `{text, level 0/1, prov[], icon?}`, at most 5 | P10 |
| `panels[]` | cards, columns, quadrants or phases: `{heading, bullets, value, icon, rating}` | archetype limits |
| `chart`/`charts[]` | `{kind, categories, series[{name, values, prov, highlight}], unit, decimals, highlight_categories, annotations}` | P2, P14 |
| `table` | `{columns ≤8, rows ≤14, highlight_rows, unit, prov}` | P3 |
| `takeaway` | one sentence that interprets the exhibit | P14 |
| `source` | `{text, prov[]}`; required on every data slide | P13 |
| `notes` | detail cut from the slide | P10 |
| `gaps[]` | numbers the model could not source; rendered as visible `[GAP …]` markers | P13 |

Provenance IDs (`F12`, `T3.4`) come from the intake fact sheet. Every number on a slide needs one (P13). The
renderer writes `provenance.json` next to the deck, and the validator reads it back.

## 3. Archetype library

Each archetype maps to a layout in the client template (the template loader finds it by placeholder signature,
falling back to the forge default template). Word caps are body text outside tables and charts (P10,
`density.by_archetype`). They were calibrated on 16 English reference decks: median 114 body words per content page,
p90 185, chart labels included.

| # | Archetype | Use when the title says… | Elements | Body words |
|---|---|---|---|---|
| 1 | `cover` | — | title, subtitle, date, client | exempt |
| 2 | `agenda` | — | 3–6 items, current item highlighted on section trackers | exempt |
| 3 | `section_divider` | — | number, title, one-line purpose | exempt |
| 4 | `executive_summary` | the whole answer | 3–5 numbered findings, each with one support line | 120 |
| 5 | `bar_takeaway` | X is larger/smaller than Y | one bar/column chart + 2–3 takeaways panel | 80 |
| 6 | `column_trend` | X grew/fell over time | column chart, CAGR annotation | 80 |
| 7 | `line_trend` | X diverged/recovered | line chart, ≤4 series, event markers | 80 |
| 8 | `stacked_composition` | the mix shifted | stacked or 100% stacked columns | 80 |
| 9 | `waterfall_bridge` | A became B because of … | waterfall (built from stacked columns) | 60 |
| 10 | `marimekko_market_map` | share by segment and player | variable-width bars | 60 |
| 11 | `scatter_positioning` | X correlates with Y / outliers | scatter with labelled points | 60 |
| 12 | `bubble_portfolio` | size, growth and share together | bubble chart | 60 |
| 13 | `matrix_2x2` | items fall into four groups | 2x2 with axis labels, ≤12 items | 60 |
| 14 | `harvey_ball_comparison` | options differ on criteria | Harvey-ball table, ≤6×6 | 60 |
| 15 | `table_highlight` | one row/column stands out | native table ≤14 rows, highlight rows | 40 + table |
| 16 | `text_matrix` | several items × several facets | numbered rows × 2–3 columns (McKinsey dense matrix) | 160 |
| 17 | `kpi_tiles` | headline numbers | 2–4 KPIs, each with a comparison value | 50 |
| 18 | `process_steps` | how something works | 3–6 steps, chevrons | 80 |
| 19 | `timeline_roadmap` | what happens when | 3–5 phases with dates and owners | 100 |
| 20 | `gantt_plan` | workplan | native Gantt from stacked bars | 60 |
| 21 | `tornado_sensitivity` | what drives the outcome | tornado chart, ≤8 drivers | 60 |
| 22 | `two_exhibits` | two views prove one point | two charts or chart + table, shared title | 80 |
| 23 | `options_evaluation` | option B is best | options × criteria, recommended option highlighted | 120 |
| 24 | `case_example` | a peer shows it works | peer, what they did, the result (with numbers) | 110 |
| 25 | `recommendations_now_next_later` | what to do | 3 columns, owners and dates | 110 |
| 26 | `quote_voice` | stakeholders say … | ≤3 quotes with attribution | 60 |
| 27 | `appendix_divider` | — | — | exempt |
| 28 | `end` | — | contact / next steps | exempt |

Selection rule: the archetype is chosen to prove the title, not at random. The same archetype may appear at most 3
times in a row (P15). Stock imagery is never used unless the user supplied it.

## 4. Renderer contract (what the deterministic renderer guarantees)

Input: the approved storyline (`storyline.json`), the slide JSON list, the template (`.potx`/`.pptx`) and
`rules.yaml`. Output: `deck.pptx`, `provenance.json`, `storyline.json`, `brief.json`, and a QA report.

1. **Template first.** Every slide uses a template layout. The title goes in the title placeholder. Body content goes
   in placeholders, or on shapes snapped to the template grid (12 columns between the master body placeholder's edges;
   gutter from the template, else `layout.gutter_in`). Edges snap within 0.05 in (P7, P8).
2. **Theme only.** Colours are written as `schemeClr` slots, never `srgbClr` (P6). Fonts are `+mj-lt`/`+mn-lt` theme
   references or the allowlist (P4). Highlight = `accent1`; muted = `tx2` at a lumMod. Charts use the same slots.
3. **Native objects.** Charts are chart parts with an embedded workbook, and every series references cells in it
   (P2). Waterfalls, Gantts, Marimekkos and tornados are built from native bar/column charts. Tables are `a:tbl` (P3).
   Harvey balls and 2x2s are native shapes, grouped at most one level deep (P17).
4. **Fit by cutting, never by shrinking.** Text is measured with font metrics before writing. If it does not fit at
   the layout's size, the renderer reports a `content` failure with the word budget. It never sets a size below
   12 pt body / 9 pt footnote, and never relies on `normAutofit` (P5).
5. **One look per element (P19).** The title box, title size and face, content left margin, source line position,
   slide number and chart label size are identical on every content slide. This is a renderer guarantee and the
   validator checks it. References hold title size constant on 82–99% of pages; the forge rebuild holds it on 100%.
6. **Source line.** It sits at the same height in the bottom band of every data slide, in 9–10 pt, and starts with
   "Source:" (P13).
7. **Identification.** Each slide's `cSld/@name` carries `arch:<archetype> node:<node>` (P12, P15). Icons carry alt
   text `icon:<set>:<name>:<meaning>` (P9, P18). Intended overlays are named `ov_*` (P8).
8. **Accessibility.** Alt text goes on every picture and chart, and shape order follows reading order: title first,
   then top to bottom, left to right (P18).
9. **Nothing locked**, no text as images, no gradients, decorative lines or emoji (P9, P17).
10. **Gaps are visible.** An unsourced number becomes `[GAP: what is missing]` in the text and a P13 failure.

## 5. Workbook block schema

Full JSON Schema: `app/engines/forge/schemas/workbook_block.schema.json`. It describes the model's rows and
inputs together with sources, reported totals, checks, circularity policy, a dashboard and recompute targets. The model writes keys and formula templates (`={revenue@prev}*(1+growth)`). The builder assigns
cells, named ranges, styles, number formats, frozen panes, the Checks sheet and the cover's `MasterCheck`.

Sheet roles: Cover, Inputs (blue constants, each with a source), Calc (IS/BS/CF and schedules), Outputs, Dashboard
and Checks. A role can be set explicitly with a defined name `_role_<Sheet>`. Otherwise it is inferred from the
sheet name (`xlsx.sheet_roles`).

## 6. Rules and thresholds

Every threshold is in `app/engines/forge/rules.yaml`. Summary:

**PowerPoint (validate_pptx.py)**

| Rule | Complaint fixed | Check (threshold key) |
|---|---|---|
| P0 | "PowerPoint found a problem with content … repair" | every XML part valid against the ECMA-376 Transitional schemas (Office's own forms accepted where [MS-OI29500] differs); relationships resolve; content types; unique shape and slide ids; table rows match the grid; embedded chart workbooks checked too (`ooxml_check.py`) |
| P1 | export breaks layouts | LibreOffice render; every rendered text span inside a text box (`render.box_tolerance_in` 0.08) |
| P2 | charts become images | chart parts with embedded workbook; series refs resolve; cache = cells; no chart-named pictures |
| P3 | tables become images | native `a:tbl`; no table-named pictures; OCR of images when an OCR engine is configured |
| P4 | fonts substituted | typefaces ⊆ theme + allowlist; ≤2 families; ≤3 sizes per role; embedding if required |
| P5 | text overflows | font-metric fit (`text_fit.*`); body ≥12 pt, footnote ≥9 pt, title ≥20 pt after autofit scale |
| P6 | off-brand colours | zero `srgbClr` in slides and charts outside `colors.srgb_exceptions` |
| P7 | master ignored | title placeholder filled; master geometry matches slide size; edges on grid (0.05 in) |
| P8 | inconsistent spacing | left edges ≤ grid columns; inside margins; no undeclared partial overlaps |
| P9 | mixed icons, clutter | ≤4 icons, one approved set; no gradients, unconnected lines, emoji |
| P10 | overfilled slides | body words ≤80 (per-archetype caps), ≤5 bullets, ≤2 levels |
| P11 | topic-label titles | verb present, ≤15 words, ≤2 lines |
| P12 | storyline jumps/repeats | every slide maps to an approved node; title cosine similarity ≤0.8 |
| P13 | hallucinated numbers | every number has provenance; gap markers flagged; source line on data slides, bottom band |
| P14 | text vs chart disagree | quoted numbers match series values (0.6% tolerance) or derived values (differences, % changes, CAGR, shares of ≥3-item compositions) |
| P15 | generic AI look | archetype declared; ≤3 consecutive same archetype |
| P16 | too many slides; missing opening or closing | main section count = brief; when the brief asks for an opening (title/cover/agenda) or closing (closing slide, next steps, conclusion, "end with …") slide, slide 1 is a cover and the last slide is a `closing` (or `end`) slide |
| P17 | not editable | group depth ≤1; no forbidden locks |
| P18 | inaccessible | alt text on pictures/charts (a file name doesn't count); reading order |
| P19 | inconsistent fonts and placement | title box, title size and face, body face, content margin, source line, chart label size identical across content slides |
| P20 | big or disguised empty space ("looks AI-generated") | on the render, the largest empty block in a content slide's body is ≤20% of the body (`whitespace.max_empty_share`; chart plot areas count as content; McKinsey content pages median 6%, p90 12%). From the file: table rows ≤0.25in taller than their text (`max_row_air_in`); bars ≤0.6in and columns ≤1.2in thick (`max_bar_in`, `max_column_in`). Before render: fill targets per archetype (`build.fill_checks`) |

**Excel (validate_xlsx.py)**: recalculates headless in LibreOffice first.

| Rule | Check |
|---|---|
| X0 | the file opens in Excel without a repair prompt (same check as P0) |
| X1 | zero typed-in numbers on calc/output/check sheets (period headers excepted; input-styled cells allowed) |
| X2 | formula literals ⊆ {0, 1, 2, 4, 12, 52, 100, 365, 1e3, 1e6, 1e9} |
| X3 | no plug-labelled lines; assets − liabilities − equity = 0 in every period |
| X4 | historical (…A / Actual) constants carry a comment or Source column; tie-out rows = 0 |
| X5 | zero error values after recalc; every defined name resolves; no undefined names in formulas |
| X6 | Tarjan cycle detection on the cell graph; cycles fail unless iterative calc and a circuit-breaker name exist |
| X7 | key outputs equal the engine's independent Python recompute (`recompute.json`, 0.1%) |
| X8 | one R1C1 pattern per forecast row (first period may differ) |
| X9 | output sheets and workbook charts hold no pasted values |
| X10 | one number format per row, negatives in parentheses, unit in label, colour convention (input blue, link green, formula black), frozen panes, no merged data cells |
| X11 | before/after diff of every changed cell (`--before`) |
| X12 | a Checks sheet; `MasterCheck` = AND of all checks |
| X13 | no externalLink parts, no external references, no VBA |

## 7. Measured gaps between the engine today and this spec

From `scorecard.csv` and the one-shot McKinsey rebuilds (`mckinsey-rebuild-test.md`):

- The current slides renderer (`engines/slides/renderer.py`) draws on the Blank layout with hardcoded RGB, so it
  fails P6, P7 and P15 by construction. It needs to become the template-placeholder renderer described in §4.
- It writes no source line (0% of rebuilt slides against 47% median in references) and no provenance map, so it
  fails P13.
- Its type is Calibri with 13 body sizes. References use one sans family (Arial in 15 of 17) at 10–14 pt body,
  mostly 24–25 pt titles and 8–9 pt footnotes.
- It is already strong on P19 consistency (100% title size and position) and on action titles (73–90% with verbs,
  against a reference median of 58%).
