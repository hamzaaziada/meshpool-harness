# Teardown: datalab-to/marker

Commit `d580b63874d9c8510fa54494975b9df1e8b527d0` (2026-11-13), package `marker-pdf 2.2.1`. Code Apache-2.0
(`LICENSE`); model weights under a modified AI Pubs Open RAIL-M licence (`MODEL_LICENSE`). Tested 2026-11-15.
Labels: [observation] measured or seen in the code; [inference] my reading; [unknown] not established.

## Tested setup

Installing was cheap, so it was tested: isolated venv from the pinned clone (`uv pip install <clone>`), 561 MB, no
torch in the default install (onnxruntime, `indra-ocr` 1.1.2, `surya-ocr` 0.24.0); weights fetched from Hugging Face
(`datalab-to/indra-ocr`) on first run plus 15 MB in `~/.cache/datalab`. CLI `marker_single <pdf> --mode fast
--output_format markdown|json`. Same 4-vCPU machine and the same Cleopatra Hospitals PDFs as the docling note.
[observation]

## Results

| document | marker fast (CPU) | docling (CPU, OCR on) |
|---|---|---|
| Cleopatra FY2025 earnings release, 15 pp | 13.5 s (17.5 s wall incl. start-up) | 169 s (70 s with OCR off) |
| Cleopatra consolidated FS, 73 pp, 4 scanned | 25.5 s (29 s wall) | 524 s |

[observation]

- Scanned balance sheet (FS page 5): all 45 rows separate and every amount right (better than docling, which merged two
  row pairs), but it added an empty "-" column and a stray "4" after one figure ("4,411,955,147 4"). [observation]
- ER income statement (text layer): structure right, but **6 cells came out as LaTeX math with spaced digits**
  ("\$4 0 . 6 \%\$" for 40.6%, "\$( 7 1 . 4 )\$" for (71.4)) and the "-" placeholders for nil rows were dropped.
  Across the files: 18 such math spans in the ER and 70 in the FS ("\$2 0 \%\$", "\$-\$", ...). A digit check that
  strips spaces would recover most of them; a naive number parser would read "4 0 . 6" as four numbers. [observation]
- Numeric recall of the Markdown against the PDF text layer: ER 87.4% (docling Markdown 80.5%, our pypdf 99.0%); FS
  97.3% (docling 98.1%, pypdf 98.9%). [observation, `scratchpad/td/scripts/num_fidelity.py`]
- JSON output: a tree of pages and blocks with ids like `/page/12/PageHeader/2`, `bbox`, `polygon`,
  `section_hierarchy` and HTML content, so page and position provenance are available. [observation]

## How it would run as an isolated service

- `marker_server` (FastAPI, `marker/scripts/server.py`, default host 127.0.0.1:8000): `POST /marker` with a file path
  plus `page_range`, `output_format`, `mode`; paths are confined to `MARKER_ALLOWED_DIR` (realpath + commonpath check,
  `_validate_local_filepath`), allowed extensions only, upload cap `MARKER_MAX_UPLOAD_BYTES` (100 MB); models load once
  in the app lifespan. [observation]
- Shape for us [inference]: its own container or venv, weights baked in at build time, no outbound network at run time,
  a shared drop folder as `MARKER_ALLOWED_DIR`, called from `meshpool_harness/docread.py` over HTTP with a timeout and a
  page range. `--use_llm` (Gemini by default, or an OpenAI-compatible endpoint) must stay off unless pointed at our
  gateway and budgeted. No telemetry code found in `marker/` (grep for telemetry/posthog/analytics). [observation]

## What it does well

- Fast on CPU: 2.9 pages/s on a 73-page statement set with scans, 5-20x faster than docling here; README claims
  23.6 pages/s in `fast` and 57 in `no_ocr` on its benchmark machine [observation for ours; README claim unverified].
- Good row separation on the scanned statement; per-block bbox provenance. [observation]
- Small install with ONNX models, no torch by default. [observation]

## Where it breaks

- Number corruption into LaTeX math spans inside table cells: silent unless we check. [observation]
- Model licence restricts commercial use (below). [observation]
- Markdown export also loses some chart text (ER numeric recall 87%). [observation]

## What we take

Marker is the candidate for the **fast scan/table fallback** if its model licence fits; otherwise docling. If used:
(1) run as the isolated service above; (2) post-process every table cell: collapse `\$...\$` spans, remove spaces
between digits, and reject a cell whose digits do not match the PDF text layer for that page when one exists (pypdf
text is the ground truth for born-digital pages); (3) never let OCR-only numbers into a report without a footing check.
Pointer: same seam as docling - `meshpool_harness/docread.py:_pdf` OCR branch (`want` pages) and a table option.

## Licence note (what the ledger terms mean for us)

Ledger: "code Apache-2.0 at this commit (not GPL); model weights under modified OpenRAIL-M, free under $5M revenue".
- Code: Apache-2.0, usable commercially with notice. [observation]
- Weights (`MODEL_LICENSE`, README "Commercial usage"): free for research, personal use and startups under USD 5M in
  funding or revenue; above that, commercial use of the weights needs a paid licence from Datalab. OpenRAIL-M also
  carries use-based restrictions that must be passed on to anyone we let use the model (Section III, distribution
  terms). [observation]
- For us [inference]: fine for this evaluation and for internal use while we are under the threshold; a hosted product
  for clients that crosses USD 5M funding or revenue needs a Datalab licence or a switch to docling (MIT). Keep it
  behind the service boundary so the switch is a config change. The CLA in the repo concerns contributions only.
