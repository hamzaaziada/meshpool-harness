"""A small MCP server that ships with Meshpool, so MCP works with nothing else installed.

Run by the agent through mcp.json:  {"command": "python", "args": ["-m", "meshpool_harness.agent.demo_mcp_server"]}
Tools: current_time, calculate, project_stats. Use it as a template for your own server: each tool is a schema
plus a Python function.
"""
from __future__ import annotations

import ast
import json
import operator
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


def current_time(utc_offset_hours: float = 0) -> str:
    t = datetime.now(timezone(timedelta(hours=utc_offset_hours)))
    return t.strftime("%A %d %B %Y, %H:%M:%S") + f" (UTC{utc_offset_hours:+g})"


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv, ast.USub: operator.neg,
        ast.UAdd: operator.pos}


def calculate(expression: str) -> str:
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            if isinstance(n.op, ast.Pow) and abs(ev(n.right)) > 1000:
                raise ValueError("exponent too large")
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        raise ValueError("only numbers and + - * / // % ** ( ) are allowed")
    return f"{expression} = {ev(ast.parse(expression, mode='eval'))}"


SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"}


def project_stats(path: str = ".") -> str:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"not a folder: {root}")
    files, lines = Counter(), Counter()
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP]
        for f in filenames:
            n += 1
            if n > 20000:
                break
            ext = Path(f).suffix.lower() or "(none)"
            files[ext] += 1
            try:
                with open(Path(dirpath) / f, "rb") as fh:
                    lines[ext] += sum(1 for _ in fh) if (Path(dirpath) / f).stat().st_size < 5_000_000 else 0
            except OSError:
                pass
    rows = [f"{ext:<10} {files[ext]:>6} files {lines[ext]:>9} lines" for ext, _ in files.most_common(15)]
    return f"{root}\n{sum(files.values())} files, {sum(lines.values())} lines\n" + "\n".join(rows)


TOOLS = {
    "current_time": (current_time, "Current date and time at a UTC offset (Cairo is +2).",
                     {"utc_offset_hours": {"type": "number", "description": "Hours from UTC, e.g. 2 or -5"}}, []),
    "calculate": (calculate, "Evaluate an arithmetic expression exactly, e.g. '(1200*0.15)/12'.",
                  {"expression": {"type": "string"}}, ["expression"]),
    "project_stats": (project_stats, "Count files and lines by file type in a folder (skips .git, node_modules).",
                      {"path": {"type": "string", "description": "Folder path; default is the current folder"}}, []),
}


def handle(msg: dict) -> dict | None:
    m, rid = msg.get("method"), msg.get("id")
    if rid is None:
        return None                                       # notification
    if m == "initialize":
        ver = (msg.get("params") or {}).get("protocolVersion") or "2025-06-18"
        res = {"protocolVersion": ver, "capabilities": {"tools": {}},
               "serverInfo": {"name": "meshpool-tools", "version": "1.0.0"}}
    elif m == "ping":
        res = {}
    elif m == "tools/list":
        res = {"tools": [{"name": k, "description": d, "inputSchema": {"type": "object", "properties": p,
                                                                       "required": r}}
                         for k, (_, d, p, r) in TOOLS.items()]}
    elif m == "tools/call":
        p = msg.get("params") or {}
        name, args = p.get("name"), p.get("arguments") or {}
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"unknown tool {name}"}}
        try:
            text, err = str(TOOLS[name][0](**args)), False
        except Exception as e:
            text, err = f"{type(e).__name__}: {e}", True
        res = {"content": [{"type": "text", "text": text}], "isError": err}
    else:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {m}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": res}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            out = handle(json.loads(line))
        except ValueError:
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
