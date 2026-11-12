# Profile notes — verified provider facts

Started in phase 0. Every claim here is either read from an official provider
page or observed on this machine; URLs are cited. Anything unverified is marked
**unverified**. Profiles (`profiles/<family>.yaml`) will be tuned on dev data in
later phases; this file is the sourced baseline.

Prices live in `eval/pricing.json`. Raw captured pages: `/tmp/price/*.html` at
capture time (not committed).

## deepseek (`deepseek-v4.1-flash`, meshpool id `deepseek-v4.1-flash`)

* **Pricing** (https://api-docs.deepseek.com/quick_start/pricing, read
  2026-11-09): per 1M tokens, DeepSeek-V4.1-Flash column —
  input cache **hit** off-peak `$0.003` / peak `$0.006`; input cache **miss**
  off-peak `$0.15` / peak `$0.30`; output off-peak `$0.60` / peak `$1.20`.
  Off-peak is **half** peak.
* **Peak hours**: `01:00–04:00` and `06:00–10:00` UTC, Mon–Fri, excluding
  Chinese public holidays. All other hours (including weekends) are off-peak.
  → run benchmarks outside those windows (PROMPT §<budget> confirms this).
* **Caching**: automatic on repeated prefixes (the page bills a cache-hit rate
  with no explicit opt-in). Observed on this machine: a 2-turn Junie task showed
  10,496 cached input tokens out of 11,564 total (~91% hit rate).
* **Context / output**: 1M-token context, max output up to 384K per the page;
  **through meshpool the per-call output cap is 16,384** (PROMPT access notes) —
  write large files in parts.
* **Legacy name**: `deepseek-v4-flash` is retired and served by the
  V4.1-Flash-1017 model at the Flash price. So `deepseek-v4.1-flash` and
  `deepseek-v4-flash` are the same model commercially.
* Reasoning/thinking: the page says both models support thinking mode by
  default; exact effort parameters through meshpool are **unverified** (to be
  probed with one cheap call in phase 2/3).

## openai (`gpt-6.1-terra`, `gpt-6-luna`)

* **Pricing** (https://platform.openai.com/docs/pricing, read 2026-11-09),
  per 1M tokens, standard tier:
  * `gpt-6.1-terra`: input `$1.00`, output `$5.00`, cached input `$0.05`,
    cache write 30m `$1.25`, 12h `$2.50`.
  * `gpt-6-luna`: input `$0.10`, output `$0.50`, cached input `$0.01`,
    cache write 30m `$0.125`, 12h `$0.25`.
* The page also lists `gpt-6-astra`, `gpt-6.1-sol`, `gpt-6-cyber`, `gpt-5.6-sol`.
* **Unresolved**: `gpt-5.6-terra` and `gpt-5.6-luna` reachable via meshpool are
  **not** on the OpenAI pricing page (no row), so their prices are marked
  unresolved in `pricing.json` rather than guessed.
* **Caching**: the page documents prompt caching with cached-input and
  cache-write prices (min prefix length not captured here yet — **unverified**;
  to confirm before relying on it).
* Per PROMPT §<budget>, phase-3 smoke tests may call **only the cheaper tier**,
  `gpt-6-luna` (≤ $0.25). Terra is off-limits unless Luna fails outright.

## anthropic (`claude-sonnet-5.5`, `claude-haiku-5.5`)

* **Pricing** (https://docs.anthropic.com/en/docs/about-claude/pricing, read
  2026-11-09), per 1M tokens, standard:
  * `claude-sonnet-5.5`: input `$2.00`, output `$10.00`, cache write 5m
    `$2.50`, cache write 1h `$4.00`, cache hit `$0.10` (5% of input).
  * `claude-haiku-5.5`: input `$0.10`, output `$0.50`, cache write 5m
    `$0.125`, cache write 1h `$0.20`, cache hit `$0.01`. Prompts over 100k
    tokens cost more (input `$0.20`, output `$1.00`).
* **Caching**: explicit — cache-control breakpoints must be placed after the
  tool definitions and system prompt; a cache hit costs 5% of input. Whether
  meshpool passes `cache_control` through is **unverified** (probe with one
  cheap call; record here). Per PROMPT §<budget>, phase-3 smoke tests use the
  cheaper tier `claude-haiku-5.5` (≤ $0.25); do not call `claude-sonnet-5.5`.

## glm (`glm-5.3`, `glm-4.7`)

* **Pricing unresolved.** https://open.bigmodel.cn/pricing renders its table
  client-side (JavaScript) and returned no static prices in phase 0. Not
  guessed; marked unresolved in `pricing.json`.
* Caching / tool-calling / reasoning docs: to be read in phase 3 if budget
  allows (PROMPT allows up to $1.25 per family for glm-5.3 and kimi-k3).

## kimi / moonshot (`kimi-k3`)

* **Pricing unresolved.** https://platform.moonshot.ai/docs/pricing lists the
  K3 series (1,048,576-token context) but the price `<tbody>` is empty in both
  the HTML and the `.md` version — values are injected by JavaScript. Not
  guessed; marked unresolved.
* Documented: context caching is enabled automatically for repeated prefixes
  with a 5-minute TTL, and batch inference bills at 60% of standard.

## meshpool routed tiers (`meshpool-fast`, `meshpool-balanced`, `meshpool-max`)

* No public price list and admin endpoints are not exposed; prices unresolved.
  These are candidate *routing* targets, to be measured by spend delta when
  used, not priced from a table.

## Cross-cutting facts

* The meshpool `usage` object gives `prompt_tokens` and `completion_tokens`
  only — **no cached-token breakdown** — so cache hit rate is measured from
  spend deltas per call, not usage fields (matches PROMPT access notes).
* Everything goes through one OpenAI-compatible endpoint; provider-specific
  features (Anthropic `cache_control`, reasoning-effort params) must be probed
  per the plan and the pass/fail recorded above.
## Measured meshpool billing (deepseek-v4.1-flash), 2026-11-09/10

Actual key spend (`x-litellm-key-spend` deltas) vs per-call token usage priced at DeepSeek off-peak list rates:
off-peak window 22:00-23:30 UTC factor 2.25; peak window 01:05-02:35 UTC factor 2.20. Meshpool bills a flat
~2.2x DeepSeek off-peak list (about DeepSeek's peak price) at all hours, so off-peak scheduling saves nothing through
meshpool. All reported costs use the measured factor. Meshpool reports cached prompt tokens per call
(`prompt_tokens_details.cached_tokens`), so cache hit rates are exact. No distinct reasoning mode is exposed:
`reasoning_effort` low/high returns no reasoning tokens.
