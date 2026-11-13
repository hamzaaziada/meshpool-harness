"""Per-call metrics shared by the benchmark loop (core/loop.py) and the app agent (agent/core.py), so both report the
same numbers and a benchmark result can be compared with what users see."""
from __future__ import annotations


def cached_tokens(usage: dict) -> int:
    """Cached prompt tokens from an OpenAI-style or DeepSeek-style usage block."""
    u = usage or {}
    return (u.get("prompt_tokens_details") or {}).get("cached_tokens") or u.get("prompt_cache_hit_tokens") or 0


class CacheMeter:
    """Counts full cache misses: calls where under half of the previous prompt was cached. In run C, 73% of avoidable
    miss tokens came right after a cut-off call (harness-lab/findings/002); this makes that visible on every run."""

    def __init__(self):
        self.last_prompt = 0

    def observe(self, usage: dict, into: dict) -> None:
        prompt = (usage or {}).get("prompt_tokens", 0) or 0
        cached = cached_tokens(usage)
        if self.last_prompt and cached < 0.5 * self.last_prompt:
            into["full_miss_calls"] = into.get("full_miss_calls", 0) + 1
            into["full_miss_tokens"] = into.get("full_miss_tokens", 0) + prompt - cached
        self.last_prompt = prompt
