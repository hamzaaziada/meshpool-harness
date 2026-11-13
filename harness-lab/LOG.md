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

## 2026-11-12, cycle 8: first real benchmarks (owner approved benchmarking)

- Built `bench/mw_bench.py`, `bench/ver_bench.py`, `bench/report.py` and `bench/spend.py`: same-time runs on
  `deepseek-v4.1-flash` through the gateway. Every agent gets the same task text and caps, grading is official, and
  a spend stop reads the key's spend header. Codex CLI 0.167.2 and pi 1.5.2 were installed here.
- MiniWoB sentinel round 1 (10 tasks, seed 1, all at the same time):

  | Agent | Solved | $/solved | s/solved |
  |---|---|---|---|
  | v5 (v4 + browser fixes) | 10/10 | 0.0016 | 17.1 |
  | pi | 10/10 | 0.0013 | 20.6 |
  | v4 | 9/10 | 0.0022 | 27.5 |
  | Codex | 10/10 | 0.0035 | 36.8 |

  v5 is fastest. terminal took 12 s against 84–114 s for the others, and book-flight 33 s against 47–156 s. pi is
  cheaper because v5 cached only 54% of its input, against pi's 87%.
- Cause found (findings/006): our per-task `prompt_cache_key` made the gateway miss shared prefixes. New `v5` preset:
  v4 with the key off, plus the browser fixes and parallel read-only batches. Keep-warm pings now use max_tokens=24.
- Verified runner checked: the official reference patch for pytest-7490 resolves (swebench pinned to 4.0.4; 5.x
  needs an `image` field). Smoke run: v5 used 18 calls in 80 s, the app agent 25 calls in 135 s; no full misses.
- Fairness: on MiniWoB the browser tool is part of the test setup. Round 2 adds Codex and pi with the fixed tool
  (`codex5`, `pi5`), so a v5 win can be split into harness and tool.

## 2026-11-12, cycle 9: results so far, and observability tools (`MH_OBSERVE`, off by default)

- Ablation, Verified dev, 10 tasks: v5 8/10. Dropping the todo list, same-turn finish and the scope prompt each
  solved 7/10, so all three are dropped. Dropping the todo list cut calls by 22% but lost a task.
- Terminal-Bench dev, 4 tasks: v5 3/4, app agent 2/4, Codex 1/4, Pi 1/4. On sqlite-db-truncate, Codex and Pi
  crashed on the gateway's hex filter (our withhold handles it). When v5 hits the wall cap it loses its usage report;
  to fix.
- App: harness descriptions now say what each is best at, from measured results; the browser fixes are on in the
  app's browser tool.
- findings/007: the model edits nearly blind (0.5 repro runs before the first edit), then iterates (27 calls after).
  Built `probe` and `trace` (core/observe.py) in both loops, with a prompt line. Tested on real pytest and script
  runs. Next: A/B on Verified dev after the test set finishes.

## 2026-11-12, cycle 10: compiler-style feedback and code navigation (owner's idea; off by default)

- `MH_LINT_EDIT`: after each edit or write to a Python file, Ruff's error rules (syntax, undefined names,
  redefinitions; no style rules) run on the file before and after. Only the problems the edit introduced are added to
  the edit result, plus the files changed so far. Python's compile() is the fallback without Ruff.
- `MH_NAV`: `symbol <name>` returns a Python name's definition, signature and every use, via Jedi. On our own app
  package: 46 uses of `Workspace` in 0.8 s.
- Ruff and Jedi are MIT. They are used as tools, not copied, and listed as the optional extra `code-intel`.
- Tests: `test_diagnose.py` (new error reported, old ones not repeated, fallback, definition and uses). 137 pass.
- The queued A/B now has five arms: v5, v5 + observe, v5 + all three tools, app, app + all three.

## 2026-11-13: key budget reached; Verified test set stopped at 8 valid tasks

- The Meshpool key hit its budget cap ($66.41) during the 9th test task. From then on every call failed with HTTP 429
  budget_exceeded. Those 4 tasks (16 rows) moved to results/discarded_infra_rows.jsonl. All benchmark runs stopped,
  under the owner's rule to stop at the first budget refusal. Spend in this session: $63.35 to $66.42.
