"""Parts library: a slide assembled from versatile parts placed on a grid.

A part is an idea, not a slide: the same KPI tiles, process, timeline or 2x2 can fill a whole slide, a half, a third
or a quarter. The content model picks a layout (full, halves, halves_v, thirds, quarters, main_side, side_main,
main_stack, stack_main) and one part per region; the renderer fits each part to the box it gets.

    {"archetype": "grid", "title": ..., "layout": "main_side", "look": "editorial",
     "parts": [{"type": "chart", "heading": "Revenue, $M", "chart": {...}},
               {"type": "stats", "items": [{"value": "34%", "label": "Gross margin", "delta": "+3 pp"}]}]}

Part types: chart, table, stats, text, callout, process, timeline, matrix2x2, harvey, quote. Everything is a native
shape, chart or table in theme colours (MSO_THEME_COLOR) only; text that belongs to a shape is written into it.
Looks: editorial (hairline headings, no cards), boxed (light card per part, tab headings), minimal (plain headings,
more air), bold (accent tab headings).
"""
from __future__ import annotations

import copy
import math
import re

from pptx.enum.dml import MSO_THEME_COLOR as TC
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from . import render as R

GAP = 0.25                     # between regions
FLOOR = 10                     # smallest body size a part may shrink to (the dense-archetype floor)

LAYOUTS = {"full": 1, "halves": 2, "halves_v": 2, "thirds": 3, "quarters": 4, "main_side": 2, "side_main": 2,
           "main_stack": 3, "stack_main": 3}
BY_COUNT = {1: "full", 2: "halves", 3: "thirds", 4: "quarters"}

LOOKS = {
    "editorial": {"heading": "rule", "card": False, "pad": 0.0, "after": 0.12, "inset": 0.0, "tab": None},
    "boxed": {"heading": "tab", "card": True, "pad": 0.15, "after": 0.12, "inset": 0.0, "tab": TC.TEXT_2},
    "minimal": {"heading": "plain", "card": False, "pad": 0.0, "after": 0.22, "inset": 0.15, "tab": None},
    "bold": {"heading": "tab", "card": False, "pad": 0.0, "after": 0.14, "inset": 0.0, "tab": TC.ACCENT_1},
}

PART_TYPES = ("chart", "table", "stats", "text", "callout", "process", "timeline", "matrix2x2", "harvey", "quote")


# ------------------------------------------------------------------ grid
def regions(layout: str, top: float = R.BODY_Y, bottom: float = R.BODY_B) -> list[tuple[float, float, float, float]]:
    """(x, y, w, h) in inches for each region of `layout`, inside LEFT..RIGHT and top..bottom, 0.25in apart."""
    x0, W, H = R.LEFT, R.RIGHT - R.LEFT, bottom - top
    half_w, half_h = (W - GAP) / 2, (H - GAP) / 2
    third = (W - 2 * GAP) / 3
    main, side = (W - GAP) * 2 / 3, (W - GAP) / 3
    xr = x0 + half_w + GAP
    yb = top + half_h + GAP
    if layout == "full":
        return [(x0, top, W, H)]
    if layout == "halves":
        return [(x0, top, half_w, H), (xr, top, half_w, H)]
    if layout == "halves_v":
        return [(x0, top, W, half_h), (x0, yb, W, half_h)]
    if layout == "thirds":
        return [(x0 + i * (third + GAP), top, third, H) for i in range(3)]
    if layout == "quarters":
        return [(x0, top, half_w, half_h), (xr, top, half_w, half_h), (x0, yb, half_w, half_h), (xr, yb, half_w, half_h)]
    if layout == "main_side":
        return [(x0, top, main, H), (x0 + main + GAP, top, side, H)]
    if layout == "side_main":
        return [(x0, top, side, H), (x0 + side + GAP, top, main, H)]
    if layout == "main_stack":
        return [(x0, top, half_w, H), (xr, top, half_w, half_h), (xr, yb, half_w, half_h)]
    if layout == "stack_main":
        return [(x0, top, half_w, half_h), (x0, yb, half_w, half_h), (xr, top, half_w, H)]
    raise ValueError(f"unknown layout '{layout}' (one of {', '.join(LAYOUTS)})")


def _fewer(layout: str, k: int) -> str:
    """The layout with exactly k regions closest to the one asked for (a region is never left empty)."""
    if k <= 1:
        return "full"
    if k == 2:
        return {"main_stack": "main_side", "stack_main": "side_main", "main_side": "main_side",
                "side_main": "side_main", "halves_v": "halves_v"}.get(layout, "halves")
    if k == 3:
        return "main_stack" if layout == "quarters" else "thirds"
    return BY_COUNT.get(k, "quarters")


# ------------------------------------------------------------------ helpers
def _note(s, text):
    if s is not None:
        s["notes"] = (s.get("notes", "") + "\n[QA] " + text).strip()


def _plain(t) -> str:
    return re.sub(r"\*\*|\^\w+", "", str(t or ""))


def _txt(v) -> str:
    if isinstance(v, dict):
        return str(v.get("text") or v.get("title") or v.get("label") or v.get("lead") or v.get("value") or "")
    return "" if v is None else str(v)


def _in_shape(shp, blocks, ctx, n, margins=(0.08, 0.05, 0.08, 0.05), anchor=MSO_ANCHOR.TOP):
    """Write blocks into the shape's own text frame (margins l, t, r, b in inches)."""
    tf = shp.text_frame
    tf.word_wrap = True
    bp = tf._txBody.find(qn("a:bodyPr"))
    for c in list(bp):
        bp.remove(c)
    for k in list(bp.attrib):
        del bp.attrib[k]
    tf.margin_left, tf.margin_top, tf.margin_right, tf.margin_bottom = (Inches(max(0.0, m)) for m in margins)
    tf.vertical_anchor = anchor
    for b in blocks:
        b.setdefault("align", PP_ALIGN.LEFT)
    R._write(shp, blocks, ctx, n)
    return shp


