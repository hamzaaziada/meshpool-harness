"""GitHub issues and pull requests. Env: GITHUB_TOKEN (or GH_TOKEN); GITHUB_API_URL for GitHub Enterprise."""
from __future__ import annotations

import os
import re

from . import register
from .base import Connector, Task, http_json


@register("github")
class GitHub(Connector):
    def __init__(self):
        self.api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
        tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", "")
        self.h = {"Authorization": f"Bearer {tok}"} if tok else {}

    @staticmethod
    def parse(ref: str):
        m = re.match(r"(?:https://github\.com/)?([\w.-]+)/([\w.-]+)(?:#|/issues/|/pull/)(\d+)$", ref.strip())
        if not m:
            raise ValueError(f"GitHub ref must look like owner/repo#123, got {ref!r}")
        return m.group(1), m.group(2), int(m.group(3))

    def fetch(self, ref: str) -> Task:
        o, r, n = self.parse(ref)
        j = http_json("GET", f"{self.api}/repos/{o}/{r}/issues/{n}", self.h)
        return Task("github", f"{o}/{r}#{n}", j.get("title", ""), j.get("body") or "", j.get("html_url", ""),
                    [l["name"] for l in j.get("labels", [])], {"owner": o, "repo": r, "number": n})

    def report(self, task: Task, result: dict) -> None:
        m = task.meta
        http_json("POST", f"{self.api}/repos/{m['owner']}/{m['repo']}/issues/{m['number']}/comments", self.h,
                  {"body": self.summary(result)})

    def open_pr(self, task: Task, branch: str, base: str = "main") -> str:
        m = task.meta
        j = http_json("POST", f"{self.api}/repos/{m['owner']}/{m['repo']}/pulls", self.h,
                      {"title": f"Fix #{m['number']}: {task.title}", "head": branch, "base": base, "draft": True,
                       "body": f"Draft fix by the Meshpool Harness for #{m['number']}. Review before merging."})
        return j.get("html_url", "")
