# Meshpool Harness

A small, cheap, model-agnostic agent harness. One command routes a request to an engine (code, terminal, data,
sheets, slides, browser) and runs it on any OpenAI-compatible model. Built and measured on DeepSeek V4.1 Flash
against Junie, Claude Code, Codex and a plain bash-only agent on the same tasks: see `results.md`.

## Benchmarked settings

`meshpool run` turns on the benchmarked flag set for code and browser tasks (`ENGINE_FLAGS` in
`meshpool_harness/cli.py`); explicit `MH_*` settings win. Results: `docs/BENCHMARKS.md`.

## Quickstart (your computer)

```bash
pip install -e .                    # adds the `meshpool` command
pip install playwright              # optional: browser engine (computer use)
export MESHPOOL_BASE_URL=https://llm.meshpool.ai/v1   # or any OpenAI-compatible endpoint
export MESHPOOL_API_KEY=...         # never commit keys
export MH_MODEL=deepseek-v4.1-flash # any model id the endpoint serves
export MH_WIRE=responses            # optional: Responses route (much better prompt caching on Meshpool)

meshpool run "Fix the failing test in tests/test_api.py" --workdir .          # coding
meshpool run "Open https://example.com and tell me the page title" --engine browser   # computer use
meshpool run "Make a 6-slide board deck from sales.csv" --files sales.csv      # office
meshpool work github:owner/repo#12 --workdir . --report                        # GitHub / Jira / custom tickets
```

Settings, plug-ins (skills, model profiles, engines, connectors) and the GitHub Action: `docs/dev/PLUGINS.md`.
How the harness works (loop, tools): `HARNESS.md`. Contributing: `CONTRIBUTING.md`.

## Setup for the benchmarks

```bash
pip install python-pptx openpyxl pandas pyarrow pyyaml pytest pillow
```

LibreOffice (`soffice`) and `pdftoppm` are needed for slide and workbook QA. Docker is needed for the coding
benchmarks.

```bash
export MESHPOOL_BASE_URL=https://llm.meshpool.ai/v1   # or the local spend guard: http://127.0.0.1:8099/v1
export MESHPOOL_API_KEY=...                           # never committed; .env is gitignored
```

## Use it

```bash
python -m meshpool_harness route "Analyze this CSV and build a deck with the key findings" --files sales.csv
python -m meshpool_harness run "Why did churn go up in March?" --files customers.csv --out ./out
python -m meshpool_harness run "Fix the failing login test" --workdir ~/code/myrepo
python -m meshpool_harness run "Build a 3-year revenue model" --engine sheets --out ./out
```

`run` prints the route, every step's answer, artifacts, the checks that passed, turns, and tokens. Exit code 0 only
if every engine finished with passing checks.

## How a request flows

1. **Router** (`meshpool_harness/router/`). Deterministic rules first: attachment types, words that name a
   deliverable only when a creation verb precedes them ("make a deck" vs "from this tracker"), repo presence, explicit
   `--format`. Mixed requests become chains in the order code -> data -> sheets -> slides; each step gets only the
   previous step's answer and files. Below 0.6 confidence one 60-token classification call decides; below 0.5 the
   request goes to `general`. `--engine` and `--model` override.
2. **Engine** (`engines/<name>/engine.py`): owns its system prompt (byte-stable, so the provider cache hits),
   its tools, and a deterministic `finish_check`.
3. **Core loop** (`meshpool_harness/core/`): strict tool schemas with precise errors, tool-output truncation with the
   full text saved to a file, guards implemented as code (below), turn caps, append-only history for cache hits.

## Engines

| Engine | What it adds | Finish is rejected unless |
|---|---|---|
| code | container-native commands (no nested `docker exec` quoting), model-free locate hints, issue examples executed before turn 0, regression gate on related tests, one fresh second attempt when the first runs out of budget (the repo is reset to the user's own state, uncommitted and untracked files included, never to HEAD) | a check command passed after the last edit; no existing test was modified; related tests that passed before still pass |
| data | number tracing, verbatim-quote checker, lean output rules | every number in the answer appears in executed code output; every quote exists verbatim in a source; requested files exist; plain-language check passes |
| sheets | banker-style builder (Cover -> Inputs -> Calc -> Outputs -> Checks, colored tabs, linked tab map, blue inputs) | LibreOffice recalculation shows no errors, no hardcoded numbers in calc/output tabs, Checks tab shows OK, Cover tab exists |
| slides | JSON deck spec, deterministic renderer (layout, type scale, native charts/tables, font-metric text fitting), QA | no overflow, overlap, low contrast, >2 fonts, wrong slide count; plain-language check passes |
| general | core tools only | (no mandatory check) |

## Guards (failure catalog as code)

`finish_without_check`, `finish_evidence` (cited command must be a passing command after the last edit),
`stale_edit` (file changed since read), `test_tampering` (judged by `git status`, so a revert clears it),
`repeat_failure` (3 identical failures force a new approach, 5 abort), `error_not_quoted` (only for real error lines),
`missing_deliverable`, `regression` (code), `untraceable_number` and `fabricated_quote` (data), workbook and deck QA,
`reading_level` / `long_sentence` / `jargon` (plain language, grade 9 max). Every trigger is logged to
`failures.csv`.

## Profiles

`profiles/<family>.yaml`, chosen by model id (`meshpool_harness/profiles.py`), overridable with `MH_PROFILE`. A
profile sets caching (automatic prefix vs explicit `cache_control`), tool-call style, edit format, sampling per role
(Kimi K3 rejects `temperature` through meshpool, so it is omitted), output limits, and the role-to-model map.
Sources and what was tested: `profiles/notes.md`, `results.md`.

Add a profile: copy `profiles/glm.yaml`, set `family`, `match` (substring of the model id) and `models`, then run
the phase-3 smoke tests (`eval/phase3.sh`).

Add an engine: create `engines/<name>/engine.py` with `solve(task, *, workdir, task_id, ...)` returning
`{ok, answer, artifacts, evidence, turns, reason, usage}` and a `finish_check`; add its words to
`meshpool_harness/router/rules.py` and examples to `tests/stress/routing_stress.jsonl`.

## Spend control

`/home/user/guard/spend_guard.py` (outside the repo) is a local OpenAI-compatible proxy in front of meshpool. It
reads `x-litellm-key-spend` on every response, logs per-call token usage with a tag (task id or `/tag/<name>/` in the
URL), retries upstream 5xx, refuses calls and kills runs at $33 above the starting spend, and survives restarts.
`eval/ledger.py` is the in-process ledger with phase budgets.

## Benchmarks (identical text, files, containers and graders for every agent)

| Benchmark | Runner | Grader |
|---|---|---|
| SWE-rebench V2 (coding A) | `eval/swe_task.py` | official FAIL_TO_PASS / PASS_TO_PASS in the instance image (`eval/swe_grade.py`) |
| Terminal-Bench 2.0 (coding B) | `eval/tb_task.py` | the task's own `tests/test.sh` reward |
| Hard analysis (8 tasks) | `eval/analysis_task.py` | `eval/grade_analysis_hard.py` (code-computed gold) |
| Routing (40 stress requests) | `pytest tests/test_router.py` | gold engine chains |

```bash
python3 eval/run_matrix.py --bench swe --split dev --variant code-v3 --workers 3   # resumable, parallel
python3 eval/report.py --match                                                    # pass rates, CIs, cost per solved
```

Subsets are frozen with seed 20261109 in `eval/subsets/`. Dev subsets are for tuning; test subsets are run once.
