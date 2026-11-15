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
from engines.forge.common import SCALE as _SCALE, numbers as _numbers  # noqa: E402
from engines.forge.validate_pptx import validate  # noqa: E402

MODEL = os.environ.get("FORGE_MODEL") or os.environ.get("MH_MODEL", "deepseek-v4.1-flash")
STORY_MODEL = os.environ.get("FORGE_STORY_MODEL", MODEL)

_ARCH = """Archetypes (pick the one that best PROVES the title; never more than 3 of the same in a row). The
renderer applies a strict consulting design; you only fill fields.
- cover: title, subtitle, "date" (the occasion and today's month and year, e.g. "Board meeting, " + the month).
- grid: a slide assembled from PARTS on a grid when one exhibit is not enough (or too much). "layout": full, halves,
  halves_v, thirds, quarters, main_side (2/3 + 1/3), side_main, main_stack (left half + two stacked quarters right),
  stack_main; "look": editorial | boxed | minimal | bold; "parts": one per region, each {"type", "heading", ...}:
  chart {"chart": as above}; table {"table": as above}; stats {"items": [{"value": "34%", "label", "delta"}]};
  text {"lead", "bullets"}; callout {"text" or "bullets"}; process {"steps": [{"title", "text"}]};
  timeline {"milestones": [{"date", "label"}]}; matrix2x2 {"x_axis", "y_axis", "quadrants": 4 x {"title", "items"}};
  harvey {"criteria": [...], "rows": [{"label", "scores": [0-4 per criterion]}]}; quote {"text", "attribution"}.
  Parts are ideas, not rules: use one only where it proves the title; a part can fill a quarter, a half or the slide.
- executive_summary: the answer first, right after the cover. "title": the governing thought (the recommendation or
  answer, with a verb); "points": 3-5 of {"lead": the claim one section of the deck proves (<= 12 words), "bullets":
  1-2 evidence lines with their numbers, "prov"}; "decision": optional one line, the decision being requested
  today, written "Decision today: <approve / choose / fund what, by when>"; it must NOT restate the title (the title
  is the answer, the decision line is the ask) - leave it out when there is no decision to take.
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
- benchmark: companies (or deals) compared on 1-3 metrics: {"benchmark": {"companies": [...], "panels": [{"title":
  "EV / EBITDA (x)", "unit": "x" | "%", "values": [one per company, null = not meaningful], "median", "p25",
  "p75"}], "highlight": [index of the subject company], "logos": optional {company: image path}}, "takeaway",
  "bullets"}. Bars in one order across panels, a Median bar and median line: use it instead of a multiples table.
- football_field: valuation ranges by method: "chart" {"categories": methods, "series": [{"name": "Low", "values"},
  {"name": "High", "values"}], "unit": "$", "decimals": 2, "marker": {"label": "Current price", "value"}}.
- Sensitivity grids and other value grids: table_focus with "table": {..., "heat_grid": {"center": base value,
  "base": [row, col]}} shades every cell by its distance from the base case.
Choose the exhibit by the comparison (Zelazny): ranking -> bar or benchmark; change over time -> column or line;
parts of a whole -> stacked or waterfall; a bridge from one total to another -> waterfall; ranges -> football field;
two-way sensitivity -> heat map; qualitative scores -> grid with a harvey part. A table only when the reader needs
to look up exact values across many columns.
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
Titles: one sentence stating the takeaway, with a verb, <= 15 words, no full stop. Every data slide has a source.
ONE chart = ONE measure in ONE unit. Line charts need >= 4 periods. Sort bars largest first.

A number the facts do not state but that follows from them by arithmetic (an offer price from the unaffected price
and the premium, a change, a ratio, a share) goes in "calcs": [{"id": "C1", "value": "$65.37", "formula":
"48.42 * (1 + 0.35)", "from": ["F33"]}] and is cited by its C id in prov. The formula uses only numbers written in the
"from" facts (plus 1, 2, 4, 12, 100, 1000), + - * / and parentheses; the engine recomputes every calc and rejects one
that does not match. Never estimate: no calc, no number."""

# The house style every deck gets without being asked: how a top strategy firm's slide reads.
CONSULTING = """House style (always, whatever the brief says about look):
- Answer first. The titles, read in order, tell the whole story; each title is the slide's takeaway in plain words,
  not a topic label ("North drives 38% of growth", never "Regional overview").
- One message per slide. If a title needs "and" to join two ideas, it is two slides.
- Show, then tell: every slide has an exhibit (chart, table, numbered steps, icon rows, big numbers, 2x2). A slide
  of bullets only is a failed slide; pick the grid archetype with parts instead.
- Every data slide states its "so what" on the page: a takeaway line, a stat or a callout that says what the
  evidence means for the reader's decision.
- Emphasise one thing per exhibit (highlight_categories, the focus row) and leave the rest muted. Label values
  directly; no legends where labels fit.
- Short text: phrases inside exhibits, bullets of one line where possible. Sentence case everywhere, no capitals for
  emphasis, no em dashes, no jargon, no filler ("leverage", "robust", "holistic", "synergies").
- Steps, options, drivers and pillars get a one-word "icon" meaning so the renderer can draw a line icon.
- Never invent a number. Missing evidence is [GAP: what], shown as N/A."""

# The exhibit forms a senior partner mixes; the storyline names one per slide and the deck must vary them.
SIDE_PANEL = {"bar_takeaway", "column_trend", "line_trend", "stacked_composition", "waterfall_bridge", "table_focus",
              "table_highlight", "benchmark"}
VISUALS = """Visual plan: give every slide a "visual" naming its exhibit form, chosen from the message, the way a senior
partner storyboards: ranking -> "bar chart"; change over time -> "column chart" or "line chart"; parts of a whole or
a bridge -> "waterfall"; peers -> "benchmark bars"; a choice between options -> "options vs criteria" (grid with a
harvey part, options as rows) or "2x2 matrix" (grid matrix2x2); a sequence or plan -> "roadmap" (grid timeline) or
"process steps" (grid process); a few headline numbers -> "big numbers" (grid stats beside a chart or text); issues or
drivers with evidence -> "icon rows" (challenge_matrix); a chart and its argument -> "exhibit + argument"
(exhibit_argument); lookup values -> "table". Rules: slide 2 (after the cover) is the executive_summary; never the same
visual on two slides in a row; at most two chart-plus-side-panel slides (bar_takeaway, column_trend, line_trend,
waterfall_bridge, table_focus, benchmark) in a row; in 8 or more slides use at least 5 different visuals; when the
brief asks a question or a decision, one slide weighs the options against criteria; when it ends on action, the
plan is a roadmap or process (or the closing steps), not bullets."""


def story_variety(slides: list, brief: str = "") -> list[str]:
    """What a senior partner would send back about the storyboard's visual plan, slide numbers 1-based."""
    body = [x for x in slides if isinstance(x, dict) and x.get("archetype") not in ("cover", "agenda", "section_divider")]
    msgs = []
    if len(slides) >= 6 and not any(x.get("archetype") == "executive_summary" for x in slides[:3]):
        msgs.append("slide 2 must be the executive_summary (the answer and the 3-5 claims that prove it)")
    vis = [(x.get("visual") or x.get("archetype") or "").strip().lower() for x in slides]
    arch = [x.get("archetype") for x in slides]
    for i in range(1, len(slides)):
        if vis[i] and vis[i] == vis[i - 1] and arch[i] not in ("cover", "agenda", "section_divider"):
            msgs.append(f"slides {i} and {i + 1} use the same visual ({vis[i]}); change one")
    run = 0
    for i, a in enumerate(arch, 1):
        run = run + 1 if a in SIDE_PANEL else 0
        if run == 3:
            msgs.append(f"slides {i - 2}-{i} are three chart-plus-side-panel pages in a row; make slide {i - 1} "
                        "another form (big numbers, 2x2, options vs criteria, roadmap, icon rows, exhibit + argument)")
    if len(body) >= 7 and len({v for v in vis if v}) < 5:
        msgs.append(f"only {len({v for v in vis if v})} different visuals in {len(slides)} slides; use at least 5")
    if re.search(r"\?|\bshould\b|\bwhether\b|\boptions?\b|\bdecide\b|\bchoose\b", brief or "", re.I) and len(body) >= 6 \
            and not any(re.search(r"option|criteria|2x2|matrix|harvey", v) for v in vis):
        msgs.append("the brief asks for a decision: add one slide that weighs the options against criteria "
                    "(options vs criteria or a 2x2 matrix)")
    return msgs


