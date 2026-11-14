"""A local OpenAI-compatible relay to the user's ChatGPT sign-in, for agents that speak chat completions (Pi, Hermes).

  http://127.0.0.1:<port>/v1/chat/completions   streamed or not, tools included
  http://127.0.0.1:<port>/v1/models             the models the ChatGPT plan offers

Why a relay instead of handing each agent the sign-in: ChatGPT refresh tokens are single-use and shared with the
Codex CLI, so every extra program that refreshes them can sign the others out. Through the relay, the Meshpool
process stays the only refresher (core/chatgpt.py, under its file lock) and the agents never see the ChatGPT token.

The relay listens on 127.0.0.1 only and every request needs a random bearer token made per Meshpool process, which
Meshpool hands to the agents it starts. It runs in a daemon thread and stops with the Meshpool process.
"""
from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from meshpool_harness.core import chatgpt as CG

MAX_BODY = 64 * 1024 * 1024
_LOCK = threading.Lock()
_STATE: dict = {"server": None, "url": "", "token": ""}


def _chunk(rid: str, model: str, delta: dict | None = None, finish: str | None = None, usage: dict | None = None) -> bytes:
    ev = {"id": rid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
          "choices": [] if usage is not None else [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
    if usage is not None:
        ev["usage"] = usage
    return f"data: {json.dumps(ev)}\n\n".encode()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):                  # no request log on the console
        pass

    def _authorized(self) -> bool:
        got = self.headers.get("Authorization", "")
        return hmac.compare_digest(got.encode(), f"Bearer {_STATE['token']}".encode())

    def _json(self, code: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, code: int, msg: str, kind: str = "invalid_request_error"):
        self._json(code, {"error": {"message": msg, "type": kind}})

    def do_GET(self):
        if not self._authorized():
            return self._error(401, "Missing or wrong relay token.", "authentication_error")
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            ids = list(dict.fromkeys([CG.default_model()] + CG.models()))
            return self._json(200, {"object": "list", "data": [{"id": i, "object": "model", "owned_by": "openai"}
                                                               for i in ids]})
        self._error(404, "Not found.")

    def do_POST(self):
        if not self._authorized():
            return self._error(401, "Missing or wrong relay token.", "authentication_error")
        if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
            return self._error(404, "Not found.")
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return self._error(413 if n > MAX_BODY else 400, "Bad request size.")
        try:
            body = json.loads(self.rfile.read(n))
        except ValueError:
            return self._error(400, "The body is not JSON.")
        stream = bool(body.get("stream"))
        model = CG.resolve_model(body.get("model"))
        rid = "chatcmpl-" + uuid.uuid4().hex[:24]
        task = self.headers.get("session_id") or self.headers.get("X-Session-Id") or ""
        if not stream:
            try:
                r = CG.chat(body, task_id=task)
            except CG.UsageLimit as e:
                return self._json(429, {"error": {"message": str(e), "type": "insufficient_quota", "code": "insufficient_quota"}})
            except CG.NotSignedIn as e:
                return self._error(401, str(e), "authentication_error")
            except RuntimeError as e:
                return self._error(_status(e), str(e), "api_error")
            from meshpool_harness.core.wire import from_responses
            out = from_responses(r)
            out.update(id=rid, object="chat.completion", created=int(time.time()), model=model)
            return self._json(200, out)
        # Streamed: text goes out as it arrives; tool calls and usage follow when the reply is complete.
        started, sent_text = [False], [False]

        def start():
            if not started[0]:
                started[0] = True
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(_chunk(rid, model, {"role": "assistant", "content": ""}))

        def on_text(t: str):
            start()
            sent_text[0] = True
            self.wfile.write(_chunk(rid, model, {"content": t}))
            self.wfile.flush()
        try:
            r = CG.chat(body, task_id=task, on_text=on_text)
        except (BrokenPipeError, ConnectionResetError):
            return
        except CG.UsageLimit as e:
            if not started[0]:
                return self._json(429, {"error": {"message": str(e), "type": "insufficient_quota",
                                                  "code": "insufficient_quota"}})
            self.wfile.write(f"data: {json.dumps({'error': {'message': str(e), 'type': 'insufficient_quota'}})}\n\n".encode())
            self.close_connection = True
            return
        except RuntimeError as e:
            if not started[0]:
                kind = "authentication_error" if isinstance(e, CG.NotSignedIn) else "api_error"
                return self._error(401 if isinstance(e, CG.NotSignedIn) else _status(e), str(e), kind)
            self.wfile.write(f"data: {json.dumps({'error': {'message': str(e), 'type': 'api_error'}})}\n\n".encode())
            self.close_connection = True
            return
        from meshpool_harness.core.wire import from_responses
        c = from_responses(r)
        msg = c["choices"][0]["message"]
        start()
        if msg.get("content") and not sent_text[0]:
            self.wfile.write(_chunk(rid, model, {"content": msg["content"]}))   # text that came without deltas
        for i, tc in enumerate(msg.get("tool_calls") or []):
            self.wfile.write(_chunk(rid, model, {"tool_calls": [{"index": i, "id": tc["id"], "type": "function",
                                                                 "function": tc["function"]}]}))
        self.wfile.write(_chunk(rid, model, {}, c["choices"][0]["finish_reason"]))
        if (body.get("stream_options") or {}).get("include_usage"):
            self.wfile.write(_chunk(rid, model, usage=c["usage"]))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


def _status(e: Exception) -> int:
    import re
    m = re.search(r"HTTP (\d{3})", str(e))
    code = int(m.group(1)) if m else 502
    return code if 400 <= code < 600 else 502


def ensure() -> tuple[str, str]:
    """(base URL ending in /v1, bearer token) of this process's relay, started on first use."""
    with _LOCK:
        if _STATE["server"] is None:
            srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            srv.daemon_threads = True
            _STATE.update(server=srv, token=secrets.token_urlsafe(32),
                          url=f"http://127.0.0.1:{srv.server_address[1]}/v1")
            threading.Thread(target=srv.serve_forever, name="chatgpt-relay", daemon=True).start()
        return _STATE["url"], _STATE["token"]


def stop() -> None:
    with _LOCK:
        srv = _STATE["server"]
        _STATE.update(server=None, url="", token="")
    if srv is not None:
        srv.shutdown()
        srv.server_close()


def env() -> dict:
    """The environment an agent process needs to reach the relay."""
    url, tok = ensure()
    return {"MESHPOOL_CHATGPT_RELAY_URL": url, "MESHPOOL_CHATGPT_RELAY_KEY": tok}
