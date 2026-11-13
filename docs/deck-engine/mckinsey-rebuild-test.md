# The McKinsey one-shot rebuild test

**Question:** given only the context and the idea of a McKinsey deck (no images, no layout), can our engine rebuild a
deck a partner would accept, in one shot?

**Answer on 13 Nov 2026: no.** The storyline and titles are close. The exhibits, sourcing, type and use of the page
are not. Details and the gap list are below. The test is repeatable, so rerun it after each engine change.

```bash
cd app
python -m engines.forge.rebuild_eval REF.pdf OUT_DIR --slides 12            # level "idea" (the real test)
python -m engines.forge.rebuild_eval REF.pdf OUT_DIR --level content         # easier: full page text, no layout
```

## Method

1. **Brief.** From the reference PDF's text layer only, one model call writes the context, the governing idea, the
   argument (3–8 points, not slide titles) and a fact list with sources. No images, page structure or slide titles
   reach the builder.
2. **Build.** The current slides engine (`engines/slides/pipeline.py`, DeepSeek V4.1 Flash) builds once, with
   12 slides requested.
3. **Score.** The same measurements run on both sides: `measure_pdf` on the reference and on the rebuild's
   LibreOffice render, plus `validate_pptx` (P1–P19) on the rebuild. It also computes storyline coverage (best title
   similarity per reference title), fact coverage (share of the reference's text-layer numbers found in the rebuild)
   and side-by-side contact sheets for a blind review.

Cost: 7–12 model calls per deck. Key spend went from $2.597 to $2.628 for both runs together, about $0.015 per rebuild.

## Results

| Measure | McK DC Transportation: reference | rebuild | McK Future of Boards: reference | rebuild |
|---|---|---|---|---|
| Evidence (sha256 prefix) | 26b116c1 | 3abcdd65 | 027980b2 | fdeda23b |
| Content pages / slides | 39 | 12 (10 content) | 14 | 12 (11 content) |
| Body words, median per page | 141 | 78 | 66.5 | 69 |
| Title words, median | 13 | 12.5 | 7 | 11 |
| Titles with a verb (heuristic) | 77% | 90% | 43% | 73% |
| Source line on page | **87%** | **0%** | **50%** | **0%** |
| Vector drawings per page, median (exhibit richness) | 16 (max 132) | 14.5 (max 64) | 8 | 11 |
| Fonts | Arial body, **Georgia** serif titles | Carlito (Calibri) only | Arial, Georgia titles | Carlito only |
| Type scale: title / body / footnote pt | 25 / 10–14 / 8–9 | 28 / 11–14 / 9 | 25 / 12–18 / 8–9 | 28 / 12–18 / 9 |
| Fact coverage (reference numbers found) | 251 numbers | 29 (12%; expected at 12 of 39 pages) | 10 (most exhibits are raster) | 10 (100%) |
| Forge rules failing (P1–P19) | n/a (PDF) | P4–P8, P10–P15, P18, P19 | n/a | P4–P7, P10–P13, P15, P18, P19 |

Visual comparison (contact sheets in the run folder, inspected directly):

- **Reference:** multi-panel exhibits on almost every page (bubble chart with annotations, ridership time series with
  event markers, choropleth maps, ranked tables with a highlighted row, a text matrix with numbered challenges).
  Each exhibit has a subtitle with unit and period, a numbered tracker ("1b:", "2a:") and a source line.
- **Rebuild:** clean and consistent, with action titles and a sensible flow (SCR → five challenges → peer cases →
  now/next/later). But the exhibits are single bar charts, KPI tiles and card grids that leave the bottom third
  empty. There are no exhibit subtitles or units and no source lines. One chart contradicts its title: a forecast
  "fall of 3%" is plotted as +2.0. P14 now catches this.

## Why it fails, and what fixes each gap

| Gap | Root cause (observed in code or output) | Fix | Rule that will prove it |
|---|---|---|---|
| No source lines although the brief names sources | the spec→render path has no `source` field on most slide types; the brief's sources are dropped | make `source` required on data archetypes in the schema; renderer writes it at a fixed height | P13 |
| Thin exhibits | the renderer has 10 slide types; bar/column/line/pie only | archetype library §3 of engine-spec (waterfall, scatter, bubble, Marimekko, text matrix, two_exhibits, table_highlight …) | P15 + blind review |
| Empty lower thirds | layouts are fixed-height and content isn't sized to the canvas | archetype layouts fill the body area; density floor (warn below 50% fill) | blind review (white_space) |
| Calibri, 13–16 body sizes | hardcoded theme in `slides/theme.py`, sizes shrink to fit | template loader + theme fonts; fit by cutting, not shrinking | P4, P5, P19 |
| Hardcoded colours, Blank layout | renderer draws free shapes with RGB | template-placeholder renderer | P6, P7 |
| Chart contradicts title | no text/chart agreement check in the old QA | `validate_pptx` P14 in the repair loop | P14 |
| No exhibit subtitle/unit | not in the old spec | `subtitle` + `chart.unit` in slide schema | P13 (unit), review |

## Bar to pass

A rebuild passes when all of these hold:

1. All content-scope rules pass after repair. P1–P19 are all `pass`, apart from `not_run` P3, which needs OCR.
2. Source line share ≥ the reference's.
3. Exhibit richness, measured as median drawings per page, is ≥ 70% of the reference's.
4. One sans body family and the reference type scale: the deck's own constant title size (24–25 pt in 12 of 17 references), 10–14 pt body, 8–9 pt footnotes.
5. The blind reviewer scores "consultant_made" ≥ 4 on the rebuild, on the same scale where the reference pages
   scored 4–5.
