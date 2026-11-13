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
from engines.forge.density import DEFAULT as DEFAULT_DENSITY, FILL, STORY, WRITE, normalize as _density  # noqa: E402
from engines.forge.render import ARCHETYPES, render  # noqa: E402
from engines.forge.validate_pptx import _candidates, _numbers, validate  # noqa: E402

MODEL = os.environ.get("FORGE_MODEL") or os.environ.get("MH_MODEL", "deepseek-v4.1-flash")
STORY_MODEL = os.environ.get("FORGE_STORY_MODEL", MODEL)

_ARCH = """Archetypes (pick the one that best PROVES the title; never more than 3 of the same in a row). The
renderer applies a strict consulting design; you only fill fields.
- cover: title, subtitle, "date" (e.g. "Board meeting, March 2026").
- grid: a slide assembled from PARTS on a grid when one exhibit is not enough (or too much). "layout": full, halves,
  halves_v, thirds, quarters, main_side (2/3 + 1/3), side_main, main_stack (left half + two stacked quarters right),
  stack_main; "look": editorial | boxed | minimal | bold; "parts": one per region, each {"type", "heading", ...}:
  chart {"chart": as above}; table {"table": as above}; stats {"items": [{"value": "34%", "label", "delta"}]};
  text {"lead", "bullets"}; callout {"text" or "bullets"}; process {"steps": [{"title", "text"}]};
  timeline {"milestones": [{"date", "label"}]}; matrix2x2 {"x_axis", "y_axis", "quadrants": 4 x {"title", "items"}};
  harvey {"criteria": [...], "rows": [{"label", "scores": [0-4 per criterion]}]}; quote {"text", "attribution"}.
  Parts are ideas, not rules: use one only where it proves the title; a part can fill a quarter, a half or the slide.
- closing: the last slide when the deck ends on action. "title" (the call to action, with a verb), "steps": 3-5 of
  {"action": imperative, <= 8 words, "detail": what it involves and why, <= 25 words, with its numbers,
  "milestones": 1-3 short lines (first milestones, decision gates, the measure of success), "owner": who,
  "timing": when, "prov": [fact ids]}.
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
  "size": 12 (10 if more than 14 rows)}; "takeaway": {"text": one bold line}; "bullets": 2-4 lines interpreting
  the table (required when it has fewer than 12 rows: a short table sits beside them).
- bar_takeaway / column_trend / line_trend / waterfall_bridge: "subtitle" (measure, unit, period), one "chart"
  {kind, categories, series[{name, values}], unit, decimals, highlight_categories} on the left 2/3, a "takeaway"
  {"text"} (bold lead) and "bullets" [2-3 {"text","prov"}] interpreting it on the right 1/3.
{fill}
Text markup: **bold phrase**; a bold phrase containing a number is shown in the emphasis colour; ^1 is a
superscript footnote marker. Numbers in bullets are emphasised automatically. Footnotes go in "footnotes"."""


def arch_help(density: str = "read") -> str:
    """The archetype guide with the density mode's FILL targets (density.py)."""
    return _ARCH.replace("{fill}", FILL[density])


ARCH_HELP = arch_help("read")                     # the dense send-ahead guide (kept for callers that import it)

SLIDE_SHAPE = """Slide JSON: {"id","node" (from the storyline),"archetype","title","title_prov":[fact ids],
 ...archetype fields..., "source": {"text": original sources, "prov": [ids]}, "footnotes": [...], "tags": optional
 ["Preliminary", "Non-exhaustive"], "notes": detail that did not fit}.
Every text element that holds numbers carries "prov": [fact ids] (bullets as {"text","prov"}; cells as
{"lead","bullets":[{"text","prov"}],"prov"}). Use ONLY fact values; if a number is missing write [GAP: what].
Titles: one sentence stating the takeaway, with a verb, <= 18 words. Every data slide has a source.
ONE chart = ONE measure in ONE unit. Line charts need >= 4 periods. Sort bars largest first."""


_OPEN_RE = re.compile(r"(?i)\b(title|cover|opening|intro(?:duction|ductory)?)\s+(slide|page)\b|\bagenda\b|"
                      r"\bstart (?:with|on) (?:a |the )?(?:title|cover)\b")
_CLOSE_RE = re.compile(r"(?i)\b(closing|ending|end|final|last|conclusion|concluding|summary|thank[- ]you|q&a)\s+"
                       r"(slide|page)\b|\bnext steps\b|\b(?:close|end|finish|conclude|wrap up) (?:with|on)\b|"
                       r"\bcall to action\b")


