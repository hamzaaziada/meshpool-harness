# Teardown: docling-project/docling

Pinned commit `440492ac0d0e14952b32dda28fede7495611c899` (pyproject version 2.150.1), MIT. Tested 2026-11-15.
Labels: [observation] measured or seen in the code; [inference] my reading; [unknown] not established.

## Tested setup

- Isolated venv (Python 3.12) in the scratchpad: `uv pip install docling pypdfium2` with the PyTorch CPU index ->
  docling 2.150.1 (same version as the pinned commit), torch 2.15.0+cpu, rapidocr; venv 1.5 GB. Models pulled from
  Hugging Face on first use (docling-layout-heron, docling-models/TableFormer; about 600 MB in `~/.cache/huggingface`).
  [observation]
- Machine: 4 vCPU, 15 GB RAM, no GPU. Runner `scratchpad/td/scripts/run_docling.py` (PdfPipelineOptions: OCR on/off,
  table structure on), checks in `drop_check.py` and `num_fidelity.py`. [observation]
- Documents:
  (a) Costco FY2026 10-K, sec.gov iXBRL HTML (1.46 MB);
  (b) Cleopatra Hospitals Group (Egypt), from its investor-relations site: FY2025 earnings release PDF (15 pages, charts
  and tables) and audited consolidated financial statements at 31 Dec 2025 (73 pages; pages 1, 3, 4, 5 are scans -
  page 5 is the **scanned balance sheet**). [observation]

## Results

