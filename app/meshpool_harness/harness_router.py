"""Meshpool Auto: route a new task to the harness that does that kind of work best.

The routing table is defaults/routing.json (a copy in the Meshpool data folder overrides it). Each category has
keywords and a score per harness, from benchmark results. A task gets the category whose keywords match it best, then
the highest-scoring harness that is installed and ready (subscriptions must be signed in). No model call is made,
so routing is instant, free and repeatable.
"""
from __future__ import annotations

import json
import os
import re

from meshpool_harness import config as C


def table() -> dict:
    for p in (C.home() / "routing.json", C.APP / "defaults" / "routing.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d.get("categories"), dict):
                return d
        except (OSError, ValueError):
            continue
    return {"categories": {"general": {"keywords": [], "scores": {"meshpool-code": 1}}}}


def classify(text: str, t: dict | None = None) -> tuple[str, list[str]]:
    t = t or table()
    low = " " + re.sub(r"\s+", " ", (text or "").lower()) + " "
    best, hits_best = "general", []
    for cat, c in t["categories"].items():
        hits = [k for k in c.get("keywords") or [] if re.search(r"(?<![a-z0-9])" + re.escape(k.strip()) + r"(?![a-z0-9])", low)]
        if len(hits) > len(hits_best):
            best, hits_best = cat, hits
    return best, hits_best


def route(text: str, kind: str = "cowork") -> dict:
    """{harness, name, category, reason, ranking} for a task."""
    from meshpool_harness import harnesses as HR
    t = table()
    cat, hits = classify(text, t)
    scores = dict((t["categories"].get(cat) or t["categories"].get("general") or {}).get("scores") or {})
    for hid, bonus in ((t.get("mode_bonus") or {}).get(kind) or {}).items():
        scores[hid] = scores.get(hid, 0) + bonus
    # A subscription spends the user's own Claude or ChatGPT plan: Auto picks one only after the user allowed it.
    subs_ok = (C.read().get("MH_AUTO_SUBSCRIPTIONS") or os.environ.get("MH_AUTO_SUBSCRIPTIONS", "0")) == "1"
    ready = {h["id"]: h for h in HR.load_all()
             if h.get("available") and not h.get("error") and h.get("type") != "auto"
             and (not h.get("subscription") or (subs_ok and h.get("signed_in")))}
    ranking = sorted(((s, hid) for hid, s in scores.items() if hid in ready), reverse=True)
    if not ranking:
        hid = HR.default_for(kind if kind in HR.MODES else "cowork")
        return {"harness": hid, "name": ready.get(hid, {}).get("name", hid), "category": cat,
                "reason": "No ranked harness is ready, so the mode's default is used.", "ranking": []}
    score, hid = ranking[0]
    why = f"{cat} task" + (f" ({', '.join(hits[:3])})" if hits else "")
    return {"harness": hid, "name": ready[hid]["name"], "category": cat, "score": score,
            "reason": f"Best benchmark score for a {why}.",
            "ranking": [{"harness": h, "name": ready[h]["name"], "score": s} for s, h in ranking[:5]]}
