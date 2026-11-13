"""Key spend from the gateway's X-Litellm-Key-Spend header (one 1-token call, about $0.000002)."""
import json
import os
import urllib.request

BASE = os.environ.get("BENCH_BASE_URL", "https://llm.meshpool.ai/v1")


def spend() -> float | None:
    body = {"model": "deepseek-v4.1-flash", "messages": [{"role": "user", "content": "1"}], "max_tokens": 1}
    req = urllib.request.Request(f"{BASE}/chat/completions", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "Bearer dummy", "Content-Type": "application/json"})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                v = r.headers.get("X-Litellm-Key-Spend")
                return float(v) if v else None
        except Exception:  # noqa: BLE001
            continue
    return None


if __name__ == "__main__":
    print(spend())
