<img src="../app/meshpool_harness/ui/brand/mark.png" alt="Meshpool" width="44">

# Meshpool Auto and adding harnesses

## How Meshpool Auto picks a harness

When a new chat starts with **Meshpool Auto**, Meshpool:

1. Sorts the task into a category: code, terminal, browser, research, documents, data, gtm or general. It matches keywords, with no model call. A phrase of several words counts for more than a single generic word, and a tie goes to the category named first in the request.
2. Takes each agent's benchmark quality for that category from the routing table, plus a small bonus for the current mode (Work or Code). Only agents that are installed and can run on your provider count (see [PROVIDERS.md](PROVIDERS.md)). An agent on a Claude or ChatGPT sign-in you did not choose as the provider counts only when **Let Auto use your other subscriptions** is on.
3. **Quality first.** It finds the best quality among those agents. Every agent within `quality_band` points of it (5 by default: one task in twenty, inside run-to-run noise) counts as equally good. An agent below that is never picked, however cheap.
4. **Then cost, then speed.** Among the equally good agents it picks the lowest cost per solved task, then the shortest time. Where the table has no cost or time for an agent, quality decides.

The chat shows which agent Auto picked and why, for example: "Benchmark quality within 5 points of the best for a code task (fix, test), at the lowest cost ($0.0069 per solved task vs $0.0079)."

## Updating the numbers from benchmarks

The routing table is `app/defaults/routing.json`. The numbers come from [BENCHMARKS.md](BENCHMARKS.md). A copy saved as `routing.json` in the Meshpool data folder overrides it on one computer.

Per category, `scores` is the quality from 0 to 100 (the solve rate where measured). `cost_usd` and `seconds` are the cost and time per solved task; take them from one run where every agent ran at the same time, or leave an agent out. Update `source` to say where the numbers came from.

```json
"data": {
  "keywords": ["excel", "spreadsheet", "forecast"],
  "scores": {"meshpool-work": 84, "hermes": 74},
  "cost_usd": {"meshpool-work": 0.012},
  "seconds": {"meshpool-work": 111}
}
```

To add a category, give it `keywords` and `scores`. An agent with no score in a category is never picked for it.

## Adding a new harness

- **ACP agent.** If the harness speaks the Agent Client Protocol, add a JSON manifest. Users can also drop it into Settings, Harnesses. For a harness shipped with Meshpool, add it to `BUILTIN` or a bundled manifest in `app/meshpool_harness/harnesses/__init__.py`. Give it a Meshpool name, such as "Meshpool Data". Then add its id to the scores in `routing.json`.
- **Python harness.** For an in-process harness like Meshpool Code, follow `app/meshpool_harness/agent/core.py`, and register it in `harnesses/__init__.py` and `ui/agent_api.py`.
