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
