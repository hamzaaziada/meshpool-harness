"""The two reviewers inside the deck build: a senior partner and a bank MD (engines/review/personas.py).

build.py runs them twice:

1. On the STORYBOARD, before any slide is written: storyboard_material() -> run_panel(target="storyboard"). Their
   research requests go to engines/forge/research.py, their storyline issues back to the storyline model, until both
   sign off or the round cap is reached.
2. On the rendered DECK, after the rule repairs: deck_panel() renders the pages (review.render_pngs), sends them in
   batches with each page's title and archetype, merges each persona's batches into one review, and route() sends
   every issue to the step that can fix it (content and data -> the slide writer, layout -> a deterministic re-layout
   or an archetype instruction, storyline -> a re-plan of the affected slides, research -> a research step).

Models: the deck review needs vision (review.chain(): MH_REVIEW_MODEL, claude-sonnet-5.5, gpt-6.1-terra, kimi-k3);
the storyboard review a strong text model (MH_STORY_REVIEW_MODEL, then rules review.personas.text_models). A
reviewer whose model fails tries the next model; a refused key stops the chain (every model would be refused). A
reviewer that never ran is reported as not run, never scored.
"""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from engines.review import personas as P

NAMES = {"partner": "Senior partner", "md": "MD"}
DEFAULTS = {"enabled": True, "bar": None, "max_storyboard_rounds": 3, "max_review_rounds": 3, "batch": 6,
            "image_width": 1100, "timeout_s": 300, "deck_models": [], "text_models": [],
            "who": ["partner", "md"]}


def settings(rules: dict | None) -> dict:
    cfg = dict(DEFAULTS, **((rules or {}).get("review") or {}).get("personas") or {})
    cfg["bar"] = dict(P.BAR, **(cfg.get("bar") or {}))
    if os.environ.get("MH_REVIEW_CALIBRATION", "1") == "0":     # grade raw, without the published-work calibration
        cfg["bar"]["calibration"] = {}
    return cfg


def personas_on(client, rules: dict | None) -> bool:
    """The reviewers run on live builds: off with MH_REVIEW=0 or MH_PERSONAS=0, rules review.enabled or
    review.personas.enabled false, and for stub clients (tests turn them on explicitly)."""
    if os.environ.get("MH_PERSONAS", "1") == "0" or not settings(rules).get("enabled", True):
        return False
    from engines.forge.build import _review_on
    return _review_on(client, rules)


def text_models(cfg: dict) -> list[str]:
    from engines.forge.review import CHAIN
    out: list[str] = []
    for m in [os.environ.get("MH_STORY_REVIEW_MODEL")] + list(cfg.get("text_models") or []) + CHAIN:
        if m and m not in out:
            out.append(m)
    return out


def deck_models(cfg: dict) -> list[str]:
    from engines.forge.review import chain
    out = list(cfg.get("deck_models") or [])
    return out + [m for m in chain(None) if m not in out]


# ------------------------------------------------------------------ calling the reviewers
def _is_auth(err: str) -> bool:
    return bool(re.search(r"HTTP 40[13]\b|invalid or missing api key|unauthori[sz]ed", err or "", re.I))


def review_one(client, persona: str, target: str, material, models: list[str], *, bar: dict | None = None,
               timeout: float = 300.0, task_id: str = "forge-panel") -> dict:
    """One reviewer, trying each model in turn until one gives a parseable review. Never raises."""
    from engines.forge.review import _with_timeout
    errors, last = [], None
    for m in models:
        try:
            r = _with_timeout(lambda: P.review(client, persona, target, material, m, task_id=task_id, bar=bar),
                              timeout)
        except Exception as e:  # noqa: BLE001 - a timeout
            r = {"persona": persona, "target": target, "status": "not_run", "verdict": None, "overall": None,
                 "scores": {}, "issues": [], "research_requests": [], "headline": "", "error": str(e)[:300]}
        r["model"] = m
        if r.get("status") == "ok":
            if errors:
                r["fallbacks"] = errors
            return r
        err = r.get("error") or ("unparseable reply: " + str(r.get("raw") or "")[:120])
        errors.append(f"{m}: {err}")
        last = r
        if _is_auth(err):                       # the gateway refuses the key: every model would be refused
            break
    out = dict(last or {"persona": persona, "target": target, "issues": [], "research_requests": [], "scores": {}},
               status="not_run", verdict=None, overall=None, errors=errors)
    out["error"] = errors[-1] if errors else "no model to try"
    return out


