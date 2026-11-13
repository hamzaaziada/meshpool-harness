# Hebbia deck anatomy (measured spec of their output)

**Status: unmeasured. No Hebbia or FlashDocs output file was supplied.** Every Hebbia item below is *unknown*
until one is. Nothing here comes from Hebbia's servers, and nothing in this document copies their templates, text
or code.

The measurement is fully automated, so filling this document in takes one command per supplied file:

```bash
cd app
npx -y rea-agents@latest inspect-artifact /abs/path/hebbia.pptx --format json > hebbia.rea.json   # step 1 inventory, Evidence ID
python -m engines.forge.measure_pptx /abs/path/hebbia.pptx --out hebbia.anatomy.json               # step 2 anatomy
python -m engines.forge.validate_pptx /abs/path/hebbia.pptx --out hebbia.qa.json                   # scored against P1-P19
python -m engines.forge.validate_xlsx /abs/path/hebbia.xlsx --out hebbia.xlsx.qa.json              # step 3 + X1-X13
```


## What gets measured, and where each answer lands

| Brief item (step 2) | Field in `*.anatomy.json` | Hebbia value |
|---|---|---|
| Generator name and version | `summary.generator`, `summary.generator_version`, `inventory.generator` | unknown |
| Member list, SHA-256, content types, core props | `inventory.members[]`, `inventory.content_types`, `inventory.core` | unknown |
| Masters, layouts, layout per slide | `summary.masters`, `layouts`, `slides[].layout` | unknown |
| Body in placeholders vs free text boxes | `summary.body_in_placeholders_share`, `summary.titles_in_placeholder_share` | unknown |
| Theme colour slots, major/minor fonts | `theme.colors`, `theme.major`, `theme.minor` | unknown |
| `schemeClr` vs `srgbClr` ratio | `colors.scheme_refs`, `colors.srgb_refs`, `colors.scheme_share` | unknown |
| Typefaces used, embedded fonts | `typefaces`, `embedded_fonts` | unknown |
| Font-size histogram by role | `font_size_hist.{title,body,footnote,chart_labels}` | unknown |
| Every shape's position and size | `slides[].shapes[].{x,y,w,h}` (inches; EMU ÷ 914400) | unknown |
| Margins, column grid, gutters | derived: `slides[].left_edges`, `slides[].consistency.content_left` | unknown |
| Distinct left edges, overlaps | `summary.avg_distinct_left_edges`; P8 failures | unknown |
| Native charts vs pictures; series → embedded cells; chart types | `slides[].charts[]` (`types`, `series[].ref`, `embedded`); P2 | unknown |
| Native tables vs images | `summary.native_tables`; P3 | unknown |
| Words, bullets, bullet depth per slide | `slides[].{body_words, bullets, bullet_levels}` | unknown |
| Title length, action vs topic | `slides[].title_words`; P11 | unknown |
| Source line presence and position | `slides[].source_line.{present,y}` | unknown |
| Images, icons, icon-set consistency | `slides[].pictures`, `slides[].icons[].set` | unknown |
| Autofit settings, estimated overflow | `summary.autofit`; `shapes[].overflow` | unknown |
| Notes, alt text, slide numbers, sections, group depth | `summary.notes_share`, P18, `summary.slide_numbers_share`, `sections`, `summary.max_group_depth` | unknown |
| Cross-slide consistency | `slides[].consistency`; P19 | unknown |

## Step 4: hypotheses about the generation pipeline (all *inference*; each needs the listed evidence)

Hebbia acquired FlashDocs. FlashDocs' public positioning is template-based generation: decks built into a
customer's own templates. That is context, not measurement.

| # | Hypothesis | Confirms it | Refutes it |
|---|---|---|---|
| H1 | Slides are filled into placeholders of a supplied master, not drawn freely | `titles_in_placeholder_share` ≈ 1; `layouts_used` spread across several layouts; `scheme_share` high | Blank layout everywhere; free text boxes; high `srgb_refs` (the pattern our own current renderer shows) |
| H2 | Generated with python-pptx or a similar library on top of a template | `app.xml` keeps the template's original generator string (python-pptx's default template reports "Microsoft Macintosh PowerPoint 14.0000", observed in our own deck, EV-f3e822db-generator); python-pptx-style shape names ("TextBox 3", "Chart 5"); member order with `ppt/slides/slideN.xml` appended after layouts | `app.xml` shows a fresh PowerPoint version and consistent PowerPoint shape naming ("Title 1", "Content Placeholder 2"); Google Slides names ("Google Shape;…") |
| H3 | Charts are native chart parts built from data | `ppt/charts/chartN.xml` + `ppt/embeddings/*.xlsx`, series `numRef` into the embedded sheet | charts as `p:pic` (PNG/EMF) with chart-like names |
| H4 | Content is cut to fit in a preprocessing step (word budgets), not by autofit | no `normAutofit fontScale`; tight word counts per layout; few overflows | `normAutofit fontScale` < 100% on many shapes; `spAutoFit` boxes that grow past their layout |
| H5 | Tables are native | `a:tbl` graphicFrames | pictures with table-like names or OCR text |
| H6 | Speaker notes carry provenance / citations | non-empty notes with document references | empty notes |

The rows for repeated shape names, relationship-ID order, layout reuse and member order are produced by
`measure_pptx` (`shape_name_patterns`, `inventory.member_order`) and REA (`inspect-artifact` Evidence) as soon as a
file exists.

## Our own output, measured the same way (the comparator we do have)

Meshpool slides engine sample deck, sha256 `f3e822db…` (REA inventory Evidence `ev_c358009fe70e…`):

- generator string "Microsoft Macintosh PowerPoint 14.0000", inherited from python-pptx's default template
  (observation, EV-f3e822db-generator);
- 10 slides, 1 master, 11 layouts available, **all 10 slides on "Blank"**; 0% of titles in placeholders
  (observation, EV-f3e822db-layouts_used, EV-f3e822db-titles_in_placeholder_share);
- theme fonts Calibri/Calibri; 13 body sizes from 12 to 96 pt (observation, EV-f3e822db-font_size_hist);
- 36% of colour references are theme slots, 64% hardcoded RGB (observation, EV-f3e822db-scheme_color_share);
- 1 native chart with an embedded workbook, 1 native table, 1 picture (observation, EV-f3e822db-native_charts);
- 35 body words per slide on average, max 61; source lines on 20% of slides (observation, EV-f3e822db-avg_body_words);
- no autofit anywhere; renderer measures text itself (observation, EV-f3e822db-autofit).

So our current renderer draws freely on a blank layout. If H1 holds for Hebbia, that is the largest architectural
difference, and engine-spec §4 closes it.
