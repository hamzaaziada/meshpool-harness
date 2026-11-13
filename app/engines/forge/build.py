"""Forge deck builder: brief -> storyline -> slide JSON -> native render -> validate -> repair (<= 3 rounds).

    python -m engines.forge.build brief.md out_dir [--slides 12]

The model writes only JSON: a storyline (ghost deck), then slide content in schemas/slide.schema.json. The renderer
owns layout. validate_pptx decides; its content-scope failures go back to the model slide by slide. Anything still
failing after the last round is written into the slide notes as [QA] lines and into qa.json. Nothing passes
silently.
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from engines.forge.common import load_rules  # noqa: E402
from engines.forge.render import ARCHETYPES, render  # noqa: E402
from engines.forge.validate_pptx import _candidates, _numbers, validate  # noqa: E402

MODEL = os.environ.get("FORGE_MODEL") or os.environ.get("MH_MODEL", "deepseek-v4.1-flash")
STORY_MODEL = os.environ.get("FORGE_STORY_MODEL", MODEL)

ARCH_HELP = """Archetypes (pick the one that best PROVES the title; never more than 3 of the same in a row). The
renderer applies a strict consulting design; you only fill fields. Dense is good when it is organised.
- cover: title, subtitle.
- challenge_matrix: numbered rows x two lenses. "columns": [row header, lens A, lens B] (e.g. ["Challenge",
  "A - Today", "B - Outlook"]); "rows": 3-5 of {"icon": one word meaning (e.g. money, congestion, store, people,
  safety, growth, risk, time), "label": "**Key word** rest of label" (<= 6 words), "cells": [A, B]} where each
  cell is {"lead": one bold sentence (<= 10 words), "bullets": 1-3 evidence lines (<= 22 words) each starting
  with its number}. Use for 'N issues/challenges/drivers, each with evidence'.
- exhibit_argument: left = one exhibit, right = the argument. "title" (left half, <= 9 words, may end with "…"),
  "title_right" (<= 9 words, continues the thought, may start with "…but"), "exhibit": {"title": what is shown,
  "unit": measure and period}, "chart" (bar/column/line, or bubble with points[{x,y,size,label,focus}],
  x_range, y_range, x_major, y_major, x_cross, y_cross, x_title), "blocks": 2-3 of {"text": intro sentence with
  **bold key phrases**, "bullets": 1-3 lines with the numbers as **bold phrases**}, "callout": {"bullets": 2-3
  one-line 'so what' points with **bold** lead words}.
- table_focus: a ranked/benchmark table with ONE highlighted row. "subtitle": measure, source table, year;
  "table": {"groups": optional [{"label","span"}] over value columns, "columns": [...], "rows": [[...]],
  "highlight_rows": [index of the focus row], "italic_rows": [benchmark/average rows], "total_row": bool,
  "heat": optional {"column index": [all numeric values of that column]} for heat-map shading,
  "size": 12 (10 if more than 14 rows)}; "takeaway": {"text": one bold line under the table}.
- bar_takeaway / column_trend / line_trend / waterfall_bridge: "subtitle" (measure, unit, period), one "chart"
  {kind, categories, series[{name, values}], unit, decimals, highlight_categories} on the left 2/3, a "takeaway"
  {"text"} (bold lead) and "bullets" [2-3 {"text","prov"}] interpreting it on the right 1/3.
Text markup: **bold phrase**; a bold phrase containing a number is shown in the emphasis colour; ^1 is a
superscript footnote marker. Numbers in bullets are emphasised automatically. Footnotes go in "footnotes"."""

SLIDE_SHAPE = """Slide JSON: {"id","node" (from the storyline),"archetype","title","title_prov":[fact ids],
 ...archetype fields..., "source": {"text": original sources, "prov": [ids]}, "footnotes": [...], "tags": optional
 ["Preliminary", "Non-exhaustive"], "notes": detail that did not fit}.