def _assemble(target: str, reviews: list[dict]) -> dict:
    ran = [r for r in reviews if r.get("status") == "ok"]
    return {"target": target, "reviews": reviews,
            "signed_off": bool(ran) and all(r["verdict"] == "sign_off" for r in ran),
            "issues": P.merge_issues(ran), "research_requests": [q for r in ran for q in r.get("research_requests") or []],
            "scores": {r["persona"]: r.get("overall") for r in reviews},
            "not_run": [r["persona"] for r in reviews if r.get("status") != "ok"]}


def run_panel(client, target: str, material, models: list[str], *, bar: dict | None = None, timeout: float = 300.0,
              task_id: str = "forge-panel", who: tuple | list | None = None) -> dict:
    """Both reviewers on the same material, in parallel. Same shape as personas.panel()."""
    who = tuple(who or P.WHO_REVIEWS[target])
    with ThreadPoolExecutor(len(who)) as ex:
        res = list(ex.map(lambda p: review_one(client, p, target, material, models, bar=bar, timeout=timeout,
                                               task_id=task_id), who))
    return _assemble(target, res)


def score(panel: dict | None) -> float | None:
    """The panel's score: the mean of the overall scores of the reviewers that ran (None when none ran)."""
    got = [r["overall"] for r in (panel or {}).get("reviews") or [] if r.get("status") == "ok"
           and isinstance(r.get("overall"), (int, float))]
    return round(sum(got) / len(got), 2) if got else None


def why_not_run(panel: dict) -> str:
    errs = " ".join(str(r.get("error") or "") + " " + " ".join(r.get("errors") or [])
                    for r in panel.get("reviews") or [] if r.get("status") != "ok")
    if _is_auth(errs):
        return "the model gateway refused the key"
    if re.search(r"\b429\b|rate limit", errs, re.I):
        return "the model gateway rate-limited the reviewers"
    if re.search(r"image|vision|multi-?modal|content part", errs, re.I):
        return "no vision model accepted the page images"
    if re.search(r"render", errs, re.I):
        return "the deck could not be rendered for review"
    if re.search(r"timed? ?out|no answer in", errs, re.I):
        return "the reviewers timed out"
    return (errs.strip()[:160] or "no reviewer response")


# ------------------------------------------------------------------ storyboard
def storyboard_material(story: dict, facts: list[dict], brief: str, n_slides: int, plan: list | None = None,
                        agenda: bool = False) -> str:
    """The ghost deck for the reviewers: the brief's question, the governing thought, each planned slide (node,
    title, archetype and visual, what it must show, its evidence ids with the facts' text), and the facts in hand."""
    by_id = {f["id"]: f for f in facts}
    slides = plan if plan is not None else [x for x in story.get("slides") or [] if isinstance(x, dict)]
    lines = []
    for i, x in enumerate(slides, 1):
        ev = [e for e in x.get("evidence") or [] if isinstance(e, str)]
        lines.append(f"{i}. [node {x.get('node')}] {x.get('archetype') or '?'}"
                     + (f" | visual: {x['visual']}" if x.get("visual") else "") + f" | \"{x.get('title') or ''}\"")
        if x.get("needs"):
            lines.append(f"   must show: {x['needs']}")
        if ev:
            lines += [f"   {e}: {by_id[e]['text'][:220]}" if e in by_id else f"   {e}: (not in the facts)" for e in ev]
        elif x.get("archetype") not in ("cover", "agenda", "section_divider"):
            lines.append("   evidence: none cited")
    used = {e for x in slides for e in x.get("evidence") or []}
    spare = [f for f in facts if f["id"] not in used]
    spare_txt = "\n".join(f"{f['id']}: {f['text'][:200]}" for f in spare)
    if len(spare_txt) > 6000:
        spare_txt = spare_txt[:6000] + "\n... (more)"
    return (f"The brief (the question the deck must answer):\n{(brief or '').strip()[:1500]}\n\n"
            f"The storyboard must have exactly {n_slides} slides, cover and closing included"
            + (" (the engine adds an agenda slide after the executive summary, built from the sections)" if agenda else "")
            + ". A slide you add must replace one you cut or merge.\n\n"
            f"Governing thought: {story.get('governing_thought') or '(none stated)'}\n\n"
            f"Storyboard ({len(slides)} slides):\n" + "\n".join(lines)
            + (f"\n\nFacts in hand that no slide uses yet:\n{spare_txt}" if spare_txt else ""))


