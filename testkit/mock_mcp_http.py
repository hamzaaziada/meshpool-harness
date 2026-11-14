"""A tiny MCP server over HTTP that requires `Authorization: Bearer abc123`, for testing custom connectors.
Usage: python mock_mcp_http.py PORT   (then add http://127.0.0.1:PORT/mcp with the token abc123)"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "Bearer abc123"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(405, {"error": "use POST"})

    def do_DELETE(self):
        self._send(200, {})

    def do_POST(self):
        if self.headers.get("Authorization") != TOKEN:
            return self._send(401, {"error": "bad token"})
        msg = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if "id" not in msg:
            return self._send(202)                       # a notification
        m, rid = msg.get("method"), msg["id"]
        if m == "initialize":
            res = {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-03-26"),
                   "capabilities": {"tools": {}}, "serverInfo": {"name": "mock-http", "version": "1"}}
        elif m == "tools/list":
            res = {"tools": [{"name": "echo", "description": "Echo the text back.",
                              "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]}
        elif m == "tools/call":
            res = {"content": [{"type": "text", "text": str(msg["params"].get("arguments", {}).get("text", ""))}]}
        else:
            return self._send(200, {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown"}})
        self._send(200, {"jsonrpc": "2.0", "id": rid, "result": res})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
