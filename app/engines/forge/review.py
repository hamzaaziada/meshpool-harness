"""Partner review: a vision model reads the RENDERED slides the way a demanding engagement partner or client would,
against our written standard (polish_rubric.md), and returns concrete, fixable complaints the build loop can act on.

    review = review_deck("deck.pptx", slides=json.load(open("slides.json")))
    fails = as_failures(review)            # {slide_no: ["[review] <issue> -> <fix> (content)", ...]}
    deck_verdict(review)                   # overall score, slides to fix, the top recurring complaints

Rendering: LibreOffice -> PDF -> pypdfium2 -> one PNG per slide (as validate_pptx does for its render checks).
Model chain (first that answers wins, per batch): `model` argument, MH_REVIEW_MODEL, claude-sonnet-5.5,
gpt-6.1-terra, kimi-k3. Batches of `batch` slides run in parallel; each call has a wall-clock timeout. Any failure
gives {"status": "not_run"} for the slides it covered (or the deck) and never raises.

The reviewer never invents numbers: a complaint about a figure must quote what is on the slide, and complaints that
cite a figure appearing neither on the slide text (from the JSON) nor in the model's own quote are dropped.
"""
from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RUBRIC_PATH = Path(__file__).with_name("polish_rubric.md")
CHAIN = ["claude-sonnet-5.5", "gpt-6.1-terra", "kimi-k3"]
SEVERITIES = ("high", "medium", "low")
SCOPES = ("content", "layout")
CATEGORIES = ("story", "visual", "layout", "typography", "data", "finish")
DROP_KEYS = {"prov", "title_prov", "node", "id", "notes", "icon", "tags", "fixed"}
NON_CONTENT = {"cover", "section_divider", "appendix_divider", "end"}

OUTPUT = """
---

## What to return

You get the deck outline (every slide title in order), then one or more slides: for each, the writer's JSON (what
was meant to appear) and the rendered image (what the reader actually sees). Judge the IMAGE against the standard
above. Use the JSON only to read small text exactly, to tell when rendered text was cut off or dropped, and for
the slide's role (archetype). Use the outline only for story complaints (S4-S7).

Precision matters more than coverage: every complaint costs a repair round. Rules:
- Raise only what you can see on this image. Quote the exact words or figures you see in "issue".
- Never invent or compute new numbers. A complaint about a figure quotes the figure as shown on the slide; a fix
  may say "use one format" or "correct to the sourced value" but must not supply a value that is not on the slide.
- Do not raise a complaint the rubric does not support, and do not restate one complaint in several ways.
- Dropped content: when a text, bullet, callout or chart in the JSON appears NOWHERE on the image, that is a real
  flaw (the renderer lost it): name what was dropped. Look over the whole image first; an element that is on the
  slide but in an odd place is a layout complaint, never "missing". Do not quote JSON-only text (ids, notes,
  [GAP] markers, provenance) as if the reader saw it.
- Covers, section dividers and end pages are judged only for typos, overlap, cut-off text and placeholders.
- Slide furniture (tracker, page number, source line, footer) is checked: a missing source line is D2 only when
  the slide shows figures and no line starting "Source" is visible.
- At most 5 complaints per slide, most severe first. A slide that meets the bar gets verdict "ship" and may have
  no complaints at all; most decent slides have 0-2.
- "fix" is one concrete instruction the slide writer (scope "content") or the renderer (scope "layout") can apply
  without asking a question: name the element and the change ("Cut the second bullet in the left panel to <= 12
  words", "Sort the bars from largest to smallest and highlight NVIDIA").

Return ONLY JSON, no prose, no code fence:
{"slides": [{"slide": <slide number>, "verdict": "ship"|"fix", "score": <1-10>,
  "complaints": [{"severity": "high"|"medium"|"low", "category": "story"|"visual"|"layout"|"typography"|"data"|"finish",
  "rule": "<rubric id, e.g. L1>", "issue": "<what is wrong, quoting the slide>", "fix": "<the concrete change>",
  "scope": "content"|"layout"}]}]}
One entry per slide you were shown, numbered as given.
"""


def rubric() -> str:
    return RUBRIC_PATH.read_text(encoding="utf-8")


def system_prompt() -> str:
    return rubric() + OUTPUT


