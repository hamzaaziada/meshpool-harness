# Teardown: presenton/presenton

Pinned commit `9af2b47dda31060d3d5070e020a0825b15c94b32` (app 0.9.12-beta). A full app: FastAPI backend, Next.js editor and renderer, and a separate `@presenton/export-core` package (v1.0.51, downloaded from the presenton-export GitHub releases) that turns the rendered web slides into PPTX with Puppeteer. Tested 2026-11-15 as the **baseline**.

Labels: **[observation]** = seen in this test run; **[inference]** = my reading, not directly tested; **[unknown]** = could not be established.

## Tested setup

- Docker is installed but no daemon runs here, so I installed it natively under the scratchpad:
  - FastAPI: `uv` venv on Python 3.11 from `uv export --frozen` of `servers/fastapi/uv.lock`.
  - Next.js: `npx npm@11.16.0 ci` (the plain `npm ci` fails; the Dockerfile pins npm 11.16), then `npm run build`.
  - Export package: `scripts/sync-presentation-export.cjs`. Its tail hung (a browser download) after the package was already installed, so I killed it.
  - nginx: replaced by a 20-line Node reverse proxy (`scratchpad/td/presenton_run/proxy.mjs`).
  - Puppeteer: pointed at the system `/usr/bin/google-chrome`.
- Config:
  - `LLM=custom`, `CUSTOM_LLM_URL=https://llm.meshpool.ai/v1`, `CUSTOM_MODEL=deepseek-v4.1-flash`, `DISABLE_IMAGE_GENERATION=true`, `WEB_GROUNDING=false`.
  - `NEXT_PUBLIC_URL=http://127.0.0.1:5001`. Without it, export targets `http://127.0.0.1/pdf-maker` (port 80, nginx) and fails with a bare 500.
- Called `POST /api/v1/ppt/presentation/generate` with the brief, `n_slides=5`, template `general`.
  - Generation made 7 LLM calls.
  - The first export failed on the URL above. I re-exported the stored presentation with `POST /api/v1/ppt/presentation/{id}/export`, with no new LLM calls.
- The icon search downloads `Qdrant/all-MiniLM-L6-v2-onnx` from Hugging Face on first use (it worked here).
- Then ran forge `validate(render=True)` and a LibreOffice render.
- Output: `scratchpad/td/out/presenton/presenton_costco.pptx`.

## What it does well

- [observation] It runs end to end from one API call: outline, layout choice per slide, content, icons, export. The export endpoint for an existing presentation (`api/v1/ppt/endpoints/presentation.py::export_existing_presentation`) lets you recover a failed export without paying for generation again. Useful.
- [observation] The PPTX export keeps text editable, positions match the web render, and the fonts (Poppins/Inter) are embedded as web fonts in the template.
- [inference] Its template system (`servers/nextjs` presentation templates + `servers/fastapi/templates`, JSON-schema slots with `maxLength` that are rendered into the LLM prompt by `utils/schema_utils.py`) is the same "model fills slots, renderer places" contract forge uses.

## Where it breaks

- [observation] **It ignores the facts it was given.** Slide 2's chart plots invented values in "USD millions": cash ≈ 450, a "Total Current Assets" bar ≈ 1,200, and a card claiming "~$1.2B total current assets; short-term investments at $0". The brief says cash is $20.21B. The chart has 5 bars but only 3 category labels.
- [observation] **No native charts or tables**: 0 chart parts, 0 `a:tbl`. The chart is a PNG (`Presenton Raster Image`), and all visuals are placeholder images because image generation was off. Forge P2 did not flag the raster chart because its name has no "chart" hint and no OCR is configured.
- [observation] Raw markdown leaks into slide text ("final**No", "**$0", "**years, notquarters"). The title slide's title is cut mid-word to "Capital Allocation at a Cross" (the export file name shows "…at-a-Crossroads…"). [inference] It was truncated to a slot length.
- [observation] Topic titles on every slide ("Where the Cash Actually Stands", "Buyback vs. Dividend", P11). No source lines (P13). 7–10 decorative icons per slide (P9). Body text at 10.5pt (P5). Titles overlap their second line in the LibreOffice render (P1).
- [observation] P0: `xml:space` on `a:t` is not schema-valid. The zip entries carry a 2107-11-30 timestamp.
- [observation] Operationally heavy: FastAPI + Next.js + nginx + Chromium + a separate export package + Hugging Face model downloads + mem0/spaCy. The default path is Docker. Error reporting is a generic 500 ("contact your administrator"); the cause is only in the server log.
- [unknown] Licence of `@presenton/export-core`: its `package.json` has no `license` field, and its source repo (presenton/presenton-export) was not checked.

## What we take

1. [observation] **A re-export endpoint** that renders an already-generated deck without regenerating (`export_existing_presentation`). Forge's equivalent: make `render.py` re-runnable from the stored slide JSON + `provenance.json` with no model calls, and expose it in `engine.py`. Small change, saves money on render-side fixes.
2. [observation → baseline evidence] Use this run as the "baseline to beat" exhibit for forge's P13/P14 provenance rules. A popular open-source generator, given six facts, put invented numbers on a chart. That supports keeping forge's fact-sheet provenance (P13) as a hard rule, and adding a check that every number on a slide resolves to the brief's fact sheet or a declared derivation.
3. [observation] Forge gap shown here: P2 should flag any large raster image on a slide whose archetype expects a chart (`bar_takeaway`, `column_trend`, …), or with chart-like aspect and dominant colour bands, whatever its name. Changes `validate_pptx.py` (P2) and `rules.yaml` (`charts.picture_name_hints` is too narrow).
4. [observation → do not take] HTML→PPTX via a headless browser (same reasons as PPTAgent; spec §1.2).

## Licence note

Apache-2.0 for the repo (LICENSE + NOTICE at the pinned commit; per the ledger). The NOTICE is large (1.3 MB, third-party attributions). The export runtime `@presenton/export-core` 1.0.51 is a separately distributed package with no licence field in its `package.json`; treat it as **[unknown]** and do not vendor it.