| document | docling time (CPU) | ours: `docread.extract` | marker `--mode fast` (for scale) |
|---|---|---|---|
| Costco 10-K HTML | 2.1 s, 63 tables, no page/bbox (HTML has no pages) | 0.02 s, rows as lines, no table structure | not run |
| Cleopatra ER, 15 pp | 69.7 s without OCR (4.6 s/page); 169 s with OCR (11 s/page; the deck's chart images get OCRed); first run 229 s incl. model download | 0.8 s | 13.5 s |
| Cleopatra FS, 73 pp, 4 scanned | 524 s with OCR (7.2 s/page), 13m52s CPU | 2.5 s, scanned pages empty ("OCR is not included in this install") | 25.5 s |

[observation, all rows]

**Table structure.**
- ER income statement (page 13, text layer): docling's table matches the source cell for cell - 25 rows x 7 columns,
  every value, sign, blank and "-" in the right column (checked against `pdftotext -layout`). Our pypdf text has the
  same numbers per row but loses column headers and collapses blank cells ("Deferred tax (7.1) 37.5 (14.5) 32.7
  -325.7%" - which column is blank cannot be told). [observation]
- Scanned balance sheet (FS page 5, image only): docling OCR + TableFormer recovered all 45 rows with every amount
  correct against the page image, except that the last block merged two row pairs ("Current income tax liabilities |
  284,221,057 3,310,317,159" and "Total current liabilities Total liabilities | 6,200,893,742"). Our path returns
  nothing for this page. [observation]
- 10-K HTML tables: values and order are right, but EDGAR's colspan layout comes out with repeated cells and spacer
  columns ("Current portion of long-term debt | ... | 2,248 | 2,248 | | | | | 75 | 75 |") and "$" in its own cell;
  needs a clean-up pass (drop empty columns, merge duplicates and "$"). [observation]
- iXBRL detection: docling sniffs the 10-K as `InputFormat.XML_XBRL` and tries its XBRL backend, which needs
  `arelle` (not installed by default) and fails; forcing HTML needs `format_options={InputFormat.XML_XBRL:
  HTMLFormatOption()}`. The XBRL backend (`docling/backend/xml/xbrl_backend.py`) turns numeric facts into key-value
  graph items - not tested. [observation]

**Page numbers and bounding boxes.** Every PDF item carries `prov` with `page_no`, `bbox` (l, t, r, b, origin
BOTTOMLEFT) and `charspan` (1,001 of 1,001 items in the FS, 462 of 462 in the ER). Table cells have row/column indices
and header flags. HTML input has no provenance. [observation]

**Silently dropped content.**
- Body text recall against the pypdfium2 text layer: ER 96.3% of tokens (99.7% counting headers/footers), FS 99.4%
  (99.7%); no page under 90%. Numeric tokens: ER 94.3%, FS 96.9% in the body layer. [observation]
- **`export_to_markdown()` drops text inside pictures.** In the ER, 238 text items (141 numbers: chart data labels such
  as quarterly revenue bars) are children of 38 pictures and appear in Markdown only as `<!-- image -->`. Numeric recall
  of the Markdown is 80.5% vs 99.0% for our pypdf text. They are in the JSON. [observation]
- Headers/footers (page numbers, report title) go to the `furniture` layer and are left out of Markdown (49 items in
  the ER); fine for us, but page numbers must come from `prov`, not from the text. [observation]
- OCR quality on the scanned auditor's report (FS page 3) is mixed: clean paragraphs read well, some lines turn to
  noise ("Wc aa oi o ss a a a any"), probably the stamp/signature overlay. [observation]

## What it does well

- Best table structure of the three on born-digital and scanned statements; column headers kept. [observation]
- Full page + bbox + charspan provenance, which is what passage-level citations to a page (and later a highlight) need.
  [observation]
- OCR and layout run locally with permissive models; one call converts PDF, DOCX, PPTX, XLSX, HTML, images. [observation]

## Where it breaks

- Speed on CPU: 4.6-11 s per page; a 300-page annual report would take 25-55 minutes on this machine. Marker's fast
  mode was 5-20x faster on the same files (see marker note). [observation for measured docs; inference for 300 pages]
- Heavy install (torch, 1.5 GB venv + 600 MB models). [observation]
- Markdown export drops picture text; HTML tables from EDGAR need clean-up; iXBRL needs arelle or an override.
  [observation]
- One row-merge error on the scanned balance sheet: totals can attach to the wrong label, so numbers from OCR tables
  still need a check (e.g. the column must foot). [observation]

## How our library ingests PDFs now

`engines/research/library.py:Corpus.add_file` -> `meshpool_harness/docread.py:extract` -> `_pdf`: pypdf text per page
(pdfminer for pages under 20 characters), OCR via `meshpool_harness/ocr.py` (RapidOCR) only for near-empty pages and
only when OCR models are installed (they are not in this install), output as `## Page N` blocks; `split_passages`
keeps the page number and nearest heading per 300-900-character passage. No table structure, no bboxes, 200k-character
cap by default. HTML: `_strip_html` (regex). [observation]

## What we take

1. Docling as an **optional, isolated table-and-scan parser**, not as the default reader: keep pypdf for speed and
   recall, and call docling (a separate venv or container behind a small HTTP endpoint) for (a) pages with no text
   layer, (b) pages where pypdf text looks tabular (many numbers per line), and (c) documents the user marks as
   financial statements. Pointer: `meshpool_harness/docread.py:_pdf`, after the per-page text pass, where `want` (OCR
   pages) is computed; add a `tables="auto|docling|never"` option. Use `page_range` per call to limit cost.
2. Read docling's **JSON, not Markdown**: take texts (including picture children - chart labels), tables as
   cell grids with header rows, and `prov.page_no`/`bbox`. Emit `## Page N` blocks as today so `split_passages` keeps
   working, plus table rows rendered as "row label | column header: value" lines, so a passage states which period a
   number belongs to. Pointer: `library.py:split_passages`, `Passage` (add optional `bbox`).
3. **Table facts**: a statement table becomes cell-level facts ("Total liabilities, 31 Dec 2025: EGP 6,200,893,742, p.5")
   that `verify.py` can check by label and period, not only by digits. This is the fix for label errors such as the
   gpt-researcher "deleveraging" claim. Add a footing check (sum of components = total) for OCR tables.
4. For EDGAR 10-Ks keep XBRL (`forge/research.py:xbrl_facts`) as the number source; docling's HTML tables need too
   much clean-up to beat it. [inference]

## Licence note

MIT at the pinned commit (ledger: "adopt (evaluate first)"). Models: docling's own layout/TableFormer models are
published by the project on Hugging Face [unknown: model licences not checked at a commit]; RapidOCR/PaddleOCR
models are Apache-2.0 [unknown at this pin]. Dependencies include torch (BSD-style). No copyleft found in the default
install [inference: not audited package by package].