# ------------------------------------------------------------------ rendering
def _page_list(pages, n: int) -> list[int]:
    """0-based page indexes from None (all), a "1-3,5" spec or an iterable of 1-based slide numbers."""
    if pages is None:
        return list(range(n))
    if isinstance(pages, str):
        from .measure_pdf import _pages
        return _pages(pages, n)
    return [int(p) - 1 for p in pages if 0 < int(p) <= n]


def render_pngs(pptx_path: str, out_dir: str | None = None, width_px: int = 1500, pages=None,
                pdf_path: str | None = None) -> list[tuple[int, Path]]:
    """[(slide number, png path)] for the chosen slides. Reuses an existing PDF when given."""
    import pypdfium2 as pdfium
    from .common import soffice_convert
    out = Path(out_dir or tempfile.mkdtemp(prefix="review_"))
    out.mkdir(parents=True, exist_ok=True)
    pdf = Path(pdf_path) if pdf_path else soffice_convert(pptx_path, "pdf", str(out))
    doc = pdfium.PdfDocument(str(pdf))
    try:
        res = []
        for i in _page_list(pages, len(doc)):
            page = doc[i]
            scale = width_px / page.get_width()
            p = out / f"slide_{i + 1:02d}.png"
            page.render(scale=scale).to_pil().convert("RGB").save(p, optimize=True)
            res.append((i + 1, p))
        return res
    finally:
        doc.close()


def image_part(png: Path | bytes) -> dict:
    data = png if isinstance(png, bytes) else Path(png).read_bytes()
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(data).decode()}}


# ------------------------------------------------------------------ JSON
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)


def _balanced(txt: str, start: int) -> str | None:
    """The bracketed value starting at txt[start], respecting strings; None when it never closes."""
    depth, ins, esc = 0, False, False
    for k in range(start, len(txt)):
        c = txt[k]
        if ins:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                ins = False
            continue
        if c == '"':
            ins = True
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return txt[start:k + 1]
    return None


def _loads(s: str):
    try:
        return json.loads(s)
    except ValueError:
        pass
    t = re.sub(r",\s*([}\]])", r"\1", s)                               # trailing commas
    t = t.replace("“", '"').replace("”", '"')                # smart quotes around keys
    try:
        return json.loads(t)
    except ValueError:
        return None


def _salvage_slides(txt: str) -> list[dict]:
    """Complete {"slide": ...} objects from a truncated or broken reply."""
    out = []
    for m in re.finditer(r'\{\s*"slide"\s*:', txt):
        blob = _balanced(txt, m.start())
        obj = _loads(blob) if blob else None
        if isinstance(obj, dict):
            out.append(obj)
    return out


def parse_reply(txt: str | None) -> list[dict] | None:
    """Per-slide dicts from a model reply: plain JSON, fenced JSON, prose around JSON, a bare list, one slide object,
    trailing commas, or a truncated reply (complete slide objects are kept). None when nothing usable is found."""
    if not txt or not txt.strip():
        return None
    cands = [m.group(1) for m in _FENCE.finditer(txt)] + [txt]
    for c in cands:
        c = c.strip()
        for k, ch in enumerate(c):
            if ch in "{[":
                blob = _balanced(c, k)
                obj = _loads(blob) if blob else None
                if obj is not None:
                    if isinstance(obj, dict) and isinstance(obj.get("slides"), list):
                        return [x for x in obj["slides"] if isinstance(x, dict)]
                    if isinstance(obj, list) and all(isinstance(x, dict) for x in obj):
                        return obj
                    if isinstance(obj, dict) and "slide" in obj:
                        return [obj]
                break                                   # first bracket did not give a usable value: try salvage
    got = _salvage_slides(txt)
    return got or None


