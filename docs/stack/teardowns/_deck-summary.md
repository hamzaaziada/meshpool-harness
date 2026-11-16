# Deck-generator teardowns: what forge should adopt

Date: 2026-11-15. Eight repos were installed in isolated venvs under the session scratchpad and run on the Costco board brief, or used to build 3 slides for the pure layout libraries. Every PPTX went through forge `validate(render=True)` and a LibreOffice → PNG render that I looked at. Per-repo notes are in this folder. Labels: [observation] tested here, [inference] my reading, [unknown] not established.

LLM use: only `deepseek-v4.1-flash` via `https://llm.meshpool.ai/v1`. The key's spend went from $16.8263 to $16.9251, **+$0.099**. That figure is key-wide and may include other users of the key. The calls I logged directly total 81k prompt and 19k completion tokens, plus 7 Presenton calls and 15 AutoPresent calls (no token log for those).

## Scoreboard (tested)

| Repo | Ran on brief? | Native chart / table | Own gate verdict | Forge quality-rule fails* | Worst defect seen |
|---|---|---|---|---|---|
| ginlix-ai/LangAlpha (pptx skill) | yes, 1 LLM call | 2 / 1 | `check.py --strict` **pass** (0 findings) | P0 P1 P11† P14† P17 P18 P20 P21 | table row renders past frame; "2.2 years of capex" arithmetic slip in outline |
| icip-cas/PPTAgent (skill, html2pptx 1.1.37) | yes, 3 LLM calls | 0 / 1 | build blocked until review fixed; `finalize complete=false` | P0 P1 P5 P8 P10 P11 P18 P19 P20 P21 | converter crash on inline SVG; flex rows broken on export |
| albertojb/mbb-ppt-generator | yes, 3 LLM calls | 0 / 0 | S3 pass after 1 fix; S4 **never passes** | P1 P5 P8 P17 P19 P20 P21 | schema/engine drift: literal "title"/"points" on slide, 3-EMU table columns |
| para-lost/AutoPresent | 2 of 5 slides ran | 0 / 0 | none | P1 P5 P8 P11 P21 | `SlidesLib` not importable as shipped; 3/5 generated programs crash |
| presenton/presenton (baseline) | yes, 7 LLM calls | 0 / 0 (chart is PNG) | none | P0 P1 P4 P5 P8 P9 P11 P13 P18 P19 P20 P21 | invented "USD millions" numbers contradicting the brief |
| tmustier/clean-slides | 3 slides via YAML | 1 / 0 (text-box grid) | `verify` OK, sidebar warning | P0 P1 P5 P14† P18 P19 P20 | python-pptx negative `axId`; duplicate shape id |
| knorq-ai/pptx-mcp-server | 3 slides via API | 1 / 1 | `check_deck_extended` 0 errors / 0 warnings | P0 P5 P14† P18 P19 P20 | empty cards; 58% empty body under table |
| likaku/Mck-ppt-design-skill | 3 slides via API | 0 / 0 | `gate_check` FAIL (1 overflow) | P1 P5 P8 P17 P18 P20 | bar chart drawn from rectangles; table from text boxes |

\* Excluding forge-house rules that no third-party deck can pass (P6 theme colours, P7 placeholders/grid, P12/P15 slide identification, P13 provenance map). † = forge false positive or over-strict call; see "Forge fixes" below.

Bottom line [inference]: none of the eight is a drop-in replacement for forge. LangAlpha produced the best LLM-driven deck and clean-slides the best-looking tables. Every repo's own checker passed or missed defects that forge's render-based checks caught. That supports forge's validator-first design. The value to take is specific techniques, ranked below.

## Ranked adoption list