- Valid comparison, 8 frozen Verified test tasks, all agents at the same time on deepseek-v4.1-flash:

  | Agent | Solved | $/solved | s/solved | calls/task |
  |---|---|---|---|---|
  | Pi | 7/8 | 0.0081 | 122 | 25.0 |
  | Meshpool Code (app agent) | 6/8 | 0.0088 | 181 | 26.2 |
  | Codex | 5/8 | 0.0098 | 109 | 22.6 |
  | Meshpool v5 (engine loop) | 6/8 | 0.0107 | 243 | 28.8 |

- Not run, for lack of budget: the remaining 8 test tasks, the observability A/B (probe, trace, symbol, edit check)
  and Terminal-Bench beyond 4 dev tasks.

## 2026-11-13: new key; code tools A/B and the full Verified test set

- New key, $4 allowed. Spent $2.18 for both runs below.
- Code tools A/B, 10 Verified dev tasks: app 10/10 $0.0069 98 s; app + all tools 9/10 $0.0089 125 s; v5 6/10
  $0.0294 235 s; v5 + observe 9/10 $0.0180 158 s; v5 + all tools 7/10 $0.0278 244 s.
  - Pre-registered rule: keep a flag if it gains solves without costing more per solve. MH_OBSERVE passes it on the
    engine loop. It fails it on the app agent (ceiling: 10/10 without it). MH_NAV and MH_LINT_EDIT fail it.
  - Decision: all three stay off by default. MH_OBSERVE for the engine loop goes to a test-set confirmation next.
- Verified test set completed to 20 tasks (12 new, same time): Pi 19/20 $0.0069 95 s; app 18/20 $0.0079 133 s;
  Codex 17/20 $0.0101 121 s; v5 17/20 $0.0093 184 s. pylint-4970 failed for every agent.
- Result: the app agent beats Codex on solves and cost; Pi still leads on solves, cost and time.

## 2026-11-13: practical check, in-app update, steadier UI; office work handed off

- Real tasks through the app on the real model (testkit/practical). Code, 4 tasks each: Meshpool Code 4/4 (18 s
  average), Pi 4/4 (17 s), Hermes 4/4 (42 s), Auto 4/4 (36 s). Office results and next steps: OFFICE_HANDOFF.md.
- In-app update from GitHub releases (updater.py), tested end to end (testkit/update_flow.sh, 7/7).
- Jitter: the sidebar was rebuilt every few seconds during a run (lost hover, could eat a click); now skipped when
  unchanged. Top bar no longer truncates or wraps at laptop widths. Probe: testkit/jitter.js, 20/20 at 3 sizes.
- Office jobs no longer write into the app's install folder.
- Regression on the mock: e2e 124/124, journeys 20/20, ui_crawl 45/45, crawl_all 31/31, midrun 99/99, unit 149.

## 2026-11-13: office track picked up; forge deck/workbook quality layer

- Forge (`app/engines/forge`, docs in `docs/deck-engine/`): validators for 19 deck rules (P1-P19) and 13 workbook
  rules (X1-X13), deck/PDF/workbook anatomy measurers, rules config, slide and workbook JSON schemas, scorecard,
  9 tests. Exposed in Cowork as read-only tools on meshpool-office: check_deck, check_workbook, measure_office_file;
  every make_deck/make_spreadsheet report now ends with a forge QA line.
- 17 public consulting decks measured (1,186 pages) to calibrate thresholds; blind visual review of our sample deck
  (overall consultant-made 2/5 vs 4-5 for reference pages).
- McKinsey one-shot rebuild test (`engines/forge/rebuild_eval.py`): fails today. Titles and consistency are good;
  0% source lines vs 50-87%, Calibri only, thin exhibits. $0.015 per rebuild; key spend now $2.63.
- Handoff open issues: 1 (cowork default) -> Meshpool Work; 2 (Markdown reports) -> Work writes .docx with
  make_document and runs check_deck/check_workbook on deliverables. Unit tests 151 passed.
