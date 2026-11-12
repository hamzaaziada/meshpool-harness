"""Model profiles: data files in profiles/<family>.yaml, chosen by model id, overridable by flag."""
from __future__ import annotations

from pathlib import Path

import yaml

DIR = Path(__file__).resolve().parents[1] / "profiles"


def load_all() -> dict:
    return {p.stem: yaml.safe_load(p.read_text()) for p in DIR.glob("*.yaml")}


def for_model(model: str, override: str | None = None) -> dict:
    allp = load_all()
    if override:
        return allp[override]
    for name, p in allp.items():
        if any(m in model for m in p.get("match", [])):
            return p
    return allp["deepseek"]


def apply(profile: dict, messages: list, body: dict, role: str = "worker") -> tuple[list, dict]:
    """Return (messages, extra body) adjusted for the family: caching markers, sampling, output param."""
    s = (profile.get("sampling") or {}).get(role) or {}
    if "temperature" in s:
        if s["temperature"] is None:
            body.pop("temperature", None)
        else:
            body["temperature"] = s["temperature"]
    lim = profile.get("limits") or {}
    if lim.get("param") == "max_completion_tokens" and "max_tokens" in body:
        body["max_completion_tokens"] = body.pop("max_tokens")
    if (profile.get("caching") or {}).get("mode") == "explicit_breakpoints" and messages and messages[0]["role"] == "system":
        sysm = messages[0]
        text = sysm["content"] if isinstance(sysm["content"], str) else sysm["content"][0]["text"]
        messages = [{"role": "system", "content": [{"type": "text", "text": text,
                                                     "cache_control": {"type": "ephemeral"}}]}] + messages[1:]
    return messages, body
