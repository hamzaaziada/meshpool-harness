<img src="../app/meshpool_harness/ui/brand/mark.png" alt="Meshpool" width="44">

# Benchmarks

Meshpool Auto uses the results on this page to choose a harness for each task (see [ROUTING.md](ROUTING.md)).

**Which harness the numbers describe.** Run C below measured the Meshpool Harness v3 *benchmark loop* (the one-shot
engine). Meshpool Code and Meshpool Work in the app run the app's own chat agent, which shares that loop's tools but
not its loop. Since 12 November 2026 the app agent is measured directly (next section).

## App agent, measured directly (12 November 2026, dev tasks)

Every agent ran at the same time on DeepSeek V4.1 Flash through the Meshpool gateway, with the same task text, caps
and graders. These are dev tasks and one run each, so a result can move by about two tasks; the frozen test tasks
confirm before any claim.

SWE-bench Verified, 9 dev tasks:

| Agent | Solved | $ per solved | Time per solved |
|---|---|---|---|
| Pi | 8/9 | $0.0072 | 108 s |
| Codex CLI | 7/9 | $0.0065 | 84 s |
| Meshpool Code (app agent) | 7/9 | $0.0084 | 107 s |
| Meshpool Harness v5 (engine loop) | 7/9 | $0.0104 | 137 s |

Computer use, MiniWoB++, 10 dev tasks (second round; the browser tool with the fixes the app now uses):

| Agent | Solved | $ per solved | Time per solved |
|---|---|---|---|
| Meshpool Harness v5 | 9/10 | $0.0009 | 18.7 s |
| Pi | 9/10 | $0.0014 | 22.5 s |
| Codex CLI | 9/10 | $0.0025 | 23.3 s |

Terminal-Bench 2.0, 4 dev tasks: v5 3/4, Meshpool Code 2/4, Codex 1/4, Pi 1/4. Codex and Pi crashed on two tasks
when the gateway's content filter rejected hex output. Four tasks is too few to rank on; the rest was cut for budget.

## App agent on the frozen test tasks (13 November 2026)

SWE-bench Verified, 20 frozen test tasks, all agents at the same time, one run each:

| Agent | Solved | $ per solved | Time per solved |
|---|---|---|---|
| Pi | 19/20 | $0.0069 | 95 s |
| Meshpool Code (app agent) | 18/20 | $0.0079 | 133 s |
| Codex CLI | 17/20 | $0.0101 | 121 s |
| Meshpool Harness v5 (engine loop) | 17/20 | $0.0093 | 184 s |

Codex reported no usage on 2 of 20 runs; its cost is scaled from the other 18. Meshpool Code beats Codex on solves
and cost but not time. Pi leads on all three. The gap to Pi (one task, $0.001 per solved) is inside run-to-run noise
but it is in Pi's favour, so we do not claim a lead.

## Code tools A/B (13 November 2026, 10 Verified dev tasks)

`MH_OBSERVE` adds `probe` and `trace`; "x" arms added `MH_NAV` and `MH_LINT_EDIT` too (both since removed: they added calls without adding solves).

| Arm | Solved | $ per solved | Time per solved |
|---|---|---|---|
| Meshpool Code (app agent) | 10/10 | $0.0069 | 98 s |
| app + all three tools | 9/10 | $0.0089 | 125 s |
| v5 (engine loop) | 6/10 | $0.0294 | 235 s |
| v5 + observe | 9/10 | $0.0180 | 158 s |
| v5 + all three tools | 7/10 | $0.0278 | 244 s |

Observe helped the engine loop (+3 tasks, 39% cheaper per solve). It did not help the app agent, which already
solved all 10. Navigation and the edit check added calls without adding solves. All three stay off until observe is
confirmed on test tasks.

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

v2 was the engine in the pre-release builds. It ran beside v3 at the same hours until it was dropped for budget.

| Benchmark | v3 | v2 |
|---|---|---|
| SWE-bench Verified (7 tasks) | 6/7, $0.041 per solved | 6/7, $0.049 per solved |
| Terminal-Bench 2.0 (9 tasks) | 6/9, $0.096 per solved | 3/9, $0.140 per solved |

v3 solves as many or more tasks for less, so it replaces v2.

## What v3 adds

- **bash_many:** several independent commands in one step.
- **Todo follow-through:** the agent lists the request's requirements and is reminded once if it ends with items open.
- **Loop and stall breakers:** a short note when the agent repeats the same calls, or makes no progress for 12 steps.
- **Step budget note:** 15 steps before the limit, a reminder to make the deliverable exist first.
- **Environment snapshot:** the project's files and installed tools in the first request.

Meshpool also takes these transport fixes from v4:

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
