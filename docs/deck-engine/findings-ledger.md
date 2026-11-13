# Findings ledger

Each finding has an ID, a label (**O** observation, **I** inference, **U** unknown), its Evidence IDs and the
boundary of what was searched. Evidence IDs take two forms:

- `EV-<sha8>-<metric>`: the forge measurers (`measure_pptx`, `measure_pdf`, `validate_*`), stable per file and
  metric;
- `ev_…`: REA `inspect-artifact` Evidence (rea-agents 6.9.0).

File identities are listed at the end.

Search boundary for the whole ledger:

- the files listed at the end;
- this repository on branch `claude/elegant-edison-1oey8d`;
- the other session's handoff (`harness-lab/OFFICE_HANDOFF.md`).

No Hebbia or FlashDocs file, HAR or SAZ capture was supplied. No request was made to Hebbia.

## Hebbia

| ID | Finding | Label | Evidence |
|---|---|---|---|
| H-1 | No Hebbia/FlashDocs deck, workbook, document or session capture was available; every Hebbia measurement is open | U | Targets in the brief were placeholders; `find / -name '*.pptx' …` found only repo and library files |
| H-2 | FlashDocs-style generation into a customer template is a plausible pipeline (placeholders in a supplied master) | I | public positioning only; confirm/refute list in `hebbia-deck-anatomy.md` H1–H6 |

## Reference decks (17 public PDFs, 1,186 pages measured)

| ID | Finding | Label | Evidence |
|---|---|---|---|
| R-1 | Reference body text is denser than the 80-word rule: median of per-deck medians 114 words per content page (range 4–156), p90 185 | O | EV-26b116c1-pdf_body_words, EV-6a820bcf-pdf_body_words, `reference-measurements.csv` |
| R-2 | Body-word counts from PDFs include exhibit labels ≥10 pt, so they overstate narrative text | I | method note in `measure_pdf.py` |
| R-3 | Titles: median 10 words (range 1–19), max per deck 15–42; the 15-word cap is stricter than most references | O | EV-*-pdf_title_words in `reference-measurements.csv` |
| R-4 | Source lines on 0–87% of content pages (median 47%); raster exhibits hide some (lower bound where `raster_exhibit_share` > 0.2) | O | EV-26b116c1-pdf_source_line_share (87%), EV-987da9a1-pdf_source_line_share (0%, raster) |
| R-5 | Type: one sans family dominates (Arial in 15 of 17; Verdana in 2 Bain decks); McKinsey adds a serif title face (Georgia); BCG uses Henderson BCG Serif in one deck | O | EV-*-pdf_fonts_top |
| R-6 | Type scale: most common title size is 24–25 pt in 12 of 17 decks (others 19–20 pt or 32–44 pt); body 10–14 pt; footnotes 8–9 pt (6–7 pt in some) | O | EV-*-pdf_title_sizes / body_sizes / footnote_sizes |
| R-7 | References break our 12 pt body / 9 pt footnote floors routinely (10 pt body in BCG, 8 pt footnotes everywhere) | O | as R-6 |
| R-8 | Consistency: title font identical on 82–100% of content pages, title size on 82–99%, title left edge on 82–100% | O | EV-26b116c1-pdf_title_size_mode_share and siblings (4 decks measured) |
| R-9 | Action-title share by heuristic: median 58% (12–91%) | O, with I for the heuristic | EV-*-pdf_title_with_verb_share |
| R-10 | Exhibits are drawn as vectors in most decks (median drawings per content page 6–57 across decks, median 17); 6 decks have ≥25% raster-exhibit pages | O | EV-*-pdf_drawings_per_page, raster_exhibit_share |
| R-11 | Blind reviewer: reference pages score 4–5 on "consultant_made" (Bain M&A, McKinsey admin, McKinsey DC 5; BCG NYCHA, ADL 4) | O | `visual-rubric.csv` |

## Our engines (comparator we do have)