# ------------------------------------------------------------------ deck
def deck_pngs(pptx: str, out_dir: str, width: int) -> list[tuple[int, Path]]:
    from engines.forge.review import render_pngs
    return render_pngs(pptx, out_dir, width_px=width)


def deck_material(pngs: list[tuple[int, Path]], slides: list[dict], batch: int) -> list[list[dict]]:
    """One user message (content parts) per batch of pages: the outline of the whole deck, then each page's number,
    archetype and title followed by its image."""
    from engines.forge.review import image_part
    n = max([k for k, _ in pngs] + [len(slides)])
    outline = "\n".join(f"{i}. [{s.get('archetype', '?')}] {s.get('title', '')}" for i, s in enumerate(slides, 1))
    out = []
    for k in range(0, len(pngs), max(1, batch)):
        b = pngs[k:k + max(1, batch)]
        parts: list[dict] = [{"type": "text", "text": (
            f"Deck outline ({n} pages, title and archetype of each):\n{outline}\n\nYou now see pages "
            f"{b[0][0]}-{b[-1][0]} of {n}" + (" (the other pages are shown in separate messages: judge the storyline "
                                              "on the outline, the pages on what you see)" if len(b) < n else "")
            + ". Name every issue by its slide number (\"slide 4\").")}]
        for i, png in b:
            s = slides[i - 1] if i - 1 < len(slides) else {}
            parts.append({"type": "text", "text": f"\nSlide {i} [{s.get('archetype', '?')}]: {s.get('title', '')}"})
            parts.append(image_part(png))
        out.append(parts)
    return out


def merge_batches(target: str, per_batch: list[dict], bar: dict | None = None) -> dict:
    """Each persona's reviews of the page batches -> one review per persona: dimension scores averaged over the
    batches that ran, issues and research requests pooled, the verdict recomputed from the bar."""
    b = dict(P.BAR, **(bar or {}))
    by_p: dict[str, list[dict]] = {}
    for pn in per_batch:
        for r in pn.get("reviews") or []:
            by_p.setdefault(r["persona"], []).append(r)
    reviews = []
    for who, rs in by_p.items():
        ok = [r for r in rs if r.get("status") == "ok"]
        if not ok:
            errs = [e for r in rs for e in (r.get("errors") or [r.get("error") or ""]) if e]
            reviews.append({"persona": who, "target": target, "status": "not_run", "verdict": None, "overall": None,
                            "scores": {}, "issues": [], "research_requests": [], "headline": "",
                            "error": errs[-1] if errs else "no reply", "errors": errs})
            continue
        dims = [k for k, _, _ in P.dimensions(who, target)]
        scores = {}
        for k in dims:
            vals = [float(r["scores"][k]) for r in ok if isinstance((r.get("scores") or {}).get(k), (int, float))]
            if vals:
                scores[k] = round(sum(vals) / len(vals), 2)
        issues = [i for r in ok for i in r.get("issues") or []]
        ov = P.overall(who, target, scores)
        passed, cal = P.passes(who, target, scores, ov, issues, b)
        worst = min(ok, key=lambda r: r.get("overall") if isinstance(r.get("overall"), (int, float)) else 99)
        reviews.append({"persona": who, "target": target, "status": "ok", "scores": scores, "overall": ov,
                        "calibrated": cal,
                        "verdict": "sign_off" if passed else "revise", "headline": worst.get("headline", ""),
                        "issues": issues, "research_requests": [q for r in ok for q in r.get("research_requests") or []],
                        "model": worst.get("model"), "batches": len(rs),
                        "batches_not_run": len(rs) - len(ok)})
    return _assemble(target, reviews)


