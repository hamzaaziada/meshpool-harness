# Ideas research: cutting cost and time per solved task

Goal: beat Codex CLI and pi on cost and time per solved task with DeepSeek V4.1 Flash
(OpenAI-style chat completions, automatic prefix caching), at equal or better solve rate on
SWE-bench Verified, Terminal-Bench 2 (TB2) and MiniWoB++.

Gathered 2026-11-12. Numbers are the authors' own unless noted. "Effort" means effort to add to
a Python loop that sends OpenAI-style `messages`: S = under a day, M = a few days, L = a week or more.

One rule shapes everything below. DeepSeek bills cached prefix tokens at a small fraction of the
normal input price (historically about 1/10; check the current price sheet). Any change that edits
old messages breaks the cache from that point on. So prune in large, rare steps, never a little
on every turn. Keep the system prompt, tool list and early messages byte-for-byte identical.

---

## 1. Google "Harness²" (Harness Squared), plus its sister project RRSI

Found. Two Google projects match.

**Harness² (Harness Squared)**
- Repo: https://github.com/google-research/harness2 (Apache-2.0, v0.1.0; "not an officially
  supported Google product").
- Paper: "Harness^2: Recursive Agent Harnessing for an Open World", Xu et al.,
  https://arxiv.org/abs/2610.14224
- What it does: the model stays fixed and an "editor" model rewrites the harness. It can edit
  8 parts: system prompt, guardrails, memory, tool guidance, subagents, skills, scripts, plugins.
  - Propose: the editor suggests general ("invariant") edits and task-specific edits.
  - Probe: each candidate harness is run on the same task.
  - Compose: a composer merges the edits by comparing how the runs behaved. No ground-truth
    answer is needed.
  - A second loop keeps a library of adopted harnesses and "evidence-backed lessons" and uses
    it to guide future edits.
- Numbers: one step gives up to +11.5 pts on WorkBuddy-Bench, +4.5 on JobBench, +11.8 pp partial
  pass on Harvey LAB (GLM-5 and Gemini 3.5 Flash). GLM-5 on WorkBuddy-Code went from 19.2 to 14.8
  steps per task. Gains grow with budget (JobBench +4.5 at k=1, +7.5 at k=5). A lesson library
  carried to new tasks gave +0.9 to +5.1 pts. Weaker models gained more. One example edit that
  helped: a guardrail "list all inputs, trace every figure to its source, reopen produced files
  and check them with a script" (JobBench task 43.1 -> 77.8).
- Use for us: offline only. It is a harness *search* method, not a runtime trick. Run it on a
  dev split (never the test split) to find prompt rules and skills for DeepSeek Flash.
  Effort: M to L (needs an eval runner and a strong editor model).

