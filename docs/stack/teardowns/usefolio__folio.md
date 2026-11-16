# Teardown: usefolio/folio

Commit `8b6ec1bb25013ae3ac1e751d6391569034796c25` (2026-04-06). **No LICENSE file**: all rights reserved under our rule.
Ideas only; nothing copied. Labels: [observation] seen in the repo; [inference] my reading; [unknown] not established.

## Tested setup

Not run. The repository holds no application code (0 source files): a README, `CLAUDE.md`, two agent skills
(`skills/criteria-search/SKILL.md`, `skills/sensemaking/SKILL.md`, each with `evals/evals.json`), an install skill and an
MCP plugin manifest pointing at a local macOS app (`http://127.0.0.1:8765/mcp`). The app itself is a closed download.
[observation]

## What it does well

- **Criteria-based search** (`skills/criteria-search/SKILL.md`): turn a search intent into 5-10 explicit, falsifiable
  criteria answerable from one document alone; separate hard filters from soft signals; show the criteria to the user
  before spending; evaluate each document against each criterion as an independent yes/no column with evidence; rank by
  number of criteria met (all / N-1 / at least M); report per-criterion pass rates and flag **non-discriminating
  criteria** (pass or fail on every document) for revision. [observation]
- **Sensemaking pipeline** (`skills/sensemaking/SKILL.md`): orient (free metadata and sampling) -> scope -> structure
  (classify) -> extract (schema) -> narrow (views/filters, no token spend) -> deepen (expensive prompt only on the
  narrowed view) -> report with the funnel counts (2,000 contracts -> 47 reviewed). Enrichments are approval-gated with
  a cost estimate; orientation and narrowing cost nothing. [observation]
- Skill evals as prompt + expected-behaviour pairs (`evals/evals.json`). [observation]

## Where it breaks

- Nothing to run or reuse: the engine is closed and the repo has no licence. [observation]
- One LLM call per document x criterion scales cost linearly; they rely on narrowing first. [inference]

## What we take (ideas only)

1. Criteria columns for `engines/research/grid.py`: before `grid()` answers questions per document, add an optional
   `criteria` mode that asks yes/no falsifiable questions per document with a cited passage, then ranks rows by
   criteria met and reports per-criterion pass rates and non-discriminating criteria. Useful for CIM screening and peer
   filing triage. Pointer: `grid.py:grid` / `_cell` already produce cited, checked cells; a yes/no cell is a special case.
2. The funnel report (documents in -> classified -> extracted -> narrowed -> deep-read) as a section of grid output.
3. Free orientation before spend: `Corpus.overview()` already plays this role; show it with a cost estimate before a
   large `grid()` run.

## Licence note

No LICENSE file at the pinned commit (ledger: "ideas only"). Do not copy the skill texts or evals.
