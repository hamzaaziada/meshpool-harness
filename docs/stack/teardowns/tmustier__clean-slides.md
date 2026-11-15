# Teardown: tmustier/clean-slides

Pinned commit `c67a56a31cba898eb6e76314f80d6b3301d71975` (package `clean-slides` 0.1.0, CLI `pptx`). Tested 2026-11-15.

Labels: **[observation]** = seen in this test run; **[inference]** = my reading, not directly tested; **[unknown]** = could not be established.

## Tested setup

- Isolated venv (`uv`, Python 3.12), `pip install -e ".[test]"`. No network, no LLM.
- Layout library driven by YAML. I wrote 3 specs myself (`scratchpad/td/out/clean/s1.yaml`, `s2.yaml`, `s3.yaml`):
  - Executive summary: a 2-column table with numbered findings as row headers.
  - Bar chart with takeaways: a `2/3` layout with chart cells (`charts:` block and `fin-1..3` cell refs) and a `sidebar:` of takeaways.
  - 5-column comparison table.
- Ran `pptx validate`, `pptx verify`, then `pptx generate s1.yaml s2.yaml s3.yaml -o clean_costco.pptx`. Then ran forge `validate(render=True)` and a LibreOffice render.
- First attempt, without `pptx init`: no titles were rendered and the sidebar landed off-slide (forge P1 `render_off_slide`). With `pptx init` (as the README says), the bundled template was used and the output is the one assessed below.

## What it does well

- [observation] The best-looking tables of the eight repos. Row headers are coloured and bold. There is a header rule, dividers between rows, equal or content-aware column widths, and a consistent left edge. Titles sit in real title placeholders (forge P7 found no `no_title_placeholder` after `init`). Fonts come from the template theme.
- [observation] Template-first design. `pptx init` copies a template plus `config.yaml`. `pptx layouts` lists each layout's placeholders and its **content areas** (primary 12.1×5.2 in; for `2/3`, primary 7.6 + secondary 3.8 in). `pptx init-config` generates that config from any PPTX. Forge's template loader does something close to this; clean-slides makes the content-area map explicit and user-editable (`docs/TEMPLATE-CONFIG.md`).
- [observation] Fitting is a constraint solver, not shrinking: `clean_slides/solver.py::ConstraintSolver.solve` runs column sizing, then row sizing, then verification. `sizing.py::ColumnSizer.size` gives wider columns to cells that need more lines, and `verification.py::LayoutVerifier` reports overlap, bounds and text fit before writing. `pptx verify` runs the solver without generating a file.
- [observation] Chart cells (`docs/CHART-CELLS.md`): native bar charts embedded in table rows, so a row label, a text cell and a bar can share one grid. This is a clean answer for "table with inline bars" exhibits.
- [observation] Generation warned about the sidebar overflow ("sidebar content overflows by ~1.7in") on the first, un-initialised run.

## Where it breaks

- [observation] **P0 failures.**
  - The chart part has negative `c:axId` values (`-2068027336`). These come straight from python-pptx's chart XML template (`pptx/chart/xmlwriter.py` L469) and are invalid against the `xs:unsignedInt` schema. Forge already rewrites them (`common.py` L229), so this is the trap forge avoids.
  - A duplicate shape id `1008` on the chart slide (the chart's `cNvPr` collides with the next text box).
  - [unknown] Whether PowerPoint actually shows a repair prompt for either issue; PowerPoint was not available here.
- [observation] "Tables" are text-box grids with line shapes, not `a:tbl` (slide 1: 10 text boxes + 4 lines). Forge P3 passed them; forge has no fake-table detection.
- [observation] Chart cells render with leader lines from the middle of each bar to the label (LibreOffice render). Bars are 0.9 in thick (forge P20 `oversized_bars`), and the chart uses only half the primary area, leaving a 36% empty block (P20).
- [observation] Rows are over-tall for short text: the exec summary and comparison table show visible air under each row. Forge P20 flagged a 22% empty block on slide 1.
- [observation] There is no source line. That is outside the tool's scope (forge P13 `no_source_line`). There is no alt text on the chart (P18).
- [observation] `pptx insert` (merging decks) refuses any slide that has a chart relationship ("supports text and hyperlinks only"). Multi-slide output has to come from a single `generate` call with several YAML files.
- [observation] Without `pptx init`, `generate` silently produces title-less slides. It prints only a hint, not an error.

## What we take

1. [inference] **Content-aware column widths plus a pre-flight verify pass for tables** (`clean_slides/sizing.py::ColumnSizer`, `solver.py::ConstraintSolver`, `verification.py::LayoutVerifier`). Forge's table archetypes (`table_highlight`, `text_matrix`, `options_evaluation`) would gain from sizing columns by the line count each needs, rather than fixed proportions. Changes `app/engines/forge/parts.py` (table builder) and `common.py`/`density.py` (measurement).
2. [inference] **An explicit content-area map per template layout** (`pptx layouts`, `template_config.py`, `docs/TEMPLATE-CONFIG.md`), including secondary areas for 2/3 split layouts. Forge's template loader (`template.py`) could emit and cache this as JSON so archetypes place into named areas. Changes `app/engines/forge/template.py`.
3. [inference] **Chart cells** (a native bar inside a table row) as a forge part for `table_highlight` / `harvey_ball_comparison` variants. Changes `parts.py` and `render.py`. Lower priority.
4. [observation] Regression fixture: this repo's chart output is a good negative test for forge's axId rewrite and duplicate-id checks.

## Licence note

MIT (LICENSE at the pinned commit; per the ledger). Code can be adapted with the copyright notice kept.
