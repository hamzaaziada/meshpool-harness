"""Router: deterministic rules first; one tiny classification call only when rules are unsure."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .rules import decide

ENGINES = ["research", "credit", "code", "data", "sheets", "slides", "general"]
DEFAULT_MODEL = os.environ.get("MH_MODEL", "deepseek-v4.1-flash")   # any OpenAI-compatible model id


@dataclass
class Route:
    engines: list
    confidence: float
    reason: str
    used_model_call: bool


def _classify(request: str, files: list[str], client, model: str | None = None) -> tuple[list[str], float, str]:
    prompt = ("Pick the engine chain for this request. Engines: research (collect and cite outside sources: web, SEC "
              "filings; a sourced report), credit (a lender's cash flow / DSCR / credit memo on a borrower), code (repo/terminal work), data (analysis of data or "
              "text), sheets (build Excel workbooks), slides (build decks), general (anything else). Chains are "
              "ordered, e.g. [\"data\",\"slides\"]. Reply with JSON only: {\"engines\": [...], \"confidence\": 0-1}.\n"
              f"Request: {request}\nAttached files: {', '.join(Path(f).name for f in files) or 'none'}")
    resp = client.chat(model=model or DEFAULT_MODEL, messages=[{"role": "user", "content": prompt}], max_tokens=60,
                       temperature=0.0, task_id="router", role="router",
                       extra={"response_format": {"type": "json_object"}})
    txt = resp["choices"][0]["message"].get("content") or "{}"
    try:
        d = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
        eng = [e for e in d.get("engines", []) if e in ENGINES] or ["general"]
        return eng, float(d.get("confidence", 0.5)), "model classification"
    except (ValueError, TypeError):
        return ["general"], 0.3, "model classification unparseable"


def route(request: str, files: list[str] | None = None, output_format: str | None = None, workdir: str | None = None,
          client=None, engine: str | None = None, model: str | None = None, task_id: str = "") -> Route:
    """Pick the engine chain. Nothing is written: the old routing.csv log sat inside the app folder (read-only in the
    signed Mac app), was never read and never got its outcome column filled."""
    files = files or []
    if engine:
        return Route(engine.split(","), 1.0, "override by flag", False)
    eng, conf, why = decide(request, files, output_format, workdir)
    used = False
    if conf < 0.6 and client is not None:
        eng2, conf2, why2 = _classify(request, files, client, model)
        used = True
        if conf2 >= 0.5:
            eng, conf, why = eng2, conf2, why2 + f" (rules said {eng} @ {conf})"
        elif conf < 0.5:
            eng, why = ["general"], why + "; low confidence -> general"
    return Route(eng, conf, why, used)
