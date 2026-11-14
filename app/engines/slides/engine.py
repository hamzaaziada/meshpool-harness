"""`meshpool run --engine slides`: decks are built by the forge engine (engines/forge/engine.py), which writes the
storyline and content with the model and renders them in the forge design system. A forge failure is an error."""
from __future__ import annotations


def solve(task: str, *, workdir: str, task_id: str, client=None, model: str | None = None, **kw) -> dict:
    from engines.forge.engine import solve as forge_solve
    return forge_solve(task, workdir=workdir, task_id=task_id, client=client, model=model, **kw)
