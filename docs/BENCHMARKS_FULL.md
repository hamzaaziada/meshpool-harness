# Meshpool benchmarks

Meshpool Code solves real software bugs about as well as the best coding agents we tested, for under a cent a task. Meshpool Work finished every office task we gave it, from Word proposals to Excel reports with charts. You choose the model, and a task costs from under a cent to a few cents.

All results use the same model, DeepSeek V4.1 Flash, through the Meshpool gateway. Every agent got the same tasks, time limits and graders, and ran at the same time. Data is from 12 and 13 November 2026.

## At a glance

| | Result |
|---|---|
| Real bug fixes solved (Meshpool Code, 29 tasks) | **25 of 29 (86%)**, within noise of the best agent |
| Cost per solved bug fix | **$0.0080**, lower than Codex CLI ($0.0092) |
| Office tasks finished (Meshpool Work) | **7 of 7** |
| Everyday coding tasks through the app | **4 of 4**, 18 s on average |
| Browser tasks (engine loop, MiniWoB++) | **9 of 10, at $0.0009 per solved task**, the cheapest of all agents |
| Cost of one office task | **$0.004 to $0.047** |

## 1. Code: SWE-bench Verified

Real bug fixes in open-source projects, graded by the official test runner. This is 29 tasks in two sets (9 development, 20 frozen test), one run per agent per task.

| Agent | Solved | Share solved | 95% range | $ per solved | Seconds per solved |
|---|---|---|---|---|---|
| Pi | 27/29 | `█████████░` 93% | 78 to 98% | $0.0070 | 99 s |
| **Meshpool Code** | **25/29** | `█████████░` 86% | 69 to 95% | **$0.0080** | 126 s |
| Codex CLI | 24/29 | `████████░░` 83% | 65 to 92% | $0.0092 | 110 s |
| Engine loop (v5) | 24/29 | `████████░░` 83% | 65 to 92% | $0.0096 | 170 s |

How to read it:

- **Meshpool Code is in the top group.** It solved one more task than Codex CLI and two fewer than Pi. With 29 tasks no pair of agents differs significantly (every p-value is 0.25 or higher), so the three are statistically tied.
- **Meshpool Code costs less per solved task than Codex CLI** ($0.0080 against $0.0092) and than the engine loop. Pi is cheapest at $0.0070.
- **On the 21 tasks all four agents solved,** Meshpool Code's cost per task ($0.0055) is within 4% of Pi's ($0.0053) and 23% below Codex's ($0.0071). Pi's lead on cost per solved task comes mostly from solving more tasks.
- **Meshpool Code is slower than Pi and Codex.** Its median task takes 109 s against 73 s for Pi and 72 s for Codex.
- **One task, pylint-4970, failed for all four agents.**

The two task sets separately:

| Agent | Development (9 tasks) | Test (20 tasks) | Test: $ per solved | Test: seconds per solved |
|---|---|---|---|---|
| Pi | 8/9 | 19/20 | $0.0069 | 95 s |
| **Meshpool Code** | 7/9 | **18/20** | **$0.0079** | 133 s |
| Codex CLI | 7/9 | 17/20 | $0.0101 | 121 s |
| Engine loop (v5) | 7/9 | 17/20 | $0.0093 | 184 s |

On the frozen test set, Meshpool Code solved more than Codex CLI and cost 22% less per solved task.

## 2. Code tools: what helps

Three optional tools were tested on 10 development tasks. "Observe" runs a script and traces what it did. "Navigation" finds where a Python name is defined and used. The "edit check" flags new errors after each edit.

| Setup | Solved | $ per solved | Seconds per solved |
|---|---|---|---|
| **Meshpool Code** | **10/10** | **$0.0069** | **98 s** |
| Meshpool Code + all three tools | 9/10 | $0.0089 | 125 s |
| Engine loop + observe | 9/10 | $0.0180 | 158 s |
| Engine loop + all three tools | 7/10 | $0.0278 | 244 s |
| Engine loop | 6/10 | $0.0294 | 235 s |

- **Meshpool Code solved all 10 tasks without any extra tool.** The tools are off by default, because they added calls without adding solves.
- **Observe helps the simpler engine loop:** 6 solved became 9, and the cost per solved task fell from $0.0294 to $0.0180. That gain is not statistically firm.
- **Run-to-run noise is about two tasks.** The engine loop solved 8 of 10 in an earlier run and 6 of 10 here, on the same tasks.

## 3. Browser use: MiniWoB++

Ten small web tasks: click, fill in a form, search, book a flight.

| Agent | Solved | $ per solved | Seconds per solved |
|---|---|---|---|
| **Engine loop (v5)** | 9/10 | **$0.0009** | 18.7 s |
| Pi with the fixed browser tool | 9/10 | $0.0012 | 19.9 s |
| Pi | 9/10 | $0.0014 | 22.5 s |
| Codex CLI with the fixed browser tool | 9/10 | $0.0018 | 15.3 s |
| Codex CLI | 9/10 | $0.0025 | 23.3 s |

All five solved 9 of 10 and failed the same task, so the test separates cost, not quality. The Meshpool engine was the cheapest agent: **64% cheaper than Codex CLI** and 36% cheaper than Pi. An earlier larger round (102 runs) had the engine loop match Claude Code (97 of 102 each) at one third of the cost.

## 4. Terminal work: Terminal-Bench 2.0

Four tasks, too few to rank agents, but the result is worth stating.

| Agent | Solved | $ per solved |
|---|---|---|
| **Engine loop (v5)** | **3/4** | **$0.0127** |
| **Meshpool Code** | 2/4 | $0.0251 |
| Codex CLI | 1/4 | $0.0388 |
| Pi | 1/4 | $0.0206 |