def _shape(sl, kind, x, y, w, h, name, fill=None, line=None, weight=0.75):
    s = sl.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    s.name = name
    R._strip_style(s)
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid()
        s.fill.fore_color.theme_color = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.theme_color = line
        s.line.width = Pt(weight)
    return s


def _vline(sl, x, y, h, color=TC.ACCENT_5, weight=0.5, name="Rule", arrow=False):
    c = sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x), Inches(y + h), Inches(x), Inches(y))
    c.name = name
    R._strip_style(c)
    c.line.color.theme_color = color
    c.line.width = Pt(weight)
    if arrow:
        _arrow(c)
    return c


def _arrow(c):
    ln = c.line._get_or_add_ln()
    ln.append(ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"}))


def _fit_trim(blocks, w, h, s, what, ctx=None, n=None, floor=FLOOR):
    """Fit at or above `floor`; drop trailing lines that still do not fit and note them."""
    bl, dropped = R._trim_to_fit(blocks, w, h, floor)
    if dropped:
        R._cut_note(s if s is not None else {}, dropped)
        if ctx is not None:
            ctx.miss(n, what, max(3, sum(len(d.split()) for d in dropped)))
    return bl


def _head_w(text, size):
    from .common import text_width_in
    return text_width_in(_plain(text), size, R._font("head", True))


def _lines(text, w, size, bold=False, head=False):
    from .common import wrap_lines
    f = R._font("head", True) if head else R._font("body", bold)
    return wrap_lines(_plain(text), w, size, f, 0.97)


# ------------------------------------------------------------------ frame: card + heading
def _frame(ctx, sl, n, part, box, look, i, s):
    """Card and heading for one part. Returns (inner box, card shape or None)."""
    x, y, w, h = box
    L = LOOKS[look]
    card = None
    if L["card"] and part.get("type") != "callout":
        card = R._fill(sl, x, y, w, h, TC.BACKGROUND_2, f"Card{i}")
    if L["inset"]:
        w, h = w - L["inset"], h - L["inset"]
    head = _plain(part.get("heading") or "").strip()
    used = 0.0
    if head:
        if L["heading"] == "tab":
            bl = [{"text": head, "size": R.bump(13), "bold": True, "numbers": False, "color": TC.BACKGROUND_1}]
            bl, ok = R._fit(bl, w - 0.24, 0.62, 11)
            th = max(0.36, R._need(bl, w - 0.24) + 0.1)
            tab = R._fill(sl, x, y, w, th, L["tab"], f"Tab{i}")
            _in_shape(tab, bl, ctx, n, (0.12, 0.03, 0.12, 0.03), MSO_ANCHOR.MIDDLE)
            if not ok:
                ctx.miss(n, f"the heading of part {i}", 3)
            used = th
        else:
            bl = [{"text": head, "size": R.bump(14), "bold": True, "numbers": False}]
            bl, ok = R._fit(bl, w, 0.62, 12)
            hh = R._need(bl, w) + 0.02
            R._write(R._box(sl, x, y, w, hh, f"Heading{i}"), bl)
            if not ok:
                ctx.miss(n, f"the heading of part {i}", 3)
            used = hh
            if L["heading"] == "rule":
                R._rule(sl, x, y + hh + 0.03, w, TC.TEXT_1, 0.75, f"HeadingRule{i}")
                used += 0.03
        used += L["after"]
    pad = L["pad"] if card is not None else 0.0
    if card is not None and head:
        inner = (x + pad, y + used, w - 2 * pad, h - used - pad)
    else:
        inner = (x + pad, y + used + pad, w - 2 * pad, h - used - 2 * pad)
    return inner, card


# ------------------------------------------------------------------ parts
def p_chart(ctx, sl, n, part, box, look, i, s, card):
    x, y, w, h = box
    c = part.get("chart") if isinstance(part.get("chart"), dict) else {k: v for k, v in part.items()
                                                                        if k not in ("type", "heading")}
    if c.get("kind") not in ("stats", "bubble"):
        probs = R.chart_problems(c)
        if probs:
            st = R.stat_stack(c)
            _note(s, f"part {i}: chart shown as figures ({'; '.join(probs)})")
            if st["items"]:
                return p_stats(ctx, sl, n, {"items": st["items"]}, box, look, i, s, card)
            return p_text(ctx, sl, n, {"bullets": [_txt(v) for v in c.get("categories") or []]}, box, look, i, s, card)
    if part.get("unit"):
        ub = [{"text": part["unit"], "size": R.bump(11), "numbers": False}]
        uh = R._need(ub, w) + 0.02
        R._write(R._box(sl, x, y, w, uh, f"Unit{i}"), ub)
        y, h = y + uh + 0.04, h - uh - 0.04
    if c.get("kind") == "stats":
        return p_stats(ctx, sl, n, {"items": c.get("items") or []}, (x, y, w, h), look, i, s, card)
    R._chart(ctx, sl, n, c, x, y, w, h, name=f"Chart{i}")


def p_table(ctx, sl, n, part, box, look, i, s, card):
    x, y, w, h = box
    t = dict(part.get("table") if isinstance(part.get("table"), dict) else part)
    t["columns"] = [R._txt(c) for c in t.get("columns") or []]
    rows = [[R._cell(c) for c in (r if isinstance(r, list) else [r])] for r in t.get("rows") or []]
    dropped = []
    while True:
        ctx.carry_rows = None
        need = R._table(ctx, sl, n, dict(t, rows=rows), x, y, w, h, dry=True)
        ctx.carry_rows = None
        if (need != float("inf") and need <= h + 0.01) or len(rows) <= 1:
            break
        k = len(rows) - (2 if t.get("total_row") and len(rows) > 2 else 1)   # a total row stays the last row
        dropped.insert(0, " | ".join(map(str, rows.pop(k))))
    if dropped:
        _note(s, f"part {i}: table rows that did not fit and were left out: " + "; ".join(dropped))
        t["highlight_rows"] = [r for r in t.get("highlight_rows") or [] if r < len(rows)]
    gf = R._table(ctx, sl, n, dict(t, rows=rows), x, y, w, h)
    ctx.carry_rows = None
    gf.name = f"Table{i}"


def _stat_grid(k, w, h, gap):
    """(cols, rows) for k tiles: tiles near a 1.6 aspect, at least 1.1in wide and 0.75in tall when possible."""
    best = None
    for rows in range(1, k + 1):
        cols = -(-k // rows)
        tw, th = (w - gap * (cols - 1)) / cols, (h - gap * (rows - 1)) / rows
        score = abs(math.log(max(tw, 0.01) / max(th, 0.01) / 1.6)) + (3 if tw < 1.1 else 0) + (3 if th < 0.75 else 0)
        score += 0.4 * (cols * rows - k)
        if best is None or score < best[0]:
            best = (score, cols, rows)
    return best[1], best[2]


def p_stats(ctx, sl, n, part, box, look, i, s, card):
    x, y, w, h = box
    items = [it if isinstance(it, dict) else {"value": str(it), "label": ""} for it in part.get("items") or []]
    if len(items) > 6:
        _note(s, f"part {i}: {len(items) - 6} figures left out: " + "; ".join(_txt(v) for v in items[6:]))
        items = items[:6]
    k = max(1, len(items))
    boxed = LOOKS[look]["card"]
    gap = 0.15 if boxed else 0.2
    cols, rows = _stat_grid(k, w, h, gap)
    tw, th = (w - gap * (cols - 1)) / cols, (h - gap * (rows - 1)) / rows
    pad = 0.12 if boxed else 0.06
    iw = tw - 2 * pad
    # one value size for every tile: the largest that fits the widest value and the tallest tile text
    vsize = 20
    for z in (40, 36, 32, 28, 24, 22, 20):
        ok = True
        for it in items:
            rest = [{"text": it.get("label") or "", "size": R.bump(12), "numbers": False, "space_before": 4}]
            if it.get("delta"):
                rest.append({"text": it["delta"], "size": R.bump(11), "bold": True, "numbers": False, "space_before": 2})
            need = (_lines(it.get("value", ""), iw, z, head=True) * R._pitch(z, 1.0) / 72 + R._need(rest, iw))
            if _head_w(it.get("value", ""), z) > iw * 0.97 or need > th - 0.1:
                ok = False
                break
        if ok:
            vsize = z
            break
    for j, it in enumerate(items):
        r_, c_ = divmod(j, cols)
        tx, ty = x + c_ * (tw + gap), y + r_ * (th + gap)
        tile = _shape(sl, MSO_SHAPE.RECTANGLE, tx, ty, tw, th, f"ov_Stat{i}_{j + 1}",
                      fill=TC.BACKGROUND_1 if boxed else None)
        bl = [{"text": _plain(it.get("value", "")), "size": vsize, "bold": True, "heading": True, "numbers": False,
               "color": TC.ACCENT_1, "line_spacing": 1.0, "prov": it.get("prov")}]
        if it.get("label"):
            bl.append({"text": it["label"], "size": R.bump(12), "numbers": False, "space_before": 4})
        if it.get("delta"):
            bl.append({"text": it["delta"], "size": R.bump(11), "bold": True, "numbers": False, "space_before": 2,
                       "color": TC.TEXT_2})
        rest, ok = R._fit(bl[1:], iw, th - 0.1 - R._pitch(vsize, 1.0) / 72, FLOOR)
        if not ok:
            ctx.miss(n, f"figure {j + 1} of part {i}", 3)
        _in_shape(tile, bl[:1] + rest, ctx, n, (pad, 0.05, pad, 0.05), MSO_ANCHOR.MIDDLE)
        if not boxed:                                    # hairlines between tiles, not boxes around them
            if c_ < cols - 1 and j + 1 < len(items):
                _vline(sl, tx + tw + gap / 2, ty + 0.08, th - 0.16, name=f"ov_StatRuleV{i}_{j + 1}")
            if r_ < rows - 1:
                R._rule(sl, tx, ty + th + gap / 2, tw, TC.ACCENT_5, 0.5, f"ov_StatRuleH{i}_{j + 1}")


def _text_blocks(part, color=TC.TEXT_1, lead_size=None, size=None):
    lead_size, size = lead_size or R.bump(14), size or R.bump(12)
    bl = []
    lead = part.get("lead") or part.get("title")
    if lead:
        bl.append({"text": _txt(lead), "size": lead_size, "bold": True, "numbers": False, "color": color,
                   "prov": part.get("prov")})
    if part.get("text"):
        bl.append({"text": _txt(part["text"]), "size": size, "color": color, "space_before": 4 if bl else 0,
                   "prov": part.get("prov")})
    for b in part.get("bullets") or []:
        b = b if isinstance(b, dict) else {"text": str(b)}
        bl.append({"text": _txt(b), "size": size, "bullet": True, "space_before": 5, "color": color,
                   "level": min(1, int(b.get("level", 0) or 0)), "prov": b.get("prov")})
    return bl


def p_text(ctx, sl, n, part, box, look, i, s, card):
    x, y, w, h = box
    bl = _text_blocks(part)
    if not bl:
        return
    bl = _fit_trim(R._grow(bl, w, h, 6 if R.DENSITY["mode"] == "present" else 2), w, h, s, f"the text of part {i}",
                   ctx, n)                              # presenting: short text steps well up the scale
    if card is not None and not part.get("heading"):
        R._shape_text(card, bl, ctx, n, (x, y, w, h))   # the card is the text's own shape
        return
    R._write(R._box(sl, x, y, w, h, f"Text{i}"), bl, ctx, n)


def p_callout(ctx, sl, n, part, box, look, i, s, card):
    x, y, w, h = box
    bl = _text_blocks(part, TC.BACKGROUND_1, R.bump(16), R.bump(13))
    if not bl:
        return
    iw, ih = w - 0.5, h - 0.3
    bl = _fit_trim(R._grow(bl, iw, ih, 6), iw, ih, s, f"the callout of part {i}", ctx, n)
    shp = R._fill(sl, x, y, w, h, TC.TEXT_2, f"Callout{i}")
    R._shape_text(shp, bl, ctx, n, (x + 0.25, y + 0.15, iw, ih), MSO_ANCHOR.MIDDLE)


def _surface(look):
    return TC.BACKGROUND_1 if LOOKS[look]["card"] else TC.BACKGROUND_2


def p_process(ctx, sl, n, part, box, look, i, s, card):
    x, y, w, h = box
    steps = [st if isinstance(st, dict) else {"title": str(st)} for st in part.get("steps") or []]
    if len(steps) > 6:
        _note(s, f"part {i}: process steps left out: " + "; ".join(_txt(v) for v in steps[6:]))
        steps = steps[:6]
    k = max(1, len(steps))
    hl = part.get("highlight")
    surf = _surface(look)
    if w / k >= 1.25 and w > h * 0.9:                    # wide: interlocking chevrons, text inside
        d, g = 0.2, 0.05
        sw = (w + (k - 1) * (d - g)) / k
        tw = sw - 2 * d - 0.14
        need = lambda z: max(R._need([{"text": _txt(st.get("title")), "size": R.bump(12) + z, "bold": True},
                                      {"text": _txt(st.get("text")), "size": R.bump(11) + z, "space_before": 4}], tw)
                             for st in steps)
        bump = next((z for z in (3, 2, 1) if need(z) <= 0.55 * h), 0)   # one type size for every step
        ch = min(h, max(1.1, need(bump) + 0.5))           # as tall as the text needs, set a little above centre
        y += (h - ch) * 0.35
        for j, st in enumerate(steps):
            sx = x + j * (sw - d + g)
            on = hl == j
            kind = MSO_SHAPE.PENTAGON if j == 0 else MSO_SHAPE.CHEVRON
            shp = _shape(sl, kind, sx, y, sw, ch, f"ov_Step{i}_{j + 1}", fill=TC.TEXT_2 if on else surf)
            shp.adjustments[0] = min(1.0, d / min(sw, ch))
            fg = TC.BACKGROUND_1 if on else TC.TEXT_1
            bl = [{"text": _txt(st.get("title")), "size": R.bump(12), "bold": True, "numbers": False,
                   "color": TC.BACKGROUND_1 if on else TC.TEXT_2}]
            if st.get("text"):
                bl.append({"text": _txt(st["text"]), "size": R.bump(11), "space_before": 4, "color": fg, "prov": st.get("prov")})
            bl = _fit_trim(R._grow(bl, tw, ch - 0.25, bump), tw, ch - 0.25, s, f"step {j + 1} of part {i}", ctx, n)
            _in_shape(shp, bl, ctx, n, (0.07 if j else 0.12, 0.12, 0.07, 0.1), MSO_ANCHOR.TOP)
        return
    # tall: a stage badge (pentagon) with the step title, its text beside it, hairlines between steps
    g = 0.08
    rh = (h - g * (k - 1)) / k
    bw = min(1.9, max(1.1, 0.36 * w))
    tx, tw = x + bw + 0.12, w - bw - 0.12
    for j, st in enumerate(steps):
        ry = y + j * (rh + g)
        on = hl == j
        badge = _shape(sl, MSO_SHAPE.PENTAGON, x, ry, bw, rh, f"ov_Step{i}_{j + 1}",
                       fill=TC.ACCENT_1 if on else TC.TEXT_2)
        badge.adjustments[0] = min(1.0, 0.18 / min(bw, rh))
        bb = [{"text": _txt(st.get("title")), "size": R.bump(12), "bold": True, "numbers": False, "color": TC.BACKGROUND_1}]
        bb = _fit_trim(bb, bw - 0.3, rh - 0.08, s, f"step {j + 1} of part {i}", ctx, n)
        _in_shape(badge, bb, ctx, n, (0.1, 0.03, 0.06, 0.03), MSO_ANCHOR.MIDDLE)
        if st.get("text"):
            tb = [{"text": _txt(st["text"]), "size": R.bump(12), "prov": st.get("prov")}]
            tb = _fit_trim(tb, tw - 0.04, rh - 0.06, s, f"step {j + 1} of part {i}", ctx, n)
            body = _shape(sl, MSO_SHAPE.RECTANGLE, tx, ry, tw, rh, f"ov_StepText{i}_{j + 1}")
            _in_shape(body, tb, ctx, n, (0.02, 0.02, 0.02, 0.02), MSO_ANCHOR.MIDDLE)
        if j < k - 1:
            R._rule(sl, tx, ry + rh + g / 2, tw, TC.ACCENT_5, 0.5, f"ov_StepRule{i}_{j + 1}")


def p_timeline(ctx, sl, n, part, box, look, i, s, card):
    x, y, w, h = box
    ms = [m if isinstance(m, dict) else {"label": str(m)} for m in part.get("milestones") or part.get("items") or []]
    if len(ms) > 8:
        _note(s, f"part {i}: milestones left out: " + "; ".join(_txt(v) for v in ms[8:]))
        ms = ms[:8]
    k = max(1, len(ms))
    hl = part.get("highlight")
    dot = 0.18
    if w > h:                                            # horizontal: dates above the axis, labels below
        sp = w / k
        cw = sp - 0.12

        def lab(j, z):
            m = ms[j]
            out = [{"text": _txt(m.get("label") or m.get("title")), "size": R.bump(12) + z, "numbers": False,
                    "align": PP_ALIGN.CENTER, "bold": bool(m.get("text")) or hl == j, "prov": m.get("prov")}]
            if m.get("text"):
                out.append({"text": _txt(m["text"]), "size": R.bump(11) + z, "align": PP_ALIGN.CENTER, "space_before": 4})
            return out
        for z in (4, 2, 0):                              # the largest type at which the band takes <= 60% of the box
            dates = [[{"text": _txt(m.get("date")), "size": R.bump(12) + z, "bold": True, "numbers": False,
                       "color": TC.ACCENT_1, "align": PP_ALIGN.CENTER}] for m in ms]
            dh = max([R._need(d, cw) for d in dates if d[0]["text"]] or [0.0]) + 0.04
            ln = max(R._need(lab(j, z), cw) for j in range(len(ms))) + 0.04
            band = dh + 0.16 + dot + 0.12 + ln
            if band <= 0.6 * h or z == 0:
                break
        if band < h:                                     # a short band sits a little above the middle of its box
            y += (h - band) * 0.4
            h = band + (h - band) * 0.6
        ly = y + dh + 0.16 + dot / 2
        R._rule(sl, x, ly, w, TC.TEXT_2, 1.5, f"ov_TimelineAxis{i}")
        lh = y + h - (ly + dot / 2 + 0.12)
        for j, m in enumerate(ms):
            cx = x + sp * (j + 0.5)
            on = hl == j
            dd = dot * (1.4 if on else 1)
            _shape(sl, MSO_SHAPE.OVAL, cx - dd / 2, ly - dd / 2, dd, dd, f"ov_TLDot{i}_{j + 1}",
                   fill=TC.ACCENT_3 if on else TC.ACCENT_1, line=TC.BACKGROUND_1, weight=1.5)
            if dates[j][0]["text"]:
                R._write(R._box(sl, cx - cw / 2, y, cw, dh, f"ov_TLDate{i}_{j + 1}", MSO_ANCHOR.BOTTOM), dates[j])
            lb = _fit_trim(lab(j, z), cw, lh, s, f"milestone {j + 1} of part {i}", ctx, n)
            R._write(R._box(sl, cx - cw / 2, ly + dot / 2 + 0.12, cw, lh, f"ov_TLLabel{i}_{j + 1}"), lb, ctx, n)
        return
    # vertical: axis down the left, each milestone's date and label to its right
    rh = h / k
    ax = x + 0.12
    _vline(sl, ax, y + 0.05, h - 0.1, TC.TEXT_2, 1.5, f"ov_TimelineAxis{i}")
    for j, m in enumerate(ms):
        ry = y + j * rh
        on = hl == j
        dd = dot * (1.4 if on else 1)
        bl = [{"text": _txt(m.get("date")), "size": R.bump(12), "bold": True, "numbers": False, "color": TC.ACCENT_1},
              {"text": _txt(m.get("label") or m.get("title")), "size": R.bump(12), "numbers": False, "bold": on,
               "prov": m.get("prov")}]
        if m.get("text"):
            bl.append({"text": _txt(m["text"]), "size": R.bump(11), "space_before": 2})
        bl = [b for b in bl if b["text"]]
        tx, tw = x + 0.4, w - 0.4
        bl = _fit_trim(bl, tw, rh - 0.08, s, f"milestone {j + 1} of part {i}", ctx, n)
        need = min(rh - 0.04, R._need(bl, tw))
        _shape(sl, MSO_SHAPE.OVAL, ax - dd / 2, ry + 0.02 + R._pitch(R.bump(12)) / 144 - dd / 2 + 0.03, dd, dd,
               f"ov_TLDot{i}_{j + 1}", fill=TC.ACCENT_3 if on else TC.ACCENT_1, line=TC.BACKGROUND_1, weight=1.5)
        R._write(R._box(sl, tx, ry, tw, max(need, rh - 0.04), f"ov_TLLabel{i}_{j + 1}"), bl, ctx, n)


def _axis(v):
    if isinstance(v, dict):
        return _txt(v.get("label")), _txt(v.get("low") or "Low"), _txt(v.get("high") or "High")
    return _txt(v), "Low", "High"


def p_matrix2x2(ctx, sl, n, part, box, look, i, s, card):
    x, y, w, h = box
    qs = [q if isinstance(q, dict) else {"title": str(q)} for q in (part.get("quadrants") or [])[:4]]
    qs += [{"title": ""}] * (4 - len(qs))
    xl, xlo, xhi = _axis(part.get("x_axis") or part.get("x") or "")
    yl, ylo, yhi = _axis(part.get("y_axis") or part.get("y") or "")
    hl = part.get("highlight")
    gw = max(R._need_w(t, 10, True) for t in (ylo, yhi)) + 0.22 if yl else 0.0
    top_h = 0.28 if yl else 0.0
    bot_h = 0.3 if xl else 0.0
    if yl:
        R._write(R._box(sl, x + gw, y, w - gw, top_h - 0.04, f"ov_MxYLabel{i}", MSO_ANCHOR.BOTTOM),
                 [{"text": f"↑ {yl}", "size": R.bump(11, 0.5), "bold": True, "numbers": False}])
    g = 0.08
    qx, qy = x + gw, y + top_h
    qw, qh = (w - gw - g) / 2, (h - top_h - bot_h - g) / 2
    surf = _surface(look)
    for j, q in enumerate(qs):
        r_, c_ = divmod(j, 2)
        on = hl == j or q.get("highlight")
        fg = TC.BACKGROUND_1 if on else TC.TEXT_1
        shp = _shape(sl, MSO_SHAPE.RECTANGLE, qx + c_ * (qw + g), qy + r_ * (qh + g), qw, qh, f"ov_Quadrant{i}_{j + 1}",
                     fill=TC.TEXT_2 if on else surf)
        bl = []
        if q.get("title"):
            bl.append({"text": _txt(q["title"]), "size": R.bump(12, 0.5), "bold": True, "numbers": False,
                       "color": TC.BACKGROUND_1 if on else TC.TEXT_2})
        for it in q.get("items") or []:
            bl.append({"text": _txt(it), "size": R.bump(11, 0.5), "bullet": True, "space_before": 3, "color": fg})
        if bl:
            bl = _fit_trim(R._grow(bl, qw - 0.24, qh - 0.16, 1), qw - 0.24, qh - 0.16, s,
                           f"quadrant {j + 1} of part {i}", ctx, n)
            _in_shape(shp, bl, ctx, n, (0.12, 0.08, 0.12, 0.08), MSO_ANCHOR.TOP)
    if yl:                                               # y axis: High at the top, Low at the bottom, an arrow up
        _vline(sl, x + gw - 0.05, qy, 2 * qh + g, TC.TEXT_1, 0.75, f"ov_MxYAxis{i}", arrow=True)
        R._write(R._box(sl, x, qy, gw - 0.16, 0.24, f"ov_MxYHigh{i}"),
                 [{"text": yhi, "size": R.bump(10, 0.5), "bold": True, "numbers": False, "align": PP_ALIGN.RIGHT}])
        R._write(R._box(sl, x, qy + 2 * qh + g - 0.24, gw - 0.16, 0.24, f"ov_MxYLow{i}", MSO_ANCHOR.BOTTOM),
                 [{"text": ylo, "size": R.bump(10, 0.5), "bold": True, "numbers": False, "align": PP_ALIGN.RIGHT}])
    if xl:                                               # x axis: Low left, the label centred, High right
        by = qy + 2 * qh + g + 0.05
        ax = R._rule(sl, qx, by, w - gw, TC.TEXT_1, 0.75, f"ov_MxXAxis{i}")
        _arrow(ax)
        lw = max(R._need_w(t, 10, True) for t in (xlo, xhi)) + 0.1
        R._write(R._box(sl, qx, by + 0.03, lw, 0.22, f"ov_MxXLow{i}"),
                 [{"text": xlo, "size": R.bump(10, 0.5), "bold": True, "numbers": False}])
        R._write(R._box(sl, qx + lw, by + 0.03, w - gw - 2 * lw, 0.22, f"ov_MxXLabel{i}"),
                 [{"text": f"{xl} →", "size": R.bump(11, 0.5), "bold": True, "numbers": False, "align": PP_ALIGN.CENTER}])
        R._write(R._box(sl, x + w - lw, by + 0.03, lw, 0.22, f"ov_MxXHigh{i}"),
                 [{"text": xhi, "size": R.bump(10, 0.5), "bold": True, "numbers": False, "align": PP_ALIGN.RIGHT}])


def _ball(sl, cx, cy, d, score, name):
    """Harvey ball: a ring and, for 25/50/75, a native pie wedge filled from 12 o'clock; 100 is a full disc."""
    full = score >= 100
    ring = _shape(sl, MSO_SHAPE.OVAL, cx - d / 2, cy - d / 2, d, d, f"ov_{name}",
                  fill=TC.TEXT_2 if full else TC.BACKGROUND_1, line=TC.TEXT_2, weight=1.0)
    R._alt(ring, f"Harvey ball {score}%")
    if 0 < score < 100:
        pie = _shape(sl, MSO_SHAPE.PIE, cx - d / 2, cy - d / 2, d, d, f"ov_{name}_fill", fill=TC.TEXT_2)
        start = 270.0
        end = (start + 360.0 * score / 100) % 360
        pie.adjustments[0] = start * 60000 / 100000
        pie.adjustments[1] = end * 60000 / 100000


def _score(v, quarters):
    try:
        f = float(str(v).rstrip("%"))
    except (TypeError, ValueError):
        return 0
    f = f * 25 if quarters else f
    return int(max(0, min(100, round(f / 25) * 25)))


def p_harvey(ctx, sl, n, part, box, look, i, s, card):
    x, y, w, h = box
    crit = [_txt(c) for c in part.get("criteria") or part.get("columns") or []][:6]
    rows = [r if isinstance(r, dict) else {"label": str(r[0]), "scores": list(r[1:])} for r in part.get("rows") or []]
    m = max(1, len(crit))
    lw = min(2.4, max(1.2, 0.34 * w))
    cw = (w - lw) / m
    head = [[{"text": c, "size": R.bump(11, 0.5), "bold": True, "numbers": False, "align": PP_ALIGN.CENTER}] for c in crit]
    hh = max([R._need(b, cw - 0.08) for b in head] or [0.3]) + 0.04
    if hh > 0.75:
        head = [[dict(b[0], size=R.bump(10, 0.5))] for b in head]
        hh = min(0.9, max(R._need(b, cw - 0.08) for b in head) + 0.04)
    for c_, b in enumerate(head):
        R._write(R._box(sl, x + lw + c_ * cw + 0.04, y, cw - 0.08, hh, f"ov_HvHead{i}_{c_ + 1}", MSO_ANCHOR.BOTTOM), b)
    R._rule(sl, x, y + hh + 0.04, w, TC.TEXT_1, 0.75, f"HvRule{i}")
    body_y = y + hh + 0.08
    rh = min(1.0, (y + h - body_y) / max(1, len(rows)))
    keep = rows
    while keep and rh < 0.3:
        _note(s, f"part {i}: Harvey-ball row left out: {_txt(keep[-1].get('label'))}")
        keep = keep[:-1]
        rh = (y + h - body_y) / max(1, len(keep))
    allv = [v for r in keep for v in r.get("scores") or []]
    quarters = all(isinstance(v, (int, float)) and 0 <= v <= 4 for v in allv) and any(v not in (0,) for v in allv) \
        and max([float(v) for v in allv] or [0]) <= 4
    d = min(0.34, rh * 0.62, cw * 0.6)
    for r_, r in enumerate(keep):
        ry = body_y + r_ * rh
        lb = [{"text": _txt(r.get("label")), "size": R.bump(12), "numbers": False, "bold": bool(r.get("highlight"))}]
        lb = _fit_trim(lb, lw - 0.1, rh - 0.04, s, f"row {r_ + 1} of part {i}", ctx, n)
        R._write(R._box(sl, x, ry, lw - 0.1, rh, f"HvLabel{i}_{r_ + 1}", MSO_ANCHOR.MIDDLE), lb)
        for c_ in range(m):
            sc = (r.get("scores") or [])[c_] if c_ < len(r.get("scores") or []) else 0
            _ball(sl, x + lw + (c_ + 0.5) * cw, ry + rh / 2, d, _score(sc, quarters), f"Hv{i}_{r_ + 1}_{c_ + 1}")
        if r_ < len(keep) - 1:
            R._rule(sl, x, ry + rh, w, TC.ACCENT_5, 0.5, f"ov_HvRowRule{i}_{r_ + 1}")


def p_quote(ctx, sl, n, part, box, look, i, s, card):
    x, y, w, h = box
    mark = 44 if w >= 3 else 32                         # a hanging quote mark beside the text
    mw = 0.5 if w >= 3 else 0.38
    R._write(R._box(sl, x, y, mw, R._pitch(mark, 1.0) / 72 + 0.06, f"ov_QuoteMark{i}"),
             [{"text": "“", "size": mark, "bold": True, "heading": True, "numbers": False, "color": TC.ACCENT_1,
               "line_spacing": 1.0}])
    x, w, ty = x + mw, w - mw, y + 0.12
    bl = [{"text": _txt(part.get("text") or part.get("quote")), "size": 18, "italic": True, "numbers": False,
           "heading": True, "prov": part.get("prov")}]
    if part.get("attribution"):
        bl.append({"text": "— " + _txt(part["attribution"]).lstrip("—– -"), "size": R.bump(12), "bold": True,
                   "numbers": False, "space_before": 10, "color": TC.TEXT_2})
    bh = y + h - ty
    bl = _fit_trim(R._grow(bl, w - 0.1, bh, 4), w - 0.1, bh, s, f"the quote of part {i}", ctx, n, 11)
    R._write(R._box(sl, x, ty, w, bh, f"Quote{i}"), bl, ctx, n)


DRAW = {"chart": p_chart, "table": p_table, "stats": p_stats, "text": p_text, "callout": p_callout,
        "process": p_process, "timeline": p_timeline, "matrix2x2": p_matrix2x2, "harvey": p_harvey, "quote": p_quote}


def _strings(v, out):
    if isinstance(v, str):
        if v.strip():
            out.append(v.strip())
    elif isinstance(v, dict):
        for k, x in v.items():
            if k not in ("type", "heading", "prov", "kind", "highlight", "values", "scores", "unit", "decimals"):
                _strings(x, out)
    elif isinstance(v, list):
        for x in v:
            _strings(x, out)
    return out


def render_part(ctx, sl, n, part, box, look="editorial", idx=1, s=None):
    """Draw one part inside box (x, y, w, h): the look's card and heading, then the body. A part that cannot be
    drawn is removed whole and its words are shown as plain text, so a slide is never left half-drawn."""
    look = look if look in LOOKS else "editorial"
    kind = part.get("type")
    before = len(sl.shapes._spTree)
    inner, card = _frame(ctx, sl, n, part, box, look, idx, s)
    fn = DRAW.get(kind)
    try:
        if fn is None:
            raise ValueError(f"unknown part type '{kind}'")
        fn(ctx, sl, n, part, inner, look, idx, s, card)
    except (KeyError, TypeError, IndexError, ValueError, AttributeError, ZeroDivisionError) as e:
        tree = sl.shapes._spTree
        for el in list(tree)[before:]:
            if el.tag != qn("p:extLst"):
                tree.remove(el)
        ctx.carry_rows = None
        ctx.overflow.append(f"slide {n}: part {idx} ({kind}) could not be drawn ({type(e).__name__}: {e}); "
                            "shown as text")
        words = _strings(part, [])
        if part.get("heading") and words and words[0] == part["heading"]:
            words = words[1:]
        inner, card = _frame(ctx, sl, n, dict(part, type="text"), box, look, idx, s)
        p_text(ctx, sl, n, {"bullets": words[:8]}, inner, look, idx, s, card)


# ------------------------------------------------------------------ the archetype
def _layout_for(s, k):
    lay = s.get("layout")
    if lay not in LAYOUTS:
        if lay:
            _note(s, f"layout '{lay}' is not one of {', '.join(LAYOUTS)}; chose one by the number of parts")
        lay = BY_COUNT.get(min(max(k, 1), 4))
    if k < LAYOUTS[lay]:
        new = _fewer(lay, k)
        _note(s, f"layout '{lay}' has {LAYOUTS[lay]} regions for {k} parts; used '{new}' so no region is empty")
        lay = new
    return lay


def a_grid(ctx, s, sl, n):
    R._header(ctx, s, sl, n)
    parts = [p for p in s.get("parts") or [] if isinstance(p, dict)]
    look = s.get("look") or "editorial"
    if look not in LOOKS:
        _note(s, f"look '{look}' is not one of {', '.join(LOOKS)}; used editorial")
        look = "editorial"
    lay = _layout_for(s, len(parts))
    boxes = regions(lay)
    if len(parts) > len(boxes):
        _note(s, f"layout '{lay}' has {len(boxes)} regions; parts left out: "
              + "; ".join(str(p.get("heading") or p.get("type")) for p in parts[len(boxes):]))
        parts = parts[:len(boxes)]
    for i, (p, b) in enumerate(zip(parts, boxes), 1):
        render_part(ctx, sl, n, p, b, look, i, s)


# ------------------------------------------------------------------ schema gate
_NEEDS = {"chart": ("chart", "categories"), "table": ("table", "rows"), "stats": ("items",), "text": ("lead", "text", "bullets"),
          "callout": ("text", "bullets", "lead"), "process": ("steps",), "timeline": ("milestones", "items"),
          "matrix2x2": ("quadrants",), "harvey": ("rows",), "quote": ("text", "quote")}


def part_errors(p, i) -> list[str]:
    if not isinstance(p, dict):
        return [f"part {i}: must be an object with a 'type'"]
    t = p.get("type")
    if t not in DRAW:
        return [f"part {i}: type '{t}' is not one of {', '.join(PART_TYPES)}"]
    if not any(p.get(k) for k in _NEEDS[t]):
        return [f"part {i} ({t}): needs one of {', '.join(repr(k) for k in _NEEDS[t])}"]
    errs = []
    if t == "chart":
        c = p.get("chart") if isinstance(p.get("chart"), dict) else p
        if c.get("kind") not in ("stats", "bubble") and (not c.get("categories") or not c.get("series")):
            errs.append(f"part {i} (chart): needs 'categories' and 'series' [{{name, values}}]")
        elif c.get("kind") not in ("stats", "bubble"):
            errs += [f"part {i} (chart): {e}" for e in R.chart_problems(c)]
    if t == "table":
        tb = p.get("table") if isinstance(p.get("table"), dict) else p
        w = len(tb.get("columns") or [])
        if not w or any(len(r) != w for r in tb.get("rows") or [] if isinstance(r, list)):
            errs.append(f"part {i} (table): every row needs {w} cells (one per column)")
    if t == "matrix2x2" and len(p.get("quadrants") or []) != 4:
        errs.append(f"part {i} (matrix2x2): needs exactly 4 quadrants (top-left, top-right, bottom-left, bottom-right)")
    if t == "harvey" and not p.get("criteria"):
        errs.append(f"part {i} (harvey): needs 'criteria' (the columns) and rows [{{label, scores}}]")
    return errs


def grid_errors(s: dict) -> list[str]:
    errs = []
    parts = s.get("parts") or []
    if not isinstance(parts, list):
        return ["'parts' must be a list of parts"]
    for i, p in enumerate(parts, 1):
        errs += part_errors(p, i)
    if s.get("layout") and s["layout"] not in LAYOUTS:
        errs.append(f"layout '{s['layout']}' is not one of {', '.join(LAYOUTS)}")
    if s.get("look") and s["look"] not in LOOKS:
        errs.append(f"look '{s['look']}' is not one of {', '.join(LOOKS)}")
    return errs


def grid_fallback(s: dict) -> dict:
    """Keep every part that can be drawn faithfully (a misleading chart becomes figures, a broken part becomes text);
    with nothing left, the slide becomes a plain argument."""
    s = dict(s)
    keep = []
    for i, p in enumerate(s.get("parts") or [], 1):
        if not isinstance(p, dict):
            continue
        if not part_errors(p, i):
            keep.append(p)
            continue
        if p.get("type") == "chart":
            c = p.get("chart") if isinstance(p.get("chart"), dict) else p
            st = R.stat_stack(c) if c.get("categories") else {"items": []}
            if st["items"]:
                keep.append({"type": "stats", "heading": p.get("heading"), "items": st["items"]})
                continue
        words = _strings(p, [])
        if p.get("heading") and words and words[0] == p["heading"]:
            words = words[1:]
        if words:
            keep.append({"type": "text", "heading": p.get("heading"), "bullets": words[:8]})
    if keep:
        s["parts"] = keep
        if s.get("layout") not in LAYOUTS:
            s.pop("layout", None)
        if s.get("look") not in LOOKS:
            s.pop("look", None)
        return s
    s["archetype"] = "argument_only"
    s["blocks"] = [{"text": "", "bullets": [{"text": t} for t in _strings(s.get("parts") or [], [])[:8]]}]
    return s