_AGENDA_RE = re.compile(r"(?i)\bagenda\b|\btable of contents\b|\bcontents (?:slide|page)\b")


def wants_agenda(brief: str, slides_wanted: int, insert: bool = False) -> bool:
    """An agenda after the cover: when the brief asks for one, and by default in any deck of 10 slides or more (the
    reader of a long deck needs the map). Never when slides are added to a deck the user already has."""
    return not insert and (bool(_AGENDA_RE.search(brief or "")) or slides_wanted >= 10)


def add_agenda(plan: list) -> list:
    """The agenda slide goes right after the cover; it is built from the storyline's sections, not by a model."""
    at = 1 if plan and plan[0].get("archetype") == "cover" else 0
    if len(plan) > at and plan[at].get("archetype") == "executive_summary":
        at += 1                                  # answer first, then the map of the deck
    return plan[:at] + [{"node": "0.1", "section": "", "title": "Agenda", "archetype": "agenda", "evidence": []}] + plan[at:]


KEEP_IN_COUNT = ("cover", "executive_summary", "closing", "next_steps", "end")


def fit_count(plan: list, n: int) -> tuple[list, list]:
    """Trim a storyline to n slides: section dividers go first (they carry no evidence), then the content slides with
    the least evidence (the later one on a tie); the cover, the executive summary and the closing slide stay.
    Returns (plan, titles cut)."""
    plan = list(plan)
    cut = []
    while len(plan) > max(1, n):
        cands = [i for i, x in enumerate(plan) if x.get("archetype") not in KEEP_IN_COUNT]
        if not cands:
            break
        div = [i for i in cands if x_arch(plan[i]) == "section_divider"]
        i = div[-1] if div else min(cands, key=lambda k: (len(plan[k].get("evidence") or []), -k))
        cut.append(plan.pop(i).get("title") or "")
    return plan, cut


def x_arch(x) -> str:
    return (x or {}).get("archetype") or ""


def fill_agenda(slides: list, plan: list) -> list:
    """Each slide keeps its storyline section; the agenda and every section divider get the section list, and a
    divider shows its own section highlighted (the section tracker)."""
    from engines.forge.render import agenda_sections
    for s, x in zip(slides, plan):
        if isinstance(s, dict) and isinstance(x, dict):
            s.setdefault("section", x.get("section") or "")
            if x.get("section_icon"):
                s.setdefault("section_icon", x["section_icon"])
    secs = agenda_sections(slides)
    for i, s in enumerate(slides):
        if s.get("archetype") == "agenda":
            s["sections"] = secs
        elif s.get("archetype") == "section_divider" and len(secs) >= 2:
            nxt = next((t.get("section") for t in slides[i + 1:] if t.get("section")), s.get("section") or "")
            cur = next((x for x in secs if x["title"].lower() == (nxt or "").lower()), None)
            if cur:
                s.update(sections=secs, current=cur["no"], title=cur["title"])
    return slides


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


def _fact_values(facts, ids, ignore) -> list[tuple[float, str]]:
    return [(abs(v), u) for f in facts if f["id"] in ids for _, v, u in _numbers(f["text"], ignore)]


def _same_number(raw: str, v: float, u: str, fv: float, fu: str) -> bool:
    """Is the quoted number (raw, v, unit u) the fact's value (fv, fu)? Within 0.6% or the rounding of the decimals
    shown ($7m matches $7.2m, never $45m), and only through same-unit scalings ($7.2m = 7,200,000; never $7.2bn)."""
    v = abs(v)
    m = re.search(r"\.(\d+)", raw)
    half = 0.5 * 10 ** -(len(m.group(1)) if m else 0)          # a figure shown with d decimals is rounded to d
    pct = ("%", "pp")
    if (u in pct) != (fu in pct):
        if u and fu:                                          # a percentage is never a $ amount or a multiple
            return False
        if u in pct:                                          # 35% against a fact written 0.35
            return abs(v / 100 - fv) <= max(0.006 * fv, half / 100)
        return abs(v - fv / 100) <= max(0.006 * fv / 100, half)
    ok = lambda a, b, tol: abs(a - b) <= max(0.006 * abs(b), tol)  # noqa: E731
    s, fs = _SCALE.get(u, 1), _SCALE.get(fu, 1)
    if (u == fu or not u or not fu) and ok(v, fv, half):        # the same figure as written
        return True
    return ok(v * s, fv * fs, half * s)                         # the same amount in another scale


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
        bad = [raw for raw, v, u in nums if not any(_same_number(raw, v, u, fv, fu) for fv, fu in vals)]
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
            if kind == "range" or s.get("archetype") == "football_field":
                kind = "range"
                if len(cats) < 2:
                    out.setdefault(n, []).append(
                        f"a football field with {len(cats)} method is not a valuation range; it needs >= 2 methods "
                        "(ideally 3+) in the same unit as the offer; with fewer, state the one range in the text")
            if kind != "range":
                pct = [bool(re.search(r"%|percent|\bpp\b", cat, re.I)) for cat in cats]
                if any(pct) and not all(pct):
                    out.setdefault(n, []).append(
                        f"chart puts a percentage ({cats[pct.index(True)]}) on the same axis as amounts "
                        f"({cats[pct.index(False)]}); one chart, one unit: state the percentage in the text or a "
                        "stat, and chart only the comparable amounts")
                qtr = [bool(re.search(r"\bQ[1-4]\b|\bH[12]\b|quarter", cat, re.I)) for cat in cats]
                ann = [bool(re.search(r"\bFY|\bfull[- ]year|annual|^\s*(19|20)\d\dE?\s*$", cat, re.I)) for cat in cats]
                if any(qtr) and any(ann):
                    out.setdefault(n, []).append(
                        f"chart mixes quarterly and annual figures ({', '.join(cats[:4])}); a quarter next to a full "
                        "year is not comparable: chart one period type, and give the annual figure in the text")
            missing = [cats[i] if i < len(cats) else str(i) for se in series
                       for i, v in enumerate(se.get("values") or [])
                       if not isinstance(v, (int, float)) and kind != "line"]
            if missing:
                out.setdefault(n, []).append(
                    f"chart has no value for {', '.join(map(str, missing[:4]))}; never draw an empty bar: chart only "
                    "categories the facts give a number for (>= 3), or switch to an archetype that fits what is known "
                    "(kpi tiles, argument) and fill the slide with the evidence that exists")
    return out


_CONSTS = {1.0, 2.0, 4.0, 12.0, 100.0, 1000.0}


def _safe_eval(formula: str):
    """Arithmetic only: numbers, + - * /, parentheses. Returns (value, literals) or raises ValueError."""
    import ast
    tree = ast.parse(formula.replace(",", "").replace("×", "*").replace("÷", "/").replace("−", "-"), mode="eval")
    lits: list[float] = []

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            lits.append(float(n.value))
            return float(n.value)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = ev(n.left), ev(n.right)
            return {ast.Add: a + b, ast.Sub: a - b, ast.Mult: a * b}.get(type(n.op)) if not isinstance(n.op, ast.Div) \
                else a / b
        raise ValueError("only numbers, + - * / and parentheses")
    return ev(tree), lits


