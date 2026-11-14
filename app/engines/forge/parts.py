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
    return re.sub(r"\*\*|\^\w+", "", "" if t is None else str(t))          # a value of 0 still shows


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
    if part.get("_bump"):                                   # the composition's type step (measure() uses the same)
        t["size"] = R.T["cell"] + int(part["_bump"])
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


STAT_VALUE_SIZES = (40, 36, 32, 28, 24, 22, 20)
STAT_GROW = 1.3            # a tile may be this much taller than its content (centred air), never more


def _stat_items(part):
    return [it if isinstance(it, dict) else {"value": str(it), "label": ""} for it in part.get("items") or []][:6]


def _stat_rest(it, size_label):
    rest = [{"text": it.get("label") or "", "size": size_label, "numbers": False, "space_before": 4}]
    if it.get("delta"):
        rest.append({"text": it["delta"], "size": R.bump(11), "bold": True, "numbers": False, "space_before": 2})
    return [b for b in rest if b["text"]]


STAT_UP_SIZES = (60, 54, 48, 44)   # figure sizes a thin slide's stats may step up to (6 pt per type step)


def stats_plan(items, w, boxed, h=None, up=0) -> dict:
    """How k figures sit in a w-wide box: (cols, rows), 'stack' tiles (figure over its label) or 'row' tiles (figure
    left, label right, for wide boxes), the figure size, and the natural tile height. With `h`, the arrangement whose
    natural height uses h best without exceeding it; without, the best-proportioned one. `up` is the composition's
    type step: on a thin slide the figures may run larger than 40 pt and the labels a step or two larger."""
    k = max(1, len(items))
    up = max(0, int(up or 0))
    sizes = tuple(z for z in STAT_UP_SIZES if z <= STAT_VALUE_SIZES[0] + 6 * up) + STAT_VALUE_SIZES
    gap = 0.15 if boxed else 0.2
    pad = 0.12 if boxed else 0.06
    cands = []
    for cols in range(1, k + 1):
        rows = -(-k // cols)
        tw = (w - gap * (cols - 1)) / cols
        for style in ("stack", "row") if tw >= 4.0 else ("stack",):
            vw_max = (tw - 2 * pad) * (0.85 if style == "stack" else 0.38)   # margin: the house face may run wider
            vsize = next((z for z in sizes if all(_head_w(it.get("value", ""), z) <= vw_max for it in items)), None)
            if vsize is None:
                continue
            vw = max(_head_w(it.get("value", ""), vsize) for it in items) * 1.15 + 0.3   # a figure never wraps
            lab = R.bump(12) + (2 if style == "row" else 0) + min(up, 3)
            if style == "stack":
                th = max(R._pitch(vsize, 1.0) / 72 + R._need(_stat_rest(it, lab), tw - 2 * pad) for it in items) + 0.2
            else:
                th = max(max(R._pitch(vsize, 1.0) / 72, R._need(_stat_rest(it, lab), tw - 2 * pad - vw)) for it in items) + 0.22
            total = rows * th + gap * (rows - 1)
            shape = abs(math.log(max(tw, 0.01) / max(th, 0.01) / (1.6 if style == "stack" else 4.0)))
            cands.append({"cols": cols, "rows": rows, "style": style, "vsize": vsize, "vw": vw, "label": lab,
                          "th": th, "tw": tw, "gap": gap, "pad": pad, "total": total,
                          "score": shape + 0.4 * (cols * rows - k) - vsize / 60})
    if not cands:                                          # a figure too wide for any tile: the smallest size, one column
        return {"cols": 1, "rows": k, "style": "stack", "vsize": 20, "vw": w, "label": R.bump(12), "th": 0.9, "tw": w,
                "gap": gap, "pad": pad, "total": k * 0.9 + gap * (k - 1), "score": 0}
    if h is None:
        return min(cands, key=lambda c: c["score"])
    fit = [c for c in cands if c["total"] <= h + 0.01]
    if not fit:
        return min(cands, key=lambda c: c["total"])
    return max(fit, key=lambda c: (min(h, c["total"] * STAT_GROW) - 0.15 * c["score"], -c["score"]))


def p_stats(ctx, sl, n, part, box, look, i, s, card):
    """Figures as tiles sized to their content (at most STAT_GROW taller), figures aligned across a row."""
    x, y, w, h = box
    raw = [it if isinstance(it, dict) else {"value": str(it), "label": ""} for it in part.get("items") or []]
    if len(raw) > 6:
        _note(s, f"part {i}: {len(raw) - 6} figures left out: " + "; ".join(_txt(v) for v in raw[6:]))
    items = raw[:6]
    boxed = LOOKS[look]["card"]
    P = stats_plan(items, w, boxed, h, up=int(part.get("_bump") or 0))
    cols, rows, gap, pad = P["cols"], P["rows"], P["gap"], P["pad"]
    th = min(P["th"] * (STAT_GROW if boxed else 3.0), max(P["th"], (h - gap * (rows - 1)) / rows))   # no card: the
                                                                                    # hairlined figures may spread
    tw = P["tw"]
    used = rows * th + gap * (rows - 1)
    y += max(0.0, (h - used) / 2) if card is None else 0.0
    top_pad = (th - P["th"]) / 2 + 0.08                   # every figure in a row starts on the same line
    for j, it in enumerate(items):
        r_, c_ = divmod(j, cols)
        tx, ty = x + c_ * (tw + gap), y + r_ * (th + gap)
        tile = _shape(sl, MSO_SHAPE.RECTANGLE, tx, ty, tw, th, f"ov_role_numeral_Stat{i}_{j + 1}",
                      fill=TC.BACKGROUND_1 if boxed else None)
        val = [{"text": _plain(it.get("value", "")), "size": P["vsize"], "bold": True, "heading": True, "numbers": False,
                "color": TC.ACCENT_1, "line_spacing": 1.0, "prov": it.get("prov")}]
        rest = _stat_rest(it, P["label"])
        if P["style"] == "stack":
            iw = tw - 2 * pad
            rest, ok = R._fit(rest, iw, th - 0.1 - R._pitch(P["vsize"], 1.0) / 72, FLOOR)
            _in_shape(tile, val + rest, ctx, n, (pad, top_pad, pad, 0.04), MSO_ANCHOR.TOP)
        else:                                             # figure left, its label right, both centred on the row
            _in_shape(tile, val, ctx, n, (pad, 0.04, tw - pad - P["vw"], 0.04), MSO_ANCHOR.MIDDLE)
            iw = tw - 2 * pad - P["vw"]
            rest, ok = R._fit(rest, iw, th - 0.12, FLOOR)
            R._write(R._box(sl, tx + pad + P["vw"], ty + 0.04, iw, th - 0.08, f"ov_StatLabel{i}_{j + 1}",
                            MSO_ANCHOR.MIDDLE), rest, ctx, n)
        if not ok:
            ctx.miss(n, f"figure {j + 1} of part {i}", 3)
        if not boxed:                                    # hairlines between tiles, not boxes around them
            if c_ < cols - 1 and j + 1 < len(items):
                _vline(sl, tx + tw + gap / 2, ty + 0.08, th - 0.16, name=f"ov_StatRuleV{i}_{j + 1}")
            if r_ < rows - 1:
                R._rule(sl, tx, ty + th + gap / 2, tw, TC.ACCENT_5, 0.5, f"ov_StatRuleH{i}_{j + 1}")


def _text_blocks(part, color=TC.TEXT_1, lead_size=None, size=None):
    up = int(part.get("_bump") or 0)                      # the composition's type step for this part
    lead_size, size = (lead_size or R.bump(14)) + up, (size or R.bump(12)) + up
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
    room = max(0, MAX_BUMP.get(R.DENSITY["mode"], 3) - int(part.get("_bump") or 0))   # never past the mode's limit
    bl = _fit_trim(R._grow(bl, w, h, min(room, 6 if R.DENSITY["mode"] == "present" else 2)), w, h, s,
                   f"the text of part {i}", ctx, n)     # presenting: short text steps well up the scale
    if card is not None and not part.get("heading"):
        R._shape_text(card, bl, ctx, n, (x, y, w, h))   # the card is the text's own shape
        return
    R._write(R._box(sl, x, y, w, h, f"Text{i}", MSO_ANCHOR.MIDDLE if card is not None else MSO_ANCHOR.TOP),
             bl, ctx, n)                                # in a card, the measuring margin is shared above and below


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
    d, g = 0.2, 0.05
    sw = (w + (k - 1) * (d - g)) / k
    tw = sw - 2 * d - 0.14
    words = [wd for st in steps for wd in re.findall(r"\S+", _txt(st.get("title")) + " " + _txt(st.get("text")))]
    fits = all(R._need_w(wd, R.bump(12), True) * 1.15 <= tw for wd in words)   # else a word breaks mid-word
    if w / k >= 1.25 and w > h * 0.9 and fits:          # wide: interlocking chevrons, text inside
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


TL_STEPS = (4, 2, 0)                                    # type steps a horizontal timeline takes, largest first


def _tl_label(m, z, on=False):
    out = [{"text": _txt(m.get("label") or m.get("title")), "size": R.bump(12) + z, "numbers": False,
            "align": PP_ALIGN.CENTER, "bold": bool(m.get("text")) or on, "prov": m.get("prov")}]
    if m.get("text"):
        out.append({"text": _txt(m["text"]), "size": R.bump(11) + z, "align": PP_ALIGN.CENTER, "space_before": 4})
    return out


def _tl_band(ms, cw, z, dot=0.18):
    """(dates blocks, date height, label height, band height) of a horizontal timeline at type step z."""
    dates = [[{"text": _txt(m.get("date")), "size": R.bump(12) + z, "bold": True, "numbers": False,
               "color": TC.ACCENT_1, "align": PP_ALIGN.CENTER}] for m in ms]
    dh = max([R._need(d, cw) for d in dates if d[0]["text"]] or [0.0]) + 0.04
    ln = max([R._need(_tl_label(m, z), cw) for m in ms] or [0.0]) + 0.04
    return dates, dh, ln, dh + 0.16 + dot + 0.12 + ln


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
        lab = lambda j, z: _tl_label(ms[j], z, hl == j)  # noqa: E731
        for z in TL_STEPS:                               # the largest type at which the band fits its box; the band
            dates, dh, ln, band = _tl_band(ms, cw, z)    # sits at the top of the box (the composition gave the box
            if band <= h or z == 0:                      # its height from the band: no white above and below it)
                break
        h = min(h, band + 0.04)
        ly = y + dh + 0.16 + dot / 2
        R._rule(sl, x, ly, w, TC.TEXT_2, 1.5, f"ov_TimelineAxis{i}")
        lh = max(0.2, y + h - (ly + dot / 2 + 0.12))
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


def _num(v):
    try:
        return float(str(v).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def _score(v, quarters):
    f = _num(v)
    if f is None:                                       # blank, n/a: an empty ball
        return 0
    f = f * 25 if quarters and not str(v).strip().endswith("%") else f
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
    # 0-4 scale or percent, judged on the numeric scores only ("3" counts as 3; blanks, n/a and "75%" do not decide)
    nums = [f for v in (v for r in keep for v in r.get("scores") or []) if not str(v).strip().endswith("%")
            for f in [_num(v)] if f is not None]
    quarters = bool(nums) and all(0 <= f <= 4 for f in nums) and any(f != 0 for f in nums)
    d = min(0.34, rh * 0.62, cw * 0.6)
    for r_, r in enumerate(keep):
        ry = body_y + r_ * rh
        lb = [{"text": _txt(r.get("label")), "size": R.bump(12), "numbers": False, "bold": bool(r.get("highlight"))}]
        lb = _fit_trim(lb, lw - 0.1, rh - 0.04, s, f"row {r_ + 1} of part {i}", ctx, n)
        R._write(R._box(sl, x, ry, lw - 0.1, rh, f"ov_HvLabel{i}_{r_ + 1}", MSO_ANCHOR.MIDDLE), lb)
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


# Column model of each layout on the 24-column grid: ((first col, last col), [part slots top to bottom]).
COLUMNS = {
    "full": [((0, 23), [0])],
    "halves": [((0, 11), [0]), ((12, 23), [1])],
    "halves_v": [((0, 23), [0, 1])],
    "thirds": [((0, 7), [0]), ((8, 15), [1]), ((16, 23), [2])],
    "quarters": [((0, 11), [0, 2]), ((12, 23), [1, 3])],
    "main_side": [((0, 15), [0]), ((16, 23), [1])],
    "side_main": [((0, 7), [0]), ((8, 23), [1])],
    "main_stack": [((0, 11), [0]), ((12, 23), [1, 2])],
    "stack_main": [((0, 11), [0, 1]), ((12, 23), [2])],
}
FLEX = ("chart", "process", "timeline", "matrix2x2")        # exhibits that take the height they are given
MIN_H = {"chart": 2.0, "process": 1.3, "timeline": 1.4, "matrix2x2": 2.6}
MAX_PARTS = 4
BUMPS = (0, 1, 2, 3)                                         # type steps a text part may take to use its column
MAX_BUMP = {"read": 3, "balanced": 5, "present": 6}          # ... at most, by density: a thin balanced slide's text
                                                             # may reach 18 pt (a presented slide's body size)
RHYTHM_MAX = 0.45         # what a thin column has left after its exhibits and type grew goes into the gaps between its
                          # parts, one equal gap each, up to this much (never into a part: a card fits its content)


def _overhead(part, w, look) -> tuple[float, float]:
    """(height the frame takes above and below the body, horizontal padding per side), as _frame draws it."""
    L = LOOKS[look]
    card = L["card"] and part.get("type") != "callout"
    head = _plain(part.get("heading") or "").strip()
    used = 0.0
    ww = w - (L["inset"] or 0)
    if head:
        if L["heading"] == "tab":
            bl, _ = R._fit([{"text": head, "size": R.bump(13), "bold": True}], ww - 0.24, 0.62, 11)
            used = max(0.36, R._need(bl, ww - 0.24) + 0.1)
        else:
            bl, _ = R._fit([{"text": head, "size": R.bump(14), "bold": True}], ww, 0.62, 12)
            used = R._need(bl, ww) + 0.02 + (0.03 if L["heading"] == "rule" else 0)
        used += L["after"]
    pad = L["pad"] if card else 0.0
    return used + (pad if head else 2 * pad) + (L["inset"] or 0), pad


def _chart_of(part):
    return part.get("chart") if isinstance(part.get("chart"), dict) else {k: v for k, v in part.items()
                                                                         if k not in ("type", "heading")}


def measure(part, w, look, bump=0) -> tuple[float, float, float]:
    """(min, natural, max) height of a part drawn w wide, frame included. Text, callouts, tables and quotes have one
    natural height (they never stretch); figures may grow a little; charts, processes, timelines and 2x2s are
    exhibits that take what they are given."""
    oh, pad = _overhead(part, w, look)
    iw = w - 2 * pad
    t = part.get("type")
    if t == "chart":
        c = _chart_of(part)
        if c.get("kind") not in ("stats", "bubble") and R.chart_problems(c):
            st = R.stat_stack(c)
            if st["items"]:
                return measure({"type": "stats", "heading": part.get("heading"), "items": st["items"]}, w, look)
        unit = (R._need([{"text": part["unit"], "size": R.bump(11)}], iw) + 0.06) if part.get("unit") else 0
        nat = R.chart_natural_h(c, iw)
        if nat:
            return oh + unit + nat, oh + unit + nat, oh + unit + nat
        return oh + unit + MIN_H["chart"], oh + unit + 3.2, 99.0
    if t == "timeline" and iw >= 4.0:                     # horizontal: the band at its largest type is its most
        ms = [m if isinstance(m, dict) else {"label": str(m)} for m in part.get("milestones") or part.get("items") or []][:8]
        cw = iw / max(1, len(ms)) - 0.12
        band = lambda z: _tl_band(ms, cw, z)[3] * 1.08 + 0.04   # noqa: E731  the renderer sets labels looser
        top = band(TL_STEPS[0])
        return oh + min(MIN_H[t], top), oh + min(top, band(TL_STEPS[1])), oh + top
    if t in FLEX:
        return oh + MIN_H[t], oh + MIN_H[t] + 0.8, 99.0
    if t == "stats":
        P = stats_plan(_stat_items(part), iw, LOOKS[look]["card"], up=bump)
        return oh + P["total"], oh + P["total"], oh + P["total"] * (STAT_GROW if LOOKS[look]["card"] else 3.0)
    mw = 0.94 if bump else 1.0                           # grown type is measured a little narrower: the renderer's
                                                         # face sets wider than measured, and large text wraps sooner
    if t == "text":
        need = R._need(_text_blocks(dict(part, _bump=bump)), iw * mw) * 1.08 + 0.06   # LibreOffice sets the house
        return oh + need, oh + need, oh + need                                    # face a little looser than measured
    if t == "callout":
        need = R._need(_text_blocks(dict(part, _bump=bump), TC.BACKGROUND_1, R.bump(16), R.bump(13)),
                       (iw - 0.5) * mw) * 1.08 + 0.34
        return oh + need, oh + need, oh + need
    if t == "table":
        tb = dict(part.get("table") if isinstance(part.get("table"), dict) else part)
        tb["columns"] = [R._txt(c) for c in tb.get("columns") or []]
        tb["rows"] = [[R._cell(c) for c in (r if isinstance(r, list) else [r])] for r in tb.get("rows") or []]
        if bump:
            tb["size"] = R.T["cell"] + bump
        from types import SimpleNamespace
        ctx = SimpleNamespace(carry_rows=None)
        need = R._table(ctx, None, 0, dict(tb), 0, 0, iw, 20.0, dry=True) * 1.06 + 0.1   # rendered rows run a little
        return oh + need, oh + need, oh + need                                           # taller than measured
    if t == "harvey":
        k = len(part.get("rows") or [])
        nat = 0.5 + 0.5 * k
        return oh + 0.4 + 0.32 * k, oh + nat, oh + 0.5 + 0.8 * k
    if t == "quote":
        bl = [{"text": _txt(part.get("text") or part.get("quote")), "size": 18 + bump, "italic": True}]
        if part.get("attribution"):
            bl.append({"text": _txt(part["attribution"]), "size": R.bump(12), "space_before": 10})
        need = R._need(bl, iw - 0.62) * 1.12 + 0.3            # the quote mark's column and its top offset
        return oh + need, oh + need, oh + need * 1.2
    return oh + 1.0, oh + 1.0, 99.0


ALTERNATIVES = {1: ["full"], 2: ["halves", "main_side", "side_main", "halves_v"],
                3: ["main_stack", "stack_main", "thirds", "main_side", "side_main", "halves", "halves_v"],
                4: ["quarters", "main_stack", "stack_main", "main_side", "side_main"]}
TEXTY = ("text", "callout", "quote")      # tables keep their size: a wrapped header is hard to measure dry
GROWS = TEXTY + ("stats",)                 # parts that step up the type scale on a thin slide
KEEP_EMPTY = 0.10                # the asked-for layout stays when it leaves at most this share of the body empty


def _compose_one(parts, lay, look, top, bottom):
    """One layout's composition: (placements, overflow in inches, empty share of the body)."""
    cols = [[span, list(slots)] for span, slots in COLUMNS[lay]]
    nreg = sum(len(sl_) for _, sl_ in cols)
    H = bottom - top
    widths = [R.span24(*sp)[1] for sp, _ in cols]
    for extra in range(nreg, min(len(parts), MAX_PARTS)):   # an extra part goes where it fits best at that width
        room = [H - sum(measure(parts[j], widths[c], look)[1] + GAP for j in cols[c][1] if j < len(parts))
                - measure(parts[extra], widths[c], look)[1] - GAP for c in range(len(cols))]
        cols[max(range(len(cols)), key=lambda c: room[c])][1].append(extra)
    out, over_all, empty = [], 0.0, 0.0
    for (sp, slots), w in zip(cols, widths):
        x = R.span24(*sp)[0]
        slots = [j for j in slots if j < len(parts)]
        if not slots:
            empty += H * w
            continue
        gaps = GAP * (len(slots) - 1)
        bumps = {j: 0 for j in slots}
        ms = {j: measure(parts[j], w, look) for j in slots}
        # step text and figures up the type scale while the column has room and no exhibit would take it anyway
        # (a callout and the figures at most two steps: a callout is already large, a figure grows 6 pt a step)
        step = lambda j, b: (min(b, 2) if parts[j].get("type") == "callout" else  # noqa: E731
                             min(b, 3) if parts[j].get("type") == "stats" else b)
        if not any(parts[j].get("type") in FLEX and ms[j][2] > 50 for j in slots):   # an exhibit that takes any
                                                                                       # height takes the room first
            for b in range(1, MAX_BUMP.get(R.DENSITY["mode"], BUMPS[-1]) + 1):
                trial = {j: (measure(parts[j], w, look, step(j, b))
                             if parts[j].get("type") in GROWS else ms[j]) for j in slots}
                if sum(m[1] for m in trial.values()) + gaps > H:
                    break
                ms = trial
                bumps = {j: (step(j, b) if parts[j].get("type") in GROWS else 0) for j in slots}
        hs = {j: ms[j][1] if parts[j].get("type") not in FLEX else ms[j][0] for j in slots}
        slack = H - sum(hs.values()) - gaps
        for _ in range(4):                                 # exhibits take the room first, then figures (capped)
            grow_ = [j for j in slots if ms[j][2] > hs[j] + 0.01]
            grow_ = [j for j in grow_ if parts[j].get("type") in FLEX] or grow_
            if slack <= 0.01 or not grow_:
                break
            share = slack / len(grow_)
            for j in grow_:
                add = min(share, ms[j][2] - hs[j])
                hs[j] += add
                slack -= add
        over = sum(hs.values()) + gaps - H
        if over > 0.01:                                    # too tall: exhibits to their minimum, then the tallest part
            for j in slots:
                if parts[j].get("type") in FLEX and over > 0:
                    take = min(over, hs[j] - ms[j][0])
                    hs[j] -= take
                    over -= take
            if over > 0.01:
                over_all += over
                last = max(slots, key=lambda j: hs[j])
                hs[last] = max(0.6, hs[last] - over)       # the part fits what it can and notes the rest
        gap = GAP
        if len(slots) > 1 and slack > 0.01:                # what is left: a larger, equal rhythm between the parts
            gap = GAP + min(slack / (len(slots) - 1), RHYTHM_MAX - GAP)
        y = top
        for j in slots:
            out.append((j, (x, y, w, hs[j]), dict(parts[j], _bump=bumps[j]) if bumps[j] else parts[j]))
            y += hs[j] + gap
        empty += max(0.0, H - (y - gap - top)) * w
    # side-by-side single cards of nearly one height share it (aligned feet); a much shorter card keeps its own
    xs = {}
    for o in out:
        xs[o[1][0]] = xs.get(o[1][0], 0) + 1
    row = [o for o in out if abs(o[1][1] - top) < 1e-6 and xs[o[1][0]] == 1 and o[2].get("type") not in FLEX]
    if len(row) >= 2:
        tallest = max(o[1][3] for o in out if abs(o[1][1] - top) < 1e-6 and xs[o[1][0]] == 1)
        for k_, (j, (x, y, w, h), p) in enumerate(out):
            if (j, (x, y, w, h), p) in row and h >= 0.8 * tallest:
                out[k_] = (j, (x, y, w, tallest), p)
    return out, over_all, empty / (H * (R.RIGHT - R.LEFT))


def compose(parts, lay, look, top, bottom, s=None) -> list[tuple[int, tuple, dict]]:
    """The slide's parts as one composition: each column of the layout stacks its parts at one gap; text, tables and
    figures take their natural height (stepping up the type scale while the column has room); exhibits take the
    rest; parts beyond the layout's regions are stacked where they fit best, never dropped. When the asked-for
    layout would overflow or leave much of the body empty, the layout that fits the content best is used instead
    (deterministic, scored on overflow and empty area). Returns [(part index, (x, y, w, h), part with its type
    step)]."""
    if len(parts) > MAX_PARTS:
        _note(s, "parts left out (at most four on a slide): "
              + "; ".join(str(p.get("heading") or p.get("type")) for p in parts[MAX_PARTS:]))
        parts = parts[:MAX_PARTS]
    k = len(parts)
    best = None
    for cand in [lay] + [c for c in ALTERNATIVES.get(k, []) if c != lay]:
        if LAYOUTS[cand] > k:
            continue
        placed, over, empty = _compose_one(parts, cand, look, top, bottom)
        if cand == lay and (any(p.get("type") == "table" for p in parts) or over <= 0.01 and empty <= KEEP_EMPTY):
            return placed                                  # the asked-for layout fits (and fills): keep it; a table
                                                           # is never moved into a narrower column for fill alone
        score = 10 * over + empty + (0 if cand == lay else 0.06)
        if best is None or score < best[0] - 1e-9:
            best = (score, cand, placed)
    if best[1] != lay:
        _note(s, f"layout '{lay}' changed to '{best[1]}': the parts fit it better (no overflow, less empty space)")
    return best[2]


def a_grid(ctx, s, sl, n):
    R._header(ctx, s, sl, n)
    parts = [p for p in s.get("parts") or [] if isinstance(p, dict)]
    look = s.get("look") or "editorial"
    if look not in LOOKS:
        _note(s, f"look '{look}' is not one of {', '.join(LOOKS)}; used editorial")
        look = "editorial"
    lay = _layout_for(s, len(parts))
    if len(parts) > LAYOUTS[lay]:
        _note(s, f"layout '{lay}' has {LAYOUTS[lay]} regions for {len(parts)} parts; the extra parts were stacked")
    notes = s.get("footnotes") or []
    notes_h = (0.14 * max(1, (len("    ".join(notes)) // 190) + 1) + 0.04) if notes else 0
    bottom = R.FOOT_RULE_Y - 0.08 - notes_h if notes else R.BODY_B
    if notes:
        ctx.notes_top = R.FOOT_RULE_Y - 0.03 - notes_h
    for j, box, p in compose(parts, lay, look, R.BODY_Y, bottom, s):
        render_part(ctx, sl, n, p, box, look, j + 1, s)


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
