# Teardown: knorq-ai/pptx-mcp-server

Pinned commit `d5c41228fcc81e7a0ffdd7b870500a1d25136d0f` (package `pptx-mcp-server` 0.5.1). Tested 2026-11-15.

Labels: **[observation]** = seen in this test run; **[inference]** = my reading, not directly tested; **[unknown]** = could not be established.

## Tested setup

- Isolated venv (`uv`, Python 3.12), `pip install -e ".[mcp,validation,test]"`. No LLM.
- I drove the library directly rather than over MCP: `engine.composites.build_deck(path, slides=[...])` with 3 slide specs (`scratchpad/td/scripts/knorq_build.py`):
  - `card_grid` executive summary (4 cards).
  - Native `chart` (column) plus a `textbox` and `bullet_block` takeaways rail.
  - Native `table` comparing the three options.
- Ran the repo's validator `engine.validation.check_deck_extended(Presentation(...))` with `font_source="heuristic"` and `"real"`. Then ran forge `validate(render=True)` and a LibreOffice render.

## What it does well

- [observation] Real native objects. 1 chart part with an embedded workbook and 1 `a:tbl`. The table has a dark header, alternate-row rules, and auto right-alignment for numeric-looking columns.
- [observation] A single structured spec (`build_deck`) with strict validation: unknown element types and missing required keys raise `EngineError` with `code`, `parameter`, `hint` (`engine/composites.py::_dispatch_element`, `_validate_slide_spec`). This is the cleanest error contract of the libraries tested. It is model-facing and actionable, like forge's `message` + `fix_scope`.
- [observation] The validator design is broad and well factored (`engine/validation.py`):
  - overlaps with container exemption: `check_slide_overlaps`, `_is_container`.
  - text overflow with an optional real-font measurement through fontTools: `_estimate_frame_needed_height_real`, `_real_wrap_lines`.
  - unreadable text: `check_unreadable_text`.
  - text projected into a divider line: `check_divider_collision`.
  - inconsistent gaps between stacked shapes: `check_inconsistent_gaps`, `_axis_groups`.
  - title wrap: `check_title_wrap`.
  - Every finding carries `severity`, `category`, `suggested_fix`.
- [observation] It explicitly states which scripts its text metrics are calibrated for: the README table and `tests/test_calibration.py`. Latin is ±10%, CJK ±15%, Hangul/Arabic/Thai are marked unsupported. Forge should publish its error bars the same way.
- [observation] `engine/flex.py` is a small CSS-flex model (`fixed` / `grow` / `content` sizing, min/max clamps, redistribution) that returns boxes and leaves drawing to a callback. Separating placement from drawing like this suits an archetype renderer.

## Where it breaks

- [observation] Its own validator reported **0 errors, 0 warnings** in heuristic mode. Forge found 62 failures. Real problems it missed: a 58% empty block under the table (P20), table rows stretched more than 0.25 in past their text (P20), 10pt card body text (P5), chart labels at 8–9pt, and body text in 6 sizes (P4).
- [observation] `font_source="real"` needs explicit `font_paths`. With none supplied, every shape fell back to the heuristic with a `font_not_measured` warning (19 warnings), so the real-font path did nothing out of the box.
- [observation] `card_grid` fills the whole body zone with 4 shadowed cards holding one line of 10pt grey text each. Most of each card is empty, which is the "disguised empty space" forge P20 targets.
- [observation] The chart showed a legend even though the spec set `legend_position: "none"`. Data labels were 8–9pt.
- [observation] The bullet block's wrapped second line has no hanging indent and uses paragraph spacing like a new item.
- [observation] P0 failure: the chart has negative `c:axId` values. These are inherited unchanged from python-pptx's XML writer (`pptx/chart/xmlwriter.py` L469). Forge already rewrites them in `common.py`.
- [observation] All colours are literal hex from a theme dict (`theme.py` `MCKINSEY` etc.), not `schemeClr` (forge P6). Slides use Blank layout + text boxes (P7).

## What we take

1. [inference] **A structured error envelope for the content model**: `code` + `parameter` + `hint` (`_envelope.py`, `EngineError` in `engine/pptx_io.py`). Forge already has `message` + `fix_scope`. Adding `parameter` (the JSON path into the slide spec, e.g. `slides[3].panels[1].bullets[0]`) would let the repair loop edit the exact field. Changes `app/engines/forge/validate_pptx.py` (`F()` failure builder) and `engine.py` (repair prompt).
2. [inference] **Divider collision and gap-consistency checks** (`validation.py::check_divider_collision`, `check_inconsistent_gaps`). Forge's P8 checks margins and overlaps but not uneven vertical rhythm in a stack of shapes. Changes `validate_pptx.py` (P8/P19).
3. [inference] **Published calibration of the text measurer per script**, enforced by a test (`tests/test_calibration.py`). Forge's `common.py`/`density.py` measurement should carry a calibration test against rendered LibreOffice line counts. Changes `app/engines/forge/common.py` plus a new test.
4. [observation → do not take] The `card_grid` "fill the zone" sizing. It produces exactly the empty-card look forge P20 forbids.

## Licence note

MIT (per the ledger; `pyproject.toml` says `license = "MIT"`). Code can be adapted with attribution.