| ID | Finding | Label | Evidence |
|---|---|---|---|
| M-1 | The slides renderer puts every slide on the Blank layout with no title placeholder | O | EV-f3e822db-layouts_used, EV-f3e822db-titles_in_placeholder_share; ev_c358009fe70ef2cd… |
| M-2 | 64% of colour references are hardcoded RGB | O | EV-f3e822db-scheme_color_share |
| M-3 | 13 body sizes (12–96 pt) and Calibri only | O | EV-f3e822db-font_size_hist, EV-f3e822db-typefaces |
| M-4 | Charts are native with embedded workbooks, and series resolve to cells | O | EV-f3e822db-charts_with_embedded_workbook; P2 pass |
| M-5 | The generator string is python-pptx's default template ("Microsoft Macintosh PowerPoint 14.0000"), so a deck's generator field doesn't prove the authoring tool | O for our file; I in general | EV-f3e822db-generator |
| M-6 | Blind review of the sample deck: overall 3/3/3/3/2 (hierarchy, white space, grid, chart clarity, consultant-made); defects: empty lower halves, an unfinished placeholder (slide 10), a title/content count mismatch (slide 5), no sources on 5–10 | O | `visual-rubric.csv` (A-01…A-overall) |
| M-7 | McKinsey one-shot rebuilds: action titles 73–90%, title size and position 100% consistent, but 0% source lines against 50–87% in the references, Calibri only, thin exhibits | O | EV-3abcdd65-*, EV-fdeda23b-*; `mckinsey-rebuild-test.md` |
| M-8 | One rebuilt chart contradicts its title (forecast "fall 3%" plotted +2.0); P14 initially missed it because 2-value series totals counted as matches; fixed | O | rebuild 3abcdd65 slide 5; P14 failure after the fix |
| M-9 | The brief carried sources ("McKinsey Board Services 2024 Board Survey"), but the rebuild dropped them all, so source loss happens in the spec→render path | O for this run; I for the cause | `rebuild/mck_future_boards/brief.md`, rebuild fdeda23b P13 |
| M-10 | Banker workbook: one number buried in a formula (`Checks!C4` 0.01), no master check flag, 2 cross-sheet links not in the link colour | O | `EV-0fa05e88-*`; ev_932600e103b21aa2… |
| M-11 | Sample workbook: typed-in numbers on Calc (B3:D3) and Outputs (B5), `#DIV/0!` in Outputs!B4, no checks sheet | O | scorecard row "sample workbook" |

## Tooling

| ID | Finding | Label | Evidence |
|---|---|---|---|
| T-1 | REA 6.9.0 runs here via `npx rea-agents@latest`; `inspect-artifact` gives a ZIP/OOXML inventory with SHA-256 per member and an Evidence ID; no MCP registration is needed | O | ev_c358009fe70ef2cd…, ev_932600e103b21aa2… |
| T-2 | github.com/morluto/rea itself returned 403 through this environment's proxy; the review used the npm package and its bundled guidance | O | curl 403 |
| T-3 | PyMuPDF is AGPL-3.0; the app ships pypdfium2 (BSD/Apache). The runtime render check (P1) uses pypdfium2; PyMuPDF is confined to offline calibration tools | O | THIRD_PARTY_NOTICES.md; `validate_pptx._pdf_spans` |
| T-4 | No OCR engine was wired into the validator, so P3's text-in-image check reports `not_run` | O | P3 status |

## Files and identities

| File | sha256 (prefix) | Role |
|---|---|---|
| app/engines/slides/examples/sample.pptx | f3e822db | ours |
| app/engines/sheets/examples/banker.xlsx | 0fa05e88 | ours |
| rebuild of McKinsey DC Transportation | 3abcdd65 | ours (rebuild test) |
| rebuild of McKinsey Future of Boards | fdeda23b | ours (rebuild test) |
| 17 reference PDFs | see `reference-measurements.csv` (`sha8` column) | references |
