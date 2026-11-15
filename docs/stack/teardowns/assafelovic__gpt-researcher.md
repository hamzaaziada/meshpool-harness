# Teardown: assafelovic/gpt-researcher

Commit `ddd4db36208d9c8e49f3b2436efda97baecb5433`, Apache-2.0. Tested 2026-11-15.
Labels: [observation] seen in a run or in the code; [inference] my reading; [unknown] not established.

## Tested setup

- Isolated venv, Python 3.12, `uv pip install -r requirements.txt` (about 200 packages: langchain v1, unstructured,
  pymupdf, litellm and others). The repo was put on `sys.path`; nothing went into the harness Python. Runner:
  `scratchpad/td/scripts/run_gptr.py`. [observation]
- LLM: `openai:deepseek-v4.1-flash` for FAST_LLM, SMART_LLM and STRATEGIC_LLM, `OPENAI_BASE_URL=https://llm.meshpool.ai/v1`,
  `OPENAI_API_KEY=dummy`. Nothing else was needed: `utils/llm.py:create_chat_completion` passes OPENAI_BASE_URL to
  langchain's ChatOpenAI. [observation]
- Embeddings: not needed. The gateway's `/v1/models` lists no embedding model (16 chat/image models). Since this commit,
  `CONTEXT_FILTER=keyword` (the default `auto` without a TypeSafe key) ranks chunks by BM25
  (`gpt_researcher/context/lexical.py`, `context/select.py`). `GPTResearcher.memory` is built lazily
  (`agent.py:290`), so no embedding client is ever created in keyword mode. [observation]
- Web search is blocked from this machine, so `REPORT_SOURCE=local`, `DOC_PATH` = 5 Costco SEC documents from sec.gov:
  FY2026 10-K (iXBRL HTML, 1.46 MB), the Q4 FY2026 earnings release (8-K Ex 99.1) and supplement (Ex 99.2), the
  Oct 14 2026 dividend release (8-K Ex 99.1) and the Nov 2026 8-K (director resignation, $30 special dividend).
  `RETRIEVER=duckduckgo` (it fails and returns `[]`, see below). Question: "What changed in Costco's capital
  allocation in FY2026?", `report_type=research_report`. [observation]

Result (one run):

| measure | value |
|---|---|
| wall time | 21.9 s (research 7.3 s, writing 14.7 s) [observation] |
| LLM calls | 3: agent choice (334 in / 55 out tokens), sub-query plan (136 / 38), report (8,560 / 3,147) [observation] |
| tokens | 9,030 in, 3,240 out [observation, from langchain usage metadata] |
| gateway spend | about $0.0066 for the run (key spend 16.826271 -> 16.832868, includes two 1-token probes) [observation] |
| gpt-researcher's own cost estimate | $0.094, 14x too high: it prices an unknown model at default OpenAI rates (`utils/costs.py`) [observation] |
| report | 14.4k characters, APA style, 2 tables, 4 references (document level) [observation] |
| context sent to the writer | 35k characters, 34 chunks: 15 from the 10-K, 10 earnings release, 5 dividend release, 2 each from the others [observation] |

## How it works (pipeline)

1. Agent choice: one FAST call picks a persona ("Finance Agent") and a role prompt (`actions/agent_creator.py`,
   prompt `prompts.py:auto_agent_instructions`). [observation]
2. Planner: `skills/researcher.py:ResearchConductor.plan_research` first runs a web search on the question
   (`_get_initial_search_results`, retriever 0) even in local mode, then `actions/query_processing.py:generate_sub_queries`
   asks STRATEGIC for `max_iterations` (3) plain-language search queries given those search results. Here the search
   failed (DDG/Wikipedia blocked) and the planner got no context, so the queries were generic web queries
   ("Costco 2026 share buybacks and investment strategy changes"). The original question is appended as a fourth
   query. [observation]
