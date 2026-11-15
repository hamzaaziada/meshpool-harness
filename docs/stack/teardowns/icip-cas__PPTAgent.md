# Teardown: icip-cas/PPTAgent

Pinned commit `9dacac53ac9ef39346461063df729396551d929e`. At this commit the repo is the **PPTAgent Skill** (`skills/pptagent/`). The host coding agent writes HTML slides, and a CLI renders, gates, and converts them with the `pptagent==1.1.37` PyPI package (`deeppresenter.html2pptx`, built on pptxgenjs + Playwright). The EMNLP-2025 reference-deck induction pipeline and the PPTEval rubric live at the `v0.2.0` tag, not at this commit. They were **not** tested here ([unknown] how they perform). Tested 2026-11-15.

Labels: **[observation]** = seen in this test run; **[inference]** = my reading, not directly tested; **[unknown]** = could not be established.

## Tested setup

- Isolated venv (`uv`, Python 3.12) with `skills/pptagent/requirements.txt`. `npm ci` in a scratchpad copy of the skill.
- `playwright install chromium` downloaded Chrome Headless Shell into the machine's shared `/opt/pw-browsers` cache. That is a browser binary, not a Python package.
- `pptagent.py doctor` reported all green after that.
- Workflow run as documented: `init --slides 5`, `scaffold`, author HTML, `review-slides`, `record-slide-review`, `build`, `review-deck`, `record-deck-review`, `finalize`.
- Authoring "host": `deepseek-v4.1-flash`, given `SKILL.md`, `references/task-contract.md` and the scaffold. Three calls in total (1.8k/6.9k tokens, then two small fix rounds). The first call returned fake tool-call markup; a "no tools in this turn" instruction fixed that.
- Visual review in `multimodal` mode, where the host views the images. I reviewed the renders myself. `text` mode needs a vision model, which the budget rule excludes.
- Output: `scratchpad/td/out/pptagent/ws/answer.pptx` (copied to `pptagent_costco.pptx`).

## What it does well

- [observation] **An evidence-bound gate.** `core.py` hashes every slide source plus shared assets (`input_snapshot`). A review only counts if it was recorded against the current render hash (`render_current`, `review_gaps`).
  - `build` refuses to run while any slide review is stale or has a major/blocker issue. Tested: it blocked with "visual review gate failed: slides/slide03.html: blocker or major issue remains".
  - `finalize` returns `complete=false` when the deck review fails. Tested: `deck_review_current: false`.
  - Editing any slide invalidates its review automatically. The agent cannot declare its own work done without fresh evidence.
- [observation] A structured review contract (`pptagent_runtime/visual.py::validate_review`): verdict ∈ {pass, fail, unjudgeable}, an issues list with severity/slide/description/suggested_fix, and "a pass with major or blocker issues is rejected".
- [observation] HTML authoring gives the most visually polished slides of the LLM-driven runs: card KPIs, a two-column cash slide, a clean table, a dark recommendation box. HTML→PPTX kept positions, fonts and the table (`a:tbl`) closely on 4 of 5 slides.
- [observation] The render sandbox blocks any network or untracked resource during screenshotting (`open_slide` routes `**/*` and aborts non-tracked URLs). It also isolates LibreOffice with a throwaway profile (`render_deck` sets `HOME`, `-env:UserInstallation`).

## Where it breaks

- [observation] **Converter crash on inline SVG.** `slide02.html` had a plain `<svg>` bar chart with no class attributes. The build died with `TypeError: el.className.includes is not a function`: on SVG elements `className` is an `SVGAnimatedString`, and `html2pptx.js` calls `.includes` on it in several places (e.g. L2043). I fixed it by having the model redraw the chart with divs.
- [observation] **No native charts.** Charts must be div bars or SVG, and SVG gets rasterised to PNG. The final deck has 0 chart parts. The bars are shapes, so the data is not editable. Forge P2 did not notice (see summary: fake-chart detection gap).
- [observation] **Export breaks flex rows.** On slide 4, `display:flex; justify-content:space-between` label/value rows came out with values stacked under labels, left-aligned. The HTML render looked right; only the PPTX render showed it. That is why the post-build deck review exists, and in this run it caught the problem.
- [observation] Text drops to 8–10pt in cards (forge P5: 98 findings; body text in 11 sizes, P4). The lower third of most slides is empty (P20). Titles run 19–23 words over 3 lines (P11).
- [observation] P0 problems in the converter output: `notesMasterIdLst` out of schema order in `presentation.xml`, a duplicate shape id on slide 3, and `a:pPr` after runs on slide 4. [unknown] whether PowerPoint repairs these.
- [observation] Content consistency is the author's problem. The first draft's table total row ("All three … $26.43B, exceeds cash") contradicted the paragraph under it. My review caught it; no code does.
- [inference] The skill depends on a multimodal host or a vision endpoint. With a text-only model and no vision endpoint, every review is either self-asserted or `unjudgeable`.

## What we take

1. [observation] **Hash-bound QA evidence.** Forge's repair loop (`engine.py`, max 3 rounds) should record each validator result against the sha256 of the exact `deck.pptx` and slide JSON it judged, and refuse to ship when the latest artifact has no current, passing evidence. That mirrors `core.py::render_current`, `review_gaps`, `finalize`. Forge already writes `sha256` into `validate()` results. The change is to make delivery check it. Changes `app/engines/forge/engine.py` and `review.py`.
2. [inference] **A strict review schema for any vision critic** (`visual.py::validate_review`: a pass cannot carry major/blocker issues, and invalid JSON is surfaced, not repaired). Apply it to forge's `critics.py` / `review.py` model-critic outputs.
3. [observation → do not take] HTML→PPTX as the render path. The forge spec forbids it (§1.2). This run confirms why: no native charts, an SVG crash, and flex layouts lost on export.
4. [unknown] PPTEval (content/design/coherence rubric, v0.2.0 tag) as a secondary eval. Not run here; would need its own pinned checkout.

## Licence note

MIT for the repo and for `pptagent` 1.1.37 (`License-Expression: MIT` in the wheel metadata). Ideas and code can be adapted with the copyright notice.
