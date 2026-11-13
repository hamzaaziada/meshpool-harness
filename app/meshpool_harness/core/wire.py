"""Stand-alone model transport: any OpenAI-compatible endpoint (chat-completions or Responses API). No eval/ needed.
Env: MESHPOOL_BASE_URL (or OPENAI_BASE_URL), MESHPOOL_API_KEY (or OPENAI_API_KEY), MH_WIRE=chat|responses."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


def to_responses(body: dict) -> dict:
    """Chat-completions request -> Responses API request (same content, same order: the prefix stays byte-stable)."""
    items = []
    for m in body.get("messages", []):
        role = m.get("role")
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": m.get("tool_call_id", ""),
                          "output": str(m.get("content") or "")})
            continue
        if m.get("content"):
            items.append({"role": role, "content": m["content"] if isinstance(m["content"], str) else json.dumps(m["content"])})
        for c in m.get("tool_calls") or []:
            f = c.get("function", {})
            items.append({"type": "function_call", "call_id": c.get("id", ""), "name": f.get("name", ""),
                          "arguments": f.get("arguments", "{}")})
    out = {"model": body["model"], "input": items, "max_output_tokens": body.get("max_tokens", 8192)}
    if body.get("prompt_cache_key"):
        out["prompt_cache_key"] = body["prompt_cache_key"]
    if body.get("temperature") is not None:
        out["temperature"] = body["temperature"]
    if body.get("tools"):
        out["tools"] = [{"type": "function", "name": t["function"]["name"],
                         "description": t["function"].get("description", ""),
                         "parameters": t["function"].get("parameters", {})} for t in body["tools"]]
        out["tool_choice"] = body.get("tool_choice", "auto")
    if (body.get("response_format") or {}).get("type") == "json_object":
        out["text"] = {"format": {"type": "json_object"}}
    return out


def from_responses(p: dict) -> dict:
    """Responses API reply -> chat-completions shape the harness expects."""
    text, calls = [], []
    for it in p.get("output", []):
        if it.get("type") == "message":
            text += [c.get("text", "") for c in it.get("content", []) if c.get("type") in ("output_text", "text")]
        elif it.get("type") == "function_call":
            calls.append({"id": it.get("call_id") or it.get("id", ""), "type": "function",
                          "function": {"name": it.get("name", ""), "arguments": it.get("arguments", "{}")}})
    u = p.get("usage") or {}
    cached = (u.get("input_tokens_details") or {}).get("cached_tokens", 0)
    msg = {"role": "assistant", "content": "".join(text) or None}
    if calls:
        msg["tool_calls"] = calls
    return {"id": p.get("id"), "choices": [{"index": 0, "message": msg,
                                            "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": u.get("input_tokens", 0), "completion_tokens": u.get("output_tokens", 0),
                      "prompt_tokens_details": {"cached_tokens": cached}, "prompt_cache_hit_tokens": cached}}


def log_usage_line(tag: str, model: str, usage: dict) -> None:
    """The spend guard cannot read Responses usage; write the per-call token line it would have written."""
    path = os.environ.get("MH_USAGE_LOG", "")       # opt-in; never a fixed machine path
    if not path or not os.path.exists(path):
        return
    with open(path, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())},{tag},{model},{usage.get('prompt_tokens', 0)},"
                f"{(usage.get('prompt_tokens_details') or {}).get('cached_tokens', 0)},{usage.get('completion_tokens', 0)}\n")




def _first_then_idle(r, idle):
    """Yield SSE lines; after the first one, cut the socket timeout to the idle cutoff."""
    first = True
    for line in r:
        yield line
        if first:
            first = False
            try:
                r.fp.raw._sock.settimeout(idle)
            except Exception:                            # noqa: BLE001 - best effort
                pass


def sse_to_chat(lines) -> dict:
    """Assemble a streamed chat-completions reply (SSE lines) into the non-streamed response shape: content,
    tool calls (deltas merged by index), reasoning text and the final usage chunk."""
    content, reasoning, calls, usage, finish, rid = [], [], {}, {}, None, None
    for raw in lines:
        line = raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else str(raw).strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            ev = json.loads(data)
        except ValueError:
            continue
        rid = ev.get("id") or rid
        if ev.get("usage"):
            usage = ev["usage"]
        for ch in ev.get("choices") or []:
            d = ch.get("delta") or {}
            if d.get("content"):
                content.append(d["content"])
            if d.get("reasoning_content"):
                reasoning.append(d["reasoning_content"])
            for tc in d.get("tool_calls") or []:
                idx = tc.get("index")
                if idx is None:                          # no index: a new call id starts a new call
                    idx = max(calls) if calls else 0
                    if tc.get("id") and calls.get(idx, {}).get("id") and calls[idx]["id"] != tc["id"]:
                        idx += 1
                slot = calls.setdefault(idx, {"id": "", "type": "function",
                                              "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                f = tc.get("function") or {}
                slot["function"]["name"] += f.get("name") or ""
                slot["function"]["arguments"] += f.get("arguments") or ""
            finish = ch.get("finish_reason") or finish
    msg = {"role": "assistant", "content": "".join(content) or None}
    if reasoning:
        msg["reasoning_content"] = "".join(reasoning)
    if calls:
        msg["tool_calls"] = [calls[i] for i in sorted(calls)]
    return {"id": rid, "choices": [{"index": 0, "message": msg, "finish_reason": finish}], "usage": usage}


class SimpleLedger:
    """Minimal drop-in for eval.ledger.Ledger when the harness is installed on its own."""

    def __init__(self, phase: str = "run"):
        self.base_url = (os.environ.get("MESHPOOL_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
                         or "https://llm.meshpool.ai/v1").rstrip("/")
        self.api_key = os.environ.get("MESHPOOL_API_KEY") or os.environ.get("OPENAI_API_KEY", "")

    def chat(self, *, model, messages, profile="deepseek", task_id="", role="worker", job_id="", max_tokens=8192,
             temperature=None, extra=None, timeout=600, retries=2, **_):
        body = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if temperature is not None:
            body["temperature"] = temperature
        body.update(extra or {})
        if os.environ.get("MH_CACHE_KEY") == "1" and task_id:
            root = task_id.split("-esc")[0]
            body["prompt_cache_key"] = root
            body["user"] = root
        wire = os.environ.get("MH_WIRE", "chat")
        stream = os.environ.get("MH_STREAM") == "1" and wire == "chat"
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
            timeout = int(os.environ.get("MH_STREAM_IDLE_S", "20"))
            idle = timeout
            if os.environ.get("MH_STREAM_FIRST_S"):    # longer wait for the first token, then the idle cutoff
                timeout = int(os.environ["MH_STREAM_FIRST_S"])
        if wire == "responses":
            body = to_responses(body)
        req = urllib.request.Request(f"{self.base_url}/{'responses' if wire == 'responses' else 'chat/completions'}",
                                     data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                                              "X-Harness-Task-Id": task_id,
                                              **({"session_id": task_id.split("-esc")[0]}
                                                 if os.environ.get("MH_SESSION_HEADERS") == "1" and task_id else {})})
        status, raw = 0, b""
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    status = r.status
                    raw = (json.dumps(sse_to_chat(_first_then_idle(r, idle))).encode() if stream and status == 200
                           else r.read())
            except urllib.error.HTTPError as e:
                raw, status = e.read(), e.code
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                raw, status = f"timed out or connection lost: {e}".encode(), 0
            if status in (200, 429) or attempt == retries:
                break
            time.sleep(1.5 * (attempt + 1))
        payload = json.loads(raw.decode("utf-8", "replace")) if raw[:1] in (b"{", b"[") else {"raw": raw[:300].decode("utf-8", "replace")}
        if status != 200:
            raise RuntimeError(f"meshpool HTTP {status}: {payload}")
        return from_responses(payload) if wire == "responses" else payload
