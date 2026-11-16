# Research agents and document parsers: what to adopt

Date 2026-11-15. Notes per repo in this folder: `assafelovic__gpt-researcher.md`, `virattt__dexter.md`,
`AI4Finance-Foundation__FinRobot.md`, `usefolio__folio.md`, `docling-project__docling.md`, `datalab-to__marker.md`,
`opendatalab__MinerU.md`. Labels: [observation] measured or seen in code; [inference] my reading; [unknown].
Working files, scripts and outputs: `/tmp/claude-0/-home-user-meshpool-harness/24bc71e4-4861-5705-9681-86c31317da60/scratchpad/td/`.

## What was tested

- gpt-researcher, local documents only (web search is blocked here), deepseek-v4.1-flash via the gateway for all LLM roles,
  BM25 keyword context filter (no embedding model on the gateway). Question: "What changed in Costco's capital allocation
  in FY2026?" over the FY2026 10-K and four 8-K documents. 22 s, 3 LLM calls, 9,030 in / 3,240 out tokens, about
  $0.0066. Every figure traced to a source, but the report drew a false conclusion ("deleveraged by $1.8bn" from a
  current-portion reclassification) and missed the April dividend raise ($1.30 -> $1.47), the 2024 $15 special dividend and
  the buyback authorisation, all stated in the 10-K. Citations are document level. [observation]
- The same five files through our `Corpus.find` (no LLM): with `per_source=2` (our default in `investigate()`) we miss the
  same three 10-K facts; with `per_source=6` we find them. Our duplicate check marks the Q4 earnings release as a duplicate
  of a short dividend press release, so `find()` hides it. [observation]
- docling and marker on a Costco 10-K (HTML) and two Cleopatra Hospitals Group (Egypt) PDFs, one with a scanned balance
  sheet. docling: best table structure, full page/bbox provenance, 4.6-11 s/page on CPU, drops chart text from Markdown.
  marker fast: 5-20x faster, good rows on the scan, but 18-70 numbers per document turned into LaTeX math spans. Our
  current PDF path (pypdf) is fastest and has the best numeric recall on text-layer pages, but no table structure and,
  in this install, no OCR: the scanned balance sheet comes back empty. [observation]
- dexter, FinRobot, folio, MinerU: code read, not run. [observation]

## Ranked adoption list

1. **Fix duplicate detection and per-source caps in our Corpus** (our bug, highest value, small change).
   `engines/research/library.py:Corpus.add_text` uses overlap / min(|A|,|B|) > 0.6, so a long document containing a short
   one's boilerplate becomes its "duplicate" (0.82 here; Jaccard 0.24) and `Corpus.find` skips it. Normalise by the new
   document's size (or use Jaccard) and do not mark provided documents or distinct SEC accessions as syndicated copies.
   In `Corpus.find`, add a relative score threshold (gpt-researcher `context/lexical.py:LexicalContextCompressor.select`:
   keep >= 0.5 x best, cap 25) and a higher `per_source` for tier-1 filings and provided documents; callers:
   `engines/forge/research.py:investigate` (`corpus.find(q, k=6, per_source=2)`), `grid.py`. [observation for the bug;
   inference for the fix]
2. **Prior-period XBRL facts** (FinRobot `edgar_provider.py:_companyfacts_annual_series` idea). In
   `engines/forge/research.py:xbrl_facts`, emit each tag's value for the prior fiscal year too, and a derived change fact
   in `economics_facts`. "What changed" questions (dividends, buybacks, capex, `LongTermDebt` vs `LongTermDebtNoncurrent`
   vs `DebtCurrent`) then get deterministic answers; the debt pair would have exposed the false deleveraging claim.
   [inference]
3. **One follow-up round for gaps** (gpt-researcher `skills/deep_research.py:process_research_results` + recursion).
   In `investigate()`, after the first `gather`, for questions with no or few facts ask one cheap call for 2 follow-up
   queries each and gather again inside the existing time budget; `dossier.py` keeps listing what is still unanswered in
   `open`. [inference]
