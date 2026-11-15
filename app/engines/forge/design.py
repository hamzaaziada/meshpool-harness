"""Design-first slides: the slide's content and the deck's design direction go to an image model, which draws ONE
designed page; a vision model reads that picture back as a strict layout spec; the spec is rebuilt here as native,
editable PowerPoint (theme colours, shapes with their text inside them, native charts and tables, library icons).

    d = Designer(client, rules, out_dir, theme=theme, density="balanced")
    specs = d.specs(slides)                    # {slide index: spec}; images kept in <out>/design/slideNN.png
    render([dict(s, _design=specs[i]) if i in specs else s for i, s in enumerate(slides)], ...)
    bad = d.check(pptx, render_result, validation, view)   # {index: why}: those slides fall back to the archetype

Why: the archetype renderer reuses a few layouts, so content pages look alike. An image model is good at the look
(hierarchy, rhythm, a distinctive composition for each message) and bad at text and data; the rebuild keeps only the
geometry and the colours of its picture and takes every word and number from the slide's JSON, so nothing the image
model misspells or invents reaches the deck. Charts are drawn from the slide's real data, never traced.

The steps, each one testable on its own:
1. design_prompt(): the image prompt for one slide: the exact text and numbers (quoted), a layout that fits the
   message and differs from the previous slides' layouts, the palette, fonts and the consulting style guide.
2. ImageClient.generate(): POST /images/generations (gpt-image-2 by default, configurable), falling back to an image
   returned by chat completions; never raises (status "not_run" with the reason).
3. reconstruct() + normalize_spec(): the image and the content to a vision model; its JSON is normalised: bboxes
   clamped to the body band, colours mapped to the nearest theme slot (the deck stays recolourable), every text run
   bound to a content atom by id or exact text (text that is not in the content is dropped and reported).
4. draw_design(): called by render._render for a slide carrying "_design": snaps edges to the 24-column grid, puts
   text inside the shape that holds it (P17), fits every text with the renderer's measurer (shrink within the role's
   floor, then grow the box into free space, else give up), draws charts, tables and other exhibits with the parts
   library, icons from the icon library. Anything it cannot do raises DesignMiss and the archetype draws the slide.
5. Designer.check(): after validation, a designed slide that fails P0/P1/P5/P8/P17 or lost content (a bullet,
   figure or chart value of its JSON is not on the page) is redrawn by its archetype, and the reason is recorded.

Modes (rules.yaml design.mode, env MH_DESIGN, engine option design, wording "designed look" / "image-designed"):
archetype (off), image (content slides, the executive summary and the closing), auto (content slides only, and only
on a live client). Cost caps: design.max_images per deck and design.time_budget_s of wall clock.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pptx.enum.dml import MSO_THEME_COLOR as TC
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

from . import render as R

MODES = ("archetype", "image", "auto")
DEFAULTS = {
    "mode": "archetype",
    "image_model": "gpt-image-2",       # any OpenAI-compatible image model on the gateway; env MH_DESIGN_IMAGE_MODEL
    "vision_models": [],                # empty: env MH_DESIGN_VISION_MODEL, then the review chain (review.chain)
    "size": "1536x864",                 # 16:9; the nearest size the API allows is tried next and cropped to 16:9
    "fallback_sizes": ["1536x1024", "1792x1024", "auto"],
    "max_images": 12,                   # image generations per deck (repairs that change a slide count again)
    "time_budget_s": 900,               # wall clock for all design calls of one deck
    "image_timeout_s": 180,
    "vision_timeout_s": 240,
    "workers": 3,
    "fallback_rules": ["P0", "P1", "P5", "P8", "P17"],
}
DESIGN_WORDS = re.compile(r"(?i)\b(?:image[- ]designed|designed[- ](?:look|pages|slides|deck)|design[- ]first|"
                          r"image[- ]design(?:ed)?|image[- ]led design)\b")
NEVER = {"cover", "section_divider", "agenda", "appendix_divider", "end"}
SUMMARY = {"executive_summary", "closing", "next_steps"}
DENSE_MATRIX = {"challenge_matrix", "text_matrix", "football_field"}     # a full-page matrix is its own exhibit

CANVAS = (13.333, 7.5)
BODY = (R.LEFT, R.BODY_Y, R.RIGHT, R.BODY_B)          # x0, y0, x1, y1: the band a design may use
SLOTS = ("dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6")
THEME = {"dk1": TC.TEXT_1, "lt1": TC.BACKGROUND_1, "dk2": TC.TEXT_2, "lt2": TC.BACKGROUND_2,
         "accent1": TC.ACCENT_1, "accent2": TC.ACCENT_2, "accent3": TC.ACCENT_3, "accent4": TC.ACCENT_4,
         "accent5": TC.ACCENT_5, "accent6": TC.ACCENT_6}
SHAPES = {"rect": MSO_SHAPE.RECTANGLE, "rounded_rect": MSO_SHAPE.ROUNDED_RECTANGLE, "ellipse": MSO_SHAPE.OVAL,
          "chevron": MSO_SHAPE.CHEVRON, "pentagon": MSO_SHAPE.PENTAGON}
TYPES = {"text", "rect", "rounded_rect", "ellipse", "chevron", "pentagon", "line", "icon", "chart", "table", "badge"}
TYPE_ALIASES = {"textbox": "text", "label": "text", "rectangle": "rect", "box": "rect", "card": "rect",
                "rounded_rectangle": "rounded_rect", "roundrect": "rounded_rect", "rounded": "rounded_rect",
                "circle": "ellipse", "oval": "ellipse", "arrow": "pentagon", "homeplate": "pentagon",
                "connector": "line", "rule": "line", "divider": "line", "exhibit": "chart", "graph": "chart",
                "number": "badge"}
ROLES = ("figure", "heading", "body", "label", "callout", "footnote")
ANCHORS = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE, "center": MSO_ANCHOR.MIDDLE,
           "bottom": MSO_ANCHOR.BOTTOM}
ALIGNS = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "centre": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}
MAX_ICONS = 4                                          # P9


class DesignMiss(Exception):
    """The spec cannot be rebuilt faithfully on this slide (content left out, text that cannot fit, a broken
    exhibit): the archetype renderer draws the slide instead."""


# ------------------------------------------------------------------ settings and mode
def settings(rules: dict | None) -> dict:
    cfg = dict(DEFAULTS, **((rules or {}).get("design") or {}))
    if os.environ.get("MH_DESIGN_IMAGE_MODEL"):
        cfg["image_model"] = os.environ["MH_DESIGN_IMAGE_MODEL"]
    if os.environ.get("MH_DESIGN_MAX_IMAGES"):
        cfg["max_images"] = int(os.environ["MH_DESIGN_MAX_IMAGES"])
    return cfg


def vision_models(cfg: dict) -> list[str]:
    from .review import chain
    out: list[str] = []
    for m in [os.environ.get("MH_DESIGN_VISION_MODEL")] + list(cfg.get("vision_models") or []) + chain(None):
        if m and m not in out:
            out.append(m)
    return out


def mode_from(task: str = "", options: dict | None = None) -> str | None:
    """The mode a request asks for: options["design"] ("image", "auto", "archetype"), else wording such as
    "designed look", "image-designed", "design-first" (image). None: the request does not say."""
    asked = str((options or {}).get("design") or "").strip().lower()
    if asked in MODES:
        return asked
    if asked in ("on", "true", "yes", "1"):
        return "image"
    if asked in ("off", "false", "no", "0"):
        return "archetype"
    return "image" if DESIGN_WORDS.search(task or "") else None


def resolve_mode(asked: str | None, rules: dict | None) -> str:
    """The build's mode: the request (argument), else env MH_DESIGN, else rules design.mode."""
    for m in (asked, os.environ.get("MH_DESIGN"), settings(rules).get("mode")):
        m = str(m or "").strip().lower()
        if m in MODES:
            return m
    return "archetype"


def eligible(s: dict, mode: str) -> bool:
    """Which slides take the design path: content slides; in image mode also the executive summary and closing.
    Never covers, agendas, dividers, computed model pages or full-page matrices."""
    a = s.get("archetype")
    if mode == "archetype" or s.get("fixed") or s.get("_cont") or a in NEVER or a in DENSE_MATRIX:
        return False
    if a in SUMMARY:
        return mode == "image"
    return True


def view(s: dict, density: str | None = None) -> dict:
    """The slide as the renderer will see it (normalised and condensed to the density): atoms are bound to this."""
    from .density import condense, normalize as dnorm, DEFAULT
    return condense(R.normalize(s), s.get("density") or dnorm(density) or DEFAULT)


# ------------------------------------------------------------------ content atoms
def _t(v) -> str:
    if isinstance(v, dict):
        return str(v.get("text") or v.get("lead") or v.get("title") or v.get("label") or "")
    return "" if v is None else str(v)


def _p(v):
    return v.get("prov") if isinstance(v, dict) else None


def plain(t: str) -> str:
    """Text as the reader sees it: no **bold** marks, ^superscripts kept as text."""
    return re.sub(r"\s+", " ", re.sub(r"\*\*", "", re.sub(r"\^(\w+)", r"\1", str(t or "")))).strip()


def _key(t: str) -> str:
    return re.sub(r"[\s.;:,]+$", "", plain(t).casefold().replace("’", "'").replace("–", "-").replace("—", "-"))


def _benchmark_chart(b: dict) -> dict | None:
    pn = (b.get("panels") or [None])[0]
    if not isinstance(pn, dict) or not b.get("companies"):
        return None
    hl = [b["companies"][i] for i in b.get("highlight") or [] if isinstance(i, int) and i < len(b["companies"])]
    return {"kind": "bar", "categories": list(b["companies"]), "series": [{"name": pn.get("title") or "Value",
            "values": list(pn.get("values") or [])}], "unit": pn.get("unit", ""), "highlight_categories": hl}