def bookends(brief: str) -> dict:
    """Opening and closing slides the brief asks for. When asked for, they must be in the deck (P16)."""
    return {"opening": bool(_OPEN_RE.search(brief or "")), "closing": bool(_CLOSE_RE.search(brief or ""))}


def _enforce_bookends(plan: list, ends: dict) -> list:
    plan = list(plan)
    if ends["opening"] and (not plan or plan[0].get("archetype") != "cover"):
        plan.insert(0, {"node": "0", "section": "", "title": plan[0].get("title", "") if plan else "", "archetype": "cover",
                        "evidence": [], "needs": "cover: deck title, subtitle, date"})
    if ends["closing"] and (not plan or plan[-1].get("archetype") not in ("closing", "end")):
        last = plan[-1] if plan else {}
        if re.search(r"(?i)next|act|recommend|priorit|plan|should|must|now", last.get("title", "")):
            plan[-1] = dict(last, archetype="closing")            # the last slide already is the call to action
        else:
            plan.append({"node": "9.9", "section": "Next steps", "title": "Next steps", "archetype": "closing",
                         "evidence": [], "needs": "closing: the next steps the argument implies"})
    return plan


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


def _words(v) -> int:
    if isinstance(v, dict):
        return sum(_words(x) for x in v.values() if isinstance(x, (str, list, dict)))
    if isinstance(v, list):
        return sum(_words(x) for x in v)
    return len(str(v).split()) if isinstance(v, str) else 0


EXHIBIT_PARTS = ("chart", "table", "stats", "process", "timeline", "matrix2x2", "harvey")


def _lists(s) -> list[list]:
    """Every bullet list on a slide (slide bullets, block bullets, cell bullets, part bullets, callout bullets)."""
    out = [s.get("bullets") or []]
    out += [b.get("bullets") or [] for b in s.get("blocks") or [] if isinstance(b, dict)]
    out += [c.get("bullets") or [] for r in s.get("rows") or [] if isinstance(r, dict)
            for c in r.get("cells") or [] if isinstance(c, dict)]
    out += [p.get("bullets") or [] for p in s.get("parts") or [] if isinstance(p, dict)]
    if isinstance(s.get("callout"), dict):
        out.append(s["callout"].get("bullets") or [])
    return [x for x in out if isinstance(x, list)]


def fill_checks(slides, density: str = "read") -> dict[int, list[str]]:
    """Pre-render: does the slide carry what its density mode needs? In 'read' (dense send-ahead) a thin slide is
    sent back for more evidence (or a merge), never stretched by the renderer to look full. 'balanced' asks for less;
    'present' never asks for MORE text: it asks for an exhibit and a takeaway, and sends back slides that are too
    busy to present (the detail belongs in the speaker notes)."""
    density = _density(density) or "read"
    out: dict[int, list[str]] = {}
    for n, s in enumerate(slides, 1):
        a, msgs = s.get("archetype"), []
        if density == "present":
            msgs += _present_checks(s, a)
        else:
            msgs += _fill_targets(s, a, density)
        c = s.get("chart") or {}
        if c.get("kind") in ("bar", "column") and len(c.get("categories") or []) == 2 and len(c.get("series") or []) == 1:
            msgs.append("a two-bar chart is a thin exhibit: show the breakdown behind it (3+ categories), the same "
                        "split over time, or a comparison with a benchmark")
        if msgs:
            out[n] = msgs
    return out


def _present_checks(s, a) -> list[str]:
    """Present mode: minimums are an exhibit and a takeaway; the rest is a cap (detail goes to the notes)."""
    msgs = []
    if a in ("bar_takeaway", "column_trend", "line_trend", "waterfall_bridge", "stacked_composition"):
        if not (s.get("takeaway") or {}).get("text") and not s.get("bullets"):
            msgs.append("give the chart a takeaway: one bold line (<= 12 words) that says what it shows")
    elif a in ("table_focus", "table_highlight"):
        if not (s.get("takeaway") or {}).get("text"):
            msgs.append("give the table a takeaway: one bold line (<= 12 words) that says what it shows")
    elif a == "exhibit_argument":
        if not s.get("blocks"):
            msgs.append("the argument is missing: give 1-2 short points (an intro line, one bullet) beside the exhibit")
    elif a == "grid":
        parts = [p for p in s.get("parts") or [] if isinstance(p, dict)]
        if parts and not any(p.get("type") in EXHIBIT_PARTS + ("quote",) for p in parts) and len(parts) < 3:
            msgs.append("a presented slide needs one exhibit: show the point as a chart, 2-4 big numbers (stats), a "
                        "process, a timeline or a 2x2, with at most one short text part beside it")
    elif a == "challenge_matrix":
        if any(not (c or {}).get("lead") for r in s.get("rows") or [] if isinstance(r, dict)
               for c in r.get("cells") or [] if isinstance(c, dict)):
            msgs.append("every cell needs a 'lead': one short line (<= 10 words); evidence goes in 'notes'")
    elif a == "closing":
        if any(not st.get("action") for st in s.get("steps") or [] if isinstance(st, dict)):
            msgs.append("every step needs an 'action' (imperative, <= 8 words)")
    busy = [len(x) for x in _lists(s) if len(x) > 3]
    long_ = [b for x in _lists(s) for b in x if _words(b) > 16]
    if busy or long_ or len(s.get("rows") or []) > 4 or len(s.get("parts") or []) > 4:
        msgs.append("too busy to present: at most 3 bullets per list, each a headline of <= 12 words, at most 4 rows "
                    "or parts; move the full sentences and evidence to 'notes' (the speaker notes)")
    return msgs


