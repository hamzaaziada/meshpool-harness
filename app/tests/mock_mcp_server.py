"""A local MCP server for tests, with every way a remote server can want you to sign in.

  /open/mcp      streamable HTTP, no sign-in
  /token/mcp     bearer token "good-token" (401 without one, and no sign-in metadata)
  /key/mcp       header "X-Api-Key: k1"
  /oauth/mcp     OAuth: protected-resource metadata, an authorization server at /as with dynamic client registration
  /secret/mcp    the same, but registration hands out a secret and the token endpoint wants HTTP Basic
  /noreg/mcp     the same, but the authorization server has no registration endpoint (bring your own client)
  /lhost/mcp     registration accepts only http://localhost:<port> redirect addresses
  /legacy/sse    the 2024-11-05 HTTP+SSE transport (a POST to it is refused, so clients fall back)

The authorize endpoint approves at once and redirects with a code; tests read the Location header.
"""
from __future__ import annotations

import base64
import hashlib
import json
import queue
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOLS = [{"name": "echo", "description": "Echo the text back.",
          "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}]


class State:
    def __init__(self):
        self.clients: dict[str, dict] = {}       # client_id -> {"secret", "redirects", "as"}
        self.codes: dict[str, dict] = {}
        self.tokens: dict[str, float] = {}       # access token -> expiry
        self.refresh: dict[str, str] = {}        # refresh token -> client_id
        self.sse: dict[str, queue.Queue] = {}
        self.token_calls: list[dict] = []
        self.registrations: list[dict] = []


S = State()


def _rpc(msg: dict) -> dict | None:
    if "id" not in msg:
        return None
    m = msg.get("method")
    if m == "initialize":
        res = {"protocolVersion": msg["params"].get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
               "serverInfo": {"name": "mock", "version": "1"}}
    elif m == "tools/list":
        res = {"tools": TOOLS}
    elif m == "tools/call":
        res = {"content": [{"type": "text", "text": "echo: " + str(msg["params"]["arguments"].get("text", ""))}]}
    else:
        return {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "no such method"}}
    return {"jsonrpc": "2.0", "id": msg["id"], "result": res}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def _json(self, code: int, obj, headers: dict | None = None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    # ----- GET: metadata, authorize, the legacy event stream -----
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        for kind in ("oauth", "secret", "noreg", "lhost"):
            if u.path == f"/.well-known/oauth-protected-resource/{kind}/mcp":
                return self._json(200, {"resource": f"{self.base}/{kind}/mcp",
                                        "authorization_servers": [f"{self.base}/as/{kind}"], "scopes_supported": ["read"]})
            if u.path == f"/.well-known/oauth-authorization-server/as/{kind}":
                meta = {"issuer": f"{self.base}/as/{kind}", "authorization_endpoint": f"{self.base}/as/{kind}/authorize",
                        "token_endpoint": f"{self.base}/as/{kind}/token", "code_challenge_methods_supported": ["S256"]}
                if kind != "noreg":
                    meta["registration_endpoint"] = f"{self.base}/as/{kind}/register"
                return self._json(200, meta)
            if u.path == f"/as/{kind}/authorize":
                cl = S.clients.get(q.get("client_id", ""))
                if not cl or q.get("redirect_uri") not in cl["redirects"]:
                    return self._json(400, {"error": "invalid_request", "error_description": "unknown client or redirect"})
                code = secrets.token_urlsafe(12)
                S.codes[code] = {"client": q["client_id"], "challenge": q["code_challenge"], "redirect": q["redirect_uri"]}
                loc = q["redirect_uri"] + "?" + urllib.parse.urlencode({"code": code, "state": q.get("state", "")})
                self.send_response(302)
                self.send_header("Location", loc)
                self.end_headers()
                return
        if u.path == "/legacy/sse":
            sid = secrets.token_hex(6)
            S.sse[sid] = queue.Queue()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(f"event: endpoint\ndata: /legacy/messages?sid={sid}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    msg = S.sse[sid].get(timeout=30)
                except queue.Empty:
                    return
                if msg is None:
                    return
                self.wfile.write(f"event: message\ndata: {json.dumps(msg)}\n\n".encode())
                self.wfile.flush()
        self._json(404, {"error": "not found"})

    # ----- POST: MCP, registration, token -----
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        raw = self._body()
        parts = u.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "as" and parts[2] == "register":
            return self._register(parts[1], json.loads(raw))
        if len(parts) == 3 and parts[0] == "as" and parts[2] == "token":
            return self._token(parts[1], dict(urllib.parse.parse_qsl(raw.decode())))
        if u.path == "/legacy/sse":
            return self._json(405, {"error": "use GET"})
        if u.path == "/legacy/messages":
            sid = dict(urllib.parse.parse_qsl(u.query)).get("sid", "")
            if sid not in S.sse:
                return self._json(404, {"error": "no session"})
            reply = _rpc(json.loads(raw))
            if reply:
                S.sse[sid].put(reply)
            self.send_response(202)
            self.end_headers()
            return
        kind = parts[0] if parts and parts[-1] == "mcp" else ""
        auth = self.headers.get("Authorization", "")
        if kind == "token" and auth != "Bearer good-token":
            return self._json(401, {"error": "unauthorized"})
        if kind == "key" and self.headers.get("X-Api-Key") != "k1":
            return self._json(401, {"error": "unauthorized"})
        if kind in ("oauth", "secret", "noreg", "lhost"):
            tok = auth[7:] if auth.startswith("Bearer ") else ""
            if not tok or S.tokens.get(tok, 0) < time.time():
                rm = f'{self.base}/.well-known/oauth-protected-resource/{kind}/mcp'
                return self._json(401, {"error": "unauthorized"}, {"WWW-Authenticate": f'Bearer resource_metadata="{rm}"'})
        if kind not in ("open", "token", "key", "oauth", "secret", "noreg", "lhost"):
            return self._json(404, {"error": "not found"})
        reply = _rpc(json.loads(raw))
        if reply is None:
            self.send_response(202)
            self.end_headers()
            return
        self._json(200, reply, {"Mcp-Session-Id": "s1"})

    def _register(self, kind: str, body: dict):
        S.registrations.append(dict(body, kind=kind))
        redirects = body.get("redirect_uris") or []
        if kind == "lhost" and not all(r.startswith("http://localhost:") for r in redirects):
            return self._json(400, {"error": "invalid_redirect_uri", "error_description": "redirect_uri not allowed"})
        cid = "c-" + secrets.token_hex(4)
        row = {"client_id": cid, "redirect_uris": redirects}
        cl = {"redirects": redirects, "secret": None, "kind": kind}
        if kind == "secret":                     # confidential: a secret, and no auth method named (RFC default: basic)
            cl["secret"] = row["client_secret"] = "s3cret"
        S.clients[cid] = cl
        return self._json(201, row)

    def _token(self, kind: str, form: dict):
        S.token_calls.append(dict(form, kind=kind, basic=self.headers.get("Authorization", "")))
        cid = form.get("client_id")
        basic = self.headers.get("Authorization", "")
        if basic.startswith("Basic "):
            cid, _, sec = base64.b64decode(basic[6:]).decode().partition(":")
            cid, sec = urllib.parse.unquote(cid), urllib.parse.unquote(sec)
        else:
            sec = form.get("client_secret")
        cl = S.clients.get(cid or "")
        if not cl:
            return self._json(401, {"error": "invalid_client"})
        if cl["secret"] and sec != cl["secret"]:
            return self._json(401, {"error": "invalid_client", "error_description": "client authentication failed"})
        if form.get("grant_type") == "authorization_code":
            c = S.codes.pop(form.get("code", ""), None)
            if not c or c["client"] != cid or c["redirect"] != form.get("redirect_uri"):
                return self._json(400, {"error": "invalid_grant"})
            ch = base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode()).digest()).rstrip(b"=")
            if ch.decode() != c["challenge"]:
                return self._json(400, {"error": "invalid_grant", "error_description": "PKCE check failed"})
        elif form.get("grant_type") == "refresh_token":
            if S.refresh.pop(form.get("refresh_token", ""), None) != cid:
                return self._json(400, {"error": "invalid_grant"})
        else:
            return self._json(400, {"error": "unsupported_grant_type"})
        at, rt = "at-" + secrets.token_hex(6), "rt-" + secrets.token_hex(6)
        S.tokens[at] = time.time() + 3600
        S.refresh[rt] = cid
        return self._json(200, {"access_token": at, "refresh_token": rt, "expires_in": 3600, "token_type": "Bearer"})


def start() -> tuple[ThreadingHTTPServer, str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"
