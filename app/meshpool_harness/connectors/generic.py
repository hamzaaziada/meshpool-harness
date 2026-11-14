"""Built-in simple connectors: a local file or stdin, and any HTTP endpoint that speaks a tiny JSON contract."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import register
from .base import Connector, Task, http_json


@register("file")
class FileSource(Connector):
    """file:path/to/task.md (first line is the title) or file:- for stdin. Results go to <path>.result.json."""

    def fetch(self, ref: str) -> Task:
        text = sys.stdin.read() if ref == "-" else Path(ref).read_text(encoding="utf-8")
        title, _, body = text.partition("\n")
        return Task("file", ref, title.strip("# ").strip(), body.strip())

    def report(self, task: Task, result: dict) -> None:
        if task.ref != "-":
            Path(task.ref + ".result.json").write_text(json.dumps(result, indent=1, default=str),
                                                         encoding="utf-8")


@register("http")
class HTTPSource(Connector):
    """http:<url>. GET <url> returns {"title", "body"}; the result is POSTed to <url>/result (or MH_HTTP_RESULT_URL).
    Optional MH_HTTP_TOKEN is sent as a Bearer token. Use this for Linear, ServiceNow, an internal queue, etc."""

    def __init__(self):
        tok = os.environ.get("MH_HTTP_TOKEN")
        self.h = {"Authorization": f"Bearer {tok}"} if tok else {}

    def fetch(self, ref: str) -> Task:
        j = http_json("GET", ref, self.h)
        return Task("http", ref, j.get("title", ""), j.get("body", ""), ref, j.get("labels", []), j)

    def report(self, task: Task, result: dict) -> None:
        http_json("POST", os.environ.get("MH_HTTP_RESULT_URL", task.ref.rstrip("/") + "/result"), self.h,
                  {"ok": bool(result.get("ok")), "summary": self.summary(result)})