def verify_calcs(slides, facts, rules) -> tuple[dict[int, list[dict]], dict[int, list[str]]]:
    """Each slide's "calcs" are recomputed: every literal must be a number in the facts it cites (or a small
    constant), and the stated value must match the result as rounded. Verified calcs become facts the slide can cite
    (ids made unique per slide: S2C1); the rest are failures sent back to the writer."""
    ignore = re.compile(rules["pptx"]["provenance"]["ignore_numbers"])
    by_id = {f["id"]: f for f in facts}
    ok_out: dict[int, list[dict]] = {}
    bad_out: dict[int, list[str]] = {}
    for n, s in enumerate(slides, 1):
        calcs = [c for c in s.get("calcs") or [] if isinstance(c, dict)]
        if not calcs:
            continue
        ren = {}
        for c in calcs:                                       # C1 on every slide -> S<n>C1, prov rewritten to match
            cid = str(c.get("id") or "")
            if cid and not re.match(r"S\d+C", cid):
                ren[cid] = f"S{n}{cid if cid.startswith('C') else 'C' + cid}"
                c["id"] = ren[cid]
        if ren:
            def fix(node):
                if isinstance(node, dict):
                    for k in ("prov", "title_prov", "from"):
                        if isinstance(node.get(k), list):
                            node[k] = [ren.get(x, x) for x in node[k]]
                    for v in node.values():
                        fix(v)
                elif isinstance(node, list):
                    for v in node:
                        fix(v)
            fix(s)
        local = dict(by_id)
        for c in calcs:
            cid, formula, value = c.get("id"), str(c.get("formula") or ""), str(c.get("value") or "")
            src = [x for x in c.get("from") or [] if x in local]
            try:
                res, lits = _safe_eval(formula)
            except Exception as e:                               # noqa: BLE001
                bad_out.setdefault(n, []).append(f"calc {cid} '{formula}': not plain arithmetic ({e})")
                continue
            pool = [(abs(v), u) for x in src for _, v, u in _numbers(local[x]["text"], ignore)]
            stray = [f"{v:g}" for v in lits if abs(v) not in _CONSTS
                     and not any(_same_number(f"{v:g}", abs(v), "", fv, fu) or _same_number(f"{v:g}", abs(v), "", fv, "")
                                 for fv, fu in pool)]
            if not src or stray:
                bad_out.setdefault(n, []).append(
                    f"calc {cid} '{formula}': " + (f"{', '.join(stray)} not in the cited facts {', '.join(src)}"
                                                   if src else "cites no facts (from: [fact ids])"))
                continue
            nums = _numbers(value, ignore) or _numbers(value, None)
            if not nums:
                bad_out.setdefault(n, []).append(f"calc {cid}: value '{value}' has no number")
                continue
            raw, v, u = nums[0]
            if not any(_same_number(raw, v, u, abs(r), "") for r in (res, res * 100 if u in ("%", "pp") else res)):
                bad_out.setdefault(n, []).append(f"calc {cid}: '{formula}' = {res:,.4g}, not {value}; fix the value "
                                                 "or the formula")
                continue
            fact = {"id": cid, "text": f"{value} (calculated: {formula}; from {', '.join(src)})", "calc": True}
            local[cid] = fact
            ok_out.setdefault(n, []).append(fact)
    return ok_out, bad_out