Every text element that holds numbers carries "prov": [fact ids] (bullets as {"text","prov"}; cells as
{"lead","bullets":[{"text","prov"}],"prov"}). Use ONLY fact values; if a number is missing write [GAP: what].
Titles: one sentence stating the takeaway, with a verb, <= 18 words. Every data slide has a source.
ONE chart = ONE measure in ONE unit. Line charts need >= 4 periods. Sort bars largest first."""


def _call(client, model, system, user, task_id, max_tokens=12000) -> dict:
    user += f"\n<run id=\"{uuid.uuid4().hex[:8]}\"/>"
    r = client.chat(model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    max_tokens=max_tokens, temperature=0.2, task_id=task_id, role="worker",
                    extra={"response_format": {"type": "json_object"}})
    txt = r["choices"][0]["message"].get("content") or "{}"
    return json.loads(txt[txt.find("{"): txt.rfind("}") + 1])


def facts_from_brief(text: str) -> list[dict]:
    """Every '- <what> = <value> (...)' line under a Facts heading becomes F1..Fn; other numeric lines too."""
    facts = []
    part = re.split(r"(?im)^\W*facts\W*$", text)
    body = part[1] if len(part) > 1 else text
    for line in body.splitlines():
        line = line.strip().lstrip("-*• ").strip()
        if not line or not re.search(r"\d", line):
            continue
        facts.append({"id": f"F{len(facts) + 1}", "text": line})
    return facts


def _fact_values(facts, ids, ignore) -> list[float]:
    vals = []
    for f in facts:
        if f["id"] in ids:
            for _, v, u in _numbers(f["text"], ignore):
                vals += _candidates(abs(v), u)
    return vals


def number_checks(slides, facts, rules) -> dict[int, list[str]]:
    """Pre-render: every number in a text element must be one of the cited facts' values (P13 honesty)."""
    ignore = re.compile(rules["pptx"]["provenance"]["ignore_numbers"])
    out: dict[int, list[str]] = {}

    def check(n, where, text, prov):
        if not text or "[GAP" in text:
            return
        nums = _numbers(text, ignore)
        if not nums:
            return
        if not prov:
            out.setdefault(n, []).append(f"{where}: numbers {', '.join(r for r, _, _ in nums)} have no fact id in prov")
            return
        vals = _fact_values(facts, set(prov), ignore)
        bad = [raw for raw, v, u in nums if not any(abs(c - fv) <= 0.006 * max(abs(fv), 1e-9) or abs(c - fv) < 0.051
                                                     for c in _candidates(abs(v), u) for fv in vals)]
        if bad:
            out.setdefault(n, []).append(f"{where}: {', '.join(bad)} not found in cited facts {', '.join(prov)}; "
                                         "use the fact's value or a [GAP] marker")
    def walk(n, node, where, prov=None):
        if isinstance(node, dict):
            p = node.get("prov") or prov
            for k in ("text", "lead", "label"):
                if isinstance(node.get(k), str):
                    check(n, where, node[k], p)
            for k, v in node.items():
                if k in ("chart", "charts", "table", "prov", "text", "lead", "label", "source", "footnotes", "notes",
                         "title", "title_prov", "exhibit", "heat"):
                    continue
                walk(n, v, where + "." + k, p)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                if isinstance(v, str):
                    check(n, where, v, prov)
                else:
                    walk(n, v, f"{where}[{i}]", prov)
    for n, s in enumerate(slides, 1):
        check(n, "title", s.get("title"), s.get("title_prov"))
        walk(n, s, "slide")
    return out


UNIT_HINT = re.compile(r"\(([^)]*)\)|%|\bLQ\b|thousand|million|billion|\$", re.I)


