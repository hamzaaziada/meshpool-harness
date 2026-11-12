"""Named presets: one switch for a tested set of flags. MH_PRESET=v3 turns on everything v3 was measured with.
Explicitly set environment variables always win (setdefault)."""
from __future__ import annotations

import os

PRESETS = {
    "v2": {"MH_HARNESS_PROFILE": "minimal", "MH_STREAM": "1", "MH_CACHE_KEY": "1"},
    # v3 = v2 + loop breaker + browser page prefetch + finish in the same turn as a successful final action.
    # (Always on in the code since v2: 400 retry, malformed-call sanitizing.) Not in v3 by design: MH_ENSEMBLE=2
    # (about doubles cost per task), a shorter stream idle cutoff (one timeout covers both first byte and idle), and
    # MH_EVIDENCE_RETRY (dev split: fired on 2 tasks whose first fix was already right; both second attempts were
    # discarded, adding up to 4x tokens for nothing).
    "v3": {"MH_HARNESS_PROFILE": "minimal", "MH_STREAM": "1", "MH_CACHE_KEY": "1", "MH_STALL": "1",
           "MH_TURN_NOTE": "1",                  # 15 turns before the cap: make the deliverable exist now
           "MH_TODO": "1",                       # required todo list; finish refused while items are open (ForgeCode)
           "MH_ENV_SNAPSHOT": "1",               # environment snapshot in the first prompt (Meta-Harness, Droid)
           "MH_DOOM": "1",                       # repeated identical calls / repeating patterns -> reminder
           "MH_MULTI_CMD": "1",                  # bash_many: several independent commands in one turn (Terminus 2/KIRA)
           "MH_400_WITHHOLD": "1",               # content-tied gateway 400: withhold the newest tool output, keep going
           "MH_BROWSER_PREFETCH": "1", "MH_BROWSER_FINISH_SAME_TURN": "1"},
    # v4 = v3 + fixes from the final v3 head-to-head (2026-11-12). Built after that run; not yet benchmarked.
    "v4": {"MH_HARNESS_PROFILE": "minimal", "MH_STREAM": "1", "MH_CACHE_KEY": "1", "MH_STALL": "1",
           "MH_TURN_NOTE": "1", "MH_TODO": "1", "MH_ENV_SNAPSHOT": "1", "MH_DOOM": "1", "MH_MULTI_CMD": "1",
           "MH_400_WITHHOLD": "1", "MH_BROWSER_PREFETCH": "1", "MH_BROWSER_FINISH_SAME_TURN": "1",
           "MH_STREAM_FIRST_S": "90",            # 14% of v3's calls were cut off waiting >20 s for the first token
           "MH_SESSION_HEADERS": "1",            # Codex-style session_id header: keep a conversation on one backend
           "MH_TERSE": "1",                      # short prose between tool calls: output tokens cost 4x input
           "MH_429_BACKOFF": "1",                # 429 with no reset time: back off 3, 6, 12 s, not a flat 65 s
           "MH_BROWSER_DERIVE": "1"},            # one line of working before acting on a value taken from the page
}

# Experimental flags, off unless set, not in any preset until a measured run keeps them (harness-lab/LOG.md):
#   MH_KEEPWARM_S=<s>     while tools run longer than <s> seconds, re-send the cached prefix with max_tokens=1 every
#                         <s> seconds so the serving side keeps the prompt cache (harness-lab/findings/001).
#                         MH_KEEPWARM_MAX caps pings per tool batch (default 20). Usage gains keepwarm_calls.
#   MH_PARALLEL_MANY=1    bash_many runs its commands at the same time when every one is read-only (tools.read_only:
#                         a conservative allow-list; any write, redirect or unknown program keeps the batch in order).
#   MH_CODE_FINISH_SAME_TURN=1  code engine: run the final check and finish in one turn (the finish guard still
#                         refuses it when that check fails). Saves one model call per solved task.
# Always on (diagnostic only): usage counts full_miss_calls / full_miss_tokens (calls where under half of the
# previous prompt was cached).


def apply(name: str | None = None) -> dict:
    name = name or os.environ.get("MH_PRESET")
    flags = PRESETS.get(name or "", {})
    for k, v in flags.items():
        os.environ.setdefault(k, v)
    return flags
