# Harness lab

Work area for improving the harness in `app/meshpool_harness` (the same package the app imports). Nothing here is
shipped with the app.

Goal: beat Codex CLI and pi on cost per solved task and time per solved task with the same model, at equal or better
solves, on SWE-bench Verified, Terminal-Bench 2 and MiniWoB++.

## Loop (one cycle)

1. Pick the top item in `BACKLOG.md`.
2. Find evidence offline first: logs, saved transcripts, a replay, a unit test with a fake model. No paid calls.
3. Build it behind an `MH_*` flag that defaults to off. Flag off must behave exactly as before.
4. Add a unit test with no model calls. Run `python3 -m pytest -q app/tests/`.
5. Add one entry to `LOG.md`: what changed, the offline evidence, the expected effect, and how a paid run would confirm it.
6. Paid benchmark runs only when the offline evidence is strong and the owner approves the cost (see the handoff rules).

## Tools

- `cache_audit.py usage.csv <tag-prefix> ...`: per-call cache audit. Splits uncached tokens into real new content
  (`delta`) and avoidable misses (`excess`), and splits cost into uncached input, cached input and output.

## Data

The full experiment package (eval scripts, result rows, per-call logs) is not in this repo. Its key numbers are in
`findings/`.