| # | Adopt | Evidence | Source pointer | Forge file(s) to change |
|---|---|---|---|---|
| 1 | **Detect fake charts and fake tables (P2/P3).** Flag clusters of aligned rectangles with numeric labels, text-box grids ≥3×3 with rules, and large raster images on chart archetypes, whatever their names. | [observation] Forge P2/P3 **passed** likaku, mbb (rectangle bars, text-box tables), PPTAgent (div bars) and Presenton (PNG chart named "Presenton Raster Image"). The name hints `(?i)chart\|graph…` in `rules.yaml` L40/42 never fired. | negative fixtures: `likaku engine.py::grouped_bar`, `data_table`; `mbb engine.py::insight_rail`; Presenton export | `validate_pptx.py` (P2, P3), `rules.yaml` (`charts`, `tables`) |
| 2 | **Schema→renderer contract test plus a "schema key leaked as text" check.** For each archetype, render a spec generated from the schema; assert every slot's text appears in the PPTX and no key name (`title`, `points`, `bullets` …) appears as slide text. | [observation] mbb's S3 gate passed specs that rendered the literal words "title"/"points" and a 3-EMU-wide table. Neither its gates nor forge flagged the leak. | `mbb engine.py` L894 `side_by_side`; `mbb __main__.py::_coerce_for_engine` | new test over `render.py`/`parts.py` + `schemas/slide.schema.json`; check in `validate_pptx.py` (P10) |
| 3 | **Bind QA evidence to the artifact hash; refuse delivery without current passing evidence.** | [observation] PPTAgent's `build` refused while a slide review was stale or failing, and `finalize` returned `complete=false` after a failed deck review. Editing a slide auto-invalidated its review. | `PPTAgent skills/pptagent/scripts/pptagent_runtime/core.py::input_snapshot`, `render_current`, `review_gaps`, `finalize` | `engine.py` (repair loop / delivery), `review.py` |
| 4 | **Repair-loop convergence guard.** A finding that repeats unchanged after a content-only round is re-scoped to `renderer` and no longer sent to the model. | [observation] mbb's `ask` layout produced the same 4 right-edge findings in both rounds. The gate can never pass, and the model kept being asked to fix it. | `mbb references/scripts/gate_check_render.py` (static `ENGINE_BUG_WHITELIST` is the manual version) | `engine.py` (repair loop), `validate_pptx.py` (`fix_scope`) |
| 5 | **Per-slot character budgets stated to the model as "N chars at this size"**, derived from forge's own measurer, plus geometry and type-scale tables in the prompt. | [observation] LangAlpha's SKILL.md (bands, floors, "28pt bold across 12.13 in ≈ 56 chars") got clean geometry from a flash model on the first call. mbb/likaku keep per-field `max_chars` in one schema and generate the cheatsheet from it. | `LangAlpha plugins/langalpha_deliverables/skills/pptx/SKILL.md` (Design Rules); `mbb references/api-schemas.yaml` + `references/scripts/generate_cheatsheet.py`; `likaku references/layout-matrix.yaml` | `rules.yaml` (`density.by_archetype` → per-slot), `density.py`, prompt assembly in `engine.py`/`brief.py` |
| 6 | **Fix forge false positives found in testing:** (a) `_has_verb` closed list misses common verbs ("absorbs", "retires"), so add a morphological fallback; (b) P14 should only compare numbers that name a plotted series/category, not every number on a chart slide. | [observation] P11 fired on two verb-bearing LangAlpha titles. P14 fired on clean-slides, knorq and LangAlpha because revenue was quoted beside a chart that did not plot it. | forge `validate_pptx.py` L40–73 `_VERBS`, `_has_verb`; P14 block | `validate_pptx.py`, `rules.yaml` (`titles`, P14 tolerance) |
| 7 | **Inherited font-size resolution through layout/master list styles, and an occlusion check** (a filled shape later in paint order hiding text). | [inference] LangAlpha `check.py` resolves run → paragraph → list style → layout → master and judges autofit at drawn size. Forge reads `fontScale` (`measure_pptx.py` L267) but not the full inheritance chain. No forge rule covers shapes hiding text. | `LangAlpha scripts/check.py::inherited_styles`, `stated_points`, `run_points`, `autofit`, `paints_over` | `measure_pptx.py`, `validate_pptx.py` (P5, P8/P17) |
| 8 | **Content-aware table column sizing with a pre-flight verify.** | [observation] clean-slides produced the cleanest tables tested: widths by line demand, a solver step, then verification before writing. [inference] Forge table archetypes use fixed proportions. | `clean-slides clean_slides/sizing.py::ColumnSizer.size`, `solver.py::ConstraintSolver.solve`, `verification.py::LayoutVerifier` | `parts.py` (tables), `common.py`/`density.py` |
| 9 | **Geometry micro-checks:** text vs separator-line collision, uneven vertical gaps in a stack, same-row font consistency. | [observation] These exist in likaku/mbb `qa.py` and knorq `validation.py`; forge P8/P19 have no equivalent. They are cheap and file-only. | `likaku mck_ppt/qa.py::_check_text_line_collision`, `_check_peer_font_consistency`; `knorq engine/validation.py::check_divider_collision`, `check_inconsistent_gaps` | `validate_pptx.py` (P8, P19) |
| 10 | **Structured error location for the repair loop**: add `parameter` (JSON path into the slide spec) next to `message`/`fix_scope`. | [inference] knorq's `{code, parameter, hint}` envelope lets a model edit the exact field. Forge messages name slide and shape but not the spec path. | `knorq src/pptx_mcp_server/_envelope.py`, `engine/pptx_io.py::EngineError` | `validate_pptx.py` (`F()`), `engine.py` (repair prompt) |
| 11 | **Number-to-fact check across all slide text** (every figure resolves to the fact sheet or a declared derivation), not only text-vs-chart. | [observation] Presenton charted invented "USD millions" figures. LangAlpha's outline said "2.2 years" (should be 3.1). mbb framed $2.8B "post-capex cash" as the pool for returns while quoting $20.21B cash. P14 cannot see any of this. | baseline evidence from `presenton__presenton.md` | `validate_pptx.py` (P13/P14), `finance_pages.py`/`research.py` fact sheet |
| 12 | **Re-render without regeneration** (render from stored slide JSON + provenance, zero model calls). | [observation] Presenton's `export_existing_presentation` recovered our failed export for $0. | `presenton servers/fastapi/api/v1/ppt/endpoints/presentation.py::export_existing_presentation` | `engine.py`, `render.py` |
| 13 | **Explicit content-area map per template layout** (primary/secondary areas for split layouts), cached as JSON. | [observation] `pptx layouts` and `pptx init-config` in clean-slides made the 2/3 split usable without code. | `clean-slides clean_slides/template_config.py`, `template_init_config.py`, `docs/TEMPLATE-CONFIG.md` | `template.py` |
| 14 | **Published calibration of the text measurer per script**, enforced by a test. | [observation] knorq states ±10% Latin / ±15% CJK and marks unsupported scripts in a test. | `knorq tests/test_calibration.py`, `engine/text_metrics.py` | `common.py` + new test |
| 15 | **SlidesBench as a secondary eval only.** | [inference] Generic decks; not consulting-grade. | `AutoPresent slidesbench/`, `evaluate/` | eval harness, not forge |

