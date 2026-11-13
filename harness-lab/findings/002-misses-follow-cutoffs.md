# 002: Most avoidable cache misses come right after a cut-off call

Source: run C (2026-11-12). Cut-offs come from the call ledger (status 0, "timed out": no streamed byte within 20 s).
Cache numbers come from the per-call usage log, joined by tag and time. 59 harness tags had both. No model calls.

| Call | Calls | Full misses | Share of all full-miss tokens |
|---|---|---|---|
| Right after a cut-off and retry | 199 | 92 (46%) | 73% (1.70M tokens) |
| Every other call | 2,453 | 179 (7%) | 27% (0.63M tokens) |

What it means:

- A retried call usually reaches a different backend with a cold cache. So the 20 s first-token cut-off cost us twice:
  the wait (GATEWAY_ISSUES #13), then a full re-read of the whole context at the uncached price.
- v4 already waits up to 90 s for the first token (`MH_STREAM_FIRST_S=90`). On polyglot-rust-c it cut cut-offs from
  20 of 62 calls to 3 of 61. If that holds, about 70% of the avoidable miss tokens go away. That is roughly 40–50%
  less uncached input on Terminal-Bench, the largest cost item, with no extra calls.
- The gap pattern in finding 001 was mostly this: a cut-off plus retry makes a long gap and a miss together.
- Keep-warm pings (`MH_KEEPWARM_S`) aim at the remaining 27%. They are now a second-order item.

What the next paid check must show: per-task `full_miss_tokens` (now in usage on every run) for v3 against v4 on the
same tasks at the same time. v4 should have far fewer.

Mechanism metrics to report for v4 against v3:

- cut-off calls per task;
- full misses per task;
- uncached tokens per task;
- model seconds per call.