def content_model(s: dict) -> tuple[list[dict], list[dict]]:
    """(atoms, exhibits) of one slide. An atom is one string the page must show: {id, role, text, required, prov,
    parent}; ids are paths into the JSON ("parts.0.items.1.value"), so a spec stays bound when the words change.
    An exhibit is a chart, table or other data part drawn natively: {id, kind, part, heading, inner, data}.
    The title, subtitle, source and footnotes are not atoms: the frame (header and footer) draws them."""
    atoms: list[dict] = []
    exh: list[dict] = []

    def add(i, role, v, required=True, parent=None, prov=None):
        t = _t(v)
        if t.strip():
            atoms.append({"id": i, "role": role, "text": t, "required": required, "prov": _p(v) or prov,
                          "parent": parent})

    def bullets(prefix, lst, role="body", parent=None):
        for j, b in enumerate(lst or []):
            add(f"{prefix}.{j}", role, b, parent=parent)
    tk = s.get("takeaway")
    if tk:
        add("takeaway", "callout", tk)
    bullets("bullets", s.get("bullets"))
    for i, b in enumerate(s.get("blocks") or []):
        if isinstance(b, dict):
            add(f"blocks.{i}", "heading" if b.get("bullets") else "body", b)
            bullets(f"blocks.{i}.bullets", b.get("bullets"))
        else:
            add(f"blocks.{i}", "body", b)
    co = s.get("callout")
    if isinstance(co, dict):
        add("callout", "callout", co)
        bullets("callout.bullets", co.get("bullets"), "callout")
    elif co:
        add("callout", "callout", co)
    ex = s.get("exhibit") if isinstance(s.get("exhibit"), dict) else {}
    heads = []
    for k in ("title", "unit"):
        if ex.get(k):
            add(f"exhibit.{k}", "heading" if k == "title" else "label", ex[k], required=False)
            heads.append(f"exhibit.{k}")
    if isinstance(s.get("chart"), dict):
        exh.append({"id": "chart", "kind": "chart", "data": True, "heading": heads, "inner": [],
                    "part": {"type": "chart", "chart": s["chart"], "heading": ex.get("title") or "",
                             "unit": ex.get("unit") or ""}})
    if isinstance(s.get("table"), dict):
        exh.append({"id": "table", "kind": "table", "data": True, "heading": [], "inner": [],
                    "part": {"type": "table", "table": s["table"]}})
    if isinstance(s.get("benchmark"), dict):
        c = _benchmark_chart(s["benchmark"])
        if c:
            exh.append({"id": "benchmark", "kind": "chart", "data": True, "heading": [], "inner": [],
                        "part": {"type": "chart", "chart": c, "heading": ""}})
    for i, pt in enumerate(s.get("points") or []):
        if isinstance(pt, dict) and pt.get("bullets"):
            add(f"points.{i}", "heading", pt.get("lead") or pt.get("text") or pt.get("title"), prov=pt.get("prov"))
            bullets(f"points.{i}.bullets", pt.get("bullets"))
        else:
            add(f"points.{i}", "body", pt.get("lead") if isinstance(pt, dict) and pt.get("lead") else pt)
    if s.get("decision"):
        add("decision", "callout", s["decision"])
    for i, st in enumerate(s.get("steps") or []):
        if isinstance(st, dict):
            for k, v in st.items():
                if k not in ("prov", "id", "icon", "node") and isinstance(v, str) and v.strip():
                    add(f"steps.{i}.{k}", "heading" if k in ("title", "action", "step") else "body", v,
                        prov=st.get("prov"))
        else:
            add(f"steps.{i}", "body", st)
    for i, pt in enumerate(s.get("parts") or []):
        if not isinstance(pt, dict):
            continue
        pid, kind = f"parts.{i}", pt.get("type")
        if pt.get("heading"):
            add(f"{pid}.heading", "heading", pt["heading"], required=kind in ("text", "stats"))
        if kind == "stats":
            for j, it in enumerate(pt.get("items") or []):
                if isinstance(it, dict):
                    add(f"{pid}.items.{j}.value", "figure", it.get("value"), prov=it.get("prov"))
                    add(f"{pid}.items.{j}.label", "label", it.get("label"), prov=it.get("prov"))
                    if it.get("delta"):
                        add(f"{pid}.items.{j}.delta", "label", it.get("delta"), prov=it.get("prov"))
        elif kind == "text":
            if pt.get("lead"):
                add(f"{pid}.lead", "body", pt["lead"], prov=pt.get("prov"))
            bullets(f"{pid}.bullets", pt.get("bullets"))
        elif kind == "callout":
            add(f"{pid}.text", "callout", pt.get("text") or "", prov=pt.get("prov"))
        elif kind == "quote":
            add(f"{pid}.text", "callout", pt.get("text") or pt.get("quote") or "", prov=pt.get("prov"))
            if pt.get("attribution"):
                add(f"{pid}.attribution", "label", pt["attribution"])
        else:                                  # chart, table, harvey, matrix2x2, process, timeline: a native exhibit
            inner = []
            items = pt.get("steps") or pt.get("milestones") or pt.get("items") or []
            if kind in ("process", "timeline"):
                for j, it in enumerate(items):
                    for k in ("date", "title", "label", "text"):
                        v = it.get(k) if isinstance(it, dict) else (it if k == "title" else None)
                        if v:
                            add(f"{pid}.items.{j}.{k}", "heading" if k in ("title", "label") else "body", v,
                                parent=pid)
                            inner.append(f"{pid}.items.{j}.{k}")
            exh.append({"id": pid, "kind": "table" if kind == "table" else "chart" if kind == "chart" else kind,
                        "data": kind not in ("process", "timeline"), "heading": [f"{pid}.heading"] if
                        pt.get("heading") else [], "inner": inner, "part": dict(pt)})
    return atoms, exh