4. **Docling as an isolated table-and-scan parser** (MIT). Keep pypdf as the default reader; call docling (own venv or
   container, HTTP) from `meshpool_harness/docread.py:_pdf` for pages with no text layer, tabular pages and documents
   marked as financial statements, with `page_range`. Read its JSON (texts incl. picture children, table cells with
   header rows, `prov.page_no`/`bbox`), not its Markdown. Emit `## Page N` blocks plus "row label | column header: value"
   lines so `library.py:split_passages` keeps page numbers and each number keeps its period. Add an optional `bbox` to
   `Passage`. [observation for quality; inference for design]
5. **Numbers rendered from fact ids, and label-aware checks** (FinRobot bull/bear instructions: "you may not write any
   number yourself - cite its evidence_id"; `artifact/contract.py` pure clauses). Let writers use `{F12}` placeholders in
   dossier/forge text and add `verify.py:check` rules: a bare number outside a placeholder, citation or `[calc:]` is a
   problem; direction words ("reduced debt", "raised the dividend") must agree with the sign of the cited change; every
   figure carries a period. Our current verifier would pass the gpt-researcher error. [inference]
6. **A per-run research log** (dexter `src/agent/scratchpad.ts` append-only JSONL; FinRobot `audit/transcript.py` with a
   secret scrubber). Append every query, URL read or failed, passage picked, fact kept or dropped (`_holds` false) to
   `research.jsonl` next to `sources.json`; in the agentic engine, warn on near-repeat queries (Jaccard >= 0.7) from
   `Corpus.search` instead of re-searching. Pointers: `investigate.gather`, `Corpus.search_and_read`, `Corpus._read_rows`.
   [inference]
7. **Frozen-retrieval and rubric evals**. gpt-researcher `evals/context_filter/collect.py` + `replay.py`: record
   `(question, corpus)` once, replay only `Corpus.find`/`_pick_model`, so retrieval changes are measured without web noise.
   Rubric items with `correctness` and `contradiction` operators and exact-pass scoring (dexter's eval format; take the
   questions from vals-ai/finance-agent, MIT). Add the Costco capital-allocation case from this test (expected: April
   dividend raise, 2024 special, $30 special, buyback authorisation, no "deleveraging"). [inference]
8. **Item-level EDGAR sections** (dexter `tools/finance/read-filings.ts` reads 10-K Items, not whole filings). Tag
   passages from `Corpus.add_edgar` with their Item (1, 1A, 5, 7, 7A, 8) and add `section=` to `Corpus.find`;
   capital-allocation answers live in Items 5, 7 and 8. [inference]
9. **Untrusted-text wrapping** (FinRobot `engine/compute/coordinators/news.py:sanitize_untrusted_block`): strip tags and
   leading headings from web/passage text and wrap it in a tagged block in `forge/research.py:_pick_model` and
   `dossier.py:_chat_json` prompts. [inference]
10. **Criteria columns for the document grid** (folio `skills/criteria-search`, ideas only): yes/no falsifiable criteria
    per document with a cited passage, rank by criteria met, flag criteria that pass or fail everywhere. Pointer:
    `engines/research/grid.py:grid`/`_cell`. [inference]
11. **marker as a fast scan/table fallback, licence-gated**: only behind the same service seam, with cell clean-up
    (collapse `\$...\$`, digits checked against the text layer) and footing checks; weights need a Datalab licence once we
    pass USD 5M funding or revenue. [observation for behaviour; licence per ledger]
12. **MinerU: not now.** Apache-2.0 plus a commercial threshold (100M MAU or USD 20M monthly revenue), a mandatory
    "uses MinerU" notice for online services, default-on telemetry, heavy models. Revisit only if docling's OCR fails on
    Arabic-script or low-quality scans. [observation]

Not adopted: gpt-researcher as a package (document-level citations, web-dependent planning, wrong cost tracking,
AGPL pymupdf in its loader tree [unknown at pin]); dexter and folio code (no licence); FinRobot's framework and paid-data
pipelines.

## Spend

Gateway key spend 16.826255 at start, 16.832868 right after the only LLM run (gpt-researcher, about $0.0066 incl.
1-token probes), 16.925148 at the end: +$0.099 overall. No LLM calls were made after the gpt-researcher run except
1-token spend probes, so the remaining ~$0.092 came from other use of the same key [inference]. Under the $0.40 cap
either way.
