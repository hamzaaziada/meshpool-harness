# Meshpool Auto and adding harnesses

## How Meshpool Auto picks a harness

When a new thread starts with **Meshpool Auto**, Meshpool:

1. Sorts the task into a category: code, terminal, browser, research, documents, data, gtm or general. It does this by matching keywords, with no model call.
2. Takes that category's score for each harness from the routing table. It adds a small bonus for the current mode (Cowork or Code).
3. Picks the highest-scoring harness that is installed and can run on your provider (see [PROVIDERS.md](PROVIDERS.md)). A harness on a Claude or ChatGPT sign-in you did not choose as the provider is picked only when **Let Auto use your other subscriptions** is on.

The thread shows which harness Auto picked and why.

## Updating the scores from benchmarks

The routing table is `app/defaults/routing.json`. The current scores come from the results in [BENCHMARKS.md](BENCHMARKS.md). A copy saved as `routing.json` in the Meshpool data folder overrides it on one computer.

For each category, set `scores` to your benchmark results, from 0 to 100 per harness id. Update `source` to say where the numbers came from.

```json
"data": {
  "keywords": ["excel", "spreadsheet", "forecast"],
  "scores": {"meshpool-work": 84, "meshpool-data": 91, "hermes": 74}
}
```

To add a category, give it `keywords` and `scores`. A harness with no score in a category is never picked for it.

## Adding a new harness

- **ACP agent.** If the harness speaks the Agent Client Protocol, add a JSON manifest. Users can also drop it into Settings, Harnesses. For a harness shipped with Meshpool, add it to `BUILTIN` or a bundled manifest in `app/meshpool_harness/harnesses/__init__.py`. Give it a Meshpool name, such as "Meshpool Data". Then add its id to the scores in `routing.json`.
- **Python harness.** For an in-process harness like Meshpool Code, follow `app/meshpool_harness/agent/core.py`, and register it in `harnesses/__init__.py` and `ui/agent_api.py`.
