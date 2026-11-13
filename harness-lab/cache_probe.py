"""Probe the gateway's prompt cache with identical ~3K-token prefixes (costs about $0.005).
Prints cached/prompt for: no key, same key, different keys, and gaps of 0, 10, 30 s."""
import json, os, time, urllib.request, uuid

BASE = os.environ.get("BENCH_BASE_URL", "https://llm.meshpool.ai/v1")
PREFIX = "You are a careful assistant. " + " ".join(f"Rule {i}: keep answers short and exact." for i in range(300))


def call(key=None, session=None, extra=""):
    body = {"model": "deepseek-v4.1-flash", "max_tokens": 1,
            "messages": [{"role": "system", "content": PREFIX}, {"role": "user", "content": "Say ok." + extra}]}
    h = {"Authorization": "Bearer dummy", "Content-Type": "application/json"}
    if key:
        body["prompt_cache_key"] = key
    if session:
        h["session_id"] = session
    req = urllib.request.Request(f"{BASE}/chat/completions", data=json.dumps(body).encode(), headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        u = json.loads(r.read()).get("usage") or {}
    c = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or u.get("prompt_cache_hit_tokens") or 0
    return f"{c}/{u.get('prompt_tokens')}"


if __name__ == "__main__":
    salt = uuid.uuid4().hex[:6]                       # a fresh prefix, so earlier runs do not warm it
    PREFIX = salt + " " + PREFIX
    print("no key, back to back:", [call() for _ in range(3)])
    k = "probe-" + salt
    print("same key, back to back:", [call(k) for _ in range(3)])
    print("new key each call:", [call(f"{k}-{i}") for i in range(3)])
    print("same key + session header:", [call(k, k) for _ in range(2)])
    for gap in (10, 30):
        time.sleep(gap)
        print(f"same key after {gap}s:", call(k), "| no key after the same gap:", call())