def content_digest(s: dict) -> str:
    keep = {k: v for k, v in s.items() if k not in ("notes", "_design", "tags", "section_icon")}
    return hashlib.sha1(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ layouts and the design prompt
LAYOUTS = {
    "big_number_hero": "one hero figure set very large on the left third, with 2-3 supporting cards on the right",
    "split_chart_insights": "the chart on the left 60%, a column of 2-3 insight cards on the right, each a bold "
                            "heading and one line",
    "stat_grid": "a row or 2x2 grid of stat tiles (big number, short label), with the explanation below them",
    "comparison_columns": "2-4 parallel columns, each with an icon, a heading and its points, divided by thin rules",
    "timeline": "a horizontal timeline with milestones on a line, dates above and labels below",
    "process_chevrons": "a row of chevrons for the steps, each with its explanation underneath",
    "matrix": "a 2x2 or scored matrix as the exhibit, the so-what in a dark side panel",
    "pyramid": "a stacked hierarchy (largest block at the base), each tier labelled, the reading on the right",
    "table_callout": "a clean hairline table across the page with a highlighted row and a callout band below",
    "icon_rows": "3-5 horizontal rows, each an icon in a circle, a bold lead and one line of support",
    "numbered_messages": "3-5 numbered key messages stacked down the page, each a number badge, a bold lead and a "
                         "line, the decision in a dark band at the bottom",
    "roadmap_steps": "numbered steps across the page as cards linked by a thin line, owner and timing under each",
    "chart_hero_band": "the chart across the full width, the takeaway in a dark band beneath it",
}


def layout_options(s: dict) -> list[str]:
    """Layouts that fit the slide's message and content, best first."""
    atoms, exh = content_model(s)
    kinds = {e["part"].get("type") for e in exh}
    stats = [a for a in atoms if a["role"] == "figure"]
    texts = len([a for a in atoms if a["role"] in ("body", "callout")])
    a = s.get("archetype")
    if a == "executive_summary":
        return ["numbered_messages", "icon_rows", "comparison_columns"]
    if a in ("closing", "next_steps"):
        return ["roadmap_steps", "process_chevrons", "numbered_messages"]
    out: list[str] = []
    if kinds & {"process"}:
        out += ["process_chevrons", "roadmap_steps"]
    if kinds & {"timeline"}:
        out += ["timeline", "roadmap_steps"]
    if kinds & {"harvey", "matrix2x2"}:
        out += ["matrix", "table_callout"]
    if "table" in kinds:
        out += ["table_callout", "split_chart_insights"]
    if "chart" in kinds:
        out += ["split_chart_insights", "chart_hero_band"] + (["big_number_hero"] if stats else [])
    if stats:
        out += ["big_number_hero", "stat_grid"] if len(stats) <= 3 else ["stat_grid", "big_number_hero"]
    if texts >= 3:
        out += ["comparison_columns", "icon_rows", "pyramid", "numbered_messages"]
    out += ["comparison_columns", "icon_rows", "stat_grid", "split_chart_insights"]
    return list(dict.fromkeys(out))


def pick_layout(s: dict, recent: list[str]) -> str:
    """The best-fitting layout not used on the last three slides (the first option when all were used)."""
    opts = layout_options(s)
    return next((o for o in opts if o not in recent[-3:]), opts[0])


STYLE_GUIDE = """Consulting style guide (top-tier strategy firm):
- Action title top-left in the title band, one or two lines, bold, the text colour; nothing above it.
- ONE message per page; every element proves the title. Generous white space, aligned to a 24-column grid with
  0.6 in side margins; elements share edges and baselines.
- Flat colours only from the palette; ONE highlight colour (the accent) for the single thing the eye must find;
  everything else in the text colour, greys and the dark brand tone.
- Charts look native to PowerPoint: flat bars, data labels on the bars, no gridlines, no 3D, no shadows.
- Thin line icons in circles, at most 4, only where they carry meaning. No stock photos, no people, no logos, no
  clip art, no gradients, no decorative swooshes, no emoji.
- Body text at least 12 pt equivalent; numbers in bold; short lines; no paragraph longer than 3 lines.
- Leave the bottom 0.9 in empty except a small source line at the bottom left and the page number at the bottom right."""


def direction(theme=None, density: str | None = None, look: str = "") -> dict:
    """The deck's design direction for the prompts: palette hexes by role, fonts, look, density."""
    from .template import theme_spec
    sp = theme_spec(theme)
    c = sp["colors"]
    roles = {"dk1": "text and titles", "lt1": "background", "dk2": "dark brand tone (bands, dark panels)",
             "lt2": "light panel grey", "accent1": "the one highlight / emphasis colour", "accent2": "warm highlight "
             "(a fall, a risk)", "accent3": "focus item in a chart", "accent4": "secondary series",
             "accent5": "hairline rules", "accent6": "muted text"}
    return {"colors": {k: c[k] for k in SLOTS}, "roles": roles, "fonts": sp["fonts"], "density": density or "balanced",
            "look": look or "clean, editorial consulting page: white background, hairline rules, light grey panels",
            "canvas_in": list(CANVAS), "margins_in": {"left": R.LEFT, "right": round(CANVAS[0] - R.RIGHT, 3),
                                                      "body_top": R.BODY_Y, "body_bottom": R.BODY_B}}


def _chart_lines(c: dict) -> list[str]:
    out = [f"kind {c.get('kind', 'column')}, unit {c.get('unit', '')}".strip()]
    for se in c.get("series") or []:
        pairs = ", ".join(f'"{cat}" = {v}' for cat, v in zip(c.get("categories") or [], se.get("values") or []))
        out.append(f'series "{se.get("name", "")}": {pairs}')
    if c.get("highlight_categories"):
        out.append("highlight: " + ", ".join(f'"{h}"' for h in c["highlight_categories"]))
    return out


def design_prompt(s: dict, direc: dict, recent: list[str], layout: str | None = None) -> str:
    """The image-generation prompt for ONE slide: the exact text and numbers (quoted), a layout that fits the message
    and avoids the layouts of the previous slides, the palette, fonts and the style guide."""
    layout = layout or pick_layout(s, recent)
    atoms, exh = content_model(s)
    pal = "; ".join(f"#{direc['colors'][k]} {direc['roles'][k]}" for k in SLOTS)
    lines = [f"Design ONE 16:9 presentation slide (1536 x 864 px, a {CANVAS[0]} x {CANVAS[1]} in page) for a "
             "top-tier strategy consulting deck. It must look designed by a senior visual designer: a distinctive "
             "composition for this message, not a generic template.",
             f"Layout: {layout.replace('_', ' ')}: {LAYOUTS.get(layout, '')}."]
    avoid = [r for r in dict.fromkeys(recent[-3:]) if r and r != layout]
    if avoid:
        lines.append("Do NOT use these layouts (the previous slides use them): "
                     + ", ".join(f"{r.replace('_', ' ')} ({LAYOUTS.get(r, '')})" for r in avoid) + ".")
    lines += [f"Palette (use only these, flat): {pal}.",
              f"Fonts: headings {direc['fonts']['major']}, body {direc['fonts']['minor']}. Look: {direc['look']}. "
              f"Density: {direc['density']} (fewer, larger elements when presenting).",
              f"Margins: {direc['margins_in']['left']} in left and right; body between "
              f"{direc['margins_in']['body_top']} in and {direc['margins_in']['body_bottom']} in from the top.",
              STYLE_GUIDE,
              "Show EXACTLY the text below, verbatim, spelled as given, and NO other words, labels, numbers or "
              "placeholder text:",
              f'TITLE (top-left): "{plain(s.get("title", ""))}"']
    if s.get("subtitle"):
        lines.append(f'SUBTITLE (under the title, regular weight): "{plain(s["subtitle"])}"')
    for a in atoms:
        if a["parent"]:
            continue
        lines.append(f'{a["role"].upper()}: "{plain(a["text"])}"')
    for e in exh:
        p = e["part"]
        if e["kind"] == "chart" and isinstance(p.get("chart"), dict):
            lines.append("CHART (draw exactly these values, labelled): " + "; ".join(_chart_lines(p["chart"])))
        elif e["kind"] == "table":
            t = p.get("table") if isinstance(p.get("table"), dict) else p
            lines.append("TABLE columns " + " | ".join(f'"{plain(c)}"' for c in t.get("columns") or []) + "; rows: "
                         + " / ".join(" | ".join(f'"{plain(R._txt(c))}"' for c in (r if isinstance(r, list) else [r]))
                                      for r in t.get("rows") or []))
        elif e["kind"] in ("harvey", "matrix2x2"):
            lines.append(f"{e['kind'].upper()} exhibit: " + json.dumps({k: v for k, v in p.items()
                                                                       if k not in ("prov", "type")})[:900])
        else:
            items = [plain(a["text"]) for a in atoms if a["parent"] == e["id"]]
            lines.append(f"{e['kind'].upper()} steps: " + " -> ".join(f'"{t}"' for t in items))
    src = (s.get("source") or {}).get("text") if isinstance(s.get("source"), dict) else s.get("source")
    if src:
        lines.append(f'SOURCE (bottom left, small): "{plain(src)}"')
    return "\n".join(lines)


# ------------------------------------------------------------------ the image model
def _post(url: str, body: dict, timeout: float) -> tuple[int, dict]:
    """POST JSON; (status, payload). The proxy injects the gateway's key; a configured key is sent when present."""
    from meshpool_harness import providers as PV
    key = PV.key() or "dummy"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, st = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, st = e.read(), e.code
    except Exception as e:  # noqa: BLE001 - timeouts, resets, DNS: the caller reports not_run
        return 0, {"error": f"{type(e).__name__}: {e}"[:300]}
    try:
        return st, json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return st, {"error": raw[:300].decode("utf-8", "replace")}


def _get_bytes(url: str, timeout: float) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()
    except Exception:  # noqa: BLE001
        return None


def to_16x9(data: bytes) -> tuple[bytes, tuple[int, int]]:
    """Centre-crop to 16:9 (an API that only offers 3:2 gives a slightly taller picture); PNG bytes and size."""
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = im.size
    if abs(w / h - 16 / 9) > 0.01:
        if w / h > 16 / 9:
            nw = int(round(h * 16 / 9))
            im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
        else:
            nh = int(round(w * 9 / 16))
            im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue(), im.size


def _image_from_chat(payload: dict) -> str | None:
    """An image in a chat-completions reply: message.images[].image_url.url, image content parts, or a data URL."""
    try:
        msg = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None
    for im in msg.get("images") or []:
        u = (im.get("image_url") or {}).get("url") if isinstance(im, dict) else None
        if u:
            return u
    c = msg.get("content")
    if isinstance(c, list):
        for p in c:
            if isinstance(p, dict):
                u = (p.get("image_url") or {}).get("url") if isinstance(p.get("image_url"), dict) else p.get("image_url")
                u = u or p.get("b64_json") or p.get("image")
                if u:
                    return u
    if isinstance(c, str):
        m = re.search(r"data:image/[a-z]+;base64,[A-Za-z0-9+/=]+", c)
        if m:
            return m.group(0)
    return None


def _decode(u: str, timeout: float) -> bytes | None:
    if u.startswith("data:"):
        return base64.b64decode(u.split(",", 1)[1])
    if u.startswith("http"):
        return _get_bytes(u, timeout)
    try:
        return base64.b64decode(u)
    except Exception:  # noqa: BLE001
        return None


class ImageClient:
    """Image generation through the OpenAI-compatible gateway. generate() never raises: {"status": "ok", "png",
    "size", "via", "model"} or {"status": "not_run", "why"}. `post` is the transport (tests stub it)."""

    def __init__(self, model: str = DEFAULTS["image_model"], base: str | None = None, timeout: float = 180,
                 post=None, sizes: list[str] | None = None):
        from meshpool_harness import providers as PV
        self.model, self.timeout = model, timeout
        self.base = (base or PV.key_base()).rstrip("/")
        self.post = post or _post
        self.sizes = sizes or [DEFAULTS["size"]] + list(DEFAULTS["fallback_sizes"])
        self.calls: list[dict] = []

    def generate(self, prompt: str) -> dict:
        why = ""
        fmt = True
        for size in self.sizes:
            body = {"model": self.model, "prompt": prompt[:30000], "size": size, "n": 1}
            if fmt:
                body["response_format"] = "b64_json"
            st, pl = self.post(f"{self.base}/images/generations", body, self.timeout)
            self.calls.append({"endpoint": "images/generations", "size": size, "status": st})
            if st == 200:
                d = (pl.get("data") or [{}])[0] if isinstance(pl, dict) else {}
                raw = _decode(d.get("b64_json") or d.get("url") or "", self.timeout)
                if raw:
                    png, wh = to_16x9(raw)
                    return {"status": "ok", "png": png, "size": wh, "via": "images", "model": self.model,
                            "asked_size": size, "usage": pl.get("usage")}
                why = "the image API answered without an image"
                break
            msg = json.dumps(pl)[:400]
            why = f"images/generations HTTP {st}: {msg}"
            if st == 400 and "response_format" in msg and fmt:      # gpt-image models always answer in b64_json
                fmt = False
                st, pl = self.post(f"{self.base}/images/generations", {k: v for k, v in body.items()
                                                                       if k != "response_format"}, self.timeout)
                self.calls.append({"endpoint": "images/generations", "size": size, "status": st})
                if st == 200:
                    d = (pl.get("data") or [{}])[0]
                    raw = _decode(d.get("b64_json") or d.get("url") or "", self.timeout)
                    if raw:
                        png, wh = to_16x9(raw)
                        return {"status": "ok", "png": png, "size": wh, "via": "images", "model": self.model,
                                "asked_size": size, "usage": pl.get("usage")}
                msg = json.dumps(pl)[:400]
                why = f"images/generations HTTP {st}: {msg}"
            if st == 400 and re.search(r"(?i)size", msg):           # try the next size the API may allow
                continue
            break
        if why and re.search(r"HTTP (0|401|403|429|5\d\d)", why):   # unreachable or refused: no second endpoint
            return {"status": "not_run", "why": why}
        # the gateway may expose the image model only through chat completions with image output
        st, pl = self.post(f"{self.base}/chat/completions",
                           {"model": self.model, "max_tokens": 1024, "modalities": ["image", "text"],
                            "messages": [{"role": "user", "content": prompt[:30000]}]}, self.timeout)
        self.calls.append({"endpoint": "chat/completions", "status": st})
        if st == 200:
            u = _image_from_chat(pl)
            raw = _decode(u, self.timeout) if u else None
            if raw:
                png, wh = to_16x9(raw)
                return {"status": "ok", "png": png, "size": wh, "via": "chat", "model": self.model,
                        "usage": pl.get("usage")}
            why = (why + "; " if why else "") + "chat completions answered without an image"
        else:
            why = (why + "; " if why else "") + f"chat/completions HTTP {st}: {json.dumps(pl)[:300]}"
        return {"status": "not_run", "why": why}


# ------------------------------------------------------------------ reconstruction (the vision model)
RECON = """You turn a picture of ONE designed presentation slide back into an editable layout. You get the picture
and the slide's content (atoms with ids, exhibits with ids). Return ONLY JSON:
{"layout": "<short name>", "elements": [ ... ]}
Canvas: 13.333 x 7.5 in, origin top-left. Every bbox is [x, y, w, h] in inches, read off the picture.
Element types and fields:
- {"type": "rect"|"rounded_rect"|"ellipse"|"chevron"|"pentagon", "bbox", "z", "fill": "#hex"|null,
   "line": "#hex"|null}                      panels, cards, bands, chevrons (shapes that hold text: give them)
- {"type": "text", "bbox", "z", "runs": [{"ref": "<atom id>", "text": "<copied exactly from that atom>",
   "role": "figure|heading|body|label|callout", "size": pt, "bold": bool, "color": "#hex",
   "align": "left|center|right", "anchor": "top|middle|bottom"}]}      one run per paragraph, top to bottom
- {"type": "line", "bbox", "z", "color": "#hex"}       horizontal or vertical hairlines only
- {"type": "icon", "bbox", "z", "meaning": "<what it stands for, 1-3 words>", "color": "#hex",
   "style": "disc"|"plain"}                   disc = the icon in a filled circle
- {"type": "badge", "bbox", "z", "number": 1-9, "fill": "#hex"}    a small numbered circle
- {"type": "chart"|"table", "bbox", "z", "ref": "<exhibit id>"}     the area the exhibit occupies (drawn natively
   from the content's data; do not transcribe its labels or bars as text)
Rules:
- Text comes ONLY from the content atoms: reference the atom by id and copy its text exactly; never write words
  that are not in an atom (the title, subtitle, source line and page number are drawn by the frame: leave them out).
- Every atom and exhibit should appear once; where the picture lost or garbled one, place it where it belongs.
- Colours are read from the picture as hex; z is the stacking order (backgrounds lowest).
- Keep elements inside x 0.6-12.73 in and y 1.55-6.62 in; leave 0.1 in or more between separate elements."""


def recon_material(png: bytes, s: dict, direc: dict, size: tuple[int, int]) -> list[dict]:
    atoms, exh = content_model(s)
    content = {"title": plain(s.get("title", "")),
               "atoms": [{"id": a["id"], "role": a["role"], "text": plain(a["text"])} for a in atoms],
               "exhibits": [{"id": e["id"], "kind": e["kind"]} for e in exh],
               "palette": {k: "#" + v for k, v in direc["colors"].items()}, "image_px": list(size)}
    return [{"type": "text", "text": "Slide content:\n" + json.dumps(content, ensure_ascii=False)},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}]


