"""A small MCP (stdio, JSON-RPC 2.0) server loop for Meshpool's built-in tool servers.

A tool is a function that returns a string, or a list of MCP content blocks (text and images). Raise ToolFailed
(or any exception) to report an error to the agent.
"""
from __future__ import annotations

import base64
import json
import sys
import threading
from typing import Callable


class ToolFailed(Exception):
    pass


def text(t: str) -> dict:
    return {"type": "text", "text": t}


def image_png(data: bytes) -> dict:
    return {"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": "image/png"}


class Tool:
    def __init__(self, fn: Callable, description: str, props: dict | None = None, required: list | None = None):
        self.fn, self.description = fn, description
        self.schema = {"type": "object", "properties": props or {}, "required": required or []}


def serve(name: str, tools: dict[str, Tool], serial: bool = False, instructions: str = "") -> None:
    """Read requests from stdin until it closes. serial=True runs tool calls one at a time, in arrival order
    (needed when the tools drive one shared browser or one mouse)."""
    out_lock, call_lock = threading.Lock(), threading.Lock()

    def reply(obj):
        with out_lock:
            sys.stdout.write(json.dumps(obj) + "\n")
            sys.stdout.flush()

    def handle(msg: dict):
        m, rid = msg.get("method"), msg.get("id")
        if rid is None:
            return
        try:
            if m == "initialize":
                ver = (msg.get("params") or {}).get("protocolVersion") or "2025-06-18"
                res = {"protocolVersion": ver, "capabilities": {"tools": {}},
                       "serverInfo": {"name": name, "version": "1.0.0"}}
                if instructions:
                    res["instructions"] = instructions
            elif m == "ping":
                res = {}
            elif m == "tools/list":
                res = {"tools": [{"name": k, "description": t.description, "inputSchema": t.schema}
                                 for k, t in tools.items()]}
            elif m == "tools/call":
                p = msg.get("params") or {}
                t = tools.get(p.get("name"))
                if not t:
                    return reply({"jsonrpc": "2.0", "id": rid,
                                  "error": {"code": -32602, "message": f"unknown tool {p.get('name')}"}})
                args = p.get("arguments") or {}
                if not isinstance(args, dict):
                    args = {}
                props = t.schema.get("properties") or {}
                extra = sorted(k for k in args if k not in props) if props else []
                args = {k: v for k, v in args.items() if k not in extra}   # a made-up argument costs a turn
                try:
                    if serial:
                        with call_lock:
                            r = t.fn(**args)
                    else:
                        r = t.fn(**args)
                    content, err = ([text(r)] if isinstance(r, str) else list(r)), False
                except TypeError as e:
                    content, err = [text(f"Bad arguments: {e}")], True
                except Exception as e:  # noqa: BLE001  report every failure to the agent, never crash
                    content, err = [text(f"{type(e).__name__}: {e}" if not isinstance(e, ToolFailed) else str(e))], True
                if extra:
                    note = f"(Ignored unknown argument(s): {', '.join(extra)}.)"
                    if content and content[0].get("type") == "text":
                        content[0] = text(f"{note}\n{content[0].get('text', '')}")
                    else:
                        content.insert(0, text(note))
                res = {"content": content, "isError": err}
            else:
                return reply({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {m}"}})
        except Exception as e:  # noqa: BLE001
            return reply({"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": str(e)}})
        reply({"jsonrpc": "2.0", "id": rid, "result": res})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        if not isinstance(msg, dict):
            reply({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}})
            continue
        threading.Thread(target=handle, args=(msg,), daemon=True).start()
