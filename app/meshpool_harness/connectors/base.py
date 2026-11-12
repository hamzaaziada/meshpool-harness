"""Connector interface: where tasks come from and where results go. Model-agnostic and engine-agnostic."""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field


@dataclass
class Task:
    source: str                 # connector name, e.g. "github", "jira"
    ref: str                    # source-specific id, e.g. "owner/repo#12", "PROJ-7"
    title: str
    body: str = ""
    url: str = ""
    labels: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def text(self) -> str:
        """The task text handed to the harness."""
        return f"{self.title}\n\n{self.body}".strip()


class Connector:
    """Subclass and register with @register("name"). fetch() reads a task; report() posts the outcome back."""
    name = "base"

    def fetch(self, ref: str) -> Task:
        raise NotImplementedError

    def report(self, task: Task, result: dict) -> None:
        """Default: do nothing. Connectors post a short comment with the outcome and any PR link."""

    @staticmethod
    def summary(result: dict) -> str:
        ok = "Solved" if result.get("ok") else "Not solved"
        parts = [f"**Meshpool Harness: {ok}.**"]
        if result.get("answer"):
            parts.append(str(result["answer"])[:1500])
        if result.get("pr_url"):
            parts.append(f"Draft PR: {result['pr_url']}")
        if result.get("cost_usd") is not None:
            parts.append(f"Model cost: ${result['cost_usd']:.3f}")
        return "\n\n".join(parts)


def http_json(method: str, url: str, headers: dict, body: dict | None = None, timeout: int = 30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Accept": "application/json", "Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}
