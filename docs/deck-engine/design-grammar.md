# Forge design grammar

This is the design system every forge deck gets, whether the content comes from a reference page or from a
one-line brief. It is measured from McKinsey reference pages (DC Transportation and Warehousing Sector Analysis,
pages 3, 4, 8 and 11) and enforced in code by `app/engines/forge/render.py`. The model never chooses fonts,
sizes, colours or positions.

## The rules

| Element | Rule | Measured on the reference |
|---|---|---|
| Title | Bold serif (Georgia; Gelasio has the same metrics), near-black, 25 pt, left-aligned, at most 2 lines. It steps down to 22 pt and then 20 pt before it may overflow | 25 pt Georgia-Bold on every page |
| Title rule | Full-width 0.75 pt black hairline under the title block (y = 1.42 in) | same |
| Exhibit subtitle | Regular sans, 18 pt, one line, directly under the title | 18 pt Arial |
| Structure | White space and hairlines only: a rule under column headers, grey 0.5 pt rules between rows, a rule above the source. No cards and no tinted boxes, except the argument half-panel | no cards anywhere |
| Emphasis | Number phrases are bold accent blue inside the sentence; `**marked phrases**` are bold; a bold phrase containing a number is blue; ordinals take superscript suffixes (3<sup>rd</sup>) | same |
| Blocks | A bold lead sentence, then small accent-blue square bullets | same |
| One highlight | One coral table row, one cyan bubble (its label in cyan), or one navy "so what" box with an arrow pointing at the evidence | same |
| Matrix rows | Bold serif numeral (20 pt), thin line icon in a thin circle (Lucide, one set), two-weight label | 20 pt Georgia numerals |
| Type scale | Dense pages 10 pt (matrix, heat table), 12 pt tables, 14/12 pt for argument text, 14 pt column heads, 8 pt footnotes, 9 pt source | as measured per page |
| Line pitch | Exact points (12 pt text on a 13 pt pitch), so PowerPoint and LibreOffice lay text out identically | 1.08 × size |
| Grid | 24 columns between 0.6 in and 12.73 in, 0.125 in gutters | fine grid |
| Footer | Footnotes (in the label column on dense matrices), rule, source line, firm label and page number | same |
| Fill | Content fills the slide; the renderer never fakes it. Type steps up the scale (10 → 12 pt dense, 12 → 14 pt argument) before any gap opens; gaps are capped (0.3 in between matrix rows); table rows keep their natural height; bars and columns keep a set thickness (gap width, not a stretched frame). The layout follows the amount of content: a short table or a chart with few points sits beside its argument, the takeaway column narrows when its text is short, a long series runs full width over a takeaway band. Thin content goes back to the content model before rendering (fill targets per archetype) and again after rendering (P20); a slide without the evidence to fill it is merged | McKinsey content pages: largest empty block median 6%, p90 12% of the body |
| Opening and closing | Cover: accent bar, 36 pt serif title, subtitle, hairline, date, firm. Closing: numbered next steps (action, detail and milestones, owner and timing) over the full body. Required when the brief asks for them | — |
| Colour | Theme slots only: text, navy, emphasis blue, coral highlight, cyan focus, mid blue, rule grey, muted grey | — |

## What keeps it from breaking on arbitrary content

1. **Schema gate.** The common shapes models produce (cell objects, bare strings) are normalised. A slide that
   still cannot meet its archetype gets precise repair messages; if repair fails, it falls back to a faithful
   simpler layout rather than rendering blank.
2. **Chart sanity.** A one-bar chart, mixed units in one chart, ranks drawn as quantities, or a short line chart
   become a stat stack of big emphasised numbers (or a full-width argument), never a misleading chart.
3. **Fit by measurement.** Every text block is measured with real font metrics (variable fonts at the right
   weight). Sizes step down only to the archetype's floor, and longer content carries onto a "(continued)"
   slide, so nothing ever runs off the page.
4. **Label solver.** Bubble labels are placed by search: the nearest free spot that avoids other labels,
   bubbles, tick labels and other leaders, and never closer to a neighbour than to its own bubble. Coincident
   points share one label.
5. **The render check (P1).** LibreOffice renders the deck. Every rendered line must sit inside its own box, no
   text may collide with other text (table text included), tables must stay inside their frames, and nothing may
   leave the slide. Failures go back to the content model as fix instructions; whatever is left is flagged in
   the speaker notes.
6. **Dead-space check (P20).** Three parts. On the render, the largest empty block in each content slide's body
   must stay under 20%; over that goes back to the content model ("fill it with the evidence or implication the
   slide is missing, or merge it with its neighbour; never pad"). Empty plot area inside a chart is data, not
   layout. From the file, two disguise checks: no table row stretched more than 0.25 in past its text, and no
   bars or columns thicker than 0.6 / 1.2 in. Before rendering, fill targets (matrix cells with 2-3 bullets,
   chart slides with 3 interpreting bullets, short tables with their argument) send thin slides back first.
7. **Opens without repair (P0).** Every XML part is validated against the Office schemas, and relationships,
   content types and ids are checked, so PowerPoint should never show its "repair" prompt. The check found one
   defect, now fixed: python-pptx writes negative chart axis ids, which the schema does not allow.

## Evidence

- Exact rebuilds of the four reference pages (kept out of the repo because they reproduce the reference text)
  pass every render check.
- Content-only builds from a text brief, on two unrelated topics, pass the render check with zero overlap or
  overflow: `examples/content_only_bain.*` and `examples/content_only_dc.*`.
