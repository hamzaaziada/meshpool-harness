"""The reviewers' standing memory: a masterclass the senior partner and the MD wrote from every review they gave
(masterclass_<kind>.md, kind = "slides" or "excel"), plus the lessons added after later reviews (lessons_<kind>.md).
Every writer prompt reads it, whatever the model; learn() commits new recurring lessons after each review.
MH_MEMORY=0 turns both off."""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAX_LESSONS = 60


def _on() -> bool:
    return os.environ.get("MH_MEMORY", "1") != "0"


def load(kind: str) -> str:
    """The masterclass and the lessons learned since, as one block for a writer prompt ("" when off or missing)."""
    if not _on():
        return ""
    parts = [p.read_text().strip() for p in (HERE / f"masterclass_{kind}.md", HERE / f"lessons_{kind}.md") if p.exists()]
    if not parts:
        return ""
    return ("MASTERCLASS from the senior partner and the MD who will review this work. Follow it; it is how they "
            "grade:\n\n" + "\n\n".join(parts))


LEARN = """You keep the review team's masterclass up to date. Below are the masterclass and lessons so far, then the
partner's and MD's notes on the latest piece of work. List the lessons in these notes that the masterclass does NOT
already cover and that would apply to other briefs: at most 5, each one line, a concrete rule with a short
"wrong -> right" example, no company names or figures (write $XB, X%). Return ONLY JSON {"lessons": ["...", ...]}
([] when everything is covered)."""


def learn(kind: str, reviews: list[dict], client, model: str, task_id: str = "memory") -> list[str]:
    """Commit the new, general lessons from one review to lessons_<kind>.md. Never raises; returns what was added."""
    if not _on() or client is None or not model:
        return []
    notes = []
    for r in reviews or []:
        if r.get("status") not in (None, "ok"):
            continue
        notes += [f"[{r.get('persona')} {i.get('severity')}] {i.get('issue')} FIX: {i.get('fix', '')}"[:300]
                  for i in r.get("issues") or [] if i.get("severity") in ("blocking", "major")]
        notes += [f"[{r.get('persona')} TO 8] {t}"[:260] for t in r.get("to_8") or []]
    if not notes:
        return []
    try:
        r = client.chat(model=model, messages=[{"role": "system", "content": LEARN},
                                               {"role": "user", "content": load(kind)[:30000] + "\n\nNEW NOTES:\n"
                                                + "\n".join(notes[:60])}],
                        max_tokens=1500, temperature=0.1, task_id=task_id, role="worker",
                        extra={"response_format": {"type": "json_object"}})
        txt = r["choices"][0]["message"].get("content") or ""
        new = [str(x).strip() for x in json.loads(txt[txt.find("{"): txt.rfind("}") + 1]).get("lessons") or []
               if str(x).strip()][:5]
    except Exception:  # noqa: BLE001 - memory is best effort
        return []
    if not new:
        return []
    path = HERE / f"lessons_{kind}.md"
    old = path.read_text().splitlines() if path.exists() else [f"# Lessons added after later reviews ({kind})", ""]
    key = lambda x: re.sub(r"[^a-z]+", " ", x.lower()).strip()[:60]    # noqa: E731
    seen = {key(x) for x in old}
    added = [x for x in new if key(x) not in seen]
    body = [x for x in old[2:] if x.startswith("- ")] + [f"- {x} ({_dt.date.today().isoformat()})" for x in added]
    path.write_text("\n".join(old[:2] + body[-MAX_LESSONS:]) + "\n")
    return added