def deck_panel(client, pptx: str, slides: list[dict], out_dir: str, cfg: dict, task_id: str = "forge-panel",
               pngs: list[tuple[int, Path]] | None = None) -> dict:
    """Render the deck and run both reviewers on it in batches of pages. Never raises."""
    try:
        if pngs is None:
            pngs = deck_pngs(pptx, out_dir, int(cfg.get("image_width") or 1100))
        if not pngs:
            raise RuntimeError("no pages rendered")
    except Exception as e:  # noqa: BLE001
        who = cfg.get("who") or ["partner", "md"]
        return _assemble("deck", [{"persona": p, "target": "deck", "status": "not_run", "verdict": None,
                                   "overall": None, "scores": {}, "issues": [], "research_requests": [],
                                   "headline": "", "error": f"render: {str(e)[:200]}"} for p in who])
    mats = deck_material(pngs, slides, int(cfg.get("batch") or 6))
    models = deck_models(cfg)
    with ThreadPoolExecutor(max(1, min(4, len(mats)))) as ex:
        per = list(ex.map(lambda m: run_panel(client, "deck", m, models, bar=cfg["bar"],
                                              timeout=float(cfg.get("timeout_s") or 300), task_id=task_id,
                                              who=cfg.get("who")), mats))
    out = merge_batches("deck", per, cfg["bar"])
    out["pages"] = len(pngs)
    out["batches"] = len(mats)
    return out


# ------------------------------------------------------------------ routing
def route(panel: dict, slides: list[dict], locked=lambda i: False) -> dict:
    """Every blocking and major issue (and minor ones on content and layout) to the step that fixes it:
    {"content": {slide: [msg]}, "layout": {slide: [issue]}, "storyline": [issue], "research": [issue],
     "unplaced": [issue]}. Issues on locked pages (computed model pages, the agenda) are not sent to the writer."""
    out: dict = {"content": {}, "layout": {}, "storyline": [], "research": [], "unplaced": []}
    for it in panel.get("issues") or []:
        n = P.slide_of(it.get("where", ""))
        scope = it.get("scope")
        if scope == "storyline":
            out["storyline"].append(it)
            continue
        if scope == "research":
            out["research"].append(it)
            continue
        if not n or n > len(slides) or locked(n - 1):
            out["unplaced"].append(it)
            continue
        if scope == "layout":
            out["layout"].setdefault(n, []).append(it)
        else:                                       # content and data
            out["content"].setdefault(n, []).append(
                f"[review] ({it.get('persona')}, {it.get('severity')}, {scope}) {it['issue']} -> {it.get('fix') or ''}")
    return out


def relayout(slide: dict, issues: list[dict]) -> str | None:
    """A deterministic layout fix when the complaint names one the renderer can make on its own; returns what was
    done, or None (the issue then goes to the writer as an archetype or layout instruction)."""
    from engines.forge.parts import BY_COUNT, LAYOUTS
    text = " ".join(f"{i.get('issue', '')} {i.get('fix', '')}" for i in issues).lower()
    done = []
    parts = [p for p in slide.get("parts") or [] if isinstance(p, dict)]
    if slide.get("archetype") == "grid" and parts:
        lay = slide.get("layout") or "full"
        if LAYOUTS.get(lay, len(parts)) != len(parts) and len(parts) in BY_COUNT:
            slide["layout"] = BY_COUNT[len(parts)]
            done.append(f"grid layout {lay} -> {slide['layout']} to match its {len(parts)} parts")
    charts = [c for c in [slide.get("chart")] + [p.get("chart") for p in parts] if isinstance(c, dict)]
    if re.search(r"\bsort|order(?:ed)? (?:the )?bars|largest first|descending", text):
        for c in charts:
            series = [s for s in c.get("series") or [] if isinstance(s, dict)]
            cats = list(c.get("categories") or [])
            if c.get("kind") in ("bar", "column") and len(series) == 1 and len(cats) == len(series[0].get("values") or []):
                vals = series[0]["values"]
                if all(isinstance(v, (int, float)) for v in vals):
                    order = sorted(range(len(vals)), key=lambda i: -vals[i])
                    if order != list(range(len(vals))):
                        c["categories"] = [cats[i] for i in order]
                        series[0]["values"] = [vals[i] for i in order]
                        done.append("bars sorted largest first")
    return "; ".join(done) or None


