# Teardown: albertojb/mbb-ppt-generator

Pinned commit `74169bc5672d25a762e3dde3f96ee74380ace0d8` (package `mbb-ppt-generator` 0.8.1, module `mbb_ppt`). It is an Apache-2.0 fork of likaku/Mck-ppt-design-skill with a five-stage, gated workflow. Tested 2026-11-15.

Labels: **[observation]** = seen in this test run; **[inference]** = my reading, not directly tested; **[unknown]** = could not be established.

## Tested setup

- Isolated venv (`uv`, Python 3.12), `pip install -e ".[dev]"`. CLI `mbb-ppt`.
- The operator agent was `deepseek-v4.1-flash`, given `SKILL.md`, `references/api-cheatsheet.md`, `references/framework/guard-rails.md` and all `experiences/*.md` (about 20k prompt tokens). Fast Track (≤5 content slides): the model writes `content.json`, then `mbb-ppt render content.json` runs the S3 content gate, renders, and runs the S4 render gate.
- Three LLM calls:
  1. Write `content.json`.
  2. Fix after the S3 gate failure.
  3. Fix after the S4 gate failure.
  I stopped after the second gate round (see below).
- Then ran forge `validate(render=True)` and a LibreOffice render.
- Output: `scratchpad/td/out/mbb/mbb_costco.pptx` (final) and `mbb_costco_round1.pptx`.

## What it does well

- [observation] **Two machine gates with a JSON verdict**, and both fired:
  - S3 caught `table_insight.rows max 6; got 7` before rendering, and the model fixed it in one round.
  - S4 (`references/scripts/gate_check_render.py`, built on `mbb_ppt/qa.py`) caught text overflow on slides 1 and 3 and shapes past the right edge on slide 5.
  - `passed` is computed by the script, and exemptions are a hard-coded `ENGINE_BUG_WHITELIST`.
- [observation] The schema is one source of truth (`references/api-schemas.yaml`, about 2,000 lines). It gives every layout's params with `max_chars`, `max` items and tuple shapes. The cheatsheet is generated from it (`references/scripts/generate_cheatsheet.py`).
- [observation] The model picked a varied, argument-led layout set (`executive_summary`, `insight_rail`, `table_insight`, `side_by_side`, `ask`), wrote conclusion titles, and put a source line with its derivations on every slide ("EPS = $9.23B / 443.1M shares; P/E = …").
- [observation] `insight_rail` (exhibit left, a dark rail of takeaways right) rendered cleanly. It is a good exhibit-plus-so-what pattern.
- [inference] The self-refinement protocol (`experiences/*.md`, append-only "Experience NNN" entries that feed back into prompts and gates) is a cheap way to turn recurring defects into checks.

## Where it breaks

- [observation] **Schema and engine disagree, and the S3 gate does not catch it.**
  - `side_by_side`: the schema wants `options: [{title, bullets}]`. The engine unpacks `for i, (otitle, pts) in enumerate(options)` (`mbb_ppt/engine.py` L894), so a dict is unpacked into its keys. The slide rendered the literal words **"title"** and **"points"** (the model used `points`, which the gate did not reject). Its 41-char option title also passed despite `max_chars: 20`.
  - `table_insight.col_widths`: the schema says `list[float]`, but the engine expects `Inches` objects. `_coerce_for_engine` (`__main__.py` L70) does not convert them, so `[3.2, 2.0]` became 3-EMU columns and the table rendered as a vertical smear of single characters.
  - Neither defect was flagged by S3. S4 flagged only a side effect on slide 3 and nothing on slide 4. Both survived two repair rounds.
- [observation] **The S4 gate cannot converge.** The `ask` layout's status column always ends 0.067 in past the slide edge (4 identical findings in both rounds). It is an engine geometry bug that is not on the whitelist, so no content edit can make `passed` true.
- [observation] Like likaku: charts are rectangles (0 chart parts), tables are text-box grids (0 `a:tbl`), colours are hard-coded RGB (forge P6), and the layout is Blank (P7). The "insight_rail" bar chart is not editable.
- [observation] Content: "post-capex cash" (net income − capex = $2.80B) is presented as the cash available for returns. The deck then argues a buyback is immaterial because $2.8B is 0.57% of market cap, ignoring the $20.21B cash balance it quotes elsewhere. Nothing in the pipeline checks reasoning across slides.
- [observation] Forge found 186 failures on the final file. That includes the slide-3 render collision (P1) and a slide-4 empty block of 81% (P20), but forge also has no check for schema-key words ("title", "points") leaking into slide text.

## What we take

1. [observation] **A schema-to-renderer contract test.** For every archetype, generate a sample spec from the JSON schema (`app/engines/forge/schemas/slide.schema.json`), render it, and assert that every string slot appears in the PPTX text and no schema key name appears as text. This repo's `side_by_side` and `col_widths` bugs are what such a test catches. Add it as a forge test over `render.py` / `parts.py`, plus a validator check "schema key leaked as text" (P10 or a new rule) in `validate_pptx.py`.
2. [inference] **Gate convergence guard.** When the same finding (rule + slide + shape + message) repeats unchanged after a content-only repair round, reclassify it as `fix_scope: renderer`, stop sending it to the model, and log it. Forge's spec already separates `renderer` from `content`. This adds automatic detection of misclassified renderer bugs. Changes `engine.py` (repair loop) and `validate_pptx.py`.
3. [inference] **Generated cheatsheet from the schema** (`generate_cheatsheet.py`): forge's content prompt should be generated from `slide.schema.json` + `rules.yaml` caps, so prompt and validator cannot drift. Changes `engine.py`/`brief.py` prompt assembly.
4. [inference] Per-run "experience" entries (`experiences/*.md`) to collect pattern-level failures. Forge already has a findings ledger; this is a lighter version and lower priority.

## Licence note

Apache-2.0 (per the ledger), derived from likaku/Mck-ppt-design-skill (also Apache-2.0). NOTICE files from both projects must be carried if code is copied.
