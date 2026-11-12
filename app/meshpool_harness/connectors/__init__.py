"""Task connectors: fetch a task from a source (GitHub, Jira, HTTP, file, or your own) and report the result back.

Use:  python -m meshpool_harness work github:owner/repo#12 --workdir .
      python -m meshpool_harness work jira:PROJ-7 --workdir . --report
Add your own: a class with fetch()/report() decorated with @register("name"), in a module listed in MH_CONNECTORS
(comma-separated import paths) or exposed through the "meshpool_harness.connectors" entry-point group.
"""
from __future__ import annotations

import importlib
import os

REGISTRY: dict[str, type] = {}


def register(name: str):
    def deco(cls):
        cls.name = name
        REGISTRY[name] = cls
        return cls
    return deco


def _load_plugins():
    for mod in ("github", "jira", "generic"):
        importlib.import_module(f"{__name__}.{mod}")
    for path in filter(None, os.environ.get("MH_CONNECTORS", "").split(",")):
        importlib.import_module(path.strip())
    try:
        from importlib.metadata import entry_points
        for ep in entry_points(group="meshpool_harness.connectors"):
            ep.load()
    except Exception:
        pass


def get(spec: str):
    """'github:owner/repo#1' -> (connector instance, ref)."""
    _load_plugins()
    name, _, ref = spec.partition(":")
    if name not in REGISTRY:
        raise ValueError(f"unknown connector {name!r}; available: {sorted(REGISTRY)}")
    return REGISTRY[name](), ref
