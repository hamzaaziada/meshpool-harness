# Teardown: ginlix-ai/LangAlpha (pptx skill)

Pinned commit `d0c893ce62fa276d7f11b904b1ace55d143d8810`. The relevant part is `plugins/langalpha_deliverables/skills/pptx/` (`SKILL.md`, `scripts/check.py`, `scripts/render.py`, `scripts/extract.py`). The rest of LangAlpha (agent platform) was not exercised. Tested 2026-11-15.

Labels: **[observation]** = seen in this test run; **[inference]** = my reading, not directly tested; **[unknown]** = could not be established.

## Tested setup

- Scripts copied to the scratchpad; isolated venv (python-pptx, PyMuPDF, Pillow). `pptxgenjs@4.0.1` installed with npm into a scratchpad `node_modules` (the skill expects a global install).
- LLM run: `deepseek-v4.1-flash` via the meshpool gateway, one call (8.1k prompt / 4.1k completion tokens). The whole `SKILL.md` was the system prompt; the user prompt was the Costco brief. The model returned the outline and `build_costco.js`, as the skill's workflow asks.
- Ran `node build_costco.js`, then the skill's own `check.py --strict` and `render.py --montage`, then forge `validate(render=True)` and a LibreOffice render.
- Output: `scratchpad/td/out/langalpha/costco_board/costco_board.pptx` (5 slides, 2 native charts, 1 native table, notes on every slide).

## What it does well

- [observation] **The best output of the LLM-driven runs.** Conclusion titles, one consistent geometry, a kicker line under each title, takeaways beside each chart, a source line on every numeric slide, speaker notes, and 2 native charts plus 1 native table. Forge P19 (consistent placement) and P5 (font minimums) passed.
- [observation] The prompt is very strong for its size (~29 KB). It specifies:
  - one band geometry for all content slides (title y 0.45, kicker 1.05, rule 1.42, content 1.75, footer 6.95);
  - a type scale with hard floors (nothing <10pt, tables ≥14pt);
  - one accent colour, with green/red reserved for gain/loss;
  - a character budget derived from the overflow model ("28pt bold across 12.13 in ≈ 56 chars");
  - explicit pptxgenjs pitfalls: never `fit:"shrink"`, because pptxgenjs writes `normAutofit` without `fontScale`, so LibreOffice shrinks and PowerPoint does not.
  A cheap model followed all of it on the first try.
- [observation] `check.py` is a careful file auditor:
  - resolves inherited font sizes through run → paragraph → list style → layout → master (`stated_points`, `run_points`, `inherited_styles`);
  - judges autofit text at its drawn size (`autofit()` reads `fontScale`/`lnSpcReduction`);
  - measures rotated and grouped shapes by their painted box (`painted_box`, `group_spin`, `walk`);
  - detects a filled shape that fully hides earlier text (`occluded`, `paints_over`);
  - checks title drift across slides (`title_of`, `title_position`) and placeholder leftovers (`Click to add`, `Lorem`, `TODO`).
  Its CLI rejects unknown flags, so a misspelt `--strict` cannot silently pass.
- [observation] `render.py` writes the new render set beside the old one and swaps it in only when complete (`previous_render_kept` on error), and maps PDF pages back to slides when LibreOffice drops hidden slides (`slide_of_page`).

## Where it breaks

- [observation] **`check.py --strict` passed the deck with zero findings, but forge found real defects:**
  - P0: pptxgenjs writes `c:axId` in an order the chart schema rejects.
  - P1: on slide 4 the table's last row ("Percent of shares outstanding", wrapped to 2 lines) renders past the table frame.
  - P20: 22% and 33% empty blocks on slides 4 and 5.
  - P17: text box laid over a shape.
  - P18: no alt text on charts.
  The overflow check is a deliberately loose estimate (0.5 em per glyph) and never looks at the render.
- [observation] Content errors the skill cannot catch:
  - The outline said "$20.21B … roughly 2.2 years of capex"; the correct figure is 3.1. The slides happened to use 3.1.
  - It labelled net income minus capex as "free cash flow". With no operating cash flow given, that is a mislabel.
  - Nothing in the skill checks numbers against inputs.
  - Forge P14 also missed both (it only compares text to chart series).
- [observation] The chart on slide 3 renders with odd value-axis ticks (−1, 4, 9, 14 …) in LibreOffice: pptxgenjs auto-scaling with no `valAxisMinVal`.
- [observation] Forge-side false positives this deck exposed:
  - P11 "topic label" fired on "Capex already absorbs 70 percent of net income" and "A $5B buyback retires about 1 percent of shares". Forge's `_has_verb` (`validate_pptx.py`) uses a closed verb list that lacks "absorb" and "retire".
  - P14 fired because revenue ($303.15B) is quoted beside a chart that does not plot revenue.
- [unknown] Whether PowerPoint repairs the pptxgenjs chart XML. pptxgenjs is widely used, so probably not ([inference]), but this was not tested.

## What we take

1. [observation] **The SKILL.md writing pattern**: geometry bands, a type scale with floors, a char budget derived from the measurer, and tool pitfalls, all as tables. It made a flash model produce clean output first time. Forge's content-model prompt (in `engine.py` / `brief.py`) should state per-archetype character budgets in the same "N chars at this size" form, computed from `common.py` metrics, not as abstract word caps.
2. [inference] **Inherited font-size resolution and autofit-scale handling** (`check.py::inherited_styles`, `stated_points`, `autofit`). Forge's `measure_pptx.py` reads `normAutofit fontScale` (L267). It should also resolve sizes through layout and master list styles, so third-party and template decks get correct P5 sizes. Changes `app/engines/forge/measure_pptx.py`.
3. [inference] **Occlusion check**: a filled shape later in paint order covering text (`check.py::paints_over`, the `occluded` finding). Forge P17 checks text laid over shapes but not shapes hiding text. Changes `validate_pptx.py` (P8/P17).
4. [inference] **Atomic render swap and a page→slide map** in the render step (`render.py`). Forge's `_render_check` should map pages to slides when hidden slides exist. Changes `validate_pptx.py::_render_check` / `measure_pdf.py`.
5. [observation] Forge fixes the tests exposed: widen `_has_verb` with a morphological fallback (any `-s`/`-ed` token after the subject noun phrase), and scope P14 to numbers that name a plotted series or category. Changes `validate_pptx.py`, `rules.yaml`.

## Licence note

Apache-2.0 (per the ledger). Ideas and scripts can be adapted with NOTICE/attribution. `check.py` is self-contained and could be vendored as a second-opinion auditor if attribution is kept.
