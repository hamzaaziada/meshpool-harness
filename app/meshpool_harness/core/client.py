"""Model clients. LiveClient goes through eval/ledger.Ledger (budget guard + ledger); MockClient replays fixtures."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WITHHELD = ("[output withheld: the API gateway rejected this command output, probably because of long hex, byte or "
            "key-like strings. Re-run the command so it prints decoded values or a short summary instead.]")


_HEXISH = None


def _hex_score(text: str) -> float:
    """Share of a text that looks like hex dumps, long hex runs or byte escapes (what the gateway's filter rejects)."""
    global _HEXISH
    if _HEXISH is None:
        import re
        _HEXISH = re.compile(r"(?:\b[0-9a-fA-F]{2}\b[ ]){6,}|[0-9a-fA-F]{16,}|(?:\\x[0-9a-fA-F]{2}){3,}")
    if not text:
        return 0.0
    return sum(len(m.group(0)) for m in _HEXISH.finditer(text)) / len(text)


def _withhold_latest_output(messages, limit=6) -> bool:
    """Withhold one tool output after a content-tied rejection: the most hex-like output anywhere in the history
    first (the trigger can be an old output; the filter is partly random), else the newest. At most `limit` per
    conversation, so a rejection caused by something else still ends the run instead of erasing the history."""
    if sum(1 for m in messages if m.get("content") == WITHHELD) >= limit:
        return False
    tools = [m for m in messages if m.get("role") == "tool" and m.get("content") != WITHHELD]
    if not tools:
        return False
    worst = max(tools, key=lambda m: _hex_score(str(m.get("content") or "")))
    target = worst if _hex_score(str(worst.get("content") or "")) > 0.05 else tools[-1]
    target["content"] = WITHHELD
    return True


_CONTEXT_RX = None


def classify_error(msg: str) -> str:
    """What a failed model call means, for the retry policy of both LiveClient.chat and the app agent (agent/core.py):
    'auth' (key refused), 'rate_limit' (429), 'gateway_reject' (the gateway's 400 "rejected as invalid", which comes
    at random for valid requests: GATEWAY_ISSUES #10), 'context' (a 400 saying the request is longer than the model's
    context), 'vision' (a 400 about images or content types), 'bad_request' (any other 400), 'transient' (5xx,
    connection lost, timeout) or 'fatal'."""
    global _CONTEXT_RX
    import re
    if _CONTEXT_RX is None:
        _CONTEXT_RX = (re.compile(r"context[ _-]?(?:length|window)|maximum context|too many tokens|prompt is too long|"
                                  r"input is too long|reduce the length|exceeds? the (?:model'?s? )?(?:context|maximum)|"
                                  r"token limit", re.I),
                       re.compile(r"image|vision|multi-?modal|content[ _-]?type|content part", re.I))
    m = msg or ""
    if "HTTP 401" in m or "HTTP 403" in m:
        return "auth"
    if "HTTP 429" in m:
        return "rate_limit"
    if "rejected as invalid" in m:
        return "gateway_reject"
    if "HTTP 400" in m:
        if _CONTEXT_RX[0].search(m):
            return "context"
        if _CONTEXT_RX[1].search(m):
            return "vision"
        return "bad_request"
    if any(code in m for code in ("HTTP 5", "HTTP 0", "upstream", "timed out")):
        return "transient"
    return "fatal"


def rate_limit_wait(msg: str, n429: int, backoff: bool = True) -> float:
    """Seconds to wait after the n-th 429 in a row: the reset time when the message gives one, else 3, 6, 12 ... 60 s
    (backoff; "please slow down" 429s clear in seconds) or 65 s (a token window)."""
    import calendar
    import re
    import time
    wait = min(60, 3 * 2 ** (max(1, n429) - 1)) if backoff else 65
    mt = re.search(r"resets at: (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", msg or "")
    if mt:
        reset = calendar.timegm(time.strptime(mt.group(1), "%Y-%m-%d %H:%M:%S"))
        wait = max(5, min(120, reset - time.time() + 3))
    return wait


class LiveClient:
    def __init__(self, phase: str = "phase_2_build"):
        try:
            from eval.ledger import Ledger          # benchmark runs: budget ledger + accounting
            self.ledger = Ledger(phase)
        except ImportError:                         # installed on its own: plain transport
            from meshpool_harness.core.wire import SimpleLedger
            self.ledger = SimpleLedger(phase)

    def chat(self, *, model, messages, tools=None, task_id="", job_id="", role="worker", profile="deepseek",
             max_tokens=8192, temperature=None, extra=None):
        import os
        from meshpool_harness.profiles import apply, for_model
        prof = for_model(model, os.environ.get("MH_PROFILE"))
        if prof["family"] != "deepseek":
            messages, b = apply(prof, messages, {"temperature": temperature}, role if role in ("planner", "verifier") else "worker")
            temperature = b.get("temperature")
            profile = prof["family"]
        ex = dict(extra or {})
        if tools:
            ex["tools"] = tools
            ex.setdefault("tool_choice", "auto")
        import time
        last = None
        for attempt in range(25):  # sporadic 502s (backoff) and 429 token-window limits (wait for reset)
            try:
                r = self.ledger.chat(model=model, messages=messages, profile=profile, task_id=task_id,
                                     role=role, job_id=job_id, max_tokens=max_tokens, temperature=temperature,
                                     extra=ex, retries=0)
                self._n429 = 0                                    # a success resets the 429 backoff
                _log_event(task_id, role, model, r)
                return r
            except RuntimeError as e:
                last = e
                msg = str(e)
                kind = classify_error(msg)
                if kind in ("bad_request", "context", "vision", "gateway_reject") and attempt == 0:
                    from meshpool_harness.core.tools import clean      # invalid request: clean every message once
                    for m in messages:
                        if isinstance(m.get("content"), str):
                            m["content"] = clean(m["content"])
                    continue
                if kind == "gateway_reject" and attempt < 4:      # gateway rejects valid requests at random
                    time.sleep(2 * attempt)                        # (GATEWAY_ISSUES #10): retry the same request
                    continue
                if (kind == "gateway_reject" and os.environ.get("MH_400_WITHHOLD") == "1"
                        and _withhold_latest_output(messages)):
                    # The rejection is tied to content (e.g. hex or byte strings in a command's output; GATEWAY_ISSUES
                    # #12), so retrying the same request cannot pass. Withhold the newest tool output and tell the
                    # model, instead of crashing the whole run.
                    continue
                if kind == "rate_limit":                    # meshpool team token limit: wait for the window to reset
                    self._n429 = getattr(self, "_n429", 0) + 1
                    time.sleep(rate_limit_wait(msg, self._n429, os.environ.get("MH_429_BACKOFF") == "1"))
                    continue
                if kind != "transient":
                    raise
                time.sleep(min(30, 2 ** (attempt + 1)))
        raise last

    def spent(self) -> float | None:
        st = getattr(self.ledger, "state", None)          # SimpleLedger (stand-alone install) has no spend state
        return getattr(st, "since_start", None) if st is not None else None


def _log_event(task_id: str, role: str, model: str, resp: dict) -> None:
    """One JSON line per model call to MH_EVENT_LOG (the web app tails it for live progress)."""
    import os
    path = os.environ.get("MH_EVENT_LOG")
    if not path:
        return
    try:
        import time
        msg = ((resp.get("choices") or [{}])[0].get("message") or {})
        calls = []
        for c in msg.get("tool_calls") or []:
            f = c.get("function") or {}
            args = f.get("arguments") or ""
            try:
                a = json.loads(args)
                short = a.get("command") or a.get("path") or a.get("pattern") or a.get("answer") or a.get("name") \
                    or a.get("url") or a.get("action") or args
            except (ValueError, AttributeError):
                short = args
            calls.append({"name": f.get("name", ""), "args": str(short)[:400]})
        u = resp.get("usage") or {}
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or u.get("prompt_cache_hit_tokens") or 0
        ev = {"t": time.time(), "task": task_id, "role": role, "model": model,
              "text": (msg.get("content") or "")[:1200], "tools": calls,
              "in": u.get("prompt_tokens", 0), "cached": cached, "out": u.get("completion_tokens", 0)}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev) + "\n")
    except Exception:
        pass


class MockClient:
    """Replays a list of assistant messages (OpenAI format). Records every request for assertions."""

    def __init__(self, script: list[dict] | str):
        if isinstance(script, str):
            script = [json.loads(l) for l in Path(script).read_text().splitlines() if l.strip()]
        self.script = list(script)
        self.requests: list[dict] = []

    def chat(self, *, model, messages, tools=None, **kw):
        self.requests.append({"model": model, "messages": json.loads(json.dumps(messages)), "tools": tools, **kw})
        if not self.script:
            raise RuntimeError("MockClient script exhausted")
        msg = self.script.pop(0)
        return {"choices": [{"message": msg, "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20}}

    def spent(self) -> float:
        return 0.0


def tool_call(name: str, args: dict, cid: str | None = None) -> dict:
    """Helper for writing mock scripts."""
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": cid or f"call_{name}_{abs(hash(json.dumps(args, sort_keys=True))) % 10**8}", "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}}]}
