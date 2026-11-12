"""Model clients. LiveClient goes through eval/ledger.Ledger (budget guard + ledger); MockClient replays fixtures."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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
                _log_event(task_id, role, model, r)
                return r
            except RuntimeError as e:
                last = e
                msg = str(e)
                if "HTTP 400" in msg and attempt == 0:      # invalid request: clean every message once, retry
                    from meshpool_harness.core.tools import clean
                    for m in messages:
                        if isinstance(m.get("content"), str):
                            m["content"] = clean(m["content"])
                    continue
                if "rejected as invalid" in msg and attempt < 4:   # gateway rejects valid requests at random
                    time.sleep(2 * attempt)                        # (GATEWAY_ISSUES #10): retry the same request
                    continue
                if "HTTP 429" in msg:                       # meshpool team token limit: wait for the window to reset
                    import re as _re
                    wait = 65
                    mt = _re.search(r"resets at: (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", msg)
                    if mt:
                        import calendar
                        reset = calendar.timegm(time.strptime(mt.group(1), "%Y-%m-%d %H:%M:%S"))
                        wait = max(5, min(120, reset - time.time() + 3))
                    time.sleep(wait)
                    continue
                if not any(code in msg for code in ("HTTP 5", "HTTP 0", "upstream", "timed out")):
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
