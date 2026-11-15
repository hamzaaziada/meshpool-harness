# Teardown: virattt/dexter

Commit `534f6be455523c5926eba72bdaf4f914555be5c9` (2026-09-23). TypeScript on Bun. **No LICENSE file** (the README says
MIT): under our rule this is all rights reserved. Ideas only; nothing below is copied, and nothing may be.
Labels: [observation] seen in the code; [inference] my reading; [unknown] not established.

## Tested setup

Not run. It needs Bun, an OpenAI-family key and a paid Financial Datasets API key for its finance tools; the ideas are
readable from the code. Read: `src/agent/agent.ts`, `scratchpad.ts`, `microcompact.ts`, `prompts.ts`,
`src/tools/finance/*.ts`, `src/skills/*/SKILL.md`, `src/evals/*`. [observation]

## What it does well

- **Append-only JSONL scratchpad per query** (`src/agent/scratchpad.ts:Scratchpad`): `init`, `tool_result` (tool name,
  args, full parsed result) and `thinking` entries, one file per query under `.dexter/scratchpad/<timestamp>_<hash>.jsonl`.
  It is "the single source of truth for all agent work on a query"; context clearing and compaction happen in memory
  only, so the file always holds the full record. [observation]
- **Soft tool limits and repeat detection**: per-tool call counts (default 3 per query) and Jaccard word overlap >= 0.7
  against earlier queries to the same tool. It never blocks; it injects a warning message into the next turn
  (`canCallTool`, `formatToolUsageForPrompt`, `agent.ts` after tool execution). [observation]
- **Context hygiene without an LLM**: `microcompact.ts` replaces old read-only tool results with
  "[Old tool result content cleared]" once more than 8 exist (keeps the last 4) or they exceed 80k tokens; large
  results are written to disk with a preview and the model is told to `read_file` sections
  (`utils/tool-result-storage.ts:persistLargeResult`, `utils/tool-result-budget.ts:enforceResultBudget`); old
  reasoning is stripped except the last 2 turns; context-overflow errors truncate and retry. [observation]
- **Meta-tools with an internal router**: `get_financials`, `get_market_data`, `read_filings` take one
  natural-language query; a small routing LLM call picks sub-tools (income statement, segments, key ratios...) and
  runs them in parallel with timeouts (`tools/finance/get-financials.ts`). `read_filings` plans ticker + filing types +
  specific 10-K/10-Q/8-K items, max 3 filings per call, and reads only those items (`tools/finance/read-filings.ts`).
  [observation]
- **Skills as checklists**: `src/skills/write-memo/SKILL.md` is an 8-step checklist (frame the trade with a variant
  view; gather data in parallel - 5y statements, segments, 8 quarters of beats/misses, insiders, holders, 10-K Item 1
  and 1A, latest 10-Q; bear/base/bull with probabilities summing to 100%; optional DCF anchor; draft; **self-critique
  pass**; render HTML; header summary). `dcf/SKILL.md` has sector WACC tables. [observation]
- **Rubric evals**: `src/evals/dataset/finance_agent.csv` (question, expert answer, type, expert minutes, rubric) with
  atomic rubric items of operator `correctness` or `contradiction`; `evaluator.ts` asks a judge model to score each item;
  `exactPass` only when every correctness item holds. [observation]
- Parallel subagents for independent sub-tasks (`spawn_subagent`, several in one turn), each isolated and returning
  one answer. [observation]

## Where it breaks

- "Task planning" and "self-reflection" in the README are not separate components at this commit: `agent.ts` is a
  plain tool loop that ends when the model answers without tool calls or hits `maxIterations`. Planning lives in the
  prompt and in skills; reflection is a step in the memo skill's checklist. [observation]
- No citation discipline: tool results carry source URLs but the answer format has no per-claim citation rule and no
  verifier. [observation]
- Data comes from one paid API (Financial Datasets); filings are read through it, not from EDGAR directly. [observation]
- Repeat detection on word overlap will miss paraphrased retries and flag legitimate period-by-period calls. [inference]

## What we lack (compared with our engine)

1. A per-run JSONL research log. Our loop has `.run/` state and `sources.json`, but `investigate()` returns only a
   summary log (`questions` with status). Dexter-style: append every search query, every URL read (with status/failure),
   every passage picked and every fact kept or dropped (`_holds` false) to `research.jsonl` next to `sources.json`.
   Pointer: `engines/forge/research.py:investigate` (inside `gather` and the picking loop) and
   `engines/research/library.py:Corpus.search_and_read` / `_read_rows`.
2. Repeat-query warnings in the agentic loop. `engine.py` lets the model call `c.search_and_read` freely; add a
   per-run query history in `Corpus` (normalised token sets) and return "similar to an earlier query: <q>" in the
   result instead of silently re-searching. Pointer: `library.py:Corpus.search`.
3. Section-addressable filings. `read_filings` asks for "10-K Item 7" rather than the whole filing. Our
   `Corpus.add_edgar` loads a whole filing; `split_passages` keeps the nearest heading but `find()` cannot filter by
   item. Add an `item` field (Item 1, 1A, 5, 7, 7A, 8) when splitting EDGAR documents and a `section=` filter in
   `Corpus.find`. Capital-allocation questions live in Items 5, 7 (Liquidity) and 8 (Equity note).
4. A self-critique step with a checklist before finishing (memo skill step 6). Our engine has the deterministic
   verifier, which is stronger on numbers, but nothing checks the argument (see the gpt-researcher "deleveraging" error).
5. Atomic-rubric evals with `contradiction` items. We have the vals-ai finance-agent set in the ledger; adopt the rubric
   format (correctness + contradiction operators, exact pass) for our research eval. Write our own judge prompt.

## What we take

Ideas only (no licence): the JSONL research log, soft repeat warnings, item-level filing reads, checklist skills with
a self-critique step, and correctness/contradiction rubric items. All re-implemented from scratch in Python.

## Licence note

No LICENSE file at the pinned commit; README's "MIT" claim does not grant a licence under our rule
(ledger: "ideas only"). Do not copy code, prompts, skill files or the eval CSV (the CSV's rows look like the vals-ai
finance-agent questions [inference]; take those from vals-ai/finance-agent, MIT, instead).
