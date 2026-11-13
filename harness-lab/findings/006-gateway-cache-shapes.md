# 006: The gateway caches only some request shapes, and our per-task cache key hurt

Source: `harness-lab/cache_probe.py` and follow-up probes on 2026-11-12. Each probe sent identical requests with a
fresh ~3K-token prefix (under $0.01 in all). Then v5 ran on three MiniWoB tasks with per-call logging.

| Requests (identical prefix, 2–3 s apart) | Cache hits |
|---|---|
| no tools, max_tokens 1 (streamed or not) | 0 of 12 |
| tools, max_tokens 1 | 0 of 5 |
| no tools, max_tokens 20 | 0 of 5 |
| tools, max_tokens 20 | 5 of 5 |
| tools, max_tokens 20, a new `prompt_cache_key` + `user` per call | 1 of 4 |
| tools, max_tokens 20, a new `session_id` header per call | 4 of 4 |
| tools, max_tokens 20, no key, different user text | 4 of 4 |

What it means:

- The gateway sends different request shapes to different backends, each with its own cache. Real agent calls
  (tools, large max_tokens) reach the caching backend.
- `MH_CACHE_KEY=1`, in every preset since v2, sends a new `prompt_cache_key` per task. That sends each task to a
  cold cache, so even the identical system prompt and tool list miss on the first call. v2's A/B test of the key was
  never finished; the log says "Unanswered". v5 turns the key off and keeps the session header.
- v5 with the key off, three tasks: no full misses inside any task, and one first call reused 1,792 cached tokens
  from the previous task.
- Keep-warm pings used max_tokens=1, a shape that never hits this cache. They now use 24.
- The app agent also sends a per-session `prompt_cache_key` by default (`MH_CACHE_KEY` defaults to on there). That is
  an app behaviour change, so it is left for the owner. Recommendation: the same as v5.