def _strings(node, where=""):
    if isinstance(node, str):
        yield where, node
    elif isinstance(node, dict):
        for k, v in node.items():
            if k not in ("prov", "notes", "chart", "charts", "node", "id", "archetype", "layout", "look", "kind",
                         "title_prov", "highlight_categories", "type", "icon"):
                yield from _strings(v, f"{where}.{k}" if where else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _strings(v, f"{where}[{i}]")


def prose_checks(slides) -> dict[int, list[str]]:
    """Sentences as the reader will see them (after gap removal): a phrase said twice in one title, unbalanced bold
    markers, a sentence ending on 'but'/'and', stacked punctuation, left-over placeholders."""
    from engines.forge.render import normalize
    out: dict[int, list[str]] = {}
    for n, s in enumerate(slides, 1):
        if s.get("fixed"):
            continue
        sn = normalize(s)
        title = re.sub(r"\*\*", "", str(sn.get("title") or "")).lower()
        w = re.findall(r"[a-z0-9$%.-]+", title)
        grams = [" ".join(w[i:i + 3]) for i in range(len(w) - 2)]
        rep = sorted({g for g in grams if grams.count(g) > 1})
        if rep:
            out.setdefault(n, []).append(f"title repeats itself ('{rep[0]}' twice); say it once, <= 15 words")
        for where, t in _strings(sn):
            if t.count("**") % 2:
                out.setdefault(n, []).append(f"{where}: unbalanced ** bold markers in '{t[:60]}'")
            if re.search(r"\b(but|and|or|of|because|while)\s*[.!?]?\s*$", t.strip(), re.I) and len(t.split()) > 3:
                out.setdefault(n, []).append(f"{where}: sentence breaks off ('...{t.strip()[-40:]}'); finish it or cut it")
            if re.search(r"[,;:]\s*[.!?]|\bTBD\b|\bXX+\b|lorem|\[\s*\]|\{\w+\}", t, re.I):
                out.setdefault(n, []).append(f"{where}: broken or placeholder text ('{t.strip()[:60]}')")
    return out


def _chart_unit(c: dict) -> str:
    u = str(c.get("unit") or "")
    if "%" in u:
        return "%"
    for pat, code in ((r"\bbn\b|\bB\b|billion", "bn"), (r"\bm\b|\bM\b|\bmm\b|million", "m"), (r"\bx\b|multiple", "x"),
                      (r"\bk\b|thousand", "k")):
        if re.search(pat, u):
            return code
    return ""


def chart_fact_checks(slides, facts, rules) -> dict[int, list[str]]:
    """Every plotted value must be a figure in the facts (or the midpoint of a range the facts give): a chart is
    evidence, so a number drawn as a bar needs the same source as a number in a sentence. Catches invented
    football-field ranges and estimated bars that the text checks cannot see."""
    ignore = re.compile(rules["pptx"]["provenance"]["ignore_numbers"])
    pool: list[tuple[float, str]] = []
    for f in facts:
        nums = _numbers(f["text"], ignore)
        pool += [(abs(v), u) for _, v, u in nums]
        pool += [((abs(a[1]) + abs(b[1])) / 2, a[2] or b[2]) for a, b in zip(nums, nums[1:])]   # 13.0-13.3 -> 13.15
    out: dict[int, list[str]] = {}
    for n, s in enumerate(slides, 1):
        if s.get("fixed"):                                   # model pages are computed, not quoted
            continue
        charts = [c for c in ([s.get("chart")] + list(s.get("charts") or [])
                              + [p.get("chart") for p in s.get("parts") or [] if isinstance(p, dict)])
                  if isinstance(c, dict)]
        for c in charts:
            u = _chart_unit(c)
            label = " ".join(str(x or "") for x in (c.get("unit"), s.get("subtitle"), (s.get("exhibit") or {}).get("unit")
                                                     if isinstance(s.get("exhibit"), dict) else ""))
            per_share = bool(re.search(r"per share|/\s*sh\b|\$/sh", label, re.I))
            bad = []
            mism = []
            for se in c.get("series") or []:
                for v in (se.get("values") or []) if isinstance(se, dict) else []:
                    if not isinstance(v, (int, float)) or not v:
                        continue
                    hits = [fu for fv, fu in pool if _same_number(f"{v:g}", float(v), u, fv, fu)]
                    if not hits:
                        bad.append(f"{v:g}")
                    elif per_share and all(_SCALE.get(fu, 1) > 1 for fu in hits):
                        mism.append(f"{v:g}")                   # a $4.9B deal value drawn on a $ per share axis
            if mism:
                out.setdefault(n, []).append(
                    f"chart is in $ per share but plots {', '.join(mism[:6])}, which the facts give as totals "
                    "($ millions/billions); per-share and total values never share an axis")
            if bad:
                out.setdefault(n, []).append(
                    f"chart plots {', '.join(bad[:6])}{' ...' if len(bad) > 6 else ''}, which are not figures in the "
                    "facts; plot only values the facts state (no estimates, no illustrative ranges), or drop the "
                    "chart and make the point with the figures that exist")
    return out


def _content_failures(res: dict) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for f in res["failures"]:
        if f["status"] == "fail" and f["fix_scope"] == "content" and f.get("slide"):
            out.setdefault(int(f["slide"]), []).append(f"[{f['rule']}] {f['message']}")
    return out


def _why_not_run(rv: dict) -> str:
    """Why a partner review did not run, in plain words (the gateway refusing the key, a render failure, ...)."""
    errs = " ".join(str(e) for e in (rv or {}).get("errors") or [])
    if re.search(r"\b401\b|invalid or missing api key|unauthori[sz]ed", errs, re.I):
        return "gateway refused the key"
    if re.search(r"\b429\b|rate limit", errs, re.I):
        return "gateway rate limit"
    if errs.startswith("render") or "no slides rendered" in errs:
        return "the deck could not be rendered for review"
    return (errs[:120] or "no reviewer response")


def review_verdict(reviews: list) -> dict | None:
    """The deck's partner-review verdict for the answer and qa.json: the final review's, or, when the final review
    did not run (every batch refused, say), the first review's, with a plain note that the final one did not run and
    why. A review that never ran at all says so too: the score never disappears silently."""
    if not reviews:
        return None
    from engines.forge import review as RV
    last = reviews[-1]
    v = RV.deck_verdict(last)
    if v.get("status") != "not_run":
        if last.get("status") == "partial" and last.get("not_run"):
            v["note"] = (f"partner review partial: slides {', '.join(map(str, last['not_run']))} not reviewed "
                         f"({_why_not_run(last)})")
        return v
    why = _why_not_run(last)
    earlier = next((r for r in reversed(reviews[:-1]) if RV.deck_verdict(r).get("status") != "not_run"), None)
    if earlier is None:
        return dict(v, note=f"partner review not run: {why}")
    v = RV.deck_verdict(earlier)
    v["final_status"] = "not_run"
    v["note"] = (f"final partner review not run: {why}; the score is the first review's, before the repairs it "
                 "asked for")
    return v


def used_fact_ids(slides) -> set:
    """Fact ids the deck cites anywhere: every prov and title_prov list, and the facts each calc is computed from."""
    used: set = set()

    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                if k in ("prov", "title_prov") and isinstance(x, list):
                    used.update(str(i) for i in x if i)
                elif k == "calcs" and isinstance(x, list):
                    for c in x:
                        if isinstance(c, dict):
                            used.update(str(i) for i in c.get("from") or [] if i)
                            if c.get("id"):
                                used.add(str(c["id"]))
                else:
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(slides)
    return used


THIN_TAGS = ("[P20]", "[fill]", "[thin]")


def substance_hint(slides, facts, limit: int = 14) -> str:
    """For a thin slide: the facts the deck has NOT used yet, numeric ones first, and what to build from them."""
    used = used_fact_ids(slides)
    left = [f for f in facts if f.get("id") and f["id"] not in used]
    if not left:
        return ("Every fact is already used in the deck: make the slide denser with what it has (a stats row of its "
                "own numbers, the comparison or the implication as a callout), or merge it with its neighbour.")
    num = [f for f in left if re.search(r"\d", f.get("text", ""))]
    pick = (num + [f for f in left if f not in num])[:limit]
    return ("The slide is thin: the page ends well above its foot. Add ONE real part built from facts the deck has "
            "NOT used yet (cite them in prov), never filler: a chart when 3+ of them are numbers in one unit, a stats "
            "row (2-4 figures with labels) for unrelated headline numbers, or an implication callout that says what "
            "they mean for the decision. Unused facts:\n" + "\n".join(f"  {f['id']}: {f['text']}" for f in pick)
            + (f"\n  (+{len(left) - len(pick)} more unused)" if len(left) > len(pick) else ""))


def _review_on(client, rules) -> bool:
    """The partner review runs on live builds (it needs a vision model): off with MH_REVIEW=0 or rules review.enabled
    false, and for stub clients in tests."""
    if os.environ.get("MH_REVIEW", "1") == "0" or not (rules.get("review") or {}).get("enabled", True):
        return False
    try:
        from meshpool_harness.core.client import LiveClient
    except Exception:  # noqa: BLE001
        return False
    return isinstance(client, LiveClient)


def replan_storyline(client, story_sys: str, story: dict, panel: dict, new_facts: list, research: dict | None,
                     brief: str, facts: list, n: int, task_id: str) -> dict | None:
    """The storyline again, with the reviewers' issues, their cuts, merges and additions, and the facts research
    found (or did not find). Same JSON, same slide count. None when the call fails or returns no slides."""
    issues = "\n".join(f"- [{i['severity']}, {i['scope']}, {i.get('persona')}] {i.get('where') or 'storyline'}: "
                       f"{i['issue']} -> {i.get('fix') or ''}" for i in panel.get("issues") or [])
    cm = "\n".join(f"- {c.get('action')} slides {c.get('slides')}: {c.get('title') or ''}"
                   for r in panel.get("reviews") or [] for c in r.get("cut_or_merge") or [])
    found = "\n".join(f"- {f['id']}: {f['text']}" for f in new_facts)
    none = "\n".join(f"- {q['question']}" for q in (research or {}).get("questions") or []
                     if isinstance(q, dict) and q.get("status") != "found")
    skipped = (research or {}).get("skipped")
    head = ("REVISION. A senior partner and a bank MD reviewed your storyboard and did not sign it off. Re-plan it: fix "
            "every blocking and major issue below, apply the cuts, merges and additions that keep the slide count, "
            "and use the new facts (cite their ids in evidence) where they carry a point. Where research found "
            "nothing, do not plan a slide that needs that number: merge the point or make it with the facts in hand. "
            "Never invent a fact. Keep exactly the number of slides asked for, the opening and closing rules and the "
            "house style. Return the full storyboard JSON.\n\n")
    user = (f"Brief:\n{brief[:12000]}\n\nFacts (cite by id):\n" + "\n".join(f"{f['id']}: {f['text']}" for f in facts)
            + f"\n\nCurrent storyboard ({len(story.get('slides') or [])} slides, must stay {n}):\n{json.dumps(story)}"
            + f"\n\nReviewers' issues:\n{issues or '- none listed'}"
            + (f"\n\nCut, merge or add:\n{cm}" if cm else "")
            + (f"\n\nNew facts from research:\n{found}" if found else "")
            + (f"\n\nResearch found nothing for:\n{none}" if none else "")
            + (f"\n\nResearch was not run ({skipped}): plan only what the facts in hand support." if skipped else ""))
    try:
        again = _call(client, STORY_MODEL, head + story_sys, user, task_id, 6000)
    except Exception:  # noqa: BLE001
        return None
    sl = [x for x in again.get("slides") or [] if isinstance(x, dict)] if isinstance(again, dict) else []
    if len(sl) < max(2, n - 2):
        return None
    again["slides"] = sl
    return again


REPLAN_DECK = """You are the engagement manager. A senior partner and a bank MD reviewed the built deck below and
raised storyline issues. Re-plan ONLY what the issues need: retitle a slide so the titles tell the story, reorder,
merge two slides into one, cut one, or add a missing slide in the place a cut or merge freed. The deck keeps exactly
the same number of slides. Locked slides (marked) stay exactly as they are; the cover stays first and the closing slide
stays last. Return JSON {"slides": [{"from": [original slide numbers], "title": str, "archetype": str,
"needs": "what the page must show"}]} listing every slide in the new order: "from" is the slide whose content the
new slide keeps (one number), two numbers to merge them, [] for a new slide (only where a cut or merge freed the
slot). Titles: the takeaway in one sentence with a verb, <= 15 words, using only numbers the slides already show."""


def check_replan(entries, slides: list, locked) -> tuple[list, str]:
    """A deck re-plan is applied only when it keeps the count, every locked page once and unchanged, the cover first
    and the closing slide last, and uses no slide twice. Returns (entries, "") or ([], why)."""
    n = len(slides)
    if not isinstance(entries, list) or len(entries) != n:
        return [], f"re-plan has {len(entries) if isinstance(entries, list) else 0} slides, the deck {n}"
    out, used = [], []
    for e in entries:
        if not isinstance(e, dict):
            return [], "re-plan entry is not an object"
        src = [int(x) for x in e.get("from") or [] if isinstance(x, (int, float, str)) and str(x).strip().isdigit()]
        if any(not 1 <= k <= n for k in src):
            return [], f"re-plan names a slide outside 1-{n}"
        if not src and (e.get("archetype") not in ARCHETYPES or e.get("archetype") in ("cover", "agenda")
                        or not str(e.get("title") or "").strip()):
            return [], "a new slide needs a title and a content archetype"
        used += src
        out.append(dict(e, **{"from": src}))
    if len(used) != len(set(used)):
        return [], "re-plan uses a slide twice"
    for i in range(n):
        if locked(i) and not any(e["from"] == [i + 1] for e in out):
            return [], f"locked slide {i + 1} moved, merged or cut"
    if slides[0].get("archetype") == "cover" and out[0]["from"] != [1]:
        return [], "the cover must stay first"
    if slides[-1].get("archetype") in ("closing", "end") and n not in out[-1]["from"]:
        return [], "the closing slide must stay last"
    return out, ""


def panel_report(sb: dict, deck_hist: list, final: dict | None, research_log: list, on: bool, insert: bool) -> dict:
    """The reviewers' record for qa.json and the answer: every storyboard and deck round (scores, verdicts, what was
    done), the research done, the final verdict line, and, when they could not run, why. Never a made-up score."""
    if not on:
        return {"status": "off", "why": sb.get("off") or "reviewers off"}
    from engines.forge import critics

    def rnd(e, extra=()):
        pn = e.get("panel") or {}
        d = {"round": e.get("round"), "score": e.get("score"), "signed_off": e.get("signed_off"),
             "scores": pn.get("scores"), "verdicts": {r["persona"]: r.get("verdict") for r in pn.get("reviews") or []},
             "not_run": pn.get("not_run"), "issues": len(pn.get("issues") or [])}
        if pn.get("not_run"):
            d["why_not_run"] = critics.why_not_run(pn)
        for k in extra:
            if e.get(k) is not None:
                d[k] = e[k]
        return d
    sbr = sb.get("rounds") or []
    sb_last = sbr[-1]["panel"] if sbr else None
    if final is None or not any(r.get("status") == "ok" for r in final.get("reviews") or []):
        status = "not_run"
    elif final.get("signed_off"):
        status = "signed_off" if not final.get("not_run") else "partial"
    else:
        status = "revise"
    return {
        "status": status, "line": critics.verdict_line(final),
        "storyboard": {"rounds": [dict(rnd(e, ("replanned",)), new_facts=[f["id"] for f in e.get("new_facts") or []],
                                       research=[{k: q.get(k) for k in ("question", "status", "facts")}
                                                 for q in (e.get("research") or {}).get("questions") or []
                                                 if isinstance(q, dict)] or (e.get("research") or {}).get("skipped"))
                                  for e in sbr],
                       "signed_off": sb.get("signed_off"), "final": critics.compact(sb_last),
                       "line": critics.verdict_line(sb_last, "storyboard")},
        "deck": {"rounds": [rnd(e, ("reverted_to", "kept", "actions", "note")) for e in deck_hist],
                 "final": critics.compact(final)},
        "research": {"allowed": sb.get("research_allowed"), "why_not": sb.get("why_no_research"),
                     "steps": [{"step": r.get("step"), "seconds": r.get("seconds"),
                                "questions": [{k: q.get(k) for k in ("question", "status", "facts", "sources")}
                                              for q in r.get("questions") or []],
                                "new_facts": [{k: f.get(k) for k in ("id", "text", "url")} for f in r.get("facts") or []]}
                               for r in research_log]},
    }


def build(brief: str, out_dir: str, slides_wanted: int = 12, client=None, rules=None, task_id="forge",
          theme=None, density: str | None = None, fixed_pages: list | None = None,
          extra_facts: list | None = None, template: str | None = None, brand: dict | None = None,
          insert: bool = False, research: bool | None = None) -> dict:
    """research: allow the reviewers' research step (None: on for live builds, see research.allowed()).
    density: "present" (presenter-led), "balanced" (default) or "read" (dense send-ahead); see density.py. It
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
    agenda = wants_agenda(brief, slides_wanted, insert)
    narrative_n = max(3, slides_wanted - len(fixed) - agenda) if not insert else max(1, slides_wanted)
    fixed_txt = ("\nThese data pages are already built from the model and will be inserted as a section before the "
                 "closing slide; do NOT plan slides that repeat them, build the argument around them and cite their "
                 "facts (W ids) where you use their numbers. Your slides carry no valuation tables, grids or charts of "
                 "their own (the pages below have them, computed from the model); never estimate or interpolate a "
                 "number the model does not state:\n" + "\n".join(f"- {p['title']}" for p in fixed)
                 if fixed else "")
    ends = bookends(brief) if not insert else {"opening": False, "closing": False}
    opening = ('slide 1 MUST be the cover (node "0", archetype cover; the brief asks for an opening slide); '
               if ends["opening"] else 'slide 1 is the cover (node "0"); ')
    closing = ("the LAST slide MUST be archetype closing with the next steps (the brief asks for a closing slide)."
               if ends["closing"] else "end with what to do next.")
    if insert:     # slides added to a deck the user already has: its own cover, agenda and close stay as they are
        opening = ("these slides go into an existing deck after its own slides: NO cover, NO agenda, NO divider and "
                   "NO closing slide, only content slides (charts, tables, comparisons) that make the brief's points; ")
        closing = ""
    fact_txt = "\n".join(f"{f['id']}: {f['text']}" for f in facts)
    calls = 0

    # ---- stage 2: storyline (ghost deck)
    story_sys = f"""You are an engagement manager at a top strategy firm. Plan the storyline
before any slide exists. Return JSON: {{"governing_thought": str, "structure": "SCR",
"slides": [{{"node": "1.1", "section": str, "section_no": "1", "section_icon": "one-word meaning", "title": str,
"archetype": str, "visual": str, "evidence": [fact ids], "needs": "what the slide must show"}}]}}.
Section names are 2-5 plain words in sentence case ("Where the market is going"); they label the agenda. Do NOT
plan an agenda slide: the engine adds it from your sections.
Exactly {narrative_n} slides: {opening}then the argument in 3-4 sections; {closing} Use the archetypes below.{fixed_txt}
{STORY[density]}
{CONSULTING}
{VISUALS}
Each slide must have the evidence its point needs. Plan ONLY slides whose evidence
is in the facts: a table needs >= 3 comparable rows of facts, a chart >= 3 data
points in one unit. If the facts are thin on a point, merge it into another slide instead of planning a gap. Read top to bottom, the titles alone must tell the whole story; no two titles
make the same point. Titles: one sentence with a verb, <= 15 words, stating the takeaway with a number where a fact
supports it. Choose varied archetypes that prove each title.
{help_}"""
    story = _call(client, STORY_MODEL, story_sys, f"Brief:\n{brief[:14000]}\n\nFacts (cite by id):\n{fact_txt}",
                  task_id + "-story", 6000)
    calls += 1
    notes = story_variety([x for x in story.get("slides", []) if isinstance(x, dict)], brief) if not insert else []
    if notes and len(story.get("slides") or []) >= 4:
        try:                                     # the partner's read of the storyboard, sent back once
            again = _call(client, STORY_MODEL, "You revise a consulting storyboard's visual plan. Keep the governing "
                          "thought, the number of slides, the order of the argument and the evidence; change archetypes, "
                          "visuals and, where needed, titles so every point below is fixed. Return the same JSON shape.\n"
                          + VISUALS + "\n" + help_, "Fix:\n- " + "\n- ".join(notes) + "\n\nStoryboard:\n"
                          + json.dumps(story), task_id + "-story2", 6000)
            calls += 1
            if isinstance(again.get("slides"), list) and abs(len(again["slides"]) - len(story["slides"])) <= 1 and \
                    len(story_variety(again["slides"], brief)) < len(notes):
                story = again
        except Exception:
            pass

    # ---- stage 2b: the storyboard loop. The senior partner and the MD read the ghost deck before any slide is
    # written; what they say is missing goes to a focused research step, and the storyline is re-planned with their
    # issues and the new facts, until both sign off or the round cap (engines/forge/critics.py, research.py)
    from engines.forge import critics, research as RS
    cfg = critics.settings(rules)
    panel_on = critics.personas_on(client, rules) and not insert
    rs_ok, rs_why = RS.allowed(client, rules, brief, research)
    rcaps = RS.caps(rules)
    research_steps = 0
    research_log: list[dict] = []
    sb: dict = {"rounds": [], "research_allowed": rs_ok, "why_no_research": rs_why or None}
    if panel_on:
        max_sb = max(1, int(cfg["max_storyboard_rounds"]))
        for rnd in range(1, max_sb + 1):
            mat = critics.storyboard_material(story, facts, brief, narrative_n, agenda=agenda)
            pn = critics.run_panel(client, "storyboard", mat, critics.text_models(cfg), bar=cfg["bar"],
                                   timeout=float(cfg["timeout_s"]), task_id=f"{task_id}-sb{rnd}", who=cfg["who"])
            calls += len(pn["reviews"])
            entry = {"round": rnd, "storyline": story, "panel": pn, "score": critics.score(pn),
                     "signed_off": pn["signed_off"]}
            sb["rounds"].append(entry)
            if pn["signed_off"] or len(pn["not_run"]) == len(pn["reviews"]) or rnd == max_sb:
                break
            new: list[dict] = []
            qs = RS.questions_from(pn, int(rcaps["max_questions"]))
            if qs and rs_ok and research_steps < int(rcaps["max_research_steps"]):
                res_ = RS.investigate(qs, facts, client=client, models=critics.text_models(cfg), out_dir=str(out),
                                      rules=rules, task_id=f"{task_id}-research-sb{rnd}")
                research_steps += 1
                new = res_["facts"]
                facts.extend(new)
                entry["research"] = res_
                research_log.append(dict(res_, step=f"storyboard round {rnd}"))
            elif qs:
                entry["research"] = {"skipped": rs_why or "research step cap reached",
                                     "questions": [q["question"] for q in qs]}
            entry["new_facts"] = new
            again = replan_storyline(client, story_sys, story, pn, new, entry.get("research"), brief, facts,
                                     narrative_n, f"{task_id}-story-r{rnd}")
            calls += 1
            if not again:
                entry["replanned"] = False
                break
            entry["replanned"] = True
            story = again
        sb["signed_off"] = bool(sb["rounds"]) and sb["rounds"][-1]["signed_off"]
        critics.dump(out / "storyboard.json", sb)
    else:
        sb["off"] = "reviewers off (offline client, MH_REVIEW=0, MH_PERSONAS=0 or rules)" if not insert else \
            "slides added to an existing deck"
    (out / "storyline.json").write_text(json.dumps(story, indent=1))
    nodes = [{"id": s.get("node"), "title": s.get("title")} for s in story.get("slides", [])]

    # ---- stage 3: content, one call per slide (in parallel), each with only its own facts
    from concurrent.futures import ThreadPoolExecutor
    plan = _enforce_bookends([x for x in story.get("slides", []) if isinstance(x, dict)
                              and x.get("archetype") != "agenda"], ends)
    if fixed and len(plan) > narrative_n:
        # The model often plans more than asked when model pages exist, and the extra slides restate (or worse,
        # re-estimate) those pages' numbers. Keep the opening slides and the closing one, at the asked length.
        plan = plan[:narrative_n - 1] + plan[-1:]
    # The brief's slide count is a hard limit (P16): the storyline model often plans one or two slides more than
    # asked, and the bookends add one; the agenda and the executive summary fit inside the count, never on top.
    plan, cut = fit_count(plan, narrative_n)
    if cut:
        story["cut_to_fit_count"] = cut
        (out / "storyline.json").write_text(json.dumps(story, indent=1))
    if agenda:
        plan = add_agenda(plan)
    nodes = [{"id": x.get("node"), "title": x.get("title")} for x in plan]

    def one(x):
        if x.get("archetype") in ("agenda", "section_divider"):   # built from the storyline, no model call
            return {k: x.get(k) for k in ("node", "archetype", "title", "section") if x.get(k) is not None}
        ev = set(x.get("evidence") or [])
        rel = [f for f in facts if f["id"] in ev] or facts
        if x.get("archetype") in ("executive_summary", "closing"):
            rel = facts                       # they sum up the whole deck: every fact, or they write gaps for known numbers
        try:
            from meshpool_harness.assistant import HONESTY
            return _call(client, MODEL, f"""You write ONE consulting slide as JSON for a renderer that owns all
design. {HONESTY} A number that is not in the facts is written [GAP: what is missing] (it shows as N/A).
{CONSULTING}
{WRITE[density]}
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
    fill_agenda(slides, plan)
    is_fixed = lambda i: bool(slides[i].get("fixed")) or slides[i].get("archetype") in ("agenda", "section_divider")  # noqa: E731
    from engines.forge import clarity                          # a second, smaller model reads every line once
    clear = [clarity.review(slides, client, main_model=MODEL, task_id=task_id + "-clarity")]
    calls += 1
    def collect(r, res):                                   # every content check, after a render
        fails = _content_failures(res)
        calcs_ok, calcs_bad = verify_calcs(slides, facts, rules)
        for n, msgs in calcs_bad.items():
            fails.setdefault(n, []).extend(f"[calc] {m}" for m in msgs)
        known = facts + [f for v in calcs_ok.values() for f in v]
        for n, msgs in number_checks(slides, known, rules).items():
            fails.setdefault(n, []).extend(f"[P13] {m}" for m in msgs)
        for n, msgs in fill_checks(slides, density).items():
            fails.setdefault(n, []).extend(f"[fill] {m}" for m in msgs)
        for n, msgs in chart_checks(slides).items():
            fails.setdefault(n, []).extend(f"[chart] {m}" for m in msgs)
        for n, msgs in chart_fact_checks(slides, known, rules).items():
            fails.setdefault(n, []).extend(f"[P13] {m}" for m in msgs)
        for n, msgs in prose_checks(slides).items():
            fails.setdefault(n, []).extend(f"[prose] {m}" for m in msgs)
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
        return fails

    rounds = []
    pptx = out / "deck.pptx"

    def draw():
        r = render(slides, str(pptx), carry=False, theme=theme, density=density, template=template,
                   brand=brand)          # the brief's slide count is a hard limit
        prov = json.loads(Path(r["provenance"]).read_text())
        res = validate(str(pptx), rules, brief={"slide_count": len(slides) if fixed else slides_wanted, **ends},
                       provenance=prov, storyline={"nodes": nodes}, render=True, density=density)
        return r, res

    def repair(fails, tag):                                    # one call per failing slide, all in parallel
        nonlocal calls

        def fix_one(item):
            n, msgs = item
            s = slides[n - 1]
            ev = set(next((x.get("evidence", []) for x in story.get("slides", []) if x.get("node") == s.get("node")), []))
            rel = [f for f in facts if f["id"] in ev] or facts
            if any(m.startswith(("[P20]", "[fill]", "[schema]", "[review]")) for m in msgs):
                rel = facts                                  # a thin slide needs the whole evidence base to fill
            if any(m.startswith(THIN_TAGS) for m in msgs):   # ... and to know which of it is still unused
                msgs = list(msgs) + [substance_hint(slides, facts)]
            try:
                return n, _call(client, MODEL, f"""Fix this one consulting slide. Change only words, numbers, archetype
choice or the number of items; never layout. Keep node and keep the point of the title. Return the full slide JSON.
{CONSULTING}
{WRITE[density]}
{SLIDE_SHAPE}
{help_}""", "Failures to fix:\n- " + "\n- ".join(msgs) + f"\n\nSlide:\n{json.dumps(s)}\n\nFacts:\n" +
                    "\n".join(f"{f['id']}: {f['text']}" for f in rel), f"{task_id}-fix{n}-{tag}", 6000)
            except Exception:
                return n, None
        repaired = set()
        todo = [(n, m) for n, m in sorted(fails.items()) if n - 1 < len(slides) and not is_fixed(n - 1)]
        if not todo:
            return repaired
        with ThreadPoolExecutor(min(8, max(1, len(todo)))) as ex:
            for n, fixed_ in ex.map(fix_one, todo):
                calls += 1
                if isinstance(fixed_, dict) and fixed_.get("archetype") in ARCHETYPES:
                    for k in ("section", "section_icon"):          # the agenda's page numbers follow the section
                        if slides[n - 1].get(k):
                            fixed_.setdefault(k, slides[n - 1][k])
                    slides[n - 1] = fixed_
                    repaired.add(n - 1)
        if repaired:                                            # repaired slides get the same clarity read
            clear.append(clarity.review(slides, client, only=repaired, main_model=MODEL,
                                        task_id=f"{task_id}-clarity-{tag}"))
            calls += 1
        return repaired

    for rnd in range(rules["repair"]["max_rounds"] + 1):
        r, res = draw()
        fails = collect(r, res)
        rounds.append({"round": rnd, "rules": res["rules"], "content_failures": sum(len(v) for v in fails.values())})
        if not fails or rnd == rules["repair"]["max_rounds"]:
            break
        if len(rounds) >= 3 and rounds[-1]["content_failures"] >= rounds[-3]["content_failures"]:
            break                                            # two rounds without progress: stop paying for repairs
        repair(fails, f"r{rnd}")

    # the review panel: the senior partner and the MD read the rendered pages; every issue goes to the step that can
    # fix it (content and data -> the writer, layout -> a re-layout or an archetype instruction, storyline -> a re-plan
    # of the affected slides, research -> a research step and a rewrite); re-render, re-validate, review again until
    # both sign off, the round cap, or two rounds without a better score. The best-scoring version is kept.
    reviews = []
    deck_hist: list[dict] = []
    panel_final = None
    if panel_on:
        import copy

        def ids_for(text):                                   # a slide for a question that names none: the title
            from engines.research.library import tokens       # sharing most of its words
            qt = set(tokens(text))
            best_n, best_k = None, 1
            for i, s_ in enumerate(slides):
                if is_fixed(i) or s_.get("archetype") in ("cover", "agenda", "section_divider"):
                    continue
                k = len(qt & set(tokens(str(s_.get("title") or ""))))
                if k > best_k:
                    best_n, best_k = i + 1, k
            return best_n

        def replan_deck(issues, tag):
            nonlocal calls
            outline = "\n".join(f"{i}. [{s_.get('archetype')}] {s_.get('title')}" + (" (locked)" if is_fixed(i - 1) else "")
                                 for i, s_ in enumerate(slides, 1))
            try:
                d = _call(client, STORY_MODEL, REPLAN_DECK + "\n" + CONSULTING + "\n" + VISUALS,
                          f"Deck ({len(slides)} slides):\n{outline}\n\nStoryline issues:\n"
                          + "\n".join(f"- [{i['severity']}, {i.get('persona')}] {i.get('where') or 'storyline'}: "
                                      f"{i['issue']} -> {i.get('fix') or ''}" for i in issues),
                          f"{task_id}-replan-{tag}", 4000)
                calls += 1
            except Exception as e:  # noqa: BLE001
                return {"status": "failed", "why": str(e)[:200]}, None, {}
            plan2, why = check_replan(d.get("slides") if isinstance(d, dict) else None, slides, is_fixed)
            if why:
                return {"status": "rejected", "why": why}, None, {}
            old, new_, asks_ = list(slides), [], {}
            remap = {}
            for k, e in enumerate(plan2):
                src, title, arch = e["from"], str(e.get("title") or "").strip(), e.get("archetype")
                if len(src) == 1:
                    s_ = old[src[0] - 1]
                    remap[src[0]] = k + 1
                    if is_fixed(src[0] - 1) or ((not title or title == s_.get("title"))
                                                and (not arch or arch == s_.get("archetype"))):
                        new_.append(s_)
                        continue
                    new_.append(dict(s_))
                    asks_[k + 1] = [f"[review] (storyline) This page now makes the point: '{title or s_.get('title')}'"
                                    + (f", as a {arch} page" if arch in ARCHETYPES and arch != s_.get("archetype") else "")
                                    + f". Rewrite it to prove that title{': ' + e['needs'] if e.get('needs') else ''}."]
                elif src:
                    base = dict(old[src[0] - 1])
                    remap[src[0]] = k + 1
                    others = [old[j - 1] for j in src[1:]]
                    new_.append(base)
                    asks_[k + 1] = [f"[review] (storyline) Merge: this page now carries slides {', '.join(map(str, src))} "
                                    f"as one, titled '{title}'. Keep the strongest evidence of both. The other slide: "
                                    + json.dumps([{k_: v for k_, v in o.items() if k_ not in ('notes',)} for o in others])[:3000]]
                else:
                    x = {"node": f"R{k + 1}", "section": "", "title": title, "archetype": arch,
                         "needs": e.get("needs") or "", "evidence": []}
                    s_ = one(x)
                    calls += 1
                    s_.setdefault("node", x["node"])
                    new_.append(s_)
            slides[:] = new_
            fill_agenda(slides, [{} for _ in slides])
            nodes[:] = [{"id": s_.get("node"), "title": s_.get("title")} for s_ in slides]
            return {"status": "applied", "plan": [{"from": e["from"], "title": e.get("title")} for e in plan2]}, remap, asks_

        def apply_panel(pn, tag):
            nonlocal calls, research_steps
            rt = critics.route(pn, slides, is_fixed)
            act: dict = {"content": sorted(rt["content"]), "layout": sorted(rt["layout"]), "unplaced": len(rt["unplaced"])}
            asks: dict[int, list[str]] = {}
            if rt["storyline"]:
                act["storyline"], remap, extra = replan_deck(rt["storyline"], tag)
                if remap is not None:                         # issues follow their slide to its new place
                    rt["content"] = {remap[n]: m for n, m in rt["content"].items() if n in remap}
                    rt["layout"] = {remap[n]: m for n, m in rt["layout"].items() if n in remap}
                    for n, m in extra.items():
                        asks.setdefault(n, []).extend(m)
            qs = RS.questions_from(pn, int(rcaps["max_questions"]))
            if qs and rs_ok and research_steps < int(rcaps["max_research_steps"]):
                res_ = RS.investigate(qs, facts, client=client, models=critics.text_models(cfg), out_dir=str(out),
                                      rules=rules, task_id=f"{task_id}-research-{tag}")
                research_steps += 1
                facts.extend(res_["facts"])
                research_log.append(dict(res_, step=f"deck review {tag}"))
                act["research"] = {"questions": [{k: q[k] for k in ("question", "status", "facts")} for q in res_["questions"]],
                                   "new_facts": [f["id"] for f in res_["facts"]]}
                for q in res_["questions"]:
                    if not q["facts"]:
                        continue
                    n = critics.P.slide_of(q.get("where", "")) or ids_for(q["question"])
                    if n and not is_fixed(n - 1):
                        asks.setdefault(n, []).append(
                            f"[review] (research) The reviewers asked: {q['question']}. New sourced facts "
                            f"{', '.join(q['facts'])} answer it: use them where they prove or correct this page, cite "
                            "them in prov and add their source to the source line.")
            elif qs:
                act["research"] = {"skipped": rs_why or "research step cap reached",
                                   "questions": [q["question"] for q in qs]}
            fixed_layout = {}
            for n, its in rt["layout"].items():
                done = critics.relayout(slides[n - 1], its)
                if done:
                    fixed_layout[n] = done
                else:
                    asks.setdefault(n, []).extend(critics.layout_instruction(its))
            act["layout_fixed"] = fixed_layout
            for n, msgs in rt["content"].items():
                asks.setdefault(n, []).extend(msgs)
            for n in asks:                                     # with the slide's open rule failures, fixed together
                asks[n] += [m for m in fails.get(n, []) if m not in asks[n]]
            act["to_writer"] = sorted(asks)
            act["rewritten"] = sorted(i + 1 for i in repair(asks, tag)) if asks else []
            return act

        best = None
        stall = 0
        max_rv = max(1, int(cfg["max_review_rounds"]))
        for rnd in range(1, max_rv + 1):
            pn = critics.deck_panel(client, str(pptx), slides, str(out / ".review" / f"r{rnd}"), cfg,
                                    task_id=f"{task_id}-panel{rnd}")
            calls += len(pn["reviews"]) * int(pn.get("batches") or 1)
            sc = critics.score(pn)
            entry = {"round": rnd, "panel": pn, "score": sc, "signed_off": pn["signed_off"]}
            deck_hist.append(entry)
            if sc is None:                                    # nobody could review: say so, never score
                entry["note"] = "reviewers did not run: " + critics.why_not_run(pn)
                break
            if best is None or sc >= best["score"]:
                improved = best is None or sc > best["score"]
                best = {"score": sc, "slides": copy.deepcopy(slides), "round": rnd, "panel": pn,
                        "nodes": copy.deepcopy(nodes)}
                stall = 0 if improved else stall + 1
            else:                                             # worse than the best version: back to it
                stall += 1
                slides[:] = copy.deepcopy(best["slides"])
                nodes[:] = copy.deepcopy(best["nodes"])
                entry["reverted_to"] = best["round"]
                r, res = draw()
                fails = collect(r, res)
            if pn["signed_off"] or rnd == max_rv or stall >= 2:
                break
            entry["actions"] = apply_panel(best["panel"] if entry.get("reverted_to") else pn, f"panel{rnd}")
            r, res = draw()
            fails = collect(r, res)
            if fails and repair(fails, f"panel{rnd}-rules"):   # the rewrites pass the rule checks again
                r, res = draw()
                fails = collect(r, res)
            rounds.append({"round": f"panel{rnd}", "rules": res["rules"],
                           "content_failures": sum(len(v) for v in fails.values())})
        if best is not None and slides != best["slides"]:   # keep the best-scoring version
            slides[:] = copy.deepcopy(best["slides"])
            nodes[:] = copy.deepcopy(best["nodes"])
            r, res = draw()
            fails = collect(r, res)
            deck_hist[-1]["kept"] = best["round"]
        panel_final = best["panel"] if best is not None else (deck_hist[-1]["panel"] if deck_hist else None)
        (out / "review.json").write_text(json.dumps({"rounds": deck_hist, "kept_round": best["round"] if best else None,
                                                     "bar": cfg["bar"]}, indent=1, default=str))
    elif _review_on(client, rules):
        # the single generic partner review (review.personas.enabled false): content complaints go back to the
        # writer once, then the deck is reviewed again for the verdict (engines/forge/review.py)
        from engines.forge import review as RV
        rv = RV.review_deck(str(pptx), slides=slides, client=client, out_dir=str(out / ".review"),
                            task_id=f"{task_id}-review")
        reviews.append(rv)
        calls += 1
        asks = RV.as_failures(rv, scopes=("content",))
        if asks:
            for n, msgs in asks.items():                     # with the slide's open rule failures, fixed together
                asks[n] = list(msgs) + [m for m in fails.get(n, []) if m not in msgs]
            if repair(asks, "review"):
                r, res = draw()
                fails = collect(r, res)
                rounds.append({"round": "review", "rules": res["rules"],
                               "content_failures": sum(len(v) for v in fails.values())})
                rv = RV.review_deck(str(pptx), slides=slides, client=client, out_dir=str(out / ".review"),
                                    task_id=f"{task_id}-review-final")
                reviews.append(rv)
                calls += 1
        for n, msgs in RV.as_failures(rv, severities=("high",)).items():   # what is still high goes to the notes
            fails.setdefault(n, []).extend(m for m in msgs if m not in fails.get(n, []))
        (out / "review.json").write_text(json.dumps(reviews, indent=1, default=str))
    # freeze what is left into the notes: nothing passes silently
    left = fails
    open_review = critics.review_notes(panel_final, len(slides)) if panel_final else {}
    if (left or open_review) and rules["repair"]["flag_in_file"]:
        for n, msgs in left.items():
            if n - 1 < len(slides):
                slides[n - 1]["notes"] = (slides[n - 1].get("notes", "") + "\n" + "\n".join("[QA] " + m for m in msgs)).strip()
        for n, msgs in open_review.items():                  # the reviewers' blocking and major issues still open
            slides[n - 1]["notes"] = (slides[n - 1].get("notes", "") + "\n" + "\n".join(msgs)).strip()
        r = render(slides, str(pptx), carry=False, theme=theme, density=density, template=template,
                   brand=brand)          # the brief's slide count is a hard limit
        prov = json.loads(Path(r["provenance"]).read_text())
        res = validate(str(pptx), rules, brief={"slide_count": slides_wanted, **ends}, provenance=prov,
                       storyline={"nodes": nodes}, render=True, density=density)
    (out / "slides.json").write_text(json.dumps(slides, indent=1))
    (out / "facts.json").write_text(json.dumps(facts + [f for v in verify_calcs(slides, facts, rules)[0].values()
                                                         for f in v], indent=1))
    res.pop("measurement", None)
    (out / "qa.json").write_text(json.dumps(dict(res, rounds=rounds, unresolved=left), indent=1, default=str))
    (out / "clarity.json").write_text(json.dumps(clear, indent=1, default=str))
    verdict = review_verdict(reviews)
    panel = panel_report(sb, deck_hist, panel_final, research_log, panel_on, insert)
    qa = json.loads((out / "qa.json").read_text())
    if verdict is not None:                                  # the verdict travels with the QA record too
        qa["review"] = verdict
    qa["panel"] = panel                                      # the reviewers' history, scores and open issues
    (out / "qa.json").write_text(json.dumps(qa, indent=1, default=str))
    return {"pptx": str(pptx), "calls": calls, "rounds": rounds, "rules": res["rules"], "density": density,
            "unresolved": {k: v for k, v in left.items()}, "review": verdict, "panel": panel,
            "slides": len(slides)}


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
