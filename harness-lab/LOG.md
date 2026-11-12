# Lab log

One entry per cycle: change, flag, offline evidence, expected effect, how a paid run confirms it.

## 2026-11-12, cycle 0: set-up and cache audit

- Merged the experiment package's app-safety fixes into the repo's harness: timing log and token log are opt-in
  (no fixed machine path), the failure catalog ignores file errors and can be moved or turned off, and `run_task`
  is exported as the safe entry point. Added `test_app_safety.py` and the missing router stress fixture. 73 tests pass.
- Cache audit of run C (findings/001): about 70% of our uncached input on Terminal-Bench is avoidable full cache
  misses after gaps over 40 s. Uncached input is the largest cost item.

## 2026-11-12, cycle 1: keep-warm pings (`MH_KEEPWARM_S`, off by default)

- Change: while a turn's tools run, a background thread re-sends the conversation up to the last tool result with
  `max_tokens=1` every N seconds (at most 20 per batch). The ping is an exact prefix of the next real request. A
  failed ping is ignored. Usage now also counts `full_miss_calls` and `full_miss_tokens` on every run.
- Offline evidence: `test_keepwarm.py` (pings are valid prefixes, off by default, failures never break a run).
- Expected effect: if misses are idle eviction, Terminal-Bench uncached input drops from about 84K to about 30K
  tokens per task, roughly a third off list cost per task. If misses are routing to a cold backend, pings may land on
  another backend and do nothing; then item 2 is the fix.
- Paid confirmation (needs approval, about $0.50): 4 Terminal-Bench dev tasks with long builds, arms
  `MH_KEEPWARM_S=20` and off, side by side. Keep if `full_miss_tokens` per task falls by half or more and solves do
  not drop.

## 2026-11-12, cycle 2: parallel read-only batches (`MH_PARALLEL_MANY`, off by default)

- Fix to cycle 1: the keep-warm ping used task id `<id>-kw`, which gives it a different `prompt_cache_key` and
  session header than the real call. If the gateway routes by key, the ping would warm the wrong backend. It now
  keeps the real task id; `role="keepwarm"` marks it. Test added.
- Checked backlog item 5: our tool output cap (3,000 head plus 3,000 tail chars) is already tighter than Codex's.
  No change.
- Change: `bash_many` describes its commands as independent but ran them one by one. With the flag on, a batch whose
  commands are all read-only runs on up to 4 threads. `read_only()` is a conservative allow-list (cat, grep, git log,
  sed -n, version checks ...). Any redirect, `$( )`, heredoc, `-exec`, `-delete`, `sed -i`, install, script run or
  unknown program keeps the batch in order. Output text is identical to the sequential run.
- Offline evidence: `test_parallel_many.py` (3 commands of 0.6 s: 1.8 s in order, under 1.3 s in parallel, same
  output; a write followed by a read stays in order; capped outputs get unique files). Tests no longer write the
  failure catalog into the package folder (`tests/conftest.py`).
- Expected effect: small. It saves tool time only on turns that batch slow reads (large-repo greps, `find`, `git log`
  on big histories). Model time dominates our wall time, so expect under 5% wall time on Terminal-Bench.
- Paid confirmation: not worth a run on its own; bundle it with the next sentinel A/B.
- Open question for the owner: offline work cannot show why book-flight and terminal take 20+ turns, or whether
  keep-warm works. Both need a few real transcripts (cents per MiniWoB task).

## 2026-11-12, cycle 3: cause of the cache misses (analysis only)

- Joined the call ledger's cut-off calls with the per-call cache log (findings/002). 73% of avoidable full-miss
  tokens come on the call right after a 20 s first-token cut-off. Those retries miss the cache 46% of the time,
  against 7% for other calls.
- Conclusion: v4's 90 s first-token wait should fix most of the cost gap as well as the time gap, with no extra calls.
  Keep-warm drops to a second-order item.
- Also found: per model call, Codex took about 9 s on Terminal-Bench and 4.5 s on Verified; we took about 20 s and
  11 s. Codex makes as many calls per task as we do or more, so the speed gap is per-call wait, not call count.
  Cut-offs explain part of it (14% of calls lost 20 s or more each).
- No code change. Next paid step (needs approval): v3 against v4 on 4 Terminal-Bench dev tasks, same time, about
  $0.60. Compare cut-offs, full misses, uncached tokens and seconds per call.

## 2026-11-12, cycle 4: check and finish in one turn (`MH_CODE_FINISH_SAME_TURN`, off by default)

- Change: one prompt line for the code engine. When the model expects its check to pass, it calls the check and
  finish together. The browser engine has done this since v3.
- Safety: the finish guard already needs a passing command after the last edit, and the evidence must cite it. A
  failing check in the same turn gets the finish refused, and the model sees the failing output. Unit-tested.
- Expected effect: one model call less per solved task, about 3% of calls on Verified and 5–10 s each.
- Paid confirmation: bundle with the next sentinel A/B. Watch for new wrong finishes (none expected, given the guard).

## 2026-11-12, cycle 5: scope check before finish (`MH_SCOPE_CHECK`, off by default)

- Evidence: findings/003. Both v3 Verified misses added changes past the core fix, one of them in an unrelated file.
- Change: once, at the first finish of a code task, if `git diff --numstat` shows more than one non-test file or more
  than 30 non-test lines, the harness lists the size per file and asks the model to revert unneeded edits and
  finish again. Test files are ignored. A git error shows nothing.
- Offline evidence: `test_scope_check.py` (one-file fix: nothing shown; two files: shown once, test dir left out; off
  by default).
- Paid confirmation: Verified dev split, v4 against v4 plus this flag. Keep under the pre-registered rule, with no
  new wrong finishes.

## 2026-11-12, cycle 6: MiniWoB long tail and the two loops

- Built `mw_probe.py`: drives our browser tool by hand on real MiniWoB pages, with no model.
- findings/004: book-flight and terminal (about 40% of a round's turns for every agent) fail on four tool problems:
  read-only fields hang 10 s, autocomplete menus are not listed yet, red-outline errors are invisible, and
  key-driven widgets ignore set values. Four flags fix them. Both tasks reach reward 1 by hand. Normal inputs and
  textareas keep the fast path. Tested on a local page with real Chromium (`test_browser_flags.py`).
- findings/005: the app's chat agents run their own loop (`agent/core.py`), so the benchmark measures a sibling of
  what users run. Recommendation: shared helpers for finish checks and cache counters, then benchmark the app agent.
- Python Playwright installed in this container for the probe; Chromium is preinstalled.

## 2026-11-12, cycle 7: one core for both loops (owner approved)

- Shared modules:
  - `core/webfix.py`: browser fixes, now also in the app's browser tool;
  - `core/metrics.py`: cache-miss meter in both loops;
  - `core/finishing.py`: scope check in both loops, worded "reply" for the app agent.
- `run_task(engine="agent" | "agent-work")` runs the app's chat agent headless, so it can be benchmarked. The Agent
  class gains an optional `sessions_dir`, and nothing else changes for the app.
- Tests: `test_agent_headless.py` covers the run staying inside the workdir, errors returned rather than raised, and
  the scope check shown once.
- With every flag off, the app behaves as before. The always-on change is two new usage counters
  (`full_miss_calls`, `full_miss_tokens`) in the saved session usage.
- Checked by hand: the app's browser tool on a local page shows (read-only) and (invalid), and sets a picker field
  at once.