On one task Codex CLI and Pi crashed when the gateway's content filter rejected hex output. The Meshpool agents withhold that output and carry on.

## 5. Real tasks through the app

These run through the app on the real model, approved the way a user clicking Allow would, and graded on the files left behind. This tests the whole app, not only the agent loop. Each run cost $0.004 to $0.047.

**Code, 4 tasks each**

| Agent | Passed | Average time |
|---|---|---|
| Pi | 4 of 4 | 17 s |
| **Meshpool Code** | **4 of 4** | **18 s** |
| Meshpool Auto | 4 of 4 | 36 s |
| Hermes | 4 of 4 | 42 s |

**Office: seconds per task, every run passed**

| Task | Meshpool Work | Hermes | Pi | Meshpool Auto |
|---|---|---|---|---|
| Word proposal with headings, bullets and tables | 42 | 64 | 15 | 115 |
| Excel report from a CSV: live formulas, currency, chart, frozen header | 111 | 109 | 120 | 110 |
| 6 to 8 slide deck with charts | 170 | 176 | 115 | 174 |
| Edit a contract in place, keeping formatting | 15 | not run | not run | passed |
| Three invoice PDFs into an Excel sheet with a total | passed | not run | not run | passed |
| PDF copy of a Word file | passed | not run | not run | passed |
| One thank-you letter per customer, as Word files | passed | not run | not run | passed |

Meshpool Work passed 7 of 7. The decks were opened and looked at: Meshpool Work's and Hermes' were both leadership-ready, with action titles, real charts and correct numbers, and Work's was slightly cleaner. Times for the last four rows were not written down.

## 6. Meshpool Work and Claude Cowork

Cowork's column comes from Anthropic's public documentation. We have not run Cowork side by side with Meshpool Work.

| Capability | Meshpool Work | Claude Cowork |
|---|---|---|
| Create Word, Excel, PowerPoint and PDF files | Yes, tested on 7 tasks | Yes |
| Read PDF, Word, Excel, PowerPoint, CSV; OCR | Yes | Yes |
| Browser use and computer use | Yes, on Mac and Windows | Yes, computer use in beta |
| Connectors, skills, scheduled tasks, memory | Yes | Yes |
| Choose your own model | **Yes, any model on the Meshpool endpoint** | No, Anthropic models |
| Pricing | **Cents per task on the Meshpool endpoint** | $20 to $200 a month, $20 a seat for teams |
| Runs in the cloud with your laptop closed | No | Yes |
| Parallel sub-agents | No | Yes |
| Word tracked changes and comments | Not tested | Documented |
| Excel pivot tables and conditional formatting | Not tested | Not documented |
| PDF form filling and merging | Not tested | Documented |

Meshpool Work finished every everyday office task we tried. For polished consulting-style decks and advanced Excel and Word features, we have not proven it yet: a strict blind review scored a sample deck from the deck engine 2 of 5, against 4 to 5 for real consulting pages, and the advanced features have not been tried.

## 7. Earlier round, with Claude Code

On 12 November a larger comparison included Claude Code and a plain DeepSeek agent. It used an older engine (v3), different tasks and different limits, and Claude Code ran on DeepSeek V4.1 Flash, not on Claude models. Its raw rows are not in the repository, so these numbers repeat the docs. Do not compare its costs with the tables above.

| Benchmark | Meshpool engine v3 | Claude Code | Codex CLI | Plain DeepSeek agent |
|---|---|---|---|---|
| Verified, 20 tasks | 18/20, $0.039, 6.9 min | 20/20, $0.036, 5.7 min | 17/20, $0.026, 2.6 min | 20/20, $0.027, 7.5 min |
| Terminal-Bench, 15 tasks | 6/15, $0.182, 29.1 min | 4/15, $0.380, 42.2 min | 8/15, $0.060, 14.8 min | 7/15, $0.210, 41.9 min |
| MiniWoB++, 102 runs | 97/102, $0.0027, 17.4 s | 97/102, $0.0082, 21.4 s | not run | 101/102, $0.0026, 29.0 s |

Each cell shows tasks solved, cost per solved task and time per solved task. Since then the engine has gone from v3 to v5: v5 solved as many Verified tasks as v4 (7 of 9 each), 10% cheaper and 32% faster per solved task.

## What was not measured

- Meshpool Work and Meshpool Auto on standard benchmarks, and times for four of the seven office tasks.
- Meshpool Code on MiniWoB++ browser tasks.
- Claude Cowork run side by side with Meshpool Work.
- Claude Code and Codex CLI on their own subscription models. Every run here uses DeepSeek V4.1 Flash.
- More than 29 Verified tasks, or more than one run per task. Terminal-Bench has only 4 tasks for the current agents.
- The app's windows on a real Mac and Windows computer, and office files opened in desktop Word, Excel and PowerPoint. They pass file-format checks only.

## Method

- **Same conditions.** One model, one gateway, the same task text, limits and graders. All agents for a task ran at the same time, because gateway caching and speed change by the hour.
- **Cost** is worked out from each run's token counts at the gateway's list prices: $0.15 per million uncached input tokens, $0.003 cached, $0.60 output. **Cost per solved task** is total cost divided by tasks solved; **time per solved task** is total run time divided by tasks solved. Failed runs count toward both.
- **Ranges** are 95% Wilson intervals. **p-values** are exact two-sided tests on the tasks where exactly one of two agents solved.
- **Scoring** keeps only tasks every compared agent finished without an infrastructure error. 24 rows lost to a key budget limit were dropped before scoring. Codex CLI did not report usage on 4 of its 29 Verified runs, so its cost is scaled up from the other 25.
- **Sources.** Result rows are in `harness-lab/bench/results`, the scorer is `harness-lab/bench/report.py`, and the written summary is `docs/BENCHMARKS.md`.