def _num(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except (TypeError, ValueError):
        return default


_RULE_CAT = {"S": "story", "V": "visual", "L": "layout", "T": "typography", "D": "data", "F": "finish"}
_FIGURE = re.compile(r"(?<![A-Za-z\d.])\d[\d,]*(?:\.\d+)?")


def _figures(t: str) -> set[str]:
    return {f.replace(",", "").rstrip(".") for f in _FIGURE.findall(t or "")}


def _flatten(obj) -> str:
    if isinstance(obj, dict):
        return " ".join(_flatten(v) for k, v in obj.items() if k not in DROP_KEYS)
    if isinstance(obj, list):
        return " ".join(_flatten(v) for v in obj)
    return str(obj) if obj is not None else ""


def normalize(entry: dict, n: int, slide: dict | None = None, known: set[str] | None = None) -> dict:
    """Clamp one model entry to the contract. When the slide JSON is known, a complaint whose fix supplies a figure
    that is on neither the deck (`known`: every figure in the deck JSON) nor in the complaint's own quote is dropped:
    the reviewer must not invent numbers."""
    if slide is not None:
        known = (known or set()) | _figures(_flatten(slide))
    comps, dropped = [], []
    for c in entry.get("complaints") or []:
        if not isinstance(c, dict):
            continue
        issue = str(c.get("issue") or "").strip()
        fix = str(c.get("fix") or "").strip()
        if not issue or not fix:
            continue
        sev = str(c.get("severity") or "medium").lower()
        sev = sev if sev in SEVERITIES else "medium"
        rule = str(c.get("rule") or "").strip().upper()[:4]
        cat = str(c.get("category") or "").lower()
        cat = cat if cat in CATEGORIES else _RULE_CAT.get(rule[:1], "finish")
        scope = str(c.get("scope") or "").lower()
        scope = scope if scope in SCOPES else ("layout" if cat == "layout" else "content")
        if known is not None:
            new = _figures(fix) - _figures(issue) - known
            new = {f for f in new if not (f.isdigit() and int(f) <= 40)}   # word limits, pt sizes, counts
            if new:
                dropped.append({"issue": issue, "fix": fix, "why": f"fix supplies figures not on the slide: "
                                f"{', '.join(sorted(new))}"})
                continue
        comps.append({"severity": sev, "category": cat, "rule": rule, "issue": issue, "fix": fix, "scope": scope})
    comps.sort(key=lambda c: SEVERITIES.index(c["severity"]))
    verdict = "fix" if any(c["severity"] in ("high", "medium") for c in comps) else "ship"
    score = _num(entry.get("score"), 1, 10, 6 if verdict == "fix" else 8)
    out = {"slide": n, "verdict": verdict, "score": score, "complaints": comps[:6]}
    if dropped:
        out["dropped"] = dropped
    return out


# ------------------------------------------------------------------ the call
def _with_timeout(fn, timeout: float):
    """Run fn() with a wall-clock limit. The worker thread is a daemon: a stalled upstream never blocks exit."""
    box: dict = {}

    def run():
        try:
            box["v"] = fn()
        except BaseException as e:  # noqa: BLE001 (reported to the caller as a failed call)
            box["e"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"no answer in {timeout:.0f}s")
    if "e" in box:
        raise box["e"]
    return box["v"]


def _slide_context(slide: dict | None, limit: int = 2500) -> str:
    if not slide:
        return "(no JSON)"
    keep = {k: v for k, v in slide.items() if k not in DROP_KEYS}

    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if k not in DROP_KEYS}
        if isinstance(o, list):
            return [strip(v) for v in o]
        return o
    s = json.dumps(strip(keep), ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + " ...(truncated)"


def _outline(slides: list[dict] | None, n_pages: int) -> str:
    if not slides:
        return "(outline not given)"
    return "\n".join(f"{i}. [{s.get('archetype', '?')}] {s.get('title', '')}" for i, s in enumerate(slides, 1))


def build_messages(batch: list[tuple[int, Path]], slides: list[dict] | None, n_pages: int) -> list[dict]:
    parts: list[dict] = [{"type": "text", "text": f"Deck outline ({n_pages} slides):\n{_outline(slides, n_pages)}\n\n"
                          f"Review slides {', '.join(str(n) for n, _ in batch)}."}]
    for n, png in batch:
        sl = slides[n - 1] if slides and n - 1 < len(slides) else None
        parts.append({"type": "text", "text": f"\nSlide {n} ({(sl or {}).get('archetype', 'unknown')}). "
                      f"Writer's JSON: {_slide_context(sl)}\nRendered image of slide {n}:"})
        parts.append(image_part(png))
    parts.append({"type": "text", "text": f"\nReturn the JSON for slides {', '.join(str(n) for n, _ in batch)}."
                  f"\n<run id=\"{uuid.uuid4().hex[:8]}\"/>"})
    return [{"role": "system", "content": system_prompt()}, {"role": "user", "content": parts}]


def chain(model: str | None = None) -> list[str]:
    out: list[str] = []
    for m in [model, os.environ.get("MH_REVIEW_MODEL")] + CHAIN:
        if m and m not in out:
            out.append(m)
    return out


def _usage(r: dict) -> dict:
    u = r.get("usage") or {}
    cost = u.get("cost") or u.get("total_cost") or r.get("cost") or 0
    return {"in": int(u.get("prompt_tokens") or 0), "out": int(u.get("completion_tokens") or 0),
            "cost": float(cost) if isinstance(cost, (int, float, str)) and str(cost).replace(".", "", 1).isdigit() else 0.0}


def review_deck(pptx_path: str, slides: list[dict] | None = None, client=None, model: str | None = None,
                pages=None, batch: int = 3, workers: int = 4, timeout: float = 240.0, out_dir: str | None = None,
                pngs: list[tuple[int, Path]] | None = None, task_id: str = "forge-review") -> dict:
    """Review the rendered deck. Returns
    {"status": "ok"|"partial"|"not_run", "model": ..., "slides": [{"slide", "verdict", "score", "complaints"}],
     "not_run": [slide numbers], "errors": [...], "usage": {"in", "out"}, "seconds": ...}.
    `slides` is the deck's slide JSON (slides.json), used for the outline and per-slide context.
    `pages`: None (all), "1-3,5" or [1, 2, 5]. Never raises."""
    t0 = time.time()
    try:
        if pngs is None:
            pngs = render_pngs(pptx_path, out_dir, pages=pages)
        n_pages = max([n for n, _ in pngs] + [len(slides or [])])
    except Exception as e:  # noqa: BLE001
        return {"status": "not_run", "model": None, "slides": [], "not_run": [], "errors": [f"render: {e}"],
                "usage": {"in": 0, "out": 0}, "seconds": round(time.time() - t0, 1)}
    if not pngs:
        return {"status": "not_run", "model": None, "slides": [], "not_run": [], "errors": ["no slides rendered"],
                "usage": {"in": 0, "out": 0}, "seconds": round(time.time() - t0, 1)}
    if client is None:
        try:
            from meshpool_harness.core.client import LiveClient
            client = LiveClient("forge_review")
        except Exception as e:  # noqa: BLE001
            return {"status": "not_run", "model": None, "slides": [], "not_run": [n for n, _ in pngs],
                    "errors": [f"client: {e}"], "usage": {"in": 0, "out": 0}, "seconds": round(time.time() - t0, 1)}
    models = chain(model)
    batches = [pngs[k:k + max(1, batch)] for k in range(0, len(pngs), max(1, batch))]
    errors: list[str] = []
    lock = threading.Lock()
    deck_figs = _figures(_flatten(slides)) if slides else None

    def one(b):
        want = [n for n, _ in b]
        for m in models:
            msgs = build_messages(b, slides, n_pages)
            try:
                r = _with_timeout(lambda: client.chat(model=m, messages=msgs, max_tokens=4000, temperature=0.0,
                                                      task_id=task_id, role="verifier"), timeout)
                txt = ((r.get("choices") or [{}])[0].get("message") or {}).get("content")
                if isinstance(txt, list):                    # some providers return content parts
                    txt = "".join(p.get("text", "") for p in txt if isinstance(p, dict))
                got = parse_reply(txt)
                if not got:
                    raise ValueError(f"unparseable reply: {str(txt)[:160]!r}")
                by_n = {}
                for e in got:
                    k = _num(e.get("slide"), 0, 10 ** 4, 0)
                    if k in want:
                        by_n[k] = e
                if not by_n and len(got) == len(want):       # model renumbered (1..k): map by order
                    by_n = dict(zip(want, got))
                if not by_n:
                    raise ValueError("reply named none of the slides shown")
                res = [normalize(by_n[k], k, slides[k - 1] if slides and k - 1 < len(slides) else None, deck_figs)
                       for k in want if k in by_n]
                return m, res, [k for k in want if k not in by_n], _usage(r)
            except Exception as e:  # noqa: BLE001 (model missing, timeout, bad JSON: next model)
                with lock:
                    errors.append(f"slides {want[0]}-{want[-1]} {m}: {type(e).__name__}: {str(e)[:200]}")
        return None, [], want, {"in": 0, "out": 0, "cost": 0.0}

    with ThreadPoolExecutor(max(1, min(workers, len(batches)))) as ex:
        results = list(ex.map(one, batches))
    out_slides = sorted((s for _, res, _, _ in results for s in res), key=lambda s: s["slide"])
    missing = sorted(n for _, _, miss, _ in results for n in miss)
    used = [m for m, res, _, _ in results if m]
    usage = {k: sum(u[k] for *_, u in results) for k in ("in", "out", "cost")}
    usage["cost"] = round(usage["cost"], 4)
    status = "ok" if not missing else ("partial" if out_slides else "not_run")
    return {"status": status, "model": max(set(used), key=used.count) if used else None,
            "models": dict(Counter(used)), "slides": out_slides, "not_run": missing, "errors": errors,
            "usage": usage, "seconds": round(time.time() - t0, 1), "calls": len(batches)}


# ------------------------------------------------------------------ for the build loop
def as_failures(review: dict, severities=("high", "medium"), scopes=SCOPES) -> dict[int, list[str]]:
    """{slide number: ["[review] <issue> -> <fix> (<scope>)", ...]} for the build loop's repair round. Only the
    given severities and scopes (the slide writer can act on "content"; "layout" is for the renderer)."""
    out: dict[int, list[str]] = {}
    if not isinstance(review, dict) or review.get("status") == "not_run":
        return out
    for s in review.get("slides") or []:
        for c in s.get("complaints") or []:
            if c.get("severity") in severities and c.get("scope") in scopes:
                out.setdefault(int(s["slide"]), []).append(f"[review] {c['issue']} -> {c['fix']} ({c['scope']})")
    return out


def deck_verdict(review: dict, top: int = 10) -> dict:
    """Overall score (mean of slide scores), the slides to fix, and the most recurring complaints by rubric rule."""
    if not isinstance(review, dict) or review.get("status") == "not_run" or not review.get("slides"):
        return {"status": "not_run", "score": None, "verdict": None, "fix": [], "top": []}
    sl = review["slides"]
    score = round(sum(s["score"] for s in sl) / len(sl), 1)
    fix = [s["slide"] for s in sl if s["verdict"] == "fix"]
    sev = Counter()
    by_rule: dict[str, dict] = {}
    for s in sl:
        for c in s["complaints"]:
            key = c.get("rule") or c["category"]
            d = by_rule.setdefault(key, {"rule": key, "category": c["category"], "count": 0, "slides": [],
                                         "worst": "low", "example": c["issue"], "fix": c["fix"]})
            d["count"] += 1
            if s["slide"] not in d["slides"]:
                d["slides"].append(s["slide"])
            if SEVERITIES.index(c["severity"]) < SEVERITIES.index(d["worst"]):
                d["worst"], d["example"], d["fix"] = c["severity"], c["issue"], c["fix"]
            sev[c["severity"]] += 1
    ranked = sorted(by_rule.values(), key=lambda d: (-d["count"], SEVERITIES.index(d["worst"])))
    return {"status": review.get("status"), "score": score,
            "verdict": "ship" if not fix and score >= 7 else "fix", "fix": fix,
            "complaints": dict(sev), "top": ranked[:top], "not_run": review.get("not_run", [])}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Partner review of a rendered deck")
    ap.add_argument("pptx")
    ap.add_argument("--slides", help="slides.json (default: beside the deck)")
    ap.add_argument("--model")
    ap.add_argument("--pages")
    ap.add_argument("--batch", type=int, default=3)
    ap.add_argument("--out", help="review.json path (default: beside the deck)")
    ap.add_argument("--png-dir")
    a = ap.parse_args(argv)
    sp = Path(a.slides) if a.slides else Path(a.pptx).with_name("slides.json")
    slides = json.loads(sp.read_text()) if sp.exists() else None
    if isinstance(slides, dict):
        slides = slides.get("slides")
    rv = review_deck(a.pptx, slides=slides, model=a.model, pages=a.pages, batch=a.batch, out_dir=a.png_dir)
    rv["verdict"] = deck_verdict(rv)
    out = Path(a.out) if a.out else Path(a.pptx).with_name("review.json")
    out.write_text(json.dumps(rv, indent=2, ensure_ascii=False))
    v = rv["verdict"]
    print(f"{rv['status']} model={rv.get('model')} score={v.get('score')} fix={v.get('fix')} "
          f"{rv['seconds']}s in={rv['usage']['in']} out={rv['usage']['out']} -> {out}")
    return 0 if rv["status"] != "not_run" else 1


if __name__ == "__main__":
    raise SystemExit(main())
