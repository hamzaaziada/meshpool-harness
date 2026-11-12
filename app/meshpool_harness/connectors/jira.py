"""Jira Cloud or Server. Env: JIRA_BASE_URL plus either JIRA_EMAIL + JIRA_API_TOKEN (Cloud) or JIRA_PAT (Server/DC)."""
from __future__ import annotations

import base64
import os

from . import register
from .base import Connector, Task, http_json


def adf_text(node) -> str:
    """Flatten Atlassian Document Format (Jira Cloud descriptions) to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if node.get("type") == "text":
        return node.get("text", "")
    inner = "".join(adf_text(c) for c in node.get("content", []))
    return inner + ("\n" if node.get("type") in ("paragraph", "heading", "listItem", "codeBlock") else "")


@register("jira")
class Jira(Connector):
    def __init__(self):
        self.base = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
        if os.environ.get("JIRA_PAT"):
            self.h = {"Authorization": f"Bearer {os.environ['JIRA_PAT']}"}
            self.v = "2"
        else:
            raw = f"{os.environ.get('JIRA_EMAIL', '')}:{os.environ.get('JIRA_API_TOKEN', '')}".encode()
            self.h = {"Authorization": "Basic " + base64.b64encode(raw).decode()}
            self.v = "3"

    def fetch(self, ref: str) -> Task:
        key = ref.strip().split("/")[-1]
        j = http_json("GET", f"{self.base}/rest/api/{self.v}/issue/{key}?fields=summary,description,labels", self.h)
        f = j.get("fields", {})
        desc = f.get("description")
        body = adf_text(desc) if isinstance(desc, dict) else (desc or "")
        return Task("jira", key, f.get("summary", ""), body.strip(), f"{self.base}/browse/{key}", f.get("labels", []),
                    {"key": key})

    def report(self, task: Task, result: dict) -> None:
        text = self.summary(result)
        body = ({"body": {"type": "doc", "version": 1, "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text}]}]}} if self.v == "3" else {"body": text})
        http_json("POST", f"{self.base}/rest/api/{self.v}/issue/{task.meta['key']}/comment", self.h, body)
