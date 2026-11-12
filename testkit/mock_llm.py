"""Mock OpenAI-compatible endpoint for testing Meshpool end to end (streaming + tool calls)."""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = sys.argv[2] if len(sys.argv) > 2 else "/tmp/mock_requests.jsonl"


def plan(messages, tools):
    names = {t["function"]["name"] for t in tools or []}
    last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
    c = messages[last_user]["content"]
    utext = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    done = [m for m in messages[last_user + 1:] if m["role"] == "tool"]
    step = len(done)
    u = utext.lower()
    if "ok" == u.strip(" .") or "single word" in u:
        return ("text", "ok")
    if "word doc" in u:
        doc = next((n for n in names if n.endswith("make_document")), None)
        if step == 0 and doc:
            return ("tool", doc, {"markdown": "# Spend report\n\n- Cache hit **82%**\n\n| Team | Spend |\n|---|---|\n| Infra | 120 |", "filename": "spend.docx"})
        return ("text", "Saved. " + (done[-1]["content"][:200] if done else "no tool"))
    if "mcp" in u:
        calc = next((n for n in names if n.endswith("calculate")), None)
        if step == 0 and calc:
            return ("tool", calc, {"expression": "(1200*0.15)/12"})
        return ("text", f"The MCP calculator says: {done[-1]['content'] if done else 'no tool'}")
    if "long task" in u and "stopped your previous request" not in u:
        if step == 0 and "bash" in names:
            return ("tool", "bash", {"command": "sleep 25; echo slept"})
        if step == 0 and "terminal" in names:
            return ("tool", "terminal", {"command": "sleep 25; echo slept"})
        return ("text", "Long task finished.")
    if "read the pdf" in u:
        if step == 0:
            return ("tool", "read_file" if "read_file" in names else "read", {"path": "report.pdf"})
        if step == 1:
            rd = next((n for n in names if n.endswith("read_document")), None)
            if rd:
                return ("tool", rd, {"path": "model.pbix"})
        return ("text", "Read: " + " || ".join(m["content"][:120].replace("\n", " ") for m in done))
    if "hello" in u:
        if step == 0:
            return ("tool", "write_file" if "write_file" in names else "write", {"path": "hello.py", "content": "print('hello from meshpool')\n"})
        if step == 1:
            return ("tool", "bash", {"command": "python3 hello.py || python hello.py"})
        return ("text", "I created **hello.py** and ran it. Output:\n```\n" + done[-1]["content"][-60:] + "\n```")
    if "finish" in u or "harness" in u:                 # one-shot harness engines: call finish
        if step == 0 and "bash" in names:
            return ("tool", "bash", {"command": "echo 42"})
        if "finish" in names:
            return ("tool", "finish", {"answer": "The answer is 42.", "evidence": ["echo 42"]})
        return ("text", "The answer is 42.")
    return ("text", "Hello from the mock model. I read your message: " + utext[:80])


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        out = json.dumps({"object": "list", "data": [{"id": m, "object": "model", "owned_by": o} for m, o in [("deepseek-v4.1-flash","deepseek"),("deepseek-v4.1","deepseek"),("anthropic/claude-sonnet-4.6","anthropic"),("anthropic/claude-haiku-4.5","anthropic"),("openai/gpt-5.1","openai"),("openai/gpt-5.1-mini","openai"),("google/gemini-3-pro","google"),("qwen/qwen3-coder","qwen"),("moonshotai/kimi-k2","moonshotai")]]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if not body.get("model"):
            open("/tmp/mock_none.json","w").write(json.dumps(body)[:3000])  # LAST_NONE
        with open(LOG, "a") as f:
            f.write(json.dumps({"model": body.get("model"), "path": self.path, "auth": self.headers.get("Authorization"),
                                "stream": body.get("stream"), "cache_key": body.get("prompt_cache_key"),
                                "n_tools": len(body.get("tools") or []), "n_msgs": len(body.get("messages", [])),
                                "tool_names": [t["function"]["name"] for t in body.get("tools") or []],
                                "has_ctx": "No em dashes" in json.dumps(body), "has_mem": "The user prefers" in json.dumps(body)}) + "\n")
        if self.headers.get("Authorization") != "Bearer test-key-1234":
            self.send_response(401); self.end_headers(); self.wfile.write(b'{"error":"bad key"}'); return
        p = plan(body["messages"], body.get("tools"))
        if p[0] == "tool":
            msg = {"role": "assistant", "content": None, "tool_calls": [{"id": f"call_{time.time_ns()}", "type": "function",
                   "function": {"name": p[1], "arguments": json.dumps(p[2])}}]}
        else:
            msg = {"role": "assistant", "content": p[1]}
        usage = {"prompt_tokens": 1000, "completion_tokens": 50, "prompt_tokens_details": {"cached_tokens": 800}}
        if not body.get("stream"):
            out = json.dumps({"id": "x", "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}], "usage": usage}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out); return
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        def ev(d):
            self.wfile.write(b"data: " + json.dumps(d).encode() + b"\n\n"); self.wfile.flush()
        if msg.get("content"):
            words = msg["content"].split(" ")
            for i, w in enumerate(words):
                ev({"id": "x", "choices": [{"index": 0, "delta": {"content": w + (" " if i < len(words) - 1 else "")}}]})
                time.sleep(0.01)
        else:
            c = msg["tool_calls"][0]
            ev({"id": "x", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": c["id"], "type": "function",
                "function": {"name": c["function"]["name"], "arguments": ""}}]}}]})
            a = c["function"]["arguments"]
            for k in range(0, len(a), 7):
                ev({"id": "x", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": a[k:k + 7]}}]}}]})
        ev({"id": "x", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if not msg.get("content") else "stop"}]})
        ev({"id": "x", "choices": [], "usage": usage})
        self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
