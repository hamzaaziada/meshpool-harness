# What the harness does, in three buckets

Each line: what we built, why, and where the idea came from. Numbers live in `results.md`.

## 1. Agent loop (`meshpool_harness/core/loop.py`, `guards.py`)

- **Append-only history, byte-stable prefix.** System prompt + tool schemas never change inside a session, so the
  provider cache serves most input. Measured cache hit rate: 79–95% of input tokens. (Provider prompt-caching docs.)
- **Finish must be earned.** `finish` is rejected unless a check command passed after the last edit, the cited
  evidence matches that command, requested files exist, and no existing test was modified. (Our failure catalog.)
- **Progress guards.** After 12 turns without an edit the agent must commit to a hypothesis; a third near-identical
  search is answered with "the results will not change"; a 5-turns-left warning. Found on a task where the agent
  spent 40 turns searching and never edited; with the guard it solved it.
- **Repeat/quote guards.** 3 identical failures force a new approach (5 abort); after a real error the next turn
  must engage with the error line.
- **Engine checks.** Each engine adds a deterministic gate: regression tests (code), number tracing and quote
  checks (data), recalculation and structure (sheets), layout QA (slides), plain-language grade ≤ 9 (all text).
- **Optional: k parallel attempts with cross-test voting** (each attempt's own repro tests judge the others, no
  hidden tests) and **one cross-family review call** at finish. (Agentless-style sample-and-select; measured, not
  assumed.)
- **Retries.** Upstream 5xx are absorbed by the guard proxy and the client, so agents never die on a 502.

## 2. Tools (`meshpool_harness/core/tools.py`, engine helpers)

- **8 core tools** with strict JSON schemas and precise error messages: `read_file` (line ranges), `grep`, `glob`,
  `edit` (exact, unique, staleness-checked), `write_file` (append for large files), `bash`, `run_tests`, `finish`.
  Plus `load_skill` and `need_context`. (SWE-agent's agent-computer-interface findings: few, simple, informative tools.)
- **Lint on edit.** A Python edit that would not parse is rejected before it lands. (SWE-agent ACI.)
- **Container-native execution.** When the task runs in Docker, commands execute inside the container directly, so
  the model never writes nested `docker exec … bash -lc '…'` quoting; file tools map container paths. Our own find:
  quoting errors were a silent source of wasted turns.
- **Output truncation.** Head and tail kept, full output saved to a file with its path. (OpenHands/SWE-agent.)
- **Model-free helpers.** Locate hints from the issue's identifiers; issue code examples executed before turn 0
  (each expression prints its value or exception); banker-style workbook builder; deterministic deck renderer.

## 3. Context (prompt layout)

- **Context layout.** [stable system + tools] → [task + hints] → [history]. Variable content never enters
  the cached prefix.
- **Compaction only when it pays.** Old tool outputs are elided only above ~150k tokens. On DeepSeek, re-reading
  cached history costs ~1/50 of fresh input, so compacting earlier costs more than it saves for typical tasks.
  (Hermes compresses at 50% of the window; we measured that this is not the cheap choice for DeepSeek.)
- **Subagents get packets, not chats.** Planner jobs and reviewers see only their sub-request and exact inputs.

## Not built (on purpose)

- **Reasoning-mode switching.** Meshpool does not expose a distinct DeepSeek reasoning mode (probed: no reasoning
  tokens at any `reasoning_effort`).