def _loads(txt: str) -> dict:
    txt = txt or ""
    a, b = txt.find("{"), txt.rfind("}")
    return json.loads(txt[a:b + 1]) if a >= 0 and b > a else {}


def reconstruct(client, png: bytes, s: dict, direc: dict, models: list[str], size=(1536, 864), timeout: float = 240,
                task_id: str = "design") -> dict:
    """The picture back to a raw layout spec: the first vision model that answers with JSON wins. Never raises."""
    why = []
    for m in models:
        box: dict = {}

        def call():
            try:
                r = client.chat(model=m, messages=[{"role": "system", "content": RECON},
                                                   {"role": "user", "content": recon_material(png, s, direc, size)}],
                                max_tokens=8000, temperature=0.1, task_id=task_id, role="worker",
                                extra={"response_format": {"type": "json_object"}})
                box["raw"] = _loads(r["choices"][0]["message"].get("content") or "")
            except Exception as e:  # noqa: BLE001
                box["err"] = f"{type(e).__name__}: {e}"[:200]
        th = threading.Thread(target=call, daemon=True)
        th.start()
        th.join(timeout)
        if th.is_alive():
            why.append(f"{m}: timed out after {timeout:.0f}s")
            continue
        if box.get("raw", {}).get("elements"):
            return {"status": "ok", "raw": box["raw"], "model": m}
        why.append(f"{m}: {box.get('err') or 'no elements in the reply'}")
    return {"status": "not_run", "why": "; ".join(why) or "no vision model configured"}