def _fill_targets(s, a, density) -> list[str]:
    """Read: today's dense targets. Balanced: the same shapes with lighter minimums."""
    light = density == "balanced"
    msgs = []
    if a == "challenge_matrix":
        need = 1 if light else 2
        thin = [i + 1 for i, r in enumerate(s.get("rows") or [])
                for c in (r.get("cells") or []) if isinstance(c, dict) and len(c.get("bullets") or []) < need]
        if thin:
            msgs.append(f"rows {sorted(set(thin))}: each cell needs a lead and "
                        + ("1-2 evidence bullets; add the evidence, or drop the row" if light else
                           "2-3 evidence bullets so the matrix fills the slide; add the evidence, or drop the row"))
    elif a in ("table_focus", "table_highlight"):
        rows = len((s.get("table") or {}).get("rows") or [])
        if rows < 12 and len(s.get("bullets") or []) < (1 if light else 2):
            msgs.append(f"the table has {rows} rows: add 'bullets' ({'1-3' if light else '2-4'} lines interpreting it, "
                        "with numbers); they sit beside a table this short. Or show it as a chart in exhibit_argument")
    elif a in ("bar_takeaway", "column_trend", "line_trend", "waterfall_bridge", "stacked_composition"):
        if len(s.get("bullets") or []) < 2 or _words(s.get("bullets")) < (20 if light else 35):
            msgs.append("the takeaway column is thin: give " + ("2-3 bullets (10-18 words each" if light else
                        "3 bullets (15-25 words each") + ", with the numbers) that interpret the chart: the driver, "
                        "the comparison, the implication")
    if a == "exhibit_argument":
        if len(s.get("blocks") or []) < 2 or _words(s.get("blocks")) < (35 if light else 60):
            msgs.append("the argument is thin: give 2-3 blocks, each an intro sentence and "
                        + ("1-2 bullets" if light else "2-3 bullets") + " with numbers, so the right half is full")
    elif a == "closing":
        if any(not st.get("detail") or (not light and not st.get("milestones"))
               for st in s.get("steps") or [] if isinstance(st, dict)):
            msgs.append("every step needs a 'detail' (what it involves, with numbers)"
                        + ("" if light else " and 1-3 'milestones'"))
    if light:
        busy = [len(x) for x in _lists(s) if len(x) > 5]
        if busy or _words({k: v for k, v in s.items() if k in ("bullets", "blocks", "rows", "parts", "callout")}) > 180:
            msgs.append("too dense for a balanced slide: at most 4-5 bullets per list and about 60-110 words on the "
                        "slide; move secondary detail to 'notes'")
    return msgs


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