def chart_checks(slides) -> dict[int, list[str]]:
    """Pre-render chart sanity: one measure in one unit per chart, enough points for the chart kind."""
    out: dict[int, list[str]] = {}
    for n, s in enumerate(slides, 1):
        charts = [c for c in ([s.get("chart")] + list(s.get("charts") or [])) if isinstance(c, dict)]
        for c in charts:
            cats = [str(x) for x in c.get("categories") or []]
            series = [se for se in c.get("series") or [] if isinstance(se, dict)]
            kind = c.get("kind", "column")
            vals = [abs(float(v)) for se in series for v in se.get("values") or [] if isinstance(v, (int, float)) and v]
            hints = {m.group(0).lower() for cat in cats for m in UNIT_HINT.finditer(cat)}
            if len(hints) > 1 or (len(series) == 1 and vals and max(vals) / max(min(vals), 1e-9) > 40 and kind != "waterfall"
                                  and len(cats) <= 4):
                out.setdefault(n, []).append("chart mixes different measures or units in one series "
                                             f"({', '.join(cats[:4])}); show one measure in one unit, or use kpi_tiles "
                                             "for unrelated headline numbers")
            if kind == "line" and len(cats) < 4:
                out.setdefault(n, []).append(f"line chart has {len(cats)} points; use a column chart for fewer than 4 periods")
            if kind in ("stacked_column", "stacked_bar", "pct_stacked_column") and len(cats) < 2:
                out.setdefault(n, []).append("stacked chart with one category is unreadable; use a bar chart of the parts, "
                                             "sorted, with the key part highlighted")
            if len(series) == 1 and len(cats) < 2 and kind != "waterfall":
                out.setdefault(n, []).append("chart with a single bar shows nothing; use kpi_tiles or add comparators")
    return out


def _content_failures(res: dict) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for f in res["failures"]:
        if f["status"] == "fail" and f["fix_scope"] == "content" and f.get("slide"):
            out.setdefault(int(f["slide"]), []).append(f"[{f['rule']}] {f['message']}")
    return out


