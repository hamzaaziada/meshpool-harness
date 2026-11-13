# 005: The app's chat agents do not run the benchmarked loop

The benchmarks run `meshpool_harness/core/loop.py` through `engines/*/engine.py`. The app's "Meshpool Code" and
"Meshpool Work" agents run their own loop in `meshpool_harness/agent/core.py` (869 lines). The two share:

- the tools (`core/tools.py`: Workspace, bash, bash_many, edit ...);
- `_compact` and `_sanitize_calls`;
- the stream parser.

They do not share:

- the turn loop and guards;
- the retry and transport code: the agent has its own `_post` with a 120 s stream wait, a cache key and a session
  header;
- the browser tool: the app uses its own MCP server, not `engines/browser/ctl.py`.

The harness registry describes Meshpool Code as "Runs Meshpool Harness v3: 18 of 20 on SWE-bench Verified". That is
only partly true: it has the v3 features, but not the measured loop.

What carries over today:

- tool changes (`MH_PARALLEL_MANY` reaches the app's bash_many);
- browser-engine changes reach only one-shot browser runs.

Loop changes do not carry over: keep-warm, the full-miss counter, the scope check and same-turn finish.

Options:

1. **Benchmark the app's agent.** Add a runner that drives `agent.core.Agent` headless (auto-approve) on Verified
   and Terminal-Bench, so we measure what users get.
2. **One core.** Move the pieces both need (finish-time checks, cache counters, retry policy) into shared helpers
   that both loops call, behind the same flags.
3. **Keep both** and port each kept change by hand (the cheapest now, but they drift).

Recommendation: 2, then 1. Shared helpers first, so a kept change lands in both loops at once. Then benchmark the
app's agent as the product number.

## Status (cycle 7)

Option 2 is done for the pieces that apply to both loops:

- `core/webfix.py`: the four browser fixes, used by the benchmark browser tool and the app's browser tool.
- `core/metrics.py`: the full cache-miss meter, used by both loops (`full_miss_calls`, `full_miss_tokens`).
- `core/finishing.py`: the scope check. The benchmark loop runs it at finish. The app agent runs it when it ends a
  reply after editing files.

Option 1 has its entry point: `run_task(text, workdir=..., engine="agent")` (or `"agent-work"`) runs the app's chat
agent headless. Every tool is allowed, there are no MCP servers, its session files go inside the workdir, and it
returns the same keys as the other engines.

Not shared, on purpose: same-turn finish and keep-warm. The app agent has no finish tool, and it already waits
120 s for a stream, so it does not have the cut-off problem.