def layout_instruction(issues: list[dict]) -> list[str]:
    return [f"[review] ({i.get('persona')}, {i.get('severity')}, layout) {i['issue']} -> {i.get('fix') or ''}. "
            "Fix it in the content: shorten or cut text that crowds or overflows, add a real part or pick an archetype "
            "or grid layout that fills the page when it is half empty, split a crowded exhibit; never change a number."
            for i in issues]


# ------------------------------------------------------------------ reporting
def who(persona: str | None) -> str:
    """'partner' -> 'Senior partner'; a merged issue's 'md, partner' -> 'Senior partner and MD'."""
    ps = sorted((persona or "").split(", "), key=lambda p: list(NAMES).index(p) if p in NAMES else 9)
    return " and ".join(NAMES.get(p, p) for p in ps if p) or "Reviewer"


def _ten(v) -> str:
    return f"{round(float(v) + 1e-9, 1):.1f}" if isinstance(v, (int, float)) else "unscored"


def verdict_line(panel: dict | None, what: str = "deck") -> str:
    """"Senior partner 8.4/10 signs off; MD 7.6/10: revise, top issue: ..." (or why the reviewers did not run)."""
    if not panel:
        return ""
    bits = []
    for r in panel.get("reviews") or []:
        name = NAMES.get(r["persona"], r["persona"])
        if r.get("status") != "ok":
            continue
        if r.get("verdict") == "sign_off":
            bits.append(f"{name} {_ten(r['overall'])}/10 signs off")
        else:
            top = next((i for i in P.merge_issues([r]) if i["severity"] in ("blocking", "major")), None) or \
                next(iter(P.merge_issues([r])), None)
            bits.append(f"{name} {_ten(r.get('overall'))}/10: revise"
                        + (f", top issue: {top['issue'][:160].rstrip('.')}" + (f" ({top['where']})" if top.get("where") else "")
                           if top else ""))
    nr = [NAMES.get(p, p) for p in panel.get("not_run") or []]
    if nr:
        bits.append(f"{' and '.join(nr)} could not review the {what} ({why_not_run(panel)}), so there is no score "
                    f"from {'them' if len(nr) > 1 else 'that reviewer'}")
    return "; ".join(bits)


def review_notes(panel: dict | None, n_slides: int) -> dict[int, list[str]]:
    """The blocking and major issues still open, as [REVIEW] lines for the speaker notes (deck-wide ones on slide
    1)."""
    out: dict[int, list[str]] = {}
    for it in (panel or {}).get("issues") or []:
        if it.get("severity") not in ("blocking", "major"):
            continue
        n = P.slide_of(it.get("where", ""))
        n = n if n and n <= n_slides else 1
        out.setdefault(n, []).append(f"[REVIEW] {who(it.get('persona'))} ({it['severity']}, "
                                     f"{it.get('scope')}): {it['issue']}" + (f" Fix: {it['fix']}" if it.get("fix") else ""))
    return out


def compact(panel: dict | None) -> dict | None:
    """A panel result without the raw replies, for qa.json and the answer."""
    if not panel:
        return None
    return {"target": panel.get("target"), "signed_off": panel.get("signed_off"), "scores": panel.get("scores"),
            "verdicts": {r["persona"]: r.get("verdict") for r in panel.get("reviews") or []},
            "headlines": {r["persona"]: r.get("headline") for r in panel.get("reviews") or [] if r.get("headline")},
            "not_run": panel.get("not_run"), "why_not_run": why_not_run(panel) if panel.get("not_run") else None,
            "open_issues": [{k: i.get(k) for k in ("severity", "scope", "where", "issue", "fix", "persona")}
                            for i in panel.get("issues") or [] if i.get("severity") in ("blocking", "major")][:20]}


def dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=1, default=str))