def build(brief: str, out_dir: str, slides_wanted: int = 12, client=None, rules=None, task_id="forge",
          theme=None, density: str | None = None, fixed_pages: list | None = None,
          extra_facts: list | None = None) -> dict:
    """density: "present" (presenter-led), "balanced" (default) or "read" (dense send-ahead); see density.py. It
    steers the storyline and content prompts, the fill checks, the renderer's type scale and the P10/P20 limits."""
    density = _density(density) or DEFAULT_DENSITY
    help_ = arch_help(density)
    from meshpool_harness.core.client import LiveClient
    client = client or LiveClient("forge_build")
    rules = rules or load_rules()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    facts = facts_from_brief(brief) + [dict(f) for f in extra_facts or []]
    fixed = [dict(p, fixed=True) for p in fixed_pages or []]
    narrative_n = max(3, slides_wanted - len(fixed))
    fixed_txt = ("\nThese data pages are already built from the model and will be inserted as a section before the "
                 "closing slide; do NOT plan slides that repeat them, build the argument around them and cite their "
                 "facts (W ids) where you use their numbers:\n" + "\n".join(f"- {p['title']}" for p in fixed)
                 if fixed else "")
    ends = bookends(brief)
    opening = ('slide 1 MUST be the cover (node "0", archetype cover; the brief asks for an opening slide); '
               if ends["opening"] else 'slide 1 is the cover (node "0"); ')
    closing = ("the LAST slide MUST be archetype closing with the next steps (the brief asks for a closing slide)."
               if ends["closing"] else "end with what to do next.")
    fact_txt = "\n".join(f"{f['id']}: {f['text']}" for f in facts)
    calls = 0

    # ---- stage 2: storyline (ghost deck)
    story = _call(client, STORY_MODEL, f"""You are an engagement manager at a top strategy firm. Plan the storyline
before any slide exists. Return JSON: {{"governing_thought": str, "structure": "SCR",
"slides": [{{"node": "1.1", "section": str, "section_no": "1", "title": str, "archetype": str,
"evidence": [fact ids], "needs": "what the slide must show"}}]}}.
Exactly {narrative_n} slides: {opening}then the argument in 3-4 sections; {closing} Use the archetypes below.{fixed_txt}
{STORY[density]}
Each slide must have the evidence its point needs. Plan ONLY slides whose evidence
is in the facts: a table needs >= 3 comparable rows of facts, a chart >= 3 data
points in one unit. If the facts are thin on a point, merge it into another slide instead of planning a gap. Read top to bottom, the titles alone must tell the whole story; no two titles
make the same point. Titles: one sentence with a verb, <= 15 words, stating the takeaway with a number where a fact
supports it. Choose varied archetypes that prove each title.
{help_}""", f"Brief:\n{brief[:14000]}\n\nFacts (cite by id):\n{fact_txt}", task_id + "-story", 6000)
    calls += 1
    (out / "storyline.json").write_text(json.dumps(story, indent=1))
    nodes = [{"id": s.get("node"), "title": s.get("title")} for s in story.get("slides", [])]

    # ---- stage 3: content, one call per slide (in parallel), each with only its own facts
    from concurrent.futures import ThreadPoolExecutor
    plan = _enforce_bookends([x for x in story.get("slides", []) if isinstance(x, dict)], ends)
    nodes = [{"id": x.get("node"), "title": x.get("title")} for x in plan]

    def one(x):
        ev = set(x.get("evidence") or [])
        rel = [f for f in facts if f["id"] in ev] or facts
        try:
            return _call(client, MODEL, f"""You write ONE consulting slide as JSON for a renderer that owns all
design. {WRITE[density]}
{SLIDE_SHAPE}
{help_}""", "Storyline (whole deck, for context):\n" + "\n".join(f"{y.get('node')}: {y.get('title')}" for y in plan)
                + f"\n\nThis slide: {json.dumps(x)}\n\nFacts (cite by id):\n"
                + "\n".join(f"{f['id']}: {f['text']}" for f in rel), f"{task_id}-c{x.get('node')}", 6000)
        except Exception as e:
            return {"archetype": "cover", "node": x.get("node"), "title": x.get("title", ""), "notes": f"content failed: {e}"}
    with ThreadPoolExecutor(6) as ex:
        content = {"slides": list(ex.map(one, plan))}
    calls += len(plan)
    slides = [s for s in content.get("slides", []) if isinstance(s, dict)]
    if fixed:                                                  # model pages go in before the closing slide
        at = len(slides) - 1 if slides and slides[-1].get("archetype") in ("closing", "next_steps", "end") \
            else len(slides)
        slides[at:at] = fixed
        plan[at:at] = [{"node": p.get("node"), "title": p.get("title"), "archetype": p.get("archetype")} for p in fixed]
        nodes = [{"id": x.get("node"), "title": x.get("title")} for x in plan]
    is_fixed = lambda i: bool(slides[i].get("fixed"))  # noqa: E731
    from engines.forge import clarity                          # a second, smaller model reads every line once
    clear = [clarity.review(slides, client, main_model=MODEL, task_id=task_id + "-clarity")]
    calls += 1
    rounds = []
    pptx = out / "deck.pptx"
    for rnd in range(rules["repair"]["max_rounds"] + 1):
        r = render(slides, str(pptx), carry=False, theme=theme, density=density)          # the brief's slide count is a hard limit
        prov = json.loads(Path(r["provenance"]).read_text())
        res = validate(str(pptx), rules, brief={"slide_count": len(slides) if fixed else slides_wanted, **ends},
                       provenance=prov, storyline={"nodes": nodes}, render=True, density=density)
        fails = _content_failures(res)
        for n, msgs in number_checks(slides, facts, rules).items():
            fails.setdefault(n, []).extend(f"[P13] {m}" for m in msgs)
        for n, msgs in fill_checks(slides, density).items():
            fails.setdefault(n, []).extend(f"[fill] {m}" for m in msgs)
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
        for i in range(len(slides)):                         # computed pages are not rewritten by a model
            if is_fixed(i):
                fails.pop(i + 1, None)
        rounds.append({"round": rnd, "rules": res["rules"], "content_failures": sum(len(v) for v in fails.values())})
        if not fails or rnd == rules["repair"]["max_rounds"]:
            break
        if len(rounds) >= 3 and rounds[-1]["content_failures"] >= rounds[-3]["content_failures"]:
            break                                            # two rounds without progress: stop paying for repairs
        repaired = set()

        def fix_one(item):                                     # one call per failing slide, all in parallel
            n, msgs = item
            s = slides[n - 1]
            ev = set(next((x.get("evidence", []) for x in story.get("slides", []) if x.get("node") == s.get("node")), []))
            rel = [f for f in facts if f["id"] in ev] or facts
            if any(m.startswith(("[P20]", "[fill]", "[schema]")) for m in msgs):
                rel = facts                                  # a thin slide needs the whole evidence base to fill
            try:
                return n, _call(client, MODEL, f"""Fix this one consulting slide. Change only words, numbers, archetype
choice or the number of items; never layout. Keep node and keep the point of the title. Return the full slide JSON.
{WRITE[density]}
{SLIDE_SHAPE}
{help_}""", "Failures to fix:\n- " + "\n- ".join(msgs) + f"\n\nSlide:\n{json.dumps(s)}\n\nFacts:\n" +
                    "\n".join(f"{f['id']}: {f['text']}" for f in rel), f"{task_id}-fix{n}-r{rnd}", 6000)
            except Exception:
                return n, None
        todo = [(n, m) for n, m in sorted(fails.items()) if n - 1 < len(slides)]
        with ThreadPoolExecutor(min(8, max(1, len(todo)))) as ex:
            for n, fixed in ex.map(fix_one, todo):
                calls += 1
                if isinstance(fixed, dict) and fixed.get("archetype") in ARCHETYPES:
                    slides[n - 1] = fixed
                    repaired.add(n - 1)
        if repaired:                                            # repaired slides get the same clarity read
            clear.append(clarity.review(slides, client, only=repaired, main_model=MODEL,
                                        task_id=f"{task_id}-clarity-r{rnd}"))
            calls += 1
    # freeze what is left into the notes: nothing passes silently
    left = fails
    if left and rules["repair"]["flag_in_file"]:
        for n, msgs in left.items():
            if n - 1 < len(slides):
                slides[n - 1]["notes"] = (slides[n - 1].get("notes", "") + "\n" + "\n".join("[QA] " + m for m in msgs)).strip()
        r = render(slides, str(pptx), carry=False, theme=theme, density=density)          # the brief's slide count is a hard limit
        prov = json.loads(Path(r["provenance"]).read_text())
        res = validate(str(pptx), rules, brief={"slide_count": slides_wanted, **ends}, provenance=prov,
                       storyline={"nodes": nodes}, render=True, density=density)
    (out / "slides.json").write_text(json.dumps(slides, indent=1))
    (out / "facts.json").write_text(json.dumps(facts, indent=1))
    res.pop("measurement", None)
    (out / "qa.json").write_text(json.dumps(dict(res, rounds=rounds, unresolved=left), indent=1, default=str))
    (out / "clarity.json").write_text(json.dumps(clear, indent=1, default=str))
    return {"pptx": str(pptx), "calls": calls, "rounds": rounds, "rules": res["rules"], "density": density,
            "unresolved": {k: v for k, v in left.items()}}


if __name__ == "__main__":
    import argparse
    import warnings
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser()
    ap.add_argument("brief")
    ap.add_argument("out_dir")
    ap.add_argument("--slides", type=int, default=12)
    ap.add_argument("--density", choices=["present", "balanced", "read"], default=None)
    a = ap.parse_args()
    r = build(Path(a.brief).read_text(), a.out_dir, a.slides, density=a.density)
    print(json.dumps(r, indent=1, default=str))
