"""Meshpool Auto: route a new task to the harness that does that kind of work best.

The routing table is defaults/routing.json (a copy in the Meshpool data folder overrides it). Each category has
keywords and, per harness, a benchmark quality score (0-100) and, where measured, the cost and time per solved task.

Auto never trades away quality: it takes the best quality among the harnesses that are installed and can run on the
chosen provider, treats every harness within `quality_band` points of it as equally good (one task in twenty is 5
points, inside run-to-run noise), and among those picks the cheapest, then the fastest. No model call is made, so
routing is instant, free and repeatable.
"""
from __future__ import annotations

import json
import os
import re

from meshpool_harness import config as C

DEFAULT_BAND = 5.0


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
    """The category whose keywords match best. A phrase of several words counts once per word, so "pull request"
    outweighs a lone generic word; a tie goes to the category whose first match comes earliest in the text."""
    t = t or table()
    low = " " + re.sub(r"\s+", " ", (text or "").lower()) + " "
    best, best_key, hits_best = "general", (0, 0), []
    for cat, c in t["categories"].items():
        hits, weight, first = [], 0, len(low)
        for k in c.get("keywords") or []:
            k = k.strip().lower()
            m = k and re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", low)
            if m:
                hits.append(k)
                weight += len(k.split())
                first = min(first, m.start())
        key = (weight, -first)
        if weight and key > best_key:
            best, best_key, hits_best = cat, key, hits
    return best, hits_best


def _ready(kind: str) -> dict:
    """Harnesses Auto may pick: installed, set up on a provider, and not a subscription the user kept for itself."""
    from meshpool_harness import harnesses as HR
    # A harness on a ChatGPT sign-in spends the user's own plan. Auto uses that plan freely when ChatGPT is the
    # provider the user chose, and otherwise only after the user allowed it.
    subs_ok = (C.read().get("MH_AUTO_SUBSCRIPTIONS") or os.environ.get("MH_AUTO_SUBSCRIPTIONS", "0")) == "1"
    chosen = HR.provider()
    return {h["id"]: h for h in HR.load_all()
            if h.get("available") and not h.get("error") and h.get("type") != "auto" and h.get("ready", True)
            and (not h.get("plan") or h["plan"] == chosen or subs_ok)}


def _money(v: float) -> str:
    return f"${v:.4f}".rstrip("0").rstrip(".") if v < 0.1 else f"${v:.2f}"


def route(text: str, kind: str = "cowork") -> dict:
    """{harness, name, category, reason, score, ranking} for a task."""
    from meshpool_harness import harnesses as HR
    t = table()
    cat, hits = classify(text, t)
    c = t["categories"].get(cat) or t["categories"].get("general") or {}
    quality = {h: float(s) for h, s in (c.get("scores") or {}).items()}
    for hid, bonus in ((t.get("mode_bonus") or {}).get(kind) or {}).items():
        if hid in quality:                      # a harness with no score in a category is never picked for it
            quality[hid] += bonus
    cost, secs = c.get("cost_usd") or {}, c.get("seconds") or {}
    ready = _ready(kind)
    scored = {h: q for h, q in quality.items() if h in ready}
    if not scored:
        hid = HR.default_for(kind if kind in HR.MODES else "cowork")
        return {"harness": hid, "name": ready.get(hid, {}).get("name", hid), "category": cat,
                "reason": "No ranked agent is ready, so the mode's default is used.", "ranking": []}
    best = max(scored.values())
    band = float(t.get("quality_band", DEFAULT_BAND))
    inf = float("inf")
    order = lambda h: (cost.get(h, inf), secs.get(h, inf), -scored[h])   # noqa: E731
    pool = sorted((h for h, q in scored.items() if q >= best - band), key=order)
    hid = pool[0]
    why = f"a {cat} task" + (f" ({', '.join(hits[:3])})" if hits else "")
    if len(pool) == 1 or hid not in cost:
        reason = f"Best benchmark quality for {why}."
    else:
        top = max(pool, key=lambda h: scored[h])
        rest = [h for h in pool if h != hid and h in cost]
        reason = (f"Top benchmark quality for {why}" if scored[hid] >= scored[top] else
                  f"Benchmark quality within {band:g} points of the best for {why}")
        reason += f", at the lowest cost ({_money(cost[hid])} per solved task"
        if rest:
            reason += f" vs {_money(min(cost[h] for h in rest))}"
        reason += ")."
    ranking = sorted(scored, key=lambda h: (-(scored[h] >= best - band), order(h)))
    return {"harness": hid, "name": ready[hid]["name"], "category": cat, "score": scored[hid], "reason": reason,
            "ranking": [{"harness": h, "name": ready[h]["name"], "score": scored[h],
                         **({"cost_usd": cost[h]} if h in cost else {}), **({"seconds": secs[h]} if h in secs else {})}
                        for h in ranking[:5]]}