3. Sub-queries run in parallel (`_process_sub_query`): in local mode every sub-query searches the SAME loaded
   documents; `context_manager.get_similar_content_by_query` -> `context/select.py:select_context` splits pages into
   1,000-char chunks (100 overlap) and keeps chunks scoring at least 0.5x the best BM25 score, at most 25
   (`KEYWORD_RELATIVE_THRESHOLD`, `KEYWORD_MAX_RESULTS`). Below 8,000 characters of input nothing is filtered.
   [observation]
4. Context compression is selection only: no summarisation of chunks in this path (`_summarize_content` exists for
   other paths). The four contexts are concatenated with "Source: <file name>" headers. [observation]
5. Writer: one SMART call with `prompts.py:generate_report_prompt` (APA in-text citations, reference list, "every
   substantive claim, figure or quote MUST carry an in-text citation"). [observation]
6. Publisher: in the single-agent path, `write_report` returns markdown; PDF/DOCX come from `multi_agents/agents/publisher.py`
   (md2pdf, htmldocx) or the backend. The LangGraph `multi_agents` flow adds browser -> planner (editor) -> human
   review -> parallel researchers -> writer -> **LLM fact checker** (`multi_agents/agents/fact_checker.py`, bounded
   revision loop in `orchestrator.py:_route_fact_check`) -> visualizer -> publisher; reviewer/reviser agents are LLM
   critics against free-text guidelines. [observation, code read; multi_agents not run]
7. Deep research (`skills/deep_research.py`): breadth x depth recursion; each round extracts
   `{"learnings": [{"insight", "sourceUrl"}], "followUpQuestions"}` (`process_research_results`) and recurses on the
   follow-ups; hardcoded to web sources (`report_source=ReportSource.Web.value`, line 455), so not run here. [observation]

## What it does well

- Cheap and fast: 3 calls, 22 s, under one US cent on deepseek-v4.1-flash for a 14k-character report. [observation]
- Every number traces to a source document: a loose trace (digits as stated, or $X.XXXB written as the filing's
  millions figure) matched 89 of 100 figures to the source texts; the 11 unmatched are all computed values
  (differences, 12.6% growth, 30 x 443.3m shares = $13.3bn), and the arithmetic is right. [observation,
  `scratchpad/td/scripts/trace_loose.py`]
- The BM25 keyword filter works without any embedding service and degrades rather than fails (`select.py` falls back
  to keyword on any error). It is benchmarked: `evals/context_filter` freezes the `(sub_query, pages)` pairs once
  (`collect.py`) and replays only the filter (`replay.py`), so the filter is the only variable. [observation]
- Clean provider seam: one env var per role, OpenAI-compatible base URL, `LLM_KWARGS` override applied last. [observation]
- Event log of the run (`_emit_research_event`, `utils/logging_config.py:JSONResearchHandler.log_event`: query planned,
  sub-query, content found with sizes). [observation]

## Where it breaks

- Wrong conclusion from right numbers. The report says Costco "deleveraged by about $1.8 billion" because long-term
  debt excluding current portion fell from $5,713m to $3,914m; the 10-K shows the current portion rose from $75m to
  $2,248m and total long-term debt rose from $5,805m to $6,175m. The same paragraph lists $496m of new debt and $69m of
  repayments, which contradicts its own conclusion. No step checks this. [observation]
- Misses the facts that answer the question. The 10-K states that the quarterly dividend rose from $1.30 to $1.47 in
  April 2026, that a $15 special dividend ($6,655m) was paid in 2024, and that $1,115m of the $4,000m buyback
  authorisation (expiring January 2027) remained. None reached the writer. The report instead calls the October
  $1.47 declaration a post-year-end increase, says "the press release does not explicitly state the prior rate", and
  calls the $30 special dividend "a departure from Costco's historical preference" - false given the 2024 special.
  [observation] Cause: one 220k-character HTML file becomes one "page"; four near-identical queries pull overlapping
  chunks; there is no gap check or second round in `research_report`. [inference]
- Citations are document level only: "(Costco Wholesale Corporation, 2026a)" with no page, section or passage. Of 68
  sentences or table rows with figures, 18 carry an in-text citation (tables get one "Source:" line). Local documents
  are cited by file name (`document/document.py` sets `url = basename(source)`; PyMuPDF's page metadata is dropped).
  [observation]
- The planner depends on web search even in local mode; with search blocked it plans blind. The DDG retriever prints
  the error and returns `[]` (`retrievers/duckduckgo/duckduckgo.py`), other retrievers raise. [observation]
- Cost tracking is wrong for non-OpenAI models (14x here). [observation]
- No source tiers, dates or independence checks; no deterministic verifier; the multi-agent "fact checker" is one LLM
  call that returns "None" or free text. [observation]

## Compared with our design

| | gpt-researcher | ours (`engines/research`, `engines/forge/research.py`) |
|---|---|---|
| planning | 3 LLM web queries from initial search results | dossier: fixed analyst checklist (`dossier.py:CHECKLIST`) + brief questions, hand-written query templates (`_queries`); engine.py: model writes an issue tree |
| retrieval | BM25 over 1,000-char chunks, relative threshold 0.5, top 25 per sub-query | BM25 x tier x recency over 300-900-char passages, `per_source` cap (`library.py:Corpus.find`) |
| compression | chunk selection only | passage selection; `_pick_model` picks fact sentences |
| citations | APA, document level | passage ids with page (`S3.p12`), verified by `verify.py:check` |
| verification | none (multi_agents: LLM fact checker) | deterministic: numbers must be in cited passages, `[calc:]` arithmetic re-run, tier and source-count rules |
| gaps | none in research_report; deep research recurses on follow-up questions | dossier lists unanswered checklist questions in `open`; engine.py asks for a second round in the prompt |

We ran our own `Corpus.find` on the same five files with the same four queries (no LLM, `scratchpad/td/scripts/ours_find.py`):
- With `per_source=2` (what `investigate()` and the engine use) it also misses the dividend raise, the buyback
  authorisation and the 2024 special dividend; with `per_source=6` it finds all three. [observation]
- Our duplicate check marked the Q4 earnings release and its supplement as "dup of" the short Oct dividend release
  (`library.py:Corpus.add_text`, overlap/min(|A|,|B|) = 0.82 and 0.61 because the short release is mostly shared
  boilerplate; Jaccard 0.24), so `find()` skips the earnings release entirely. This is a bug in our code. [observation]
- Our verifier would also pass the "deleveraging" paragraph: 3,914 and 5,713 are in the cited passage. [inference]

## What we take

1. Follow-up-question recursion with a stop rule: `skills/deep_research.py:process_research_results` returns
   learnings plus follow-up questions, and `deep_research()` recurses with breadth/depth halving. Put a one-call gap
   step in `engines/forge/research.py:investigate` after the first gather: for each question with `status != found`
   or fewer than k facts, ask for 2 follow-up queries and run one more `gather`. Bounded by the existing time budget.
2. Relative-threshold selection instead of fixed per-source caps: `context/lexical.py:LexicalContextCompressor.select`
   keeps chunks scoring >= 0.5 x best, up to 25. In `library.py:Corpus.find`, add `rel_threshold` and raise
   `per_source` for provided documents and tier-1 filings (one 10-K is many documents in one file); keep the cap for
   web sources.
3. Frozen-retrieval evaluation: `evals/context_filter/collect.py` + `replay.py` record `(sub_query, pages)` once and
   replay only the selection step. Do the same for `Corpus.find`/`_pick_model` with saved `sources.json` corpora, so
   retrieval changes are measured without web noise or LLM cost.
4. Persona/role prompt per question type (`actions/agent_creator.py`) is cheap (390 tokens); low value for us since our
   prompts are already finance-specific. Skip.
5. Do not take: document-level APA citations, the LLM-only fact checker, web-search-dependent planning, the cost
   estimator.

## Licence note

Apache-2.0 at the pinned commit (ledger: "evaluate as research base"). Code may be copied with the licence and NOTICE
kept and changes marked. Its dependency tree is large (langchain v1, unstructured, litellm, pymupdf - pymupdf is
AGPL-3.0, which matters if we ever ship that loader); take ideas and small functions, not the package. [observation
for licences of gpt-researcher; pymupdf AGPL is from its own licence, not checked at a commit: unknown for this pin]
