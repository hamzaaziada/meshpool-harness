# Backlog

Ranked by expected value per dollar of testing. Evidence first, then a flag, then a unit test. Status: idea, built
(flag exists, unmeasured), kept (measured and in a preset), dropped.

| # | Item | Lever | Evidence | Status |
|---|---|---|---|---|
| 1 | Keep-warm pings during long tool runs (`MH_KEEPWARM_S`) | cost, some speed | findings/001: full misses after gaps over 40 s hold about 70% of uncached input on Terminal-Bench | built |
| 2 | Session header for backend affinity (`MH_SESSION_HEADERS`, v4) | cost | Codex sends one and gets 96% cache; we get 87% | built (v4), unmeasured |
| 3 | Fewer round trips: push several commands per turn, parallel tool calls | speed, cost | 35 calls per task against Codex's 11 turns on Terminal-Bench | idea |
| 4 | Output tokens: terse prose (`MH_TERSE`, v4), lower reasoning where safe | cost, speed | output is 37% of our Terminal-Bench list cost | built (v4), unmeasured |
| 5 | Tool output truncation like Codex (head and tail, byte cap) | cost (new content per call), speed | 756 new tokens per call on Terminal-Bench against Codex's lower total | idea: measure our caps first |
| 6 | Scope check before finish on Verified (diff size) | solves | two Verified misses were widened patches | idea |
| 7 | Observation masking in rare large steps (JetBrains: -53% cost, same solves) | speed; cost only if not cached | for us cached context is 50x cheaper than new input, so masking saves less money than in the paper and each mask step costs one full miss; check latency per call against context size first | idea |
| 8 | Compact page slice plus several actions per call on MiniWoB (Synapse) | cost, speed | 32 calls per MiniWoB task today; most tasks need 1–2 | idea |
| 9 | Harness search with RRSI acceptance rules (noise floor, tokens must pay for score) | process | IDEAS_RESEARCH.md; matches our decide-rule | idea |
| 10 | Memory across tasks (Hermes-style notes and skills) | turns, solves | Hermes: two capped files (2,200 and 1,375 chars), frozen snapshot per session so the cache stays valid | idea |