# ------------------------------------------------------------------ normalising a spec
def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def lum(h: str) -> float:
    r, g, b = (c / 255 for c in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def nearest_slot(v, colors: dict) -> str | None:
    """A colour from the picture (hex, or a slot name) -> the nearest theme slot, so the page recolours with the
    theme. None/"none"/"transparent" stay None."""
    if v is None:
        return None
    v = str(v).strip()
    if v.lower() in ("", "none", "null", "transparent", "no"):
        return None
    if v in colors or v in SLOTS:
        return v
    m = re.fullmatch(r"#?([0-9A-Fa-f]{6})", v)
    if not m:
        return None
    r, g, b = _rgb(m.group(1))

    def dist(k):                                          # weighted RGB distance ("redmean")
        r2, g2, b2 = _rgb(colors[k])
        rm = (r + r2) / 2
        return (2 + rm / 256) * (r - r2) ** 2 + 4 * (g - g2) ** 2 + (2 + (255 - rm) / 256) * (b - b2) ** 2
    return min((k for k in SLOTS if k in colors), key=dist)


def _bbox(v, scale: float) -> list[float] | None:
    if isinstance(v, dict):
        v = [v.get("x"), v.get("y"), v.get("w", v.get("width")), v.get("h", v.get("height"))]
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        return None
    try:
        return [float(a) * scale for a in v]
    except (TypeError, ValueError):
        return None


def _clamp(b: list[float]) -> tuple[list[float] | None, bool]:
    x0, y0, x1, y1 = BODY
    x, y, w, h = b
    nx, ny = max(x0, min(x, x1)), max(y0, min(y, y1))
    nr, nb = max(x0, min(x + w, x1)), max(y0, min(y + h, y1))
    out = [nx, ny, nr - nx, nb - ny]
    changed = any(abs(a - c) > 1e-6 for a, c in zip(out, b))
    if out[2] < 0.1 and out[3] < 0.1 or out[2] <= 0.02 or out[3] < 0:
        return None, True
    return out, changed


def bind(text: str, atoms: list[dict], s: dict) -> tuple[str | None, str]:
    """A text run from the picture -> the atom it shows: exact text, or a long enough piece of exactly one atom.
    ("frame", "") for the title/subtitle/source (the frame draws those); (None, "") when it is not in the content."""
    k = _key(text)
    if not k:
        return None, ""
    frame = [s.get("title"), s.get("subtitle"), _t(s.get("source"))] + list(s.get("footnotes") or [])
    for f in frame:
        fk = _key(_t(f))
        if fk and (k == fk or (len(k) >= 12 and k in fk) or re.sub(r"^source:\s*", "", k) == fk):
            return "frame", ""
    exact = [a for a in atoms if _key(a["text"]) == k]
    if exact:
        return exact[0]["id"], "exact"
    part = [a for a in atoms if k in _key(a["text"]) and (len(k) >= 25 or len(k) >= 0.5 * len(_key(a["text"])))]
    if len(part) == 1:
        return part[0]["id"], "part"
    return None, ""


def normalize_spec(raw: dict, s: dict, colors: dict, img_size: tuple[int, int] | None = None) -> tuple[dict, dict]:
    """The vision model's JSON -> a clean spec: known types only, bboxes in inches clamped to the body band (pixels
    are scaled), colours mapped to theme slots, every text run bound to a content atom (text that is not in the
    content is dropped and reported), exhibits bound to the slide's charts and tables, icons capped at 4."""
    atoms, exh = content_model(s)
    ids = {a["id"] for a in atoms}
    eids = {e["id"]: e for e in exh}
    probs: dict = {"invented": [], "clamped": 0, "dropped": [], "unbound_exhibits": [], "duplicates": []}
    els = [e for e in (raw or {}).get("elements") or [] if isinstance(e, dict)]
    coords = [abs(float(v)) for e in els for v in (e.get("bbox") if isinstance(e.get("bbox"), list) else [])
              if isinstance(v, (int, float))]
    scale = CANVAS[0] / float((img_size or (1536, 864))[0]) if coords and max(coords) > 40 else 1.0   # pixels
    out, used, icons = [], set(), 0
    for k, e in enumerate(els):
        t = str(e.get("type") or "").strip().lower().replace("-", "_").replace(" ", "_")
        t = TYPE_ALIASES.get(t, t)
        if t not in TYPES:
            probs["dropped"].append(f"element {k}: unknown type '{e.get('type')}'")
            continue
        b = _bbox(e.get("bbox"), scale)
        if b is None:
            probs["dropped"].append(f"element {k}: no bbox")
            continue
        b, ch = _clamp(b)
        probs["clamped"] += int(ch)
        if b is None:
            probs["dropped"].append(f"element {k}: outside the body band")
            continue
        try:
            z = int(e.get("z", e.get("z_order", k)))
        except (TypeError, ValueError):
            z = k
        el = {"type": t, "bbox": [round(v, 3) for v in b], "z": z}
        if t in SHAPES or t == "badge":
            el["fill"] = nearest_slot(e.get("fill"), colors)
            el["line"] = nearest_slot(e.get("line"), colors)
            if t == "badge":
                try:
                    el["number"] = max(1, min(9, int(e.get("number") or 1)))
                except (TypeError, ValueError):
                    el["number"] = 1
                el["fill"] = el["fill"] or "accent1"
        if t == "line":
            el["color"] = nearest_slot(e.get("color") or e.get("line") or e.get("fill"), colors) or "accent5"
        if t == "icon":
            if icons >= MAX_ICONS:
                probs["dropped"].append(f"element {k}: more than {MAX_ICONS} icons")
                continue
            icons += 1
            el["meaning"] = str(e.get("meaning") or e.get("name") or "circle")[:40]
            el["color"] = nearest_slot(e.get("color"), colors) or "dk1"
            el["style"] = "disc" if str(e.get("style", "")).lower() in ("disc", "circle", "filled") else "plain"
        if t in ("chart", "table"):
            ref = str(e.get("ref") or "")
            ex = eids.get(ref) or next((x for x in exh if x["kind"] == t and x["id"] not in used), None) or \
                next((x for x in exh if x["id"] not in used and x["data"]), None)
            if ex is None or ex["id"] in used:
                probs["unbound_exhibits"].append(ref or t)
                continue
            el["ref"] = ex["id"]
            used.add(ex["id"])
        if t == "text":
            runs = e.get("runs") or e.get("paragraphs") or ([{"text": e.get("text"), "role": e.get("role")}]
                                                           if e.get("text") else [])
            good = []
            for r in runs if isinstance(runs, list) else []:
                if not isinstance(r, dict):
                    r = {"text": str(r)}
                ref = str(r.get("ref") or "")
                if ref not in ids:
                    ref, _how = bind(str(r.get("text") or ""), atoms, s)
                    if ref == "frame":
                        continue
                    if ref is None:
                        probs["invented"].append(plain(str(r.get("text") or ""))[:80])
                        continue
                elif r.get("text") and _key(r["text"]) not in _key(next(a["text"] for a in atoms if a["id"] == ref)):
                    bound, _how = bind(str(r["text"]), atoms, s)   # the id and the words disagree: trust the words
                    if bound not in (None, "frame"):
                        ref = bound
                if ref in used:
                    probs["duplicates"].append(ref)
                    continue
                used.add(ref)
                role = str(r.get("role") or "").lower()
                good.append({"ref": ref, "role": role if role in ROLES else None,
                             "size": r.get("size") if isinstance(r.get("size"), (int, float)) else None,
                             "bold": r.get("bold") if isinstance(r.get("bold"), bool) else None,
                             "color": nearest_slot(r.get("color"), colors),
                             "align": str(r.get("align") or "left").lower(),
                             "anchor": str(r.get("anchor") or "top").lower()})
            if not good:
                continue
            el["runs"] = good
        out.append(el)
    spec = {"layout": str((raw or {}).get("layout") or "")[:40], "elements": out}
    return spec, probs


def coverage(spec: dict, s: dict) -> list[str]:
    """Required atoms and exhibits of the slide that the spec does not place (an exhibit's inner atoms count as
    placed when the exhibit is; a text-only exhibit counts as placed when all its atoms are)."""
    atoms, exh = content_model(s)
    placed = {r["ref"] for e in spec.get("elements") or [] for r in e.get("runs") or []}
    placed |= {e.get("ref") for e in spec.get("elements") or [] if e.get("ref")}
    missing = []
    for x in exh:
        inner_ok = x["inner"] and all(i in placed for i in x["inner"])
        if x["id"] not in placed and not inner_ok:
            missing.append(x["id"])
        if x["id"] in placed:
            placed |= set(x["inner"])
    for a in atoms:
        if a["required"] and a["id"] not in placed:
            missing.append(a["id"])
    return missing


# ------------------------------------------------------------------ the native rebuild
EDGES_L = [R.colx24(k) for k in range(R.COLS)]
EDGES_R = [R.colx24(k) + R.CW for k in range(R.COLS)]


def snap(x: float, w: float) -> tuple[float, float]:
    """Left edge to the nearest column start, right edge to the nearest column end (at least one column wide)."""
    l_ = min(EDGES_L, key=lambda e: abs(e - x))
    r_ = min(EDGES_R, key=lambda e: abs(e - (x + w)))
    if r_ <= l_:
        r_ = next(e for e in EDGES_R if e > l_)
    return round(l_, 4), round(r_ - l_, 4)


def _inside(a, b, share=0.85) -> bool:
    """bbox a lies (mostly) inside bbox b."""
    ix = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    iy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return ix > 0 and iy > 0 and ix * iy >= share * a[2] * a[3]


def _overlap(a, b, mn=0.05) -> bool:
    ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return ox > mn and oy > mn


def _floors(s: dict, rules: dict | None = None) -> dict:
    from .common import load_rules
    tf = (rules or load_rules())["pptx"]["text_fit"]
    body = tf.get("min_dense_pt", 10) if s.get("archetype") in tf.get("dense_archetypes", []) else tf["min_body_pt"]
    return {"figure": max(18, body), "heading": body, "body": body, "label": body, "callout": body, "footnote": 8}


def scales(s: dict) -> dict:
    """Allowed sizes per role, largest first. Body roles use only the deck's own type scale (render.T in the
    density), so a designed page adds no body size P4 would count; figures sit in numeral shapes (not counted) and
    take a hero scale."""
    fl = _floors(s)
    T = R.T
    sc = {"figure": [60, 54, 44, 40, 36, 32, 28, 24, 20],
          "heading": [T["head"], T["body"], T["label"], 11, 10],
          "body": [T["body"], T["label"], 11, 10],
          "label": [T["label"], 11, 10],
          "callout": [T["sub"], T["head"], T["body"], T["label"], 11, 10],
          "footnote": [9, 8]}
    return {k: sorted({z for z in v if z >= fl[k]}, reverse=True) or [fl[k]] for k, v in sc.items()}


DEFAULT_SIZE = {"figure": 4, "heading": 0, "body": 0, "label": 0, "callout": 1, "footnote": 0}   # scale index


def _nearest(scale: list, size) -> int:
    if not isinstance(size, (int, float)):
        return None
    return min(range(len(scale)), key=lambda i: abs(scale[i] - size))


def _plan(spec: dict, s: dict) -> tuple[list[dict], list[dict]]:
    """Spec -> drawable items. Shapes and boxes snapped to the grid; text that sits inside a filled shape is
    written into that shape (stacked texts become its paragraphs); free text keeps its own box."""
    atoms, exh = content_model(s)
    amap = {a["id"]: a for a in atoms}
    els = [dict(e, bbox=list(e["bbox"])) for e in spec.get("elements") or []]
    for e in els:                                         # runs whose atom is gone (condensed or rewritten): skip
        if e["type"] == "text":
            e["runs"] = [dict(r) for r in e["runs"] if r["ref"] in amap]
    els = [e for e in els if e["type"] != "text" or e["runs"]]
    holders = [e for e in els if e["type"] in SHAPES]   # filled cards and outlined boxes hold their text
    for e in els:
        if e["type"] != "text":
            continue
        cands = [h for h in holders if _inside(e["bbox"], h["bbox"])]
        if not cands:                                    # half inside a card: pulled in, never left straddling
            half = [h for h in holders if _inside(e["bbox"], h["bbox"], 0.5)]
            if half:
                h = min(half, key=lambda h: h["bbox"][2] * h["bbox"][3])
                x0, y0 = max(e["bbox"][0], h["bbox"][0] + 0.08), max(e["bbox"][1], h["bbox"][1] + 0.05)
                x1 = min(e["bbox"][0] + e["bbox"][2], h["bbox"][0] + h["bbox"][2] - 0.08)
                y1 = min(e["bbox"][1] + e["bbox"][3], h["bbox"][1] + h["bbox"][3] - 0.05)
                e["bbox"] = [x0, y0, max(0.2, x1 - x0), max(0.15, y1 - y0)]
                cands = [h]
        if cands:
            e["_in"] = id(min(cands, key=lambda h: h["bbox"][2] * h["bbox"][3]))
    by_id = {id(h): h for h in holders}
    groups: dict[int, list] = {}
    for e in els:
        if e.get("_in"):
            groups.setdefault(e["_in"], []).append(e)
    merged = set()
    for hid, texts in groups.items():
        h = by_id[hid]
        texts.sort(key=lambda e: e["bbox"][1])
        stacked = all(texts[i]["bbox"][1] + texts[i]["bbox"][3] <= texts[i + 1]["bbox"][1] + 0.08
                      for i in range(len(texts) - 1))
        others = [o for o in els if o is not h and o not in texts and o["type"] != "line"
                  and _inside(o["bbox"], h["bbox"], 0.5)]
        ux0 = min(t["bbox"][0] for t in texts)
        ux1 = max(t["bbox"][0] + t["bbox"][2] for t in texts)
        clear = all(o["bbox"][0] >= ux1 - 0.02 or o["bbox"][0] + o["bbox"][2] <= ux0 + 0.02 or
                    o["bbox"][1] >= max(t["bbox"][1] + t["bbox"][3] for t in texts) - 0.02 or
                    o["bbox"][1] + o["bbox"][3] <= texts[0]["bbox"][1] + 0.02 for o in others)
        if stacked and clear:
            h["_texts"] = texts
            merged |= {id(t) for t in texts}
    shapes, texts = [], []
    for e in els:
        if id(e) in merged:
            continue
        if e["type"] in ("icon", "badge", "line"):
            shapes.append(e)
            continue
        x, w = snap(e["bbox"][0], e["bbox"][2])
        y = round(e["bbox"][1] / 0.05) * 0.05
        hgt = max(0.15, round((e["bbox"][1] + e["bbox"][3]) / 0.05) * 0.05 - y)
        e["_orig"] = list(e["bbox"])
        e["box"] = [x, max(BODY[1], y), w, min(hgt, BODY[3] - max(BODY[1], y))]
        (texts if e["type"] == "text" else shapes).append(e)
    _separate([e for e in shapes + texts if "box" in e])
    return shapes, texts


def _separate(items: list[dict]) -> None:
    """Snapping can push neighbours into each other: a box that now overlaps one it did not overlap before (and
    neither holds the other) starts at the next column."""
    for a in items:
        for b in items:
            if a is b or not _overlap(a["box"], b["box"]) or _overlap(a["_orig"], b["_orig"], 0.0):
                continue
            if _inside(a["box"], b["box"], 0.95) or _inside(b["box"], a["box"], 0.95):
                continue
            first, second = (a, b) if a["box"][0] <= b["box"][0] else (b, a)
            if first["box"][0] + first["box"][2] > second["box"][0]:
                edge = first["box"][0] + first["box"][2]
                nl = next((e for e in EDGES_L if e > edge + 0.01), None)
                if nl is not None:
                    right = second["box"][0] + second["box"][2]
                    second["box"][0], second["box"][2] = nl, max(R.CW, right - nl)


def _blocks(runs: list[dict], amap: dict, s: dict, sizes: dict, dark: bool, gaps: list[float] | None = None):
    multi_bullets = sum(1 for r in runs if ".bullets." in r["ref"] or r["ref"].startswith("bullets.")) >= 2
    out = []
    for i, r in enumerate(runs):
        a = amap[r["ref"]]
        role = r.get("role") or a["role"]
        role = "callout" if role == "callout" else role if role in ROLES else "body"
        if a["role"] == "figure":
            role = "figure"
        col = r.get("color")
        color = THEME.get(col) if col else None
        if dark and (col is None or lum_slot(col, s) < 0.5):
            color = TC.BACKGROUND_1                       # text on a dark panel is light, whatever the picture says
        elif color is None:
            color = TC.ACCENT_1 if role == "figure" else TC.TEXT_1
        b = {"text": a["text"], "size": sizes[role], "role": role, "prov": a.get("prov"),
             "bold": r["bold"] if r.get("bold") is not None else role in ("figure", "heading"),
             "color": color, "numbers": role not in ("figure", "heading") and not dark,
             "align": ALIGNS.get(r.get("align"), PP_ALIGN.LEFT)}
        if multi_bullets and (".bullets." in r["ref"] or r["ref"].startswith("bullets.")):
            b["bullet"] = True
        if i:
            g = (gaps[i - 1] if gaps else 0) * 72
            b["space_before"] = max(3, min(36, round(g))) if gaps else (4 if out[-1]["role"] in ("heading", "figure")
                                                                         else 6)
        out.append(b)
    return out


_SLOT_HEX: dict = {}


def lum_slot(slot: str, s: dict | None = None) -> float:
    from .template import COLORS
    return lum((_SLOT_HEX or COLORS).get(slot, "FFFFFF"))


def _fits(blocks, w, h) -> bool:
    if w <= 0.1 or h <= 0.05:
        return False
    for b in blocks:                                      # a word wider than the box would break mid-word
        words = re.findall(r"\S+", plain(b["text"]))
        if words and max(R._need_w(wd, b["size"], b.get("bold", False)) for wd in words) > w * 0.97 - (
                0.2 if b.get("bullet") else 0):
            return False
    return R._need(blocks, w) <= h - 0.02


def _room(it: dict, obstacles: list[dict]) -> float:
    """How far a text item may grow down: to the next element under it (never into it), or the body's foot."""
    x, y, w, h = it["box"]
    limit = BODY[3]
    for o in obstacles:
        if o is it.get("el"):
            continue
        ob = o.get("box") or o.get("bbox")
        if not ob or _inside(it["box"], ob, 0.9):         # its own card does not stop it (the card grows with it)
            continue
        if ob[1] >= y + h - 0.01 and ob[0] < x + w - 0.02 and ob[0] + ob[2] > x + 0.02:
            limit = min(limit, ob[1] - 0.1)
    return max(0.0, limit - (y + h))


def fit(items: list[dict], s: dict, obstacles: list[dict]) -> list[str]:
    """Fit every text item with the renderer's measurer. Peers (the same role in boxes of the same width, as in a
    row of parallel cards) share one size. A box that overflows first grows down into free space; only then its
    peers step down the role's scale, never below the role's floor. Returns what still does not fit."""
    sc = scales(s)

    def gk(it, role):
        return role, round(it["inner"][0] / 0.3)
    idx: dict = {}
    for it in items:                                      # start: the largest size the picture asked for, per peers
        for r in it["runs"]:
            role = it["roles"][r["ref"]]
            i = _nearest(sc[role], r.get("size"))
            i = min(DEFAULT_SIZE[role] if i is None else i, len(sc[role]) - 1)
            idx[gk(it, role)] = min(idx.get(gk(it, role), 99), i)

    def build(it):
        sizes = {k: sc[k][min(len(sc[k]) - 1, idx.get(gk(it, k), DEFAULT_SIZE[k]))] for k in sc}
        return _blocks(it["runs"], it["amap"], s, sizes, it["dark"], it.get("gaps"))

    def grow(it, bl) -> bool:
        need = R._need(bl, it["inner"][0]) + 0.04 - it["inner"][1]
        if need <= 0:
            return True
        if need <= _room(it, obstacles) and _fits(bl, it["inner"][0], it["inner"][1] + need):
            it["box"][3] += need
            it["inner"] = (it["inner"][0], it["inner"][1] + need)
            return True
        return False
    for _ in range(60):
        bad = []
        for it in items:
            bl = build(it)
            if not _fits(bl, *it["inner"]) and not grow(it, bl):
                bad.append(it)
        if not bad:
            break
        stepped = False
        for it in bad:                                    # step down the largest role in each failing box
            roles = sorted({it["roles"][r["ref"]] for r in it["runs"]}, key=lambda k: -sc[k][idx[gk(it, k)]])
            for k in roles:
                if idx[gk(it, k)] < len(sc[k]) - 1:
                    idx[gk(it, k)] += 1
                    stepped = True
                    break
        if not stepped:
            break
    left = []
    for it in items:
        it["blocks"] = build(it)
        if not _fits(it["blocks"], *it["inner"]):
            left.append(f"{it['name']} needs {R._need(it['blocks'], it['inner'][0]):.2f}in, "
                        f"has {it['inner'][1]:.2f}in")
    return left


def _shape(sl, kind, box, name, fill, line):
    x, y, w, h = box
    shp = sl.shapes.add_shape(SHAPES[kind], Inches(x), Inches(y), Inches(w), Inches(h))
    shp.name = name
    R._strip_style(shp)
    if fill:
        shp.fill.solid()
        shp.fill.fore_color.theme_color = THEME[fill]
    else:
        shp.fill.background()
    if line:
        shp.line.color.theme_color = THEME[line]
        shp.line.width = Pt(0.75)
    else:
        shp.line.fill.background()
    if kind == "rounded_rect":
        shp.adjustments[0] = min(0.5, 0.08 / max(0.1, min(w, h)))
    return shp


def _icon(sl, el, k):
    """A library icon: in a filled disc (white glyph) or plain in its colour. Alt text icon:<set>:<name>:<meaning>."""
    from .template import COLORS
    hexes = _SLOT_HEX or COLORS
    x, y, w, h = el["bbox"]
    d = min(w, h, 1.0)
    x, y = x + (w - d) / 2, y + (h - d) / 2
    name = R.icon_name(el["meaning"])
    glyph = hexes.get(el["color"], "0A1A2F")
    pad = d * 0.18
    if el.get("style") == "disc":
        disc = _shape(sl, "ellipse", (x, y, d, d), f"ov_IconDisc{k}", el["color"], None)
        R._alt(disc, f"circle behind the {el['meaning']} icon")
        glyph = "FFFFFF" if lum(hexes.get(el["color"], "000000")) < 0.6 else hexes.get("dk1", "0A1A2F")
        pad = d * 0.24
    iset = R.ICON_SET.get("name")
    if iset is None:
        png, svg, label = R._icon_png(name, glyph), None, f"icon:lucide:{name}:{el['meaning']}"
    else:
        iset, nm = R.icon_ref(name, iset)
        svg = R._icon_svg(nm, glyph, iset) if nm else None
        png = (R._icon_png(nm, glyph) if iset == "lucide" else None) or (R._set_png(nm, glyph, iset) if svg else None)
        label = f"icon:{iset}:{nm}:{el['meaning']}"
    if not png:
        return
    pic = sl.shapes.add_picture(png, Inches(x + pad), Inches(y + pad), Inches(d - 2 * pad), Inches(d - 2 * pad))
    pic.name = f"ov_Icon{k}_{name}"
    R._alt(pic, label)
    if svg:
        R._embed_svg(pic, svg)


TABLE_PAD = 0.16      # air a designed table's row may take past its text (render.TABLE_PADS; P20 allows 0.25in)


def _reflow_tables(ctx, sl, n, shapes, texts, emap) -> None:
    """A native table is as tall as its rows, often shorter than the area the picture gave it: what sits under it
    moves up by the difference and stretches to keep its foot (no dead band between the table and the rest)."""
    for e in shapes:
        if e["type"] != "table" or e.get("ref") not in emap:
            continue
        part = emap[e["ref"]]["part"]
        t = dict(part.get("table") if isinstance(part.get("table"), dict) else part)
        t["columns"] = [R._txt(c) for c in t.get("columns") or []]
        t["rows"] = [[R._cell(c) for c in (r if isinstance(r, list) else [r])] for r in t.get("rows") or []]
        t["_pad"] = TABLE_PAD
        x, y, w, h = e["box"]
        head = 0.5 if part.get("heading") else 0.0
        ctx.carry_rows = None
        try:
            need = R._table(ctx, sl, n, t, x, y, w, h - head, dry=True) + head + 0.08
        except Exception:  # noqa: BLE001 - measured again when drawn; a failure there is a DesignMiss
            continue
        finally:
            ctx.carry_rows = None
        delta = h - need
        if need == float("inf") or delta < 0.2:
            continue
        e["box"][3] = need
        bottom = y + h
        for o in shapes + texts:
            if o is e:
                continue
            b = o.get("box") or o["bbox"]
            if b[1] < bottom - 0.05 or b[0] >= x + w - 0.02 or b[0] + b[2] <= x + 0.02:
                continue
            b[1] -= delta
            if o["type"] in SHAPES or o["type"] == "text":
                b[3] += delta                                # keeps its foot: a band under the table grows
            for tt in o.get("_texts") or []:              # its text keeps its insets: the area grows with it
                tt["bbox"][1] -= delta
                tt["bbox"][3] += delta / len(o["_texts"])
            if "_orig" in o:
                o["_orig"][1] -= delta


def draw_design(ctx, s: dict, sl, n: int, spec: dict) -> dict:
    """Rebuild one slide from its spec, natively. The frame (title, footer) is the deck's own; the body is the
    spec's composition. Raises DesignMiss when the page would lose content or cannot fit its text."""
    from . import parts as PT
    missing = coverage(spec, s)
    if missing:
        raise DesignMiss("the design leaves out " + ", ".join(missing[:6]))
    atoms, exh = content_model(s)
    amap = {a["id"]: a for a in atoms}
    emap = {e["id"]: e for e in exh}
    shapes, texts = _plan(spec, s)
    _reflow_tables(ctx, sl, n, shapes, texts, emap)
    items = []
    for k, e in enumerate(shapes + texts):
        runs = e.get("_texts") and [r for t in e["_texts"] for r in t["runs"]] or e.get("runs")
        if not runs:
            continue
        x, y, w, h = e["box"]
        if e.get("_texts"):                               # text written into its shape: insets keep the picture's spot
            ts = e["_texts"]
            ux0 = min(t["bbox"][0] for t in ts)
            uy0 = min(t["bbox"][1] for t in ts)
            ux1 = max(t["bbox"][0] + t["bbox"][2] for t in ts)
            uy1 = max(t["bbox"][1] + t["bbox"][3] for t in ts)
            pad = 0.25 if e["type"] in ("chevron", "pentagon", "ellipse") else 0.1
            ml, mt = max(pad, ux0 - x), max(0.06, uy0 - y)
            mr, mb = max(pad, x + w - ux1), max(0.06, y + h - uy1)
            if w - ml - mr < 0.6:
                ml = mr = pad
            gaps = []                                     # space between paragraphs, as in the picture
            for ti, t in enumerate(ts):
                for j, _r in enumerate(t["runs"]):
                    if ti or j:
                        gaps.append(0.04 if j else max(0.0, t["bbox"][1] - ts[ti - 1]["bbox"][1]
                                                       - ts[ti - 1]["bbox"][3]))
            anchor = ts[0]["runs"][0].get("anchor", "top")
        else:
            ox0, ow = e["_orig"][0], e["_orig"][2]
            ml, mr = max(0.0, min(0.3, ox0 - x)), max(0.0, min(0.3, x + w - (ox0 + ow)))
            mt = mb = 0.02
            gaps = None
            anchor = runs[0].get("anchor", "top")
        fill = e.get("fill") if e["type"] in SHAPES else None
        dark = bool(fill) and lum_slot(fill) < 0.5
        roles = {}
        for r in runs:
            a = amap[r["ref"]]
            role = "figure" if a["role"] == "figure" else (r.get("role") if r.get("role") in ROLES else a["role"])
            roles[r["ref"]] = role if role in ROLES else "body"
        items.append({"el": e, "name": f"element {k}", "runs": runs, "amap": amap, "roles": roles, "dark": dark,
                      "box": e["box"], "margins": [ml, mt, mr, mb], "gaps": gaps, "anchor": anchor,
                      "inner": (w - ml - mr, h - mt - mb)})
    left = fit(items, s, shapes + texts)
    if left:
        raise DesignMiss("text does not fit: " + "; ".join(left[:3]))
    R._header(ctx, s, sl, n)
    by_el = {id(it["el"]): it for it in items}
    # reading order (P18): top to bottom, left to right; a card comes before what it holds (it starts above or at
    # its contents), and of two elements starting at the same spot the larger (the background) first
    order = sorted(shapes + texts, key=lambda e: (round((e.get("box") or e["bbox"])[1], 2),
                                                   round((e.get("box") or e["bbox"])[0], 2),
                                                   -(e.get("box") or e["bbox"])[2] * (e.get("box") or e["bbox"])[3]))
    for k, e in enumerate(order):
        t = e["type"]
        it = by_el.get(id(e))
        # a box holding a figure is a numeral (like the stat tiles): its hero sizes are not body text sizes (P4)
        tag = "role_numeral_" if it and any(it["roles"][r["ref"]] == "figure" for r in it["runs"]) else ""
        if t in SHAPES:
            shp = _shape(sl, t, e["box"], f"{tag}D{k}_{t}", e.get("fill"), e.get("line"))
            if it:
                PT._in_shape(shp, it["blocks"], ctx, n, tuple(it["margins"]), ANCHORS.get(it["anchor"], MSO_ANCHOR.TOP))
        elif t == "text":
            x, y, w, h = e["box"]
            tb = R._box(sl, x, y, w, h, f"{tag}D{k}_Text", ANCHORS.get(it["anchor"], MSO_ANCHOR.TOP))
            tf = tb.text_frame
            tf.margin_left, tf.margin_right = Inches(it["margins"][0]), Inches(it["margins"][2])
            R._write(tb, it["blocks"], ctx, n)
        elif t == "line":
            x, y, w, h = e["bbox"]
            col = THEME.get(e.get("color"), TC.ACCENT_5)
            if h <= 0.06 or w >= h:
                R._rule(sl, x, y + h / 2, w, col, 0.75, f"D{k}_Rule")
            else:
                PT._vline(sl, x + w / 2, y, h, col, 0.75, f"D{k}_Rule")
        elif t == "icon":
            _icon(sl, e, k)
        elif t == "badge":
            x, y, w, h = e["bbox"]
            d = max(0.3, min(w, h, 0.6))
            shp = _shape(sl, "ellipse", (x + (w - d) / 2, y + (h - d) / 2, d, d), f"ov_Badge{k}", e["fill"], None)
            col = TC.BACKGROUND_1 if lum_slot(e["fill"]) < 0.6 else TC.TEXT_1
            PT._in_shape(shp, [{"text": str(e["number"]), "size": max(12, min(20, round(d * 34))), "bold": True,
                                "numbers": False, "color": col, "align": PP_ALIGN.CENTER}], None, None,
                         (0, 0, 0, 0), MSO_ANCHOR.MIDDLE)
        elif t in ("chart", "table"):
            ex = emap.get(e["ref"])
            if ex is None:
                raise DesignMiss(f"exhibit {e['ref']} is not on this slide")
            part = copy.deepcopy(ex["part"])
            if t == "table" and isinstance(part.get("table"), dict):
                part["table"]["_pad"] = TABLE_PAD
            placed_heads = {r["ref"] for o in texts + shapes for r in (o.get("runs") or [])} | \
                {r["ref"] for o in shapes for tt in o.get("_texts") or [] for r in tt["runs"]}
            if ex["heading"] and all(h_ in placed_heads for h_ in ex["heading"]):
                part["heading"], part["unit"] = "", ""      # the spec sets the exhibit's heading itself
            before = len(ctx.overflow)
            PT.render_part(ctx, sl, n, part, tuple(e["box"]), "editorial", 90 + k, s)
            if any("could not be drawn" in m for m in ctx.overflow[before:]):
                raise DesignMiss(f"exhibit {e['ref']} could not be drawn")
    return {"layout": spec.get("layout"), "elements": len(order)}


# ------------------------------------------------------------------ after rendering: did the page keep everything?
def slide_text(slide) -> tuple[str, list[float]]:
    """All text on a slide (shapes, tables, chart categories) and every number cached in its charts."""
    from lxml import etree
    texts, nums = [], []
    for shp in slide.shapes:
        if shp.has_text_frame:
            texts.append(shp.text_frame.text)
        if getattr(shp, "has_table", False) and shp.has_table:
            texts += [c.text for r in shp.table.rows for c in r.cells]
        if getattr(shp, "has_chart", False) and shp.has_chart:
            x = etree.fromstring(shp.chart.part.blob)
            for v in x.iter("{http://schemas.openxmlformats.org/drawingml/2006/chart}v"):
                try:
                    nums.append(float(v.text))
                except (TypeError, ValueError):
                    texts.append(v.text or "")
    return re.sub(r"\s+", " ", " ".join(texts)), nums


def lost_content(slide, s: dict) -> list[str]:
    """What of the slide's JSON is not on the rendered slide: required atoms (their text, whitespace-insensitive)
    and chart values (in the chart cache, or written out as text)."""
    text, nums = slide_text(slide)
    flat = re.sub(r"\s+", "", plain(text)).casefold()
    atoms, exh = content_model(s)
    out = []
    for a in atoms:
        if not a["required"] or a["parent"]:
            continue
        if re.sub(r"\s+", "", plain(a["text"])).casefold() not in flat:
            out.append(f"{a['id']} ({plain(a['text'])[:40]})")
    for e in exh:
        if e["kind"] == "table":                          # every cell's words (a row the table dropped is lost)
            t = e["part"].get("table") if isinstance(e["part"].get("table"), dict) else e["part"]
            for r in t.get("rows") or []:
                for c in (r if isinstance(r, list) else [r]):
                    w = re.sub(r"\s+", "", plain(R._txt(c))).casefold()
                    if w and w not in flat:
                        out.append(f"{e['id']} cell '{plain(R._txt(c))[:30]}'")
        c = e["part"].get("chart") if e["kind"] == "chart" else None
        for se in (c or {}).get("series") or []:
            for v in se.get("values") or []:
                if isinstance(v, (int, float)) and not any(abs(v - u) <= 1e-6 * max(1, abs(v)) for u in nums) \
                        and R.fmt_value(float(v), "").replace(",", "") not in text.replace(",", ""):
                    out.append(f"{e['id']} value {v}")
    return out


# ------------------------------------------------------------------ the pipeline for one deck
class Designer:
    """Design specs for a deck's slides, cached by slide content (a repaired slide is designed again while the
    caps allow; otherwise its old spec is rebound to the new words, or the archetype draws it). Never raises."""

    def __init__(self, client, rules: dict | None, out_dir: str, theme=None, density: str | None = None,
                 mode: str = "image", images: ImageClient | None = None, task_id: str = "forge"):
        self.cfg = settings(rules)
        self.client, self.mode, self.density, self.task_id = client, mode, density, task_id
        self.theme = theme
        self.dir = direction(theme, density)
        _SLOT_HEX.clear()
        _SLOT_HEX.update(self.dir["colors"])
        self.out = Path(out_dir) / "design"
        self.images = images or ImageClient(self.cfg["image_model"], timeout=float(self.cfg["image_timeout_s"]))
        self.models = vision_models(self.cfg)
        self.cache: dict[str, dict] = {}                 # content digest -> record
        self.by_node: dict[str, dict] = {}               # node -> last good record (rebinding after a rewrite)
        self.rejected: dict[str, str] = {}               # content digest -> why the rebuild was refused
        self.log: dict[int, dict] = {}                   # slide number -> what happened (design.json)
        self.used = 0
        self.t0 = time.time()
        self.lock = threading.Lock()

    def budget_left(self) -> str | None:
        if self.used >= int(self.cfg["max_images"]):
            return f"image cap reached ({self.cfg['max_images']} per deck)"
        if time.time() - self.t0 > float(self.cfg["time_budget_s"]):
            return f"time budget spent ({self.cfg['time_budget_s']}s)"
        return None

    def _one(self, i: int, s: dict, layout: str, recent: list[str] | None = None) -> dict:
        v = view(s, self.density)
        rec = {"node": s.get("node"), "layout_asked": layout, "status": "not_run"}
        with self.lock:
            why = self.budget_left()
            if not why:
                self.used += 1
        if why:
            rec["why"] = why
            return rec
        prompt = design_prompt(v, self.dir, recent or [], layout)
        rec["prompt"] = prompt
        img = self.images.generate(prompt)
        rec["image"] = {k: img.get(k) for k in ("status", "why", "via", "model", "size", "asked_size")}
        if img["status"] != "ok":
            rec["why"] = "image: " + img.get("why", "")
            return rec
        self.out.mkdir(parents=True, exist_ok=True)
        path = self.out / f"slide{i + 1:02d}.png"
        path.write_bytes(img["png"])
        rec["png"] = str(path)
        rc = reconstruct(self.client, img["png"], v, self.dir, self.models, img["size"],
                         float(self.cfg["vision_timeout_s"]), f"{self.task_id}-design{i + 1}")
        if rc["status"] != "ok":
            rec["why"] = "vision: " + rc.get("why", "")
            return rec
        spec, probs = normalize_spec(rc["raw"], v, self.dir["colors"], img["size"])
        spec["layout"] = spec["layout"] or layout
        rec.update(status="ok", spec=spec, problems=probs, vision_model=rc["model"])
        (self.out / f"slide{i + 1:02d}.spec.json").write_text(json.dumps({"raw": rc["raw"], "spec": spec,
                                                                          "problems": probs}, indent=1))
        miss = coverage(spec, v)
        if miss:
            rec.update(status="incomplete", why="the design leaves out " + ", ".join(miss[:6]))
        return rec

    def specs(self, slides: list[dict]) -> dict[int, dict]:
        """{index: spec} for every eligible slide that has (or can get) a design within the caps."""
        todo, recent, out = [], [], {}
        for i, s in enumerate(slides):
            if not eligible(s, self.mode):
                continue
            d = content_digest(s)
            if d in self.rejected:
                self.log[i + 1] = {"node": s.get("node"), "path": "archetype", "why": self.rejected[d]}
                continue
            rec = self.cache.get(d)
            if rec is None:
                layout = pick_layout(s, recent)
                todo.append((i, s, d, layout, list(recent)))
                recent.append(layout)
            else:
                recent.append((rec.get("spec") or {}).get("layout") or rec.get("layout_asked") or "")
        if todo:
            with ThreadPoolExecutor(max(1, int(self.cfg["workers"]))) as ex:
                for (i, s, d, _, _r), rec in zip(todo, ex.map(lambda t: self._one(t[0], t[1], t[3], t[4]), todo)):
                    if rec["status"] == "not_run" and s.get("node") in self.by_node and \
                            not coverage(self.by_node[s["node"]]["spec"], view(s, self.density)):
                        rec = dict(self.by_node[s["node"]], rebound=True)   # same structure, new words: rebind
                    self.cache[d] = rec
        for i, s in enumerate(slides):
            if not eligible(s, self.mode):
                continue
            d = content_digest(s)
            rec = self.cache.get(d) or {}
            if d in self.rejected:
                continue
            if rec.get("status") == "ok" or rec.get("rebound"):
                out[i] = rec["spec"]
                self.by_node[str(s.get("node"))] = rec
                self.log[i + 1] = {"node": s.get("node"), "path": "image", "layout": rec["spec"].get("layout"),
                                   "png": rec.get("png"), "rebound": bool(rec.get("rebound"))}
            else:
                self.log[i + 1] = {"node": s.get("node"), "path": "archetype", "why": rec.get("why", "")}
        return out

    def reject(self, slide: dict, why: str, n: int | None = None) -> None:
        self.rejected[content_digest(slide)] = why
        if n:
            self.log[n] = dict(self.log.get(n) or {}, path="archetype", why=why)

    def check(self, pptx: str, render_res: dict, validation: dict, slides: list[dict],
              specs: dict[int, dict]) -> dict[int, str]:
        """Designed slides to redraw with their archetype: a draw-time miss, a failed P0/P1/P5/P8/P17 rule on that
        slide, or content the page lost. {index: why}."""
        bad: dict[int, str] = {}
        for n, d in (render_res.get("design") or {}).items():
            if d.get("path") == "archetype" and int(n) - 1 in specs:
                bad[int(n) - 1] = d.get("why") or "rebuild failed"
        rules = set(self.cfg["fallback_rules"])
        for f in validation.get("failures") or []:
            if f.get("rule") not in rules or f.get("status") != "fail" or f.get("severity") != "error":
                continue
            n = f.get("slide")
            if n is None and f.get("rule") == "P0":
                m = re.search(r"slide(\d+)\.xml", json.dumps(f))
                n = int(m.group(1)) if m else None
            if n is not None and n - 1 in specs and n - 1 not in bad:
                bad[n - 1] = f"{f['rule']} {f.get('check')}: {f.get('message', '')[:160]}"
        try:
            from pptx import Presentation
            prs = Presentation(pptx)
            for i in specs:
                if i in bad or i >= len(prs.slides):
                    continue
                lost = lost_content(prs.slides[i], view(slides[i], self.density))
                if lost:
                    bad[i] = "content lost on the page: " + ", ".join(lost[:5])
        except Exception as e:  # noqa: BLE001 - a check that cannot run never keeps a broken page
            for i in specs:
                bad.setdefault(i, f"content check could not run: {type(e).__name__}")
        for i, why in bad.items():
            self.reject(slides[i], why, i + 1)
        return bad

    def report(self) -> dict:
        return {"mode": self.mode, "image_model": self.images.model, "vision_models": self.models,
                "images_used": self.used, "max_images": self.cfg["max_images"],
                "seconds": round(time.time() - self.t0, 1),
                "slides": {str(k): v for k, v in sorted(self.log.items())}}
