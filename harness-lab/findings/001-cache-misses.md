# 001: Most of our uncached input is avoidable cache misses

Source: per-call usage log from run C (2026-11-12), `cache_audit.py`. List prices: uncached input $0.15/M, cached
input $0.003/M (50 times cheaper), output $0.60/M.

| Agent | Tasks | Calls/task | Prompt/call | Uncached/call | New content/call | Avoidable/call | Output/call | $ uncached/task | $ cached/task | $ output/task |
|---|---|---|---|---|---|---|---|---|---|---|
| harness, Terminal-Bench | 22 | 35.2 | 18,833 | 2,406 | 756 | 1,835 | 413 | 0.0127 | 0.0017 | 0.0087 |
| plain DeepSeek, Terminal-Bench | 16 | 39.1 | 21,596 | 3,531 | 833 | 2,953 | 476 | 0.0207 | 0.0021 | 0.0112 |
| harness, Verified | 25 | 34.6 | 14,699 | 1,160 | 530 | 629 | 205 | 0.0060 | 0.0014 | 0.0043 |
| plain DeepSeek, Verified | 20 | 32.6 | 13,741 | 855 | 603 | 309 | 203 | 0.0042 | 0.0013 | 0.0040 |
| harness, MiniWoB | 34 | 32.1 | 4,346 | 869 | 305 | 536 | 133 | 0.0042 | 0.0003 | 0.0026 |

Codex and Claude Code calls are logged with zero prompt tokens on this path, so they have no per-call rows. Their
result rows say Codex had a 96% cache rate on Terminal-Bench and about 21K uncached tokens per task. Ours is about 84K.

What the misses look like (Terminal-Bench, harness): of 752 follow-up calls, 53 were full misses (less than half of
the previous prompt was cached) and they hold almost all the avoidable tokens. Partial misses are rare. Full misses
cluster after long gaps between calls:

| Gap since the previous call | Calls | Avoidable tokens per call |
|---|---|---|
| under 5 s | 397 | 95 |
| 5–15 s | 193 | 520 |
| 15–40 s | 63 | 549 |
| over 40 s | 99 | 12,197 |

Plain DeepSeek shows the same pattern, so the cause is on the serving side (cache evicted or the call routed to a
cold backend after an idle period), not something our history does. Our history is append-only, as designed.

What it means:

- Uncached input is the largest cost item for us on every benchmark (55% of list cost on Terminal-Bench).
- Removing the avoidable misses would cut uncached input on Terminal-Bench from about 84K to about 28K tokens per
  task, close to Codex, and list cost per task by about a third.
- Context length is nearly free when cached. Shortening old tool outputs saves little money; it can still save time.

Candidate fixes (backlog items 1 and 2): the v4 session header for backend affinity, and a keep-warm call during
long tool runs. The second is cheap: a ping that re-reads a 20K prefix from cache costs about $0.00006, against about
$0.003 for re-reading it uncached.

Caveat: the log's timestamp is per call end, so a long gap can also be a slow call. The gap test does not prove
cause. A paid check must log, per call, the tool time before it and whether it was a full miss.