## Do not adopt (tested reasons)

- **HTML→PPTX conversion** (PPTAgent, Presenton). [observation] It produced no native charts, crashed on inline SVG, broke flex rows on export, and leaked raw markdown. The forge spec §1.2 already forbids it.
- **Shape-drawn charts and text-box tables** (likaku, mbb, clean-slides tables). [observation] Not editable, and they escape forge P2/P3 today (see #1).
- **Free-form code generation per slide** (AutoPresent). [observation] 3 of 5 slides crashed with a flash model, and there is no deck-level consistency.
- **"Fill the zone" card sizing** (knorq `card_grid`). [observation] It produces the empty-card look P20 targets.

## Forge validator observations from this exercise

- [observation] Forge caught real defects every repo's own checker missed: render overflow (P1), stretched rows and dead space (P20), schema problems (P0). Its render-based design is the differentiator.
- [observation] python-pptx's chart template writes negative `c:axId` (`pptx/chart/xmlwriter.py` L469). Forge already rewrites these (`common.py` L229, `merge.py` L243); clean-slides and knorq do not.
- [observation] False positives and gaps to fix: #1 (P2/P3 blind to fake charts and tables), #6 (P11 verb list, P14 scope), #2 (schema-key leak), #11 (facts outside charts).
- [unknown] Whether PowerPoint itself shows a repair prompt for the P0 findings on the third-party decks (negative axIds, duplicate ids, pptxgenjs axId order, `xml:space` on `a:t`). PowerPoint was not available.

## Artefacts

All under `/tmp/claude-0/-home-user-meshpool-harness/24bc71e4-4861-5705-9681-86c31317da60/scratchpad/td/`:
- `out/<repo>/` holds each PPTX, its `*.forge.json` validator output, and the `*_png/` renders.
- `work/` holds the pinned repo copies, with the generated content (prompts, responses, content.json, HTML, build scripts).
- `venvs/` holds the isolated environments.
- `usage.log` is the LLM token log.
