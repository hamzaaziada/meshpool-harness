# Benchmarks

Meshpool Code and Meshpool Work run **Meshpool Harness v3**. Meshpool Auto uses the results on this page to choose a harness for each task (see [ROUTING.md](ROUTING.md)).

## How the agents were measured

Every agent ran under the same conditions:

- the same model, DeepSeek V4.1 Flash, through the Meshpool gateway;
- the same tasks, caps and graders;
- the same hours (run C, 12 November 2026), because gateway caching and latency change by the hour.

Cost is billed cost per solved task. Time is total wall time divided by the number of solved tasks. Ranges are 95% confidence intervals.

## SWE-bench Verified: 20 tasks every agent finished

| Agent | Solved | $ per solved | Time per solved |
|---|---|---|---|
| Meshpool Harness v3 | 18/20 (70-97%) | $0.039 | 6.9 min |
| Codex CLI | 17/20 (64-95%) | $0.026 | 2.6 min |
| Claude Code | 20/20 (84-100%) | $0.036 | 5.7 min |
| Plain DeepSeek (mini-SWE-agent) | 20/20 (84-100%) | $0.027 | 7.5 min |

## Terminal-Bench 2.0: 15 tasks every agent finished

| Agent | Solved | $ per solved | Time per solved |
|---|---|---|---|
| Meshpool Harness v3 | 6/15 (20-64%) | $0.182 | 29.1 min |
| Codex CLI | 8/15 (30-75%) | $0.060 | 14.8 min |
| Claude Code | 4/15 (11-52%) | $0.380 | 42.2 min |
| Plain DeepSeek (mini-SWE-agent) | 7/15 (25-70%) | $0.210 | 41.9 min |

## Computer use: MiniWoB++, 102 runs (rounds 10 to 12)

| Agent | Solved | $ per solved | Time per solved |
|---|---|---|---|
| Meshpool Harness v3 | 97/102 (89-98%) | $0.0027 | 17.4 s |
| Claude Code | 97/102 (89-98%) | $0.0082 | 21.4 s |
| Plain DeepSeek (mini-SWE-agent) | 101/102 (95-100%) | $0.0026 | 29.0 s |

## Harness v3 against v2

v2 was the engine in Meshpool 2.5 and earlier. It ran beside v3 at the same hours until it was dropped for budget.

| Benchmark | v3 | v2 |
|---|---|---|
| SWE-bench Verified (7 tasks) | 6/7, $0.041 per solved | 6/7, $0.049 per solved |
| Terminal-Bench 2.0 (9 tasks) | 6/9, $0.096 per solved | 3/9, $0.140 per solved |

v3 solves as many or more tasks for less, so it replaces v2 in Meshpool 2.6.

## What v3 adds

- **bash_many:** several independent commands in one step.
- **Todo follow-through:** the agent lists the request's requirements and is reminded once if it ends with items open.
- **Loop and stall breakers:** a short note when the agent repeats the same calls, or makes no progress for 12 steps.
- **Step budget note:** 15 steps before the limit, a reminder to make the deliverable exist first.
- **Environment snapshot:** the project's files and installed tools in the first request.

Meshpool 2.6 also takes these transport fixes from v4:

- a backoff of 3, 6, 12 seconds after a rate limit with no reset time;
- a session header, so a conversation stays on one cached backend;
- after a gateway rejection tied to content, the offending tool output is withheld instead of failing the turn.

To turn the v3 behavior off, set `MH_AGENT_V3=0` in the settings file.

## What is not measured yet

- **Harness v4:** it has had spot checks only (MiniWoB++ 33/34). Its prompt changes stay off until a same-time run.
- **Pi, Hermes and the office work:** they were not in run C. Their routing scores are estimates, set below the measured harnesses.
- **The subscription harnesses:** in run C, Claude Code and Codex used DeepSeek V4.1 Flash. On your subscription they use Anthropic's and OpenAI's own models.

## Command line

`meshpool run` uses the v3 preset for code and browser tasks. Set `MH_PRESET` to `v2`, `v3` or `v4` to choose another. Explicit `MH_*` settings always win.