**RRSI (Regularized Recursive Self-Improvement)**, Google Cloud AI Research
- Repo: https://github.com/google-research/rrsi (Apache-2.0). Paper: arXiv 2609.24972
  (https://huggingface.co/papers/2609.24972). Run logs with diffs: https://regularized-rsi.com/
- Same idea, but with rules that stop overfitting and cost creep:
  - token cost must be paid for by score (dCost <= b0 + b1*dScore);
  - a candidate may not drop below best score minus a noise floor measured from repeat runs;
  - an LLM critic rejects edits that mention task names or answers (leakage);
  - edit budget starts at 3-4 changes per round and shrinks to 1; parts that stop helping are pruned.
- Numbers: TB2.1 +6.0; SWE-bench Verified 82.0 -> 83.8; held-out average +3.9 vs +0.9 for the best
  older method. Tokens per task: 2.42M (RRSI) vs 3.80M (unregularized evolution) vs 1.56M
  (original). So it costs more than the original harness, but 36% less than plain evolution.
  Gemini 3.5 Flash with the evolved harness: TB2.1 64.6 -> 78.7, SWE-V 76.8 -> 79.0.
- Use for us: copy the *acceptance rules* into our own harness experiments. Keep a change only
  if it beats the noise floor and pays for its tokens. Effort: S (process), M (automation).

---

## 2. OpenAI Codex CLI (github.com/openai/codex, Apache-2.0)

Paths are under `codex-rs/` at the current `main`.

**Auto-compaction.** `protocol/src/openai_models.rs` `auto_compact_token_limit()`: compact at
90% of the context window (`context_window * 9 / 10`), or a lower configured limit
(`model_auto_compact_token_limit`). Models also use `effective_context_window_percent: 95`
(`models-manager/src/model_info.rs`). Compaction prompt (`prompts/templates/compact/prompt.md`):
"CONTEXT CHECKPOINT COMPACTION ... handoff summary for another LLM": progress and decisions,
constraints, what remains, critical data. The new history is: initial context + recent real user
messages (capped at `COMPACT_USER_MESSAGE_MAX_TOKENS = 20_000`, `core/src/compact.rs`) + the
summary, prefixed with "Another language model started to solve this problem..."
(`summary_prefix.md`). Effect: lets long tasks finish; one big cache break per compaction.
Effort: S.

**Cache key.** `core/src/client.rs`: sends `prompt_cache_key` derived from the session id
(`PromptCacheKey::SessionDerived`), and logs a reason when the key changes. Also sends
`x-codex-*` headers (turn state, window id). For DeepSeek, caching is automatic on the prefix;
the lesson is: keep one stable prefix per session and log the cache-hit tokens from `usage`
every call. Effort: S.

**Tool output truncation.** `utils/string/src/truncate.rs`: keep the head and tail, cut the
middle, insert "…N tokens truncated…". Budget is ~4 bytes per token. Default policy is
10,000 tokens per tool output (`models.json` `truncation_policy`, `DEFAULT_MAX_OUTPUT_TOKENS`
in `core/src/unified_exec/mod.rs`). `core/src/tools/mod.rs` `format_exec_output_for_model`
prepends "Exit code", "Wall time: X seconds" and "Total output lines: N" when cut. Effort: S.

**Shell tool.** `core/src/tools/handlers/shell_spec.rs` + `core/src/unified_exec/`: `exec_command`
returns after `yield_time_ms` (default 10,000; range 250-30,000) with a session id if the command
is still running; `write_stdin` sends input or polls (empty poll waits 5,000-300,000 ms). Each
call takes `max_output_tokens` (default 10,000). This avoids both blocking forever and wasting
turns on `sleep`. Effort: M (needs a PTY process table).

**apply_patch.** `apply-patch/src/parser.rs`, `seek_sequence.rs`. Format: `*** Begin Patch` /
`*** Update File: path` / `@@ context` / `-old` `+new` / `*** End Patch`, also Add/Delete/Move.
No line numbers. Matching tries exact, then ignore trailing spaces, then ignore both ends,
then Unicode punctuation normalization. GPT models are trained on it; for DeepSeek a plain
search/replace edit tool may work as well. Test both. Effort: S (port the parser).

**Retries.** `model-provider-info/src/lib.rs`: request retries 4, stream retries 5, stream idle
timeout 300,000 ms. `codex-client/src/retry.rs`: exponential backoff with 0.9-1.1 jitter.
For a fast model a much shorter idle timeout (30-60 s) cuts tail latency. Effort: S.

**Parallel tool calls.** `core/src/session/turn.rs` sets `parallel_tool_calls: true`;
`core/src/tools/parallel.rs` runs tools that declare `supports_parallel` under a shared read
lock and others under an exclusive lock. Effort: S.

**System prompt.** `core/gpt_5_codex_prompt.md` is ~1,100 words: General, Editing constraints,
Plan tool, Special requests, Final answer style. Tells the model to use `rg`/`rg --files`.
Base instructions for newer models are ~3,400 words (`models-manager/prompt.md`). pi uses a
far shorter prompt; ours should stay short and fixed.

**Reasoning effort.** `models-manager/models.json`: default effort is "low" or "medium" per
model; the user can change it per turn. Lesson: start low, raise only on retry or hard steps.

**Memories (new).** `memories/README.md`: a background job at session start summarizes past
sessions (phase 1 per session, phase 2 consolidation) into memory files read at the next start.

---

## 3. Nous Research Hermes Agent (github.com/NousResearch/hermes-agent, MIT)

Docs: https://hermes-agent.nousresearch.com/docs/user-guide/features/memory

- **What is stored:** two small files in `~/.hermes/memories/`. `MEMORY.md` = agent notes
  (environment facts, conventions, lessons), cap 2,200 chars (~800 tokens). `USER.md` = user
  profile, cap 1,375 chars (~500 tokens) (`hermes_cli/config_defaults.py`). Entries split by `§`.
- **How written:** a `memory` tool with `add`, `patch`, `remove`. If a write passes the cap it
  fails and the agent must merge or delete entries first. So memory never grows.
- **When written:** the agent may write at any time. Every 10 turns a "nudge" asks it to
  consider saving (`memory.nudge_interval: 10`). A background review after a turn may also save
  memory or update skills.
- **How read:** loaded once at session start into the system prompt as a frozen snapshot.
  Writes during a session go to disk but do not change the prompt, to protect the prefix cache.
- **Session search:** all sessions in SQLite with FTS5; `session_search` returns raw messages,
  no LLM call, ~20 ms.
- **Skills:** stored as `SKILL.md` files; created/edited by the agent through a `skill_manage`
  tool. A skill-creation nudge fires after 10 tool iterations (`skills.creation_nudge_interval`,
  `agent/codex_runtime.py`). Only names/descriptions sit in the prompt; the body loads on use.
- **Compression:** default threshold 50% of the window, floored at 75% for windows under 512K;
  a no-LLM "proactive prune" of old tool results runs before LLM summarization
  (`agent/context_compressor_prune.py`).
- Use for us: benchmarks are single-shot, so cross-task memory helps only if we allow it
  (check benchmark rules). The frozen-snapshot and hard-cap ideas are worth copying anyway.
  Effort: S for the capped memory file, M for skills.

---

## 4. Other techniques with evidence

**Observation masking (SWE-agent; JetBrains "The Complexity Trap", arXiv 2508.21433, code
github.com/JetBrains-Research/the-complexity-trap).** Replace tool outputs older than the last
10 turns with a one-line placeholder ("N lines omitted"). Keep all reasoning and actions.
SWE-bench Verified, Qwen3-Coder 480B: raw 53.4% at $1.29 -> masking 54.8% at $0.61 (-52.7%).
LLM summarization: 53.8% at $0.64. Observations are ~84% of a turn's tokens. The paper warns
that with cheap cache hits the gap shrinks, and that badly tuned schemes lose cache. For us:
mask in chunks (for example, every 20 turns mask everything older than 10 turns), so each
prefix is reused many times. Effort: S.

**OpenHands condensers** (https://openhands.dev/blog/openhands-context-condensensation-for-more-efficient-ai-agents,
MIT). `LLMSummarizingCondenser`: when history passes a size limit, keep the first messages and
the recent tail, summarize the middle. Up to 2x lower per-turn cost; SWE-V subset 54% vs 53%
baseline. Same idea as Codex compaction, but triggered earlier. Effort: S.

**Anthropic context editing** (https://www.anthropic.com/news/context-management).
`clear_tool_uses_20250919`: past a trigger (default 100K tokens), drop old tool results but keep
the last N tool uses; a `clear_at_least` setting makes each clear big enough to be worth the
cache break. 100-turn web-search eval: -84% tokens; +29% task score alone, +39% with a memory
tool. We can do the same client-side for DeepSeek. Effort: S.

**Aider repo map** (https://aider.chat/docs/repomap.html, Apache-2.0). Tree-sitter extracts
definitions and references; PageRank ranks files and symbols; a map of ~1,024 tokens
(`--map-tokens`) is put in the prompt. Saves exploration turns (ls/grep/cat) on SWE-bench-style
repos. Put it in the first user message so it is cached. Effort: M.

**Terminus 2** (Terminal-Bench reference agent, https://www.tbench.ai/terminus). One tool: a tmux
session. Each reply is JSON with `analysis`, `plan`, a list of `commands` (each `keystrokes` and
`duration`), and `task_complete`, which must be confirmed twice. Many commands per turn means
fewer turns. On overflow it summarizes and continues. Terminus-KIRA (krafton-ai/kira) adds native
tool calls, a 30 KB output cap, "marker polling" (stop waiting once an echo marker prints, instead
of the full duration), and a completion checklist; it reports ~75% on TB2 with frontier models.
Effort: S (batch commands) to M (tmux + marker polling).

**ForgeCode** (https://forgecode.dev/blog/). They report TB2 38% -> 66% after *enforcing* todo
use (runtime checks, not prompt text), and 81.8% later. They say "please verify your work" in the
prompt did nothing; enforcement did. Also: flat tool schemas with fixed field order (fewer
malformed calls) and parallel tool runs (3-5x faster on multi-file reads). Effort: S.

**SWE-agent ACI** (https://github.com/SWE-agent/SWE-agent, MIT; paper arXiv 2405.15793).
Windowed file viewer (100 lines), capped search results (50 hits), and a lint check that rejects
edits with syntax errors before they land. The paper's ablations show each of these helps solve
rate and avoids wasted turns. Effort: S.

**Synapse / DOM state abstraction for MiniWoB++** (ICLR 2024, arXiv 2306.07863). Send a cleaned,
task-relevant subset of the DOM instead of the full page or a screenshot, and allow a whole
action sequence per call. Reported ~99% average on MiniWoB++ with GPT-3.5-class models. For
MiniWoB++ this is the largest token and turn saver: most tasks become one or two calls. Effort: M.

---

## Ranked top 10 by expected value per effort (cost and speed)

1. **Cache discipline** (fixed prefix, no timestamps or changing text in the system prompt, log
   `prompt_cache_hit_tokens` every call, prune only in big steps). Largest cost lever with
   DeepSeek. Effort S.
2. **Head+tail output truncation** at ~10K tokens with "N tokens truncated" plus exit code and
   wall time, as in Codex. Stops single huge outputs. Effort S.
3. **Chunked observation masking** (mask outputs older than the last ~10 turns, in batches). About
   -50% cost on SWE-bench at equal solve rate in published runs. Effort S.
4. **Multi-command turns / parallel tool calls** (Terminus 2 command lists, Codex
   `parallel_tool_calls`). Fewer turns = less re-sent context and less latency. Effort S.
5. **Compact DOM + batched actions for MiniWoB++** (Synapse style). Cuts most tasks to 1-2 calls.
   Effort M.
6. **Enforced todo/verify step** before `task_complete` (ForgeCode, Terminus double confirm).
   Protects solve rate when we cut cost elsewhere. Effort S.
7. **Codex-style shell with `yield_time_ms` and background sessions**, plus marker polling. Cuts
   wall time on slow commands and wasted "sleep" turns on TB2. Effort M.
8. **Auto-compaction at a fixed threshold** (Codex 90%; or earlier, like OpenHands) with the Codex
   handoff prompt. Lets long TB2 tasks finish. Effort S.
9. **Low default reasoning effort, raise on retry**, and a short stream idle timeout (30-60 s)
   with jittered backoff. Cuts time per task and tail latency. Effort S.
10. **Offline harness search with RRSI acceptance rules** (noise floor, cost must be paid by
    score, one change at a time). Finds DeepSeek-specific prompt fixes safely. Effort M-L.

Next after these: Aider-style repo map for SWE-bench (M), edit lint guard (S), capped memory
file Hermes-style if cross-task memory is allowed (S).