def build(brief: str, out_dir: str, slides_wanted: int = 12, client=None, rules=None, task_id="forge") -> dict:
    from meshpool_harness.core.client import LiveClient
    client = client or LiveClient("forge_build")
    rules = rules or load_rules()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    facts = facts_from_brief(brief)
    fact_txt = "\n".join(f"{f['id']}: {f['text']}" for f in facts)
    calls = 0

    # ---- stage 2: storyline (ghost deck)
    story = _call(client, STORY_MODEL, f"""You are an engagement manager at a top strategy firm. Plan the storyline
before any slide exists. Return JSON: {{"governing_thought": str, "structure": "SCR",
"slides": [{{"node": "1.1", "section": str, "section_no": "1", "title": str, "archetype": str,
"evidence": [fact ids], "needs": "what the slide must show"}}]}}.
Exactly {slides_wanted} slides: slide 1 is the cover (node "0"); then the argument in 3-4 sections; end with what to
do next. Use the archetypes below; prefer challenge_matrix, exhibit_argument and table_focus for dense evidence.
Plan ONLY slides whose evidence is in the facts: a table needs >= 3 comparable rows of facts, a chart >= 3 data
points in one unit. If the facts are thin on a point, merge it into another slide instead of planning a gap. Read top to bottom, the titles alone must tell the whole story; no two titles
make the same point. Titles: one sentence with a verb, <= 15 words, stating the takeaway with a number where a fact
supports it. Choose varied archetypes that prove each title.
{ARCH_HELP}""", f"Brief:\n{brief[:14000]}\n\nFacts (cite by id):\n{fact_txt}", task_id + "-story", 6000)
    calls += 1
    (out / "storyline.json").write_text(json.dumps(story, indent=1))
    nodes = [{"id": s.get("node"), "title": s.get("title")} for s in story.get("slides", [])]

    # ---- stage 3: content, one call per slide (in parallel), each with only its own facts
    from concurrent.futures import ThreadPoolExecutor
    plan = [x for x in story.get("slides", []) if isinstance(x, dict)]

    def one(x):
        ev = set(x.get("evidence") or [])
        rel = [f for f in facts if f["id"] in ev] or facts
        try:
            return _call(client, MODEL, f"""You write ONE consulting slide as JSON for a renderer that owns all
design. Fill the archetype's fields completely and densely, the way a top strategy firm's slide carries evidence.
{SLIDE_SHAPE}
{ARCH_HELP}""", "Storyline (whole deck, for context):\n" + "\n".join(f"{y.get('node')}: {y.get('title')}" for y in plan)
                + f"\n\nThis slide: {json.dumps(x)}\n\nFacts (cite by id):\n"
                + "\n".join(f"{f['id']}: {f['text']}" for f in rel), f"{task_id}-c{x.get('node')}", 6000)
        except Exception as e:
            return {"archetype": "cover", "node": x.get("node"), "title": x.get("title", ""), "notes": f"content failed: {e}"}
    with ThreadPoolExecutor(6) as ex:
        content = {"slides": list(ex.map(one, plan))}
    calls += len(plan)
    slides = [s for s in content.get("slides", []) if isinstance(s, dict)]
    rounds = []
    pptx = out / "deck.pptx"
    for rnd in range(rules["repair"]["max_rounds"] + 1):
        r = render(slides, str(pptx))
        prov = json.loads(Path(r["provenance"]).read_text())
        res = validate(str(pptx), rules, brief={"slide_count": slides_wanted}, provenance=prov,
                       storyline={"nodes": nodes}, render=True)
        fails = _content_failures(res)
        for n, msgs in number_checks(slides, facts, rules).items():
            fails.setdefault(n, []).extend(f"[P13] {m}" for m in msgs)
        for n, msgs in chart_checks(slides).items():
            fails.setdefault(n, []).extend(f"[chart] {m}" for m in msgs)
        for e in r["errors"]:
            m = re.match(r"slide (\d+): rows carried", e)
            if m and int(m.group(1)) - 1 < len(slides):
                fails.setdefault(int(m.group(1)), []).append(
                    "[P5] the content is longer than one slide (rows spilled onto a continuation slide); cut every "
                    "bullet to <= 16 words and keep at most 2 bullets per cell")
        from engines.forge.render import normalize, schema_errors
        for i, sl_ in enumerate(slides, 1):
            for e in schema_errors(normalize(sl_)) if sl_.get("archetype") not in ("cover", "section_divider") else []:
                fails.setdefault(i, []).append("[schema] " + e)
        rounds.append({"round": rnd, "rules": res["rules"], "content_failures": sum(len(v) for v in fails.values())})
        if not fails or rnd == rules["repair"]["max_rounds"]:
            break
        for n, msgs in sorted(fails.items()):
            if n - 1 >= len(slides):
                continue
            s = slides[n - 1]
            ev = set(next((x.get("evidence", []) for x in story.get("slides", []) if x.get("node") == s.get("node")), []))
            rel = [f for f in facts if f["id"] in ev] or facts
            try:
                fixed = _call(client, MODEL, f"""Fix this one consulting slide. Change only words, numbers, archetype
choice or the number of items; never layout. Keep node and keep the point of the title. Return the full slide JSON.
{SLIDE_SHAPE}
{ARCH_HELP}""", "Failures to fix:\n- " + "\n- ".join(msgs) + f"\n\nSlide:\n{json.dumps(s)}\n\nFacts:\n" +
                    "\n".join(f"{f['id']}: {f['text']}" for f in rel), f"{task_id}-fix{n}-r{rnd}", 4000)
                calls += 1
                if fixed.get("archetype") in ARCHETYPES:
                    slides[n - 1] = fixed
            except Exception:
                pass
    # freeze what is left into the notes: nothing passes silently
    left = fails
    if left and rules["repair"]["flag_in_file"]:
        for n, msgs in left.items():
            if n - 1 < len(slides):
                slides[n - 1]["notes"] = (slides[n - 1].get("notes", "") + "\n" + "\n".join("[QA] " + m for m in msgs)).strip()
        r = render(slides, str(pptx))
        prov = json.loads(Path(r["provenance"]).read_text())
        res = validate(str(pptx), rules, brief={"slide_count": slides_wanted}, provenance=prov,
                       storyline={"nodes": nodes}, render=True)
    (out / "slides.json").write_text(json.dumps(slides, indent=1))
    (out / "facts.json").write_text(json.dumps(facts, indent=1))
    res.pop("measurement", None)
    (out / "qa.json").write_text(json.dumps(dict(res, rounds=rounds, unresolved=left), indent=1, default=str))
    return {"pptx": str(pptx), "calls": calls, "rounds": rounds, "rules": res["rules"],
            "unresolved": {k: v for k, v in left.items()}}


if __name__ == "__main__":
    import argparse
    import warnings
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser()
    ap.add_argument("brief")
    ap.add_argument("out_dir")
    ap.add_argument("--slides", type=int, default=12)
    a = ap.parse_args()
    r = build(Path(a.brief).read_text(), a.out_dir, a.slides)
    print(json.dumps(r, indent=1, default=str))
