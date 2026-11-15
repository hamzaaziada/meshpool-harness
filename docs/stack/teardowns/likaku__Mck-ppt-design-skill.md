# Teardown: likaku/Mck-ppt-design-skill

Pinned commit `49221997c05e0013c6d4f6ec4d9923e936ad058f` (package `mck-ppt` 2.4.1). Tested 2026-11-15.

Labels: **[observation]** = seen in this test run; **[inference]** = my reading of code or results, not directly tested; **[unknown]** = could not be established here.

## Tested setup

- Isolated venv (`uv`, Python 3.12) under the scratchpad, `pip install -e` of the pinned clone. Pure python-pptx + lxml, no network, no LLM.
- This is a layout library, so I built 3 slides myself with its `MckEngine` API (`scratchpad/td/scripts/likaku_build.py`):
  - `executive_summary(...)`: the headline bar plus 4 numbered findings.
  - `grouped_bar(...)` with a `summary=` takeaway bar: Revenue/10, Cash, Net income, Capex.
  - `data_table(...)`: special dividend vs buyback vs fund growth.
- I ran the repo's own gate (`references/scripts/gate_check.py`), then forge `validate()` with `render=True`, then rendered through LibreOffice to PNG and inspected the pages.
- Output: `scratchpad/td/out/likaku/likaku_costco.pptx` (+ `.forge.json`, `_png/`).

## What it does well

- [observation] The API is cheap for an agent to call. One method call gives one finished slide, with page number, source line and action title placed. All three slides built first time with no layout code.
- [observation] The executive summary slide is clean and dense: the numbered items line up, and the hairline separators and source line all sit on a consistent grid. Forge found no P1/P5 problem on it apart from the title-size rule.
- [observation] The repo gate is code, not opinion. `gate_check.py` writes `gate_result.json` with a program-derived `passed`. Exemptions are a hard-coded `ENGINE_BUG_WHITELIST` set (`peer_font_inconsistency`, `chart_legend_overflow`), not something the model decides. It flagged the same slide-2 overflow that forge's P1/P5 flagged.
- [observation] `mck_ppt/qa.py` has checks forge lacks:
  - `_check_text_line_collision`: text bottom vs a separator line under it.
  - `_check_peer_font_consistency`: shapes on the same y band must share a size.
  - `_check_chart_legend_overflow`.
- [observation] `full_cleanup()` (`mck_ppt/core.py`) strips every `p:style` and theme shadow/3D after saving, to stop PowerPoint's repair prompt. The output passed forge P0 (schema + package).
- [inference] `references/layout-matrix.yaml` gives per-layout capacity: max items plus title/body/cell char budgets. It is checked before rendering by `gate_check_s3.py`. That is the same idea as forge's `density.by_archetype`, but split per field (title 40, header 15, cell 40 …).

## Where it breaks

- [observation] **Charts are not charts.** `grouped_bar` draws bars as rectangles and tick labels as text boxes. The deck has 0 chart parts, so the data cannot be edited. The README's "native chart" screenshots come from `examples/freeform_showcase.py`, which calls python-pptx `add_chart` directly, not from the engine methods.
- [observation] **Tables are not tables.** `data_table` builds a grid of text boxes and hairlines (0 `a:tbl`). Row height is `avail_h / n_rows`, so 3 rows spread over the whole body. Forge P20 measured a 47% empty block on slide 3, and the render shows widely spaced rows with a blank bottom half.
- [observation] `grouped_bar` repeats the slide title as the chart sub-title (`add_text(..., title, ...)` at `engine.py` ~L1712). The duplicate wraps to 2 lines and overflows its 0.3 in box. The repo gate (69% overflow) and forge P1 both caught it.
- [observation] Every colour is a hard-coded `RGBColor` (forge P6: 26 srgbClr per slide). Every slide uses the Blank layout with free text boxes, so there are no title placeholders (P7). Titles are Georgia 22pt, which LibreOffice substitutes.
- [observation] Forge's P2 and P3 **passed** this deck. Forge has no check for a chart faked from rectangles or a table faked from a text-box grid. This is a forge gap, not a likaku strength.
- [observation] `gate_check.py` hard-codes `~/.workbuddy/skills/mck-ppt-design` as the engine path. It only worked because the package was pip-installed.
- [inference] The engine defaults include Chinese strings (`key_takeaway` left heading '协同机制分析', `insight_title='启示：'`, `toc` title '目录'). An English deck that does not override them will leak Chinese.

## What we take

1. [inference] **Per-field char budgets in a layout matrix**, checked before render (`references/layout-matrix.yaml`, `references/scripts/gate_check_s3.py`). Forge budgets words per slide (`density.by_archetype`). Adding per-slot budgets (title, panel heading, cell) to the archetype definitions would catch overflow before rendering. Changes `app/engines/forge/rules.yaml` (`density`) and `density.py`.
2. [inference] **Text-vs-separator-line collision and same-row font consistency** (`mck_ppt/qa.py::_check_text_line_collision`, `_check_peer_font_consistency`). Both are cheap geometric checks to add to `validate_pptx.py`, next to P8 and P19.
3. [observation → do not take] Shape-drawn charts and text-box tables. Forge's native-object rule (P2/P3) is right. Use this repo instead as a test fixture for a new P2/P3 check: "a cluster of ≥3 aligned rectangles with numeric labels" or "a grid of ≥3×3 text boxes" counts as a fake chart or fake table.

## Licence note

Apache-2.0 (LICENSE at the pinned commit; per `docs/stack/license-and-repo-ledger.csv`). The NOTICE file and per-file headers ("This file must be retained…") must be kept if code is copied. Ideas and checks can be reimplemented freely. Any verbatim code needs the NOTICE carried into forge.
