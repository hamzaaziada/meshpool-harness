"""Forge renderer v2: slide JSON -> native .pptx in the reference consulting grammar. Deterministic.

The grammar (measured on McKinsey reference pages; docs/deck-engine/design-grammar.md) lives here, so no model
output can fall back to a card-template look:
- structure from white space and hairline rules (under the title, under column headers, between rows, above the
  source), never from tinted cards;
- emphasis is typographic: number phrases in body text are bold accent blue, **marked phrases** are bold, blocks
  open with a bold lead line, bullets are small accent squares;
- one highlight per exhibit: a coral table row, a cyan focus point, a navy "so what" box with an arrow;
- heavy bold serif title in the text colour with a full-width rule; exhibit subtitle in regular sans under it;
- footer system: footnotes, rule, source line, firm label and page number.
Theme colours and theme fonts only; native charts and tables. Writes <out>.pptx and <out>.provenance.json.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import BubbleChartData, CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION, XL_TICK_LABEL_POSITION
from pptx.enum.dml import MSO_THEME_COLOR as TC
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from .common import positive_axis_ids
from .template import default_template, layout

ICONS = Path(__file__).resolve().parents[1] / "slides" / "icons"
ICON_CACHE = Path(__import__("tempfile").gettempdir()) / "forge_icons"

LEFT, RIGHT = 0.6, 12.733
COLS, GUT = 24, 0.125            # 24-column grid (reference decks use a fine grid); span()/colx() take 12-column units
CW = (RIGHT - LEFT - GUT * (COLS - 1)) / COLS
TITLE_RULE_Y, SUB_Y, BODY_Y, BODY_B = 1.42, 1.06, 1.55, 6.62
FOOTNOTE_Y, FOOT_RULE_Y, SRC_Y = 6.62, 7.02, 7.06
# type scale measured on the reference pages (pt): one title size everywhere, body size set by density
T = {"title": 25, "sub": 18, "note": 8, "src": 9, "tag": 9, "label": 12, "body": 12, "lead": 12, "cell": 12,
     "head": 14, "num": 20}
DENSE = {"cell": 10, "lead": 10, "head": 14, "num": 20, "label": 10}            # challenge matrix (page 8)
ARGUMENT = {"intro": 14, "bullet": 12, "callout": 12, "exhibit": 16, "unit": 12}  # exhibit + argument (page 3)
FIRM = "Meshpool"
LEADING = 1.08                   # exact line pitch as a multiple of size (12 pt text on 13 pt, as in the reference);
                                 # exact points render identically in PowerPoint and LibreOffice, proportional does not


def _pitch(size, factor=LEADING):
    return round(size * factor * 2) / 2
_B = r"(?<![\w\-'/.])"                       # not glued to a word, a range or a date
_E = r"(?![\w\-%/])"
NUM_RE = re.compile(
    _B + r"~?[-+]?\d+(?:\.\d+)?%" + r"(?![\w\-/])"                                   # 34%, ~2%
    + r"|" + _B + r"~?[$€£]\d[\d,]*(?:\.\d+)?(?:\s?(?:K|M|B|bn|million|billion)\b)?(?:/year|/day)?"  # $1.5 billion
    + r"|" + _B + r"\d[\d,]*(?:\.\d+)?\s?(?:million|billion|thousand)\b\+?"                 # 1 million+
    + r"|" + _B + r"\d+(?:st|nd|rd|th)\b"                                                      # 3rd
    + r"|" + _B + r"\d{1,3}(?:,\d{3})+" + _E                                                  # 10,000
    + r"|" + _B + r"\d+\.\d+" + _E                                                           # 4.3, 33.7
    + r"|[¼½¾⅓⅔]")
ORD_RE = re.compile(r"^(\d+)(st|nd|rd|th)$")


def colx24(k: int) -> float:
    return LEFT + k * (CW + GUT)


def span24(a: int, b: int) -> tuple[float, float]:
    return colx24(a), colx24(b) + CW - colx24(a)


def colx(k: int) -> float:
    """Left edge of 12-column unit k."""
    return colx24(2 * k)


def span(a: int, b: int) -> tuple[float, float]:
    """(x, width) covering 12-column units a..b inclusive."""
    return span24(2 * a, 2 * b + 1)


# ------------------------------------------------------------------ rich text
def _set_font(run, size, color=TC.TEXT_1, bold=False, italic=False, heading=False, sup=False):
    f = run.font
    f.size = Pt(size)
    f.bold = bold
    f.italic = italic
    f.color.theme_color = color
    rpr = run._r.get_or_add_rPr()
    if sup:
        rpr.set("baseline", "30000")
    old = rpr.find(qn("a:latin"))
    if old is not None:
        rpr.remove(old)
    rpr.append(rpr.makeelement(qn("a:latin"), {"typeface": "+mj-lt" if heading else "+mn-lt"}))


def _segments(text: str, numbers: bool, base: str = "plain"):
    """(str, style): **x** bold (num if x is a number phrase), ^x superscript, bare number phrases num."""
    out = []
    for part in re.split(r"(\*\*[^*]+\*\*)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            inner = part[2:-2]
            whole = numbers and re.search(r"\d|[¼½¾]", inner)    # a bold phrase carrying a number is an emphasis
            out += _segments(inner, False, "num" if whole else "bold")
            continue
        for chunk in re.split(r"(\^\w+)", part):
            if not chunk:
                continue
            if chunk.startswith("^"):
                out.append((chunk[1:], "sup_" + base))
                continue
            if not numbers:
                out.append((chunk, base))
                continue
            last = 0
            for m in NUM_RE.finditer(chunk):
                if m.start() > last:
                    out.append((chunk[last:m.start()], base))
                om = ORD_RE.match(m.group(0))
                if om:
                    out += [(om.group(1), "num"), (om.group(2), "sup_num")]
                else:
                    out.append((m.group(0), "num"))
                last = m.end()
            if last < len(chunk):
                out.append((chunk[last:], base))
    return out


def _rich(p, text, size, color=TC.TEXT_1, bold=False, numbers=True, heading=False, italic=False):
    for seg, st in _segments(text, numbers):
        r = p.add_run()
        r.text = seg
        sup = st.startswith("sup_")
        st = st[4:] if sup else st
        if st == "num":
            _set_font(r, size, TC.ACCENT_1, True, italic, heading, sup)
        elif st == "bold":
            _set_font(r, size, color, True, italic, heading, sup)
        else:
            _set_font(r, size, color, bold, italic, heading, sup)


def _bullet(p, level=0, color="accent1"):
    pPr = p._p.get_or_add_pPr()
    pPr.set("marL", str(int(Inches(0.2 + 0.2 * level))))
    pPr.set("indent", str(int(-Inches(0.2))))
    clr = pPr.makeelement(qn("a:buClr"), {})
    clr.append(clr.makeelement(qn("a:schemeClr"), {"val": color}))
    pPr.append(clr)
    pPr.append(pPr.makeelement(qn("a:buSzPct"), {"val": "70000"}))
    pPr.append(pPr.makeelement(qn("a:buFont"), {"typeface": "Arial"}))
    pPr.append(pPr.makeelement(qn("a:buChar"), {"char": "▪"}))


def _box(sl, x, y, w, h, name, anchor=MSO_ANCHOR.TOP, pad=0.0):
    tb = sl.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    bp = tf._txBody.find(qn("a:bodyPr"))
    for c in list(bp):
        bp.remove(c)
    tf.margin_left = tf.margin_right = Inches(pad)
    tf.margin_top = tf.margin_bottom = Inches(0.02)
    tf.vertical_anchor = anchor
    return tb


def _shape_text(shp, blocks, ctx, n, area, anchor=MSO_ANCHOR.TOP):
    """Write the body into the shape itself (its own text frame, insets set so the text sits in `area`), so a person
    editing the deck edits one object, not a text box floating on a box."""
    sx, sy = shp.left / 914400, shp.top / 914400
    sw, sh = shp.width / 914400, shp.height / 914400
    x, y, w, h = area
    tf = shp.text_frame
    tf.word_wrap = True
    bp = tf._txBody.find(qn("a:bodyPr"))
    for c in list(bp):
        bp.remove(c)
    for k in list(bp.attrib):
        del bp.attrib[k]
    tf.margin_left, tf.margin_top = Inches(max(0.0, x - sx)), Inches(max(0.0, y - sy))
    tf.margin_right, tf.margin_bottom = Inches(max(0.0, sx + sw - x - w)), Inches(max(0.0, sy + sh - y - h))
    tf.vertical_anchor = anchor
    for b in blocks:
        b.setdefault("align", PP_ALIGN.LEFT)
    _write(shp, blocks, ctx, n)
    return shp


def _write(tb, blocks, ctx=None, n=None):
    """blocks: dicts {text, size, color, bold, bullet, level, space_before, align, italic, numbers, heading, prov}."""
    tf = tb.text_frame
    for i, b in enumerate(blocks):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        if b.get("bullet"):
            _bullet(p, b.get("level", 0))
        if b.get("align") is not None:
            p.alignment = b["align"]
        if b.get("space_before"):
            p.space_before = Pt(b["space_before"])
        p.line_spacing = Pt(_pitch(b.get("size", T["body"]), b.get("line_spacing", LEADING)))
        _rich(p, b["text"], b.get("size", T["body"]), b.get("color", TC.TEXT_1), b.get("bold", False),
              b.get("numbers", True), b.get("heading", False), b.get("italic", False))
        if ctx is not None and n is not None:
            ctx.track(n, b["text"], b.get("prov"))
    return tb


def _strip_style(shape):
    st = shape._element.find(qn("p:style"))
    if st is not None:
        shape._element.remove(st)


def _rule(sl, x, y, w, color=TC.ACCENT_5, weight=0.75, name="Rule"):
    c = sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x), Inches(y), Inches(x + w), Inches(y))
    c.name = name
    _strip_style(c)
    c.line.color.theme_color = color
    c.line.width = Pt(weight)
    return c


def _fill(sl, x, y, w, h, color, name, shape=MSO_SHAPE.RECTANGLE):
    s = sl.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    s.name = name
    _strip_style(s)
    s.fill.solid()
    s.fill.fore_color.theme_color = color
    s.line.fill.background()
    return s


def _alt(shape, text):
    shape._element.find(".//" + qn("p:cNvPr")).set("descr", text[:250])


ICON_MEANINGS = {"safety": "shield-check", "congestion": "traffic-cone", "traffic": "traffic-cone", "equity": "scale",
                 "access": "accessibility", "curbside": "store", "curb": "store", "parking": "square-parking",
                 "ridership": "bus", "transit": "train-front", "health": "heart-pulse", "money": "circle-dollar-sign",
                 "funding": "circle-dollar-sign", "cost": "circle-dollar-sign", "people": "users", "workforce": "users",
                 "growth": "trending-up", "decline": "trending-down", "risk": "triangle-alert", "time": "clock",
                 "ai": "cpu", "technology": "cpu", "deal": "handshake", "deals": "handshake", "scale": "maximize",
                 "scope": "layers", "capital": "landmark", "valuation": "gauge", "diligence": "search-check",
                 "integration": "puzzle", "region": "globe", "regions": "globe", "industry": "factory"}


@lru_cache(maxsize=256)
def _icon_png(name: str, hexcolor: str) -> str | None:
    import cairosvg
    from engines.slides.archetypes import icon_name as _base_icon_name

    def icon_name(k):
        for w in re.split(r"[-\s_]+", (k or "").lower()):
            if w in ICON_MEANINGS and (ICONS / f"{ICON_MEANINGS[w]}.svg").exists():
                return ICON_MEANINGS[w]
        return _base_icon_name(k)
    nm = icon_name(name)
    src = ICONS / f"{nm}.svg"
    if not src.exists():
        return None
    ICON_CACHE.mkdir(exist_ok=True)
    out = ICON_CACHE / f"{nm}-{hexcolor}.png"
    if not out.exists():
        svg = src.read_text().replace("currentColor", f"#{hexcolor}").replace('stroke-width="2"', 'stroke-width="1.25"')
        cairosvg.svg2png(bytestring=svg.encode(), write_to=str(out), output_width=192, output_height=192)
    return str(out)


def _icon(sl, name, x, y, d, meaning=""):
    """Thin line icon (Lucide, ISC) in a thin circle."""
    from .template import COLORS
    circ = sl.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(d), Inches(d))
    circ.name = f"ov_IconRing_{name}"
    _strip_style(circ)
    circ.fill.background()
    circ.line.color.theme_color = TC.TEXT_1
    circ.line.width = Pt(0.75)
    png = _icon_png(name, COLORS["dk1"])
    if png:
        pad = d * 0.24
        pic = sl.shapes.add_picture(png, Inches(x + pad), Inches(y + pad), Inches(d - 2 * pad), Inches(d - 2 * pad))
        pic.name = f"ov_Icon_{name}"
        _alt(pic, f"icon:lucide:{name}:{meaning or name}")


def _need(blocks, w) -> float:
    """Height the blocks need at their set sizes (Arial metrics via Liberation Sans)."""
    from .common import font_file, wrap_lines
    reg, _ = font_file("Arial", False, False, (("Arial", "Liberation Sans"),))
    bold, _ = font_file("Arial", True, False, (("Arial", "Liberation Sans"),))
    h = 0.05
    for b in blocks:
        size = b.get("size", T["body"])
        ind = 0.2 + 0.2 * b.get("level", 0) if b.get("bullet") else 0
        plain = re.sub(r"\*\*|\^\w+", "", b["text"])
        lines = wrap_lines(plain, w - ind, size, bold if b.get("bold") else reg, 0.98)
        h += lines * _pitch(size, b.get("line_spacing", LEADING)) / 72 + (b.get("space_before") or 0) / 72
    return h


def _cont_title(t: str) -> str:
    head = t.split(":")[0] if ":" in t and len(t.split(":")[0]) < 60 else " ".join(t.split()[:8])
    return head.rstrip(" .…") + " (continued)"


def _title_size(text: str, width: float, lines_max: int = 2):
    """25 pt, then 22, then 20: the largest size at which the title fits in two lines (Georgia Bold metrics)."""
    from .common import font_file, wrap_lines
    f, _ = font_file("Georgia", True, False, (("Georgia", "Gelasio"),))
    for size in (T["title"], 22, 20):
        if wrap_lines(text, width - 0.2, size, f, 0.97) <= lines_max:
            return size
    return None


def _fit(blocks, w, h, floor):
    """Shrink every block by 0.5 pt steps (keeping relative sizes) until it fits or reaches `floor`.
    Returns (blocks, fits). The renderer never lets text run past its box silently: a miss is reported."""
    import copy
    for step in range(0, 5):
        bl = copy.deepcopy(blocks)
        for b in bl:
            b["size"] = max(floor, b.get("size", T["body"]) - step)
        if _need(bl, w) <= h:
            return bl, True
    return bl, False


# ------------------------------------------------------------------ frame
class Ctx:
    def __init__(self, prov):
        self.prov = prov
        self.overflow = []

    def miss(self, n, where, words):
        self.overflow.append(f"slide {n}: {where} does not fit at the minimum size; cut about {words} words")

    def track(self, n, text, prov):
        ids = [p for p in (prov or []) if p]
        if not text or not ids:
            return
        for m in NUM_RE.finditer(re.sub(r"\*\*|\^\w+", "", text)):
            self.prov.setdefault(str(n), []).append({"text": m.group(0).strip(), "prov": ids[0]})


def _header(ctx, s, sl, n, title_w=None):
    t = sl.shapes.title
    sub = bool(s.get("subtitle"))
    # always write the full geometry: setting one dimension on an inherited placeholder resets the others
    t.left, t.width = Inches(LEFT), Inches(title_w or (RIGHT - LEFT))
    t.top, t.height = (Inches(0.2), Inches(0.86)) if sub else (Inches(0.42), Inches(0.92))
    tf = t.text_frame
    tf.text = ""
    title = re.sub(r"\*\*", "", s["title"])
    tsize = _title_size(title, title_w or (RIGHT - LEFT))
    if tsize is None:
        ctx.miss(n, "the title", max(3, len(title.split()) // 4))
        tsize = 20
    _rich(tf.paragraphs[0], title, tsize, TC.TEXT_1, True, numbers=False, heading=True)
    ctx.track(n, s["title"], s.get("title_prov"))
    w = title_w or (RIGHT - LEFT)
    if s.get("subtitle"):                             # one line: 18 pt, stepping down to 14 pt
        ssz = next((z for z in (T["sub"], 16, 15, 14) if _need([{"text": s["subtitle"], "size": z}], w) <= z * 1.2 / 72 + 0.1), 14)
        _write(_box(sl, LEFT, SUB_Y, w, 0.33, "role_subtitle_Subtitle", MSO_ANCHOR.BOTTOM),
               [{"text": s["subtitle"], "size": ssz, "numbers": False}])
        if ssz == 14 and _need([{"text": s["subtitle"], "size": 14}], w) > 0.35:
            ctx.miss(n, "the subtitle", max(3, len(s["subtitle"].split()) // 3))
    _rule(sl, LEFT, TITLE_RULE_Y, w, TC.TEXT_1, 0.75, "TitleRule")
    x = RIGHT
    for k, tag in enumerate(reversed((s.get("tags") or [])[:2] if not sub else [])):
        tw = 0.1 + 0.083 * len(tag)
        x -= tw
        _write(_box(sl, x, 0.06, tw, 0.24, f"ov_Tag{k}"),
               [{"text": tag, "size": T["tag"], "bold": True, "numbers": False, "align": PP_ALIGN.RIGHT}])
        _rule(sl, x, 0.31, tw, TC.TEXT_1, 0.75, f"ov_TagRule{k}")
        x -= 0.3


def _footer(ctx, s, sl, n):
    notes = s.get("footnotes") or []
    lc = getattr(ctx, "label_col_notes", None)
    ctx.label_col_notes = None
    if notes and lc:                                  # dense matrix: footnotes in the bottom of the label column
        _write(_box(sl, lc[0], FOOT_RULE_Y - 0.06 - 0.14 * len(notes) - 0.05, 2.0, 0.14 * len(notes) + 0.05,
                    "Footnotes", MSO_ANCHOR.BOTTOM),
               [{"text": f"{i + 1}.  {t}", "size": T["note"], "numbers": False} for i, t in enumerate(notes)])
    elif notes:
        top = getattr(ctx, "notes_top", None) or FOOTNOTE_Y
        ctx.notes_top = None
        _write(_box(sl, LEFT, top, RIGHT - LEFT, FOOT_RULE_Y - top - 0.03, "Footnotes", MSO_ANCHOR.BOTTOM),
               [{"text": "    ".join(f"{i + 1}  {t}" for i, t in enumerate(notes)), "size": T["note"], "numbers": False}])
    src = s.get("source") or {}
    if src.get("text"):
        _rule(sl, LEFT, FOOT_RULE_Y, RIGHT - LEFT, TC.TEXT_1, 0.5, "SourceRule")
        t = src["text"] if re.match(r"(?i)^(source|note)", src["text"]) else "Source: " + src["text"]
        sx, sw = span(0, 9)
        _write(_box(sl, sx, SRC_Y, sw, 0.34, "Source"), [{"text": t, "size": T["src"], "numbers": False}])
    _write(_box(sl, colx(10), SRC_Y, RIGHT - colx(10), 0.25, "Firm"),
           [{"text": f"{s.get('firm', FIRM)}      {n}", "size": T["src"] + 1, "numbers": False, "align": PP_ALIGN.RIGHT}])


# ------------------------------------------------------------------ charts
PLOT = (0.09, 0.03, 0.88, 0.85)                        # manual inner plot area (fractions of the chart frame)


def _manual_plot(chart):
    pa = chart._chartSpace.find(".//" + qn("c:plotArea"))
    lay = pa.find(qn("c:layout"))
    if lay is None:
        lay = pa.makeelement(qn("c:layout"), {})
        pa.insert(0, lay)
    for c in list(lay):
        lay.remove(c)
    ml = lay.makeelement(qn("c:manualLayout"), {})
    for tag, val in (("c:layoutTarget", "inner"), ("c:xMode", "edge"), ("c:yMode", "edge"), ("c:x", PLOT[0]),
                     ("c:y", PLOT[1]), ("c:w", PLOT[2]), ("c:h", PLOT[3])):
        ml.append(ml.makeelement(qn(tag), {"val": str(val)}))
    lay.append(ml)


def _bubble(ctx, sl, n, c, x, y, w, h):
    """Bubble chart with think-cell style labels: the plot area is fixed, so every label is a real text box placed
    next to its bubble (or at a leader-line offset), never an auto-placed chart label that can collide."""
    cd = BubbleChartData()
    pts = c["points"]
    se = cd.add_series(c.get("series_name", "items"))
    for p in pts:
        se.add_data_point(p["x"], p["y"], p.get("size", 1))
    gf = sl.shapes.add_chart(XL_CHART_TYPE.BUBBLE, Inches(x), Inches(y), Inches(w), Inches(h), cd)
    positive_axis_ids(gf.chart)
    gf.name = "Chart"
    ch = gf.chart
    ch.has_legend = False
    ch.has_title = False
    ch.font.size = Pt(T["label"])
    ch.font.color.theme_color = TC.TEXT_1
    plot = ch.plots[0]
    scale = c.get("bubble_scale", 45)
    plot.bubble_scale = scale
    s = plot.series[0]
    for i, p in enumerate(pts):
        pt = s.points[i]
        pt.format.fill.solid()
        pt.format.fill.fore_color.theme_color = TC.ACCENT_3 if p.get("focus") else (TC.ACCENT_5 if p.get("muted") else TC.TEXT_2)
        pt.format.line.color.theme_color = TC.BACKGROUND_1
    from pptx.enum.chart import XL_TICK_LABEL_POSITION
    for ax, key in ((ch.value_axis, "y"), (ch.category_axis, "x")):
        ax.has_major_gridlines = False
        ax.tick_labels.font.size = Pt(T["label"])
        ax.format.line.color.theme_color = TC.TEXT_1
        ax.tick_label_position = XL_TICK_LABEL_POSITION.LOW
        lo, hi = c[f"{key}_range"]
        ax.minimum_scale, ax.maximum_scale = lo, hi
        if c.get(f"{key}_major"):
            ax.major_unit = c[f"{key}_major"]
    if c.get("x_cross") is not None:
        ch.category_axis.crosses_at = c["x_cross"]
    if c.get("y_cross") is not None:
        ch.value_axis.crosses_at = c["y_cross"]
    _manual_plot(ch)
    _alt(gf, c.get("alt") or "Bubble chart")
    # data -> slide coordinates
    (x0, x1), (y0, y1) = c["x_range"], c["y_range"]
    px0, py0, pw, ph = x + PLOT[0] * w, y + PLOT[1] * h, PLOT[2] * w, PLOT[3] * h
    to_xy = lambda xv, yv: (px0 + (xv - x0) / (x1 - x0) * pw, py0 + (y1 - yv) / (y1 - y0) * ph)
    for i, p in enumerate(pts):
        if not p.get("label"):
            continue
        focus = bool(p.get("focus"))
        pos = p.get("label_pos") or ("left" if p["x"] > x0 + 0.62 * (x1 - x0) else "right")
        dl = s.points[i].data_label
        dl.has_text_frame = True
        dl.text_frame.text = ""
        _rich(dl.text_frame.paragraphs[0], p["label"], T["label"] - 1,
              TC.ACCENT_3 if focus else (TC.BACKGROUND_1 if pos == "center" else TC.TEXT_1), focus, numbers=False)
        dl.position = {"right": XL_LABEL_POSITION.RIGHT, "left": XL_LABEL_POSITION.LEFT, "above": XL_LABEL_POSITION.ABOVE,
                       "below": XL_LABEL_POSITION.BELOW, "center": XL_LABEL_POSITION.CENTER}[pos]
    for a in c.get("annotations") or []:
        ax_, ay_ = to_xy(a["x"], a["y"])
        _write(_box(sl, ax_ + 0.05, ay_, 1.2, 0.25, "ov_Annot"), [{"text": a["text"], "size": T["label"], "numbers": False}])
    if c.get("x_title"):
        _write(_box(sl, x + w - 3.2, y + h - 0.02, 3.2, 0.26, "ov_XTitle"),
               [{"text": c["x_title"], "size": T["label"], "numbers": False, "align": PP_ALIGN.RIGHT}])
    return gf


def _bubble_drawn(ctx, sl, n, c, x, y, w, h):
    """Native bubble chart (editable data, axes crossing at reference values) with think-cell style labels: a
    collision-free solver picks each label's spot (never over another label, a bubble or the tick labels), and the
    label is the chart's own data label moved there, so it stays attached to its point when the data is edited."""
    pts = c["points"]
    (x0, x1), (y0, y1) = c["x_range"], c["y_range"]
    tick = T["label"]
    title_h = 0.3 if c.get("x_title") else 0.0
    pl, pb = x + 0.55, y + h - 0.32 - title_h             # plot left (after y tick labels), plot bottom (above x ticks)
    pw, ph = x + w - pl - 0.05, pb - y - 0.05
    to = lambda xv, yv: (pl + (xv - x0) / (x1 - x0) * pw, pb - (yv - y0) / (y1 - y0) * ph)
    xc = c.get("x_cross", x0)
    yc = c.get("y_cross", y0)
    ax_x, _ = to(xc, y0)
    _, ax_y = to(x0, yc)
    # one native bubble chart; its plot area is pinned to the geometry the label solver works in
    cd = BubbleChartData()
    se_ = cd.add_series(c.get("series_name", "items"))
    for p in pts:
        se_.add_data_point(p["x"], p["y"], p.get("size", 1))
    h_frame = h - title_h
    gf = sl.shapes.add_chart(XL_CHART_TYPE.BUBBLE, Inches(x), Inches(y), Inches(w), Inches(h_frame), cd)
    positive_axis_ids(gf.chart)
    gf.name = "Chart"
    ch = gf.chart
    ch.has_legend = False
    ch.has_title = False
    ch.font.size = Pt(tick)
    ch.font.color.theme_color = TC.TEXT_1
    plot = ch.plots[0]
    smax = max(p.get("size", 1) for p in pts)
    dmax = c.get("max_diameter", 0.95)
    plot.bubble_scale = int(max(10, min(300, round(dmax / (0.25 * min(pw, ph)) * 100))))
    ser = plot.series[0]
    for i, p in enumerate(pts):
        pt = ser.points[i]
        pt.format.fill.solid()
        pt.format.fill.fore_color.theme_color = TC.ACCENT_3 if p.get("focus") else (TC.ACCENT_5 if p.get("muted") else TC.TEXT_2)
        pt.format.line.color.theme_color = TC.BACKGROUND_1
        pt.format.line.width = Pt(0.75)
    xm, ym = c.get("x_major", (x1 - x0) / 7), c.get("y_major", (y1 - y0) / 7)
    for ax, (lo, hi), major in ((ch.category_axis, (x0, x1), xm), (ch.value_axis, (y0, y1), ym)):
        ax.has_major_gridlines = False
        ax.tick_labels.font.size = Pt(tick)
        ax.format.line.color.theme_color = TC.TEXT_1
        ax.format.line.width = Pt(0.75)
        ax.tick_label_position = XL_TICK_LABEL_POSITION.LOW
        ax.minimum_scale, ax.maximum_scale, ax.major_unit = lo, hi, major
    ch.category_axis.crosses_at = yc                         # the x axis crosses the y axis at y = y_cross
    ch.value_axis.crosses_at = xc
    pa = ch._chartSpace.find(".//" + qn("c:plotArea"))
    lay = pa.find(qn("c:layout"))
    if lay is None:
        lay = pa.makeelement(qn("c:layout"), {})
        pa.insert(0, lay)
    for k_ in list(lay):
        lay.remove(k_)
    ml = lay.makeelement(qn("c:manualLayout"), {})
    for tag, val in (("c:layoutTarget", "inner"), ("c:xMode", "edge"), ("c:yMode", "edge"), ("c:x", (pl - x) / w),
                     ("c:y", (pb - ph - y) / h_frame), ("c:w", pw / w), ("c:h", ph / h_frame)):
        ml.append(ml.makeelement(qn(tag), {"val": f"{val:.5f}" if isinstance(val, float) else val}))
    lay.append(ml)
    occupied = [(pl - 0.1, pb, pw + 0.2, 0.3), (x, y, 0.53, h)]     # tick-label bands
    leaders = []
    circles = []
    for i, p in enumerate(pts):
        cx, cy = to(p["x"], p["y"])
        d = max(0.07, dmax * (p.get("size", 1) / smax) ** 0.5)
        circles.append((cx, cy, d / 2))
    for a in c.get("annotations") or []:
        ax_, ay_ = to(a["x"], a["y"])
        _write(_box(sl, ax_ + 0.06, ay_ - 0.12, 1.0, 0.24, "ov_Annot"),
               [{"text": a["text"], "size": tick, "numbers": False, "line_spacing": 1.1}])
        occupied.append((ax_ + 0.06, ay_ - 0.12, 0.7, 0.24))

    def cost(rect, own, own_ok=False):
        bx, by, bw_, bh_ = rect
        c_ = 0.0
        if bx < x or by < y - 0.05 or bx + bw_ > x + w + 0.1 or by + bh_ > pb:
            c_ += 100
        for k_, (ox, oy, ow, oh) in enumerate(occupied):
            ix = min(bx + bw_, ox + ow) - max(bx, ox)
            iy = min(by + bh_, oy + oh) - max(by, oy)
            if ix > 0 and iy > 0:
                c_ += 150 + ix * iy * 100                         # overlapping text or tick labels: never
        for (sx_, sy_, ex_, ey_) in leaders:                      # never sit on an earlier leader line
            for t in (0.2, 0.4, 0.6, 0.8):
                px_, py_ = sx_ + t * (ex_ - sx_), sy_ + t * (ey_ - sy_)
                if bx < px_ < bx + bw_ and by < py_ < by + bh_:
                    c_ += 20
                    break
        for j, (cx_, cy_, r_) in enumerate(circles):
            if j == own and own_ok:
                continue
            nx, ny = min(max(cx_, bx), bx + bw_), min(max(cy_, by), by + bh_)
            dd = ((nx - cx_) ** 2 + (ny - cy_) ** 2) ** 0.5
            if dd < r_ + 0.03:
                c_ += 60 + (r_ + 0.03 - dd) * 80                   # covering a bubble: very bad
        # crossing an axis line is tolerable but discouraged
        if bx < ax_x < bx + bw_:
            c_ += 3
        if by < ax_y < by + bh_:
            c_ += 3
        return c_
    pts = [dict(p) for p in pts]
    for i in range(len(pts)):                                # coincident points share one label
        for j in range(i + 1, len(pts)):
            if pts[i].get("label") and pts[j].get("label") and abs(circles[i][0] - circles[j][0]) < 0.05 \
                    and abs(circles[i][1] - circles[j][1]) < 0.05:
                keep_, drop_ = (i, j) if circles[i][2] >= circles[j][2] else (j, i)
                short = lambda t: t.split(" and ")[0].split(",")[0] if len(t) > 16 else t
                pts[keep_]["label"] = f"{short(pts[keep_]['label'])} / {short(pts[drop_]['label'])}"
                pts[drop_]["label"] = None
    order = sorted(range(len(pts)), key=lambda i: (not pts[i].get("focus"), -pts[i].get("size", 1)))
    size = tick
    for i in order:
        p = pts[i]
        if not p.get("label"):
            continue
        cx, cy, r = circles[i]
        focus = bool(p.get("focus"))
        best, best_score = None, 1e9
        for size in (tick, tick - 1):
            cand, score = _place(p, i, size, focus, cx, cy, r, circles, occupied, leaders, cost)
            if score < best_score - 0.5:
                best, best_score, used = cand, score, size
            if best_score < 4:
                break
        size = used
        pos, bx, by, lw, lh = best
        if __import__("os").environ.get("FORGE_LABEL_DEBUG"):
            print("LABEL", p["label"][:26], pos, size, round(bx, 2), round(by, 2), "score", round(best_score, 1))
        inside = pos == "c"
        color = TC.ACCENT_3 if focus else (TC.BACKGROUND_1 if inside else TC.TEXT_1)
        lsize = size - (2 if inside else 0)
        words, lines_, cur = p["label"].split(), [], ""
        for wd in words:                                   # the solver's line breaks, kept in the native label
            trial = (cur + " " + wd).strip()
            if cur and _need_w(trial, lsize, focus) > lw - 0.16 + 1e-6:
                lines_.append(cur)
                cur = wd
            else:
                cur = trial
        lines_.append(cur)
        dl = ser.points[i].data_label
        dl.has_text_frame = True
        tf_ = dl.text_frame
        for li, line in enumerate(lines_):
            para = tf_.paragraphs[0] if li == 0 else tf_.add_paragraph()
            para.alignment = PP_ALIGN.CENTER
            _rich(para, line, lsize, color, focus, numbers=False)
        bp_ = tf_._txBody.find(qn("a:bodyPr"))
        if bp_ is not None:
            bp_.set("wrap", "none")                         # the solver's line breaks are final: no re-wrapping
        dl.position = XL_LABEL_POSITION.CENTER
        dLbl = dl._dLbl
        lay_ = dLbl.find(qn("c:layout"))
        if lay_ is None:
            lay_ = dLbl.makeelement(qn("c:layout"), {})
            dLbl.insert(1, lay_)
        for k_ in list(lay_):
            lay_.remove(k_)
        mlx = lay_.makeelement(qn("c:manualLayout"), {})
        mlx.append(mlx.makeelement(qn("c:x"), {"val": f"{(bx + lw / 2 - cx) / w:.5f}"}))
        mlx.append(mlx.makeelement(qn("c:y"), {"val": f"{(by + lh / 2 - cy) / h_frame:.5f}"}))
        lay_.append(mlx)
        occupied.append((bx, by, lw, lh))
    if c.get("x_title"):                                   # below the chart frame, not laid over it
        _write(_box(sl, x + w - 3.2, y + h_frame + 0.02, 3.2, 0.26, "XTitle"),
               [{"text": c["x_title"], "size": tick, "numbers": False, "align": PP_ALIGN.RIGHT}])
    data = "; ".join(f"{p.get('label') or 'point'}: x={p['x']}, y={p['y']}, size={p.get('size', 1)}" for p in pts)
    _alt(gf, f"Bubble chart. {data}"[:900])
    return gf


def _place(p, i, size, focus, cx, cy, r, circles, occupied, leaders, cost):
    """Best label rectangle for point i at `size`: returns ((pos, x, y, w, h), score)."""
    one = _need_w(p["label"], size, focus) + 0.16
    words = p["label"].split()
    longest = max(_need_w(wd, size, focus) for wd in words) + 0.16
    cands = []
    inner = _need_w(p["label"], size - 2, focus)              # inside label, two points smaller, white
    if inner < 2 * r * 0.95 and (size - 2) * 1.3 / 72 < r:
        cands.append(("c", cx - r, cy - (size - 1) * 0.65 / 72 - 0.03, 2 * r, (size - 1) * 1.3 / 72 + 0.06))
    for lw in sorted({min(one, 2.4), max(longest, min(one / 2 + 0.35, 2.4)), max(longest, min(one / 3 + 0.4, 2.4))}):
        lines = 1 if lw >= one - 0.01 else (2 if lw >= one / 2 + 0.2 else 3)
        lh = lines * _pitch(size, 1.1) / 72 + 0.06
        for k in range(-30, 21):                      # grid search around the bubble: nearest free spot wins
            for m in range(-16, 17):
                bx_, by_ = cx + k * 0.1, cy + m * 0.08 - lh / 2
                if bx_ < cx < bx_ + lw and by_ < cy < by_ + lh:
                    continue
                pos = "r" if bx_ >= cx else ("l" if bx_ + lw <= cx else ("a" if by_ < cy else "b"))
                cands.append((pos, bx_, by_, lw, lh))

    def dist(cd):
        _, bx_, by_, lw_, lh_ = cd
        nx, ny = min(max(cx, bx_), bx_ + lw_), min(max(cy, by_), by_ + lh_)
        return max(0.0, ((nx - cx) ** 2 + (ny - cy) ** 2) ** 0.5 - r)
    def crossing(cd):
        """Leader from the bubble to the label: penalise passing through other bubbles or labels."""
        _, bx_, by_, lw_, lh_ = cd
        if dist(cd) <= 0.15:
            return 0.0
        ex, ey = min(max(cx, bx_), bx_ + lw_), min(max(cy, by_), by_ + lh_)
        pen = 0.0
        vx, vy = ex - cx, ey - cy
        L = vx * vx + vy * vy or 1
        for j, (qx, qy, qr) in enumerate(circles):
            if j == i:
                continue
            t = max(0.0, min(1.0, ((qx - cx) * vx + (qy - cy) * vy) / L))
            if ((cx + t * vx - qx) ** 2 + (cy + t * vy - qy) ** 2) ** 0.5 < qr:
                pen += 8
        for (ax_, ay_, bx_, by_) in leaders:                  # leaders never cross each other
            d1 = (bx_ - ax_) * (cy - ay_) - (by_ - ay_) * (cx - ax_)
            d2 = (bx_ - ax_) * (ey - ay_) - (by_ - ay_) * (ex - ax_)
            d3 = (ex - cx) * (ay_ - cy) - (ey - cy) * (ax_ - cx)
            d4 = (ex - cx) * (by_ - cy) - (ey - cy) * (bx_ - cx)
            if d1 * d2 < 0 and d3 * d4 < 0:
                pen += 25
        for (ox, oy, ow, oh) in occupied[2:]:                # other labels
            for t in (0.25, 0.5, 0.75):
                px_, py_ = cx + t * vx, cy + t * vy
                if ox < px_ < ox + ow and oy < py_ < oy + oh:
                    pen += 8
                    break
        return pen
    def ambiguous(cd):
        _, bx_, by_, lw_, lh_ = cd
        if cd[0] == "c":
            return 0.0
        mine = dist(cd)
        for j, (qx, qy, qr) in enumerate(circles):
            if j == i or (abs(qx - cx) < 0.05 and abs(qy - cy) < 0.05):
                continue
            nx, ny = min(max(qx, bx_), bx_ + lw_), min(max(qy, by_), by_ + lh_)
            if max(0.0, ((nx - qx) ** 2 + (ny - qy) ** 2) ** 0.5 - qr) < mine + 0.1:
                return 25.0
        return 0.0
    best = min(cands, key=lambda cd: cost(cd[1:], i, cd[0] == "c") + 6 * dist(cd) + crossing(cd) + ambiguous(cd)
               + (0.0 if cd[0] == "c" else 0.05))
    score = cost(best[1:], i, best[0] == "c") + 6 * dist(best) + crossing(best) + ambiguous(best)
    return best, score


def _need_w(text, size, bold=False) -> float:
    from .common import font_file, text_width_in
    f, _ = font_file("Arial", bold, False, (("Arial", "Liberation Sans"),))
    return text_width_in(re.sub(r"\*\*|\^\w+", "", text), size, f)


_KIND = {"bar": XL_CHART_TYPE.BAR_CLUSTERED, "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
         "stacked_column": XL_CHART_TYPE.COLUMN_STACKED, "stacked_bar": XL_CHART_TYPE.BAR_STACKED,
         "line": XL_CHART_TYPE.LINE_MARKERS, "waterfall": XL_CHART_TYPE.COLUMN_STACKED}


BAR_SLOT_MAX = 0.62        # in per category for horizontal bars: bars stay about 0.4in thick, never slabs


def _chart_box(kind, ncat, nser, x, y, w, h):
    """Size the chart frame to its data: a 3-bar chart is not stretched into slabs across the slide."""
    per = 1 + 0.45 * (max(1, nser) - 1)
    legend = 0.35 if nser > 1 else 0
    if kind in ("bar", "stacked_bar"):
        h = min(h, ncat * BAR_SLOT_MAX * (per if kind == "bar" else 1) + 0.45 + legend)
    return x, y, w, h


def _gap_for(kind, ncat, nser, w, h):
    """Gap width (% of a bar) that keeps columns at most ~0.95in and bars ~0.45in thick inside a full frame."""
    clustered = kind in ("bar", "column")
    dim = (h if kind in ("bar", "stacked_bar") else w) * 0.85
    slot = dim / max(1, ncat)
    cap = 0.45 if kind in ("bar", "stacked_bar") else 0.95
    group = cap * (max(1, nser) if clustered else 1)
    return int(max(50, min(400, (slot / group - 1) * 100)))


def _chart(ctx, sl, n, c, x, y, w, h, name="Chart"):
    if c.get("kind") == "stats":
        return _stats(ctx, sl, n, c, x, y, w, h)
    if c.get("kind") == "bubble":
        return _bubble_drawn(ctx, sl, n, c, x, y, w, h)
    kind = c.get("kind", "column")
    cats = [str(v) for v in c["categories"]]
    series = c["series"]
    if kind == "bar" and len(cats) <= 5 and max((len(v) for v in cats), default=0) <= 22:
        kind = "column"                                   # a few short bars read better standing up, full height
    x, y, w, h = _chart_box(kind, len(cats), len(series), x, y, w, h)
    cd = CategoryChartData()
    cd.categories = cats
    fmt = "0" if not c.get("decimals") else "0." + "0" * int(c["decimals"])
    if str(c.get("unit", "")).strip().lower() in ("%", "pp", "x"):          # the unit rides on the label: 38%
        u = str(c["unit"]).strip()
        fmt = f'{fmt}"{u if u != "pp" else " pp"}";-{fmt}"{u if u != "pp" else " pp"}"'
    if kind == "waterfall":
        vals = [float(v or 0) for v in series[0]["values"]]
        base, run = [], 0.0
        for i, v in enumerate(vals):
            if i == 0 or i == len(vals) - 1:
                base.append(0.0)
                run = v if i == 0 else run
            else:
                base.append(run if v >= 0 else run + v)
                run += v
        cd.add_series("base", base)
        cd.add_series(series[0]["name"], [abs(v) for v in vals], fmt)
    else:
        for se in series:
            cd.add_series(se["name"], [None if v is None else float(v) for v in se["values"]], fmt)
    gf = sl.shapes.add_chart(_KIND.get(kind, XL_CHART_TYPE.COLUMN_CLUSTERED), Inches(x), Inches(y), Inches(w),
                             Inches(h), cd)
    positive_axis_ids(gf.chart)
    gf.name = name
    ch = gf.chart
    ch.has_title = False
    ch.font.size = Pt(T["label"])
    ch.font.color.theme_color = TC.TEXT_1
    plot = ch.plots[0]
    multi = len(series) > 1 and kind != "waterfall"
    ch.has_legend = multi
    if multi:
        ch.legend.position = XL_LEGEND_POSITION.TOP
        ch.legend.include_in_layout = False
    try:
        plot.gap_width = _gap_for(kind, len(cats), len(series), w, h)
        va = ch.value_axis
        va.has_major_gridlines = False
        va.visible = False
        ca = ch.category_axis
        ca.tick_labels.font.size = Pt(T["label"])
        ca.format.line.color.theme_color = TC.TEXT_1
        if kind in ("bar", "stacked_bar"):
            ca.reverse_order = True
        ca.tick_label_position = XL_TICK_LABEL_POSITION.LOW     # labels stay clear of negative bars
    except Exception:
        pass
    hl = {str(v) for v in c.get("highlight_categories") or []}
    palette = [TC.TEXT_2, TC.ACCENT_4, TC.ACCENT_3, TC.ACCENT_5]
    for i, s in enumerate(plot.series):
        if kind == "waterfall" and i == 0:
            s.format.fill.background()
            continue
        k = i - (1 if kind == "waterfall" else 0)
        if kind == "line":
            s.format.line.color.theme_color = palette[k % 4]
            s.format.line.width = Pt(2)
            s.smooth = False
        else:
            s.format.fill.solid()
            s.format.fill.fore_color.theme_color = palette[k % 4]
            if hl and not multi:
                for j, cat in enumerate(cats):
                    if cat in hl:
                        f = s.points[j].format
                        f.fill.solid()
                        f.fill.fore_color.theme_color = TC.ACCENT_3
        s.data_labels.show_value = True
        s.data_labels.number_format = fmt
        s.data_labels.number_format_is_linked = False
        s.data_labels.font.size = Pt(T["label"])
        if kind in ("column", "bar"):                     # clustered too: labels outside, in text colour
            s.data_labels.position = XL_LABEL_POSITION.OUTSIDE_END
            s.data_labels.font.color.theme_color = TC.TEXT_1
        elif kind == "line":
            s.data_labels.position = XL_LABEL_POSITION.ABOVE
            s.data_labels.font.color.theme_color = TC.TEXT_1
            vals = [v for v in series[k]["values"]]
            if len(vals) > 12:                            # long series: label the points the story needs, not all
                nums = [(j, v) for j, v in enumerate(vals) if isinstance(v, (int, float))]
                keep = {nums[0][0], nums[-1][0], max(nums, key=lambda t: t[1])[0], min(nums, key=lambda t: t[1])[0]}
                keep |= {j for j, cat in enumerate(cats) if cat in hl}
                s.data_labels.show_value = False
                for j in sorted(keep):
                    dl = s.points[j].data_label
                    dl.show_value = True
                    dl.position = XL_LABEL_POSITION.ABOVE
                    dl.font.size = Pt(T["label"])
                    dl.font.bold = j == nums[-1][0]
        else:
            s.data_labels.font.color.theme_color = TC.BACKGROUND_1
    _alt(gf, c.get("alt") or f"{kind} chart of {', '.join(se['name'] for se in series)} ({c.get('unit', '')})")
    return gf


# ------------------------------------------------------------------ tables (hairline grammar)
def _borders(cell, bottom=None, top=None, left=None, right=None):
    """Each side: (scheme colour, weight pt) or None (no line). Written first in tcPr, as the schema requires."""
    tcPr = cell._tc.get_or_add_tcPr()
    for side in ("lnL", "lnR", "lnT", "lnB"):
        el = tcPr.find(qn(f"a:{side}"))
        if el is not None:
            tcPr.remove(el)
    for i, (side, spec) in enumerate((("lnL", left), ("lnR", right), ("lnT", top), ("lnB", bottom))):
        ln = tcPr.makeelement(qn(f"a:{side}"), {"w": str(int(Pt(spec[1]))) if spec else "0"})
        if spec:
            sf = ln.makeelement(qn("a:solidFill"), {})
            sf.append(sf.makeelement(qn("a:schemeClr"), {"val": spec[0]}))
            ln.append(sf)
        else:
            ln.append(ln.makeelement(qn("a:noFill"), {}))
        tcPr.insert(i, ln)


def _plain(t) -> str:
    """Text as it renders: markup removed (a ^1 marker is a small superscript, not two characters)."""
    return re.sub(r"\*\*|\^\w+", "", str(t))


def _table(ctx, sl, n, t, x, y, w, h, dry=False):
    groups = t.get("groups")
    cols, rows = t["columns"], t["rows"]
    hdr = 2 if groups else 1
    nrows = hdr + len(rows)
    size_ = t.get("size", 12)
    from .common import font_file, wrap_lines
    f_reg, _ = font_file("Arial", False, False, (("Arial", "Liberation Sans"),))
    f_bold, _ = font_file("Arial", True, False, (("Arial", "Liberation Sans"),))
    row_min = lambda sz: sz * 1.15 / 72 + 0.045                   # LibreOffice/PowerPoint minimum, measured
    widths = t.get("col_widths")
    colw = ([w * cw / sum(widths) for cw in widths] if widths else
            [w * 0.32] + [(w * 0.68) / max(1, len(cols) - 1)] * (len(cols) - 1))

    def col_head_h(sz):
        """Column-header row: as many lines as its most-wrapped (bold, size + 2) heading."""
        lines = max([wrap_lines(_plain(c), max(0.3, colw[ci] - 0.12), sz + 2, f_bold, 0.97) for ci, c in enumerate(cols)]
                    or [1])
        return lines * (sz + 2) * 1.25 / 72 + 0.08
    head_h = lambda sz: col_head_h(sz) + ((2 if any(len(g.get("label", "")) > 14 * g.get("span", 1) for g in groups)
                                            else 1) * (sz + 2) * 1.25 / 72 + 0.08 if groups else 0)
    bold_rows = set(t.get("highlight_rows") or []) | ({len(rows) - 1} if t.get("total_row") else set())

    def row_heights(sz, base):
        """Each row as tall as its most-wrapped cell (bold rows measured bold)."""
        out = []
        for i, r in enumerate(rows):
            f = f_bold if i in bold_rows else f_reg
            lines = max([wrap_lines(_plain(c), max(0.3, colw[ci] - 0.12), sz, f, 0.97) for ci, c in enumerate(r[:len(cols)])]
                        or [1])
            out.append(max(base, lines * sz * 1.15 / 72 + 0.045))
        return out
    while size_ > 10 and sum(row_heights(size_, row_min(size_))) + head_h(size_) > h:
        size_ -= 1
    t["size"] = size_
    hs = row_heights(size_, row_min(size_))
    if sum(hs) + head_h(size_) > h + 0.01 and len(rows) > 3:     # still too tall: carry rows to a continuation
        acc, fit = head_h(size_), 0
        for hv in hs:
            if acc + hv > h - (0.25 if t.get("total_row") else 0):
                break
            acc += hv
            fit += 1
        fit = max(3, fit)
        parts = -(-len(rows) // fit)                              # split evenly: never a stub continuation
        keep = max(3, -(-len(rows) // parts))
        ctx.carry_rows = rows[keep:] if not dry else ["dry"]
        rows = rows[:keep]
        nrows = hdr + len(rows)
        t["total_row"] = False                                    # the total row travels with the last part
    hs = row_heights(size_, row_min(size_))
    room = h - head_h(size_) - sum(hs)                            # rows stretch to fill the space they have,
    if room > 0:                                                  # a little air per row, never stretched rows:
        pad = t.get("_pad", 0.08)                                 # a short table gets a different composition
        hs = [hv + min(pad, room / len(hs)) for hv in hs]
    if dry:
        return float("inf") if ctx.carry_rows else sum(hs) + head_h(size_)
    gf = sl.shapes.add_table(nrows, len(cols), Inches(x), Inches(y), Inches(w), Inches(sum(hs) + head_h(size_)))
    gf.name = "Table"
    tbl = gf.table
    tblPr = tbl._tbl.find(qn("a:tblPr"))
    for k in list(tblPr.attrib):
        del tblPr.attrib[k]
    sid = tblPr.find(qn("a:tableStyleId"))
    if sid is not None:
        sid.text = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"          # No Style, No Grid
    for ci, cw in enumerate(colw):
        tbl.columns[ci].width = Inches(cw)
    total = bool(t.get("total_row"))
    for ri, hv in enumerate(hs):
        tbl.rows[hdr + ri].height = Inches(hv)
    gh = 0.0
    if groups:                                                   # header heights measured on renders
        two = any(len(g.get("label", "")) > 14 * g.get("span", 1) for g in groups)
        gh = (2 if two else 1) * (size_ + 2) * 1.25 / 72 + 0.08
        tbl.rows[0].height = Inches(gh)
    hh = col_head_h(size_)
    tbl.rows[hdr - 1].height = Inches(hh)
    hl, it = set(t.get("highlight_rows") or []), set(t.get("italic_rows") or [])
    heat = {}
    for k, v in (t.get("heat") or {}).items():                    # model-supplied heat values: numbers only
        try:
            nums = [float(z) for z in v if isinstance(z, (int, float)) or re.match(r"^-?[\d.]+$", str(z))]
            if nums:
                heat[int(k)] = nums
        except (TypeError, ValueError):
            continue
    size = size_

    def put(cell, text, bold=False, italic=False, align=PP_ALIGN.CENTER, color=TC.TEXT_1, sz=size, anchor=MSO_ANCHOR.MIDDLE):
        cell.margin_left = cell.margin_right = Inches(0.05)
        cell.margin_top = cell.margin_bottom = Inches(0.0)
        cell.vertical_anchor = anchor
        p = cell.text_frame.paragraphs[0]
        p.alignment = align
        _rich(p, str(text), sz, color, bold, numbers=False, italic=italic)
    if groups:
        ci = 1
        for g in groups:
            sp = g.get("span", 1)
            if g.get("label"):
                if sp > 1:
                    tbl.cell(0, ci).merge(tbl.cell(0, ci + sp - 1))
                put(tbl.cell(0, ci), g["label"], True, align=PP_ALIGN.LEFT if g.get("left") else PP_ALIGN.CENTER,
                    sz=size + 2, anchor=MSO_ANCHOR.BOTTOM)
            for k in range(ci, ci + sp):
                _borders(tbl.cell(0, k), bottom=("tx1", 0.75) if g.get("label") else None)
            ci += sp
        _borders(tbl.cell(0, 0))
    for ci, c in enumerate(cols):
        cell = tbl.cell(hdr - 1, ci)
        put(cell, c, True, align=PP_ALIGN.LEFT if ci == 0 else PP_ALIGN.CENTER, sz=size + (2 if not groups else 2),
            anchor=MSO_ANCHOR.BOTTOM)
        _borders(cell, bottom=("tx1", 0.75))
    for r_i, row in enumerate(rows):
        ri = hdr + r_i
        last = r_i == len(rows) - 1
        for ci in range(len(cols)):
            v = row[ci] if ci < len(row) else ""
            cell = tbl.cell(ri, ci)
            on = r_i in hl
            color = TC.TEXT_1
            if on:
                cell.fill.solid()
                cell.fill.fore_color.theme_color = TC.ACCENT_2
            elif ci in heat and not (total and last):
                vals = sorted(heat[ci])
                try:
                    fv = float(re.sub(r"[^\d.\-]", "", str(v)))
                    q = sum(1 for z in vals if z <= fv) / len(vals)
                    band = min(3, int(q * 4 - 1e-9))
                    shade = [(TC.ACCENT_3, 0.65), (TC.ACCENT_3, 0.0), (TC.ACCENT_4, 0.0), (TC.TEXT_2, 0.0)][band]
                    cell.fill.solid()
                    cell.fill.fore_color.theme_color = shade[0]
                    if shade[1]:
                        cell.fill.fore_color.brightness = shade[1]
                    color = TC.BACKGROUND_1 if band >= 1 else TC.TEXT_1
                except ValueError:
                    cell.fill.background()
            else:
                cell.fill.background()
            put(cell, v, on or (total and last), r_i in it, PP_ALIGN.LEFT if ci == 0 else PP_ALIGN.CENTER, color)
            if ci in heat and not on:
                # white gutters between heat cells, like separate bars
                _borders(cell, bottom=("bg1", 2.0), top=("bg1", 2.0), left=("bg1", 3.0), right=("bg1", 3.0))
            else:
                _borders(cell, bottom=("tx1", 0.75) if last else ("accent5", 0.5))
            ctx.track(n, str(v), t.get("prov"))
    _alt(gf, t.get("alt") or "Table")
    gf.rendered_h = sum(hs) + hh + gh
    return gf


# ------------------------------------------------------------------ schema gate (model output -> renderable)
def _txt(v):
    if isinstance(v, dict):
        return str(v.get("text") or v.get("lead") or v.get("value") or "")
    return "" if v is None else str(v)


def _cell(v):
    """A table cell the model wrote as {lead, bullets}: keep the detail, not just the lead."""
    if isinstance(v, dict) and v.get("bullets"):
        detail = "; ".join(_txt(b) if isinstance(b, dict) else str(b) for b in v["bullets"])
        lead = _txt(v)
        return f"{lead}: {detail}" if lead else detail
    return _txt(v)


def normalize(s: dict) -> dict:
    """Coerce the shapes models commonly produce into the renderer's shapes (never invents content)."""
    s = dict(s)
    t = s.get("table")
    if isinstance(t, dict):
        t = dict(t)
        t["columns"] = [_txt(c) for c in t.get("columns") or []]
        t["rows"] = [[_cell(c) for c in (r if isinstance(r, list) else [r])] for r in t.get("rows") or []]
        gap = re.compile(r"\[GAP[^\]]*\]")
        if any(gap.search(c) for r in t["rows"] for c in r):           # visible, footnoted gaps, not raw markers
            t["rows"] = [[gap.sub("n/a", c) for c in r] for r in t["rows"]]
            s["footnotes"] = list(s.get("footnotes") or []) + ["n/a: not available in the source data"]
            s["notes"] = (s.get("notes", "") + "\n[QA] table contains values not available in the sources").strip()
        s["table"] = t
    for key in ("bullets",):
        if isinstance(s.get(key), list):
            s[key] = [b if isinstance(b, dict) else {"text": str(b)} for b in s[key]]
    for r in s.get("rows") or []:
        if isinstance(r, dict):
            r["cells"] = [c if isinstance(c, dict) else {"lead": "", "bullets": [str(c)]} for c in r.get("cells") or []]
    if isinstance(s.get("callout"), list):
        s["callout"] = {"bullets": s["callout"]}
    if isinstance(s.get("source"), str):
        s["source"] = {"text": s["source"]}
    gaps = []
    gap_re = re.compile(r"\s*[\(\[]?\[GAP:?\s*([^\]]*)\][\)\]]?")

    def scrub(v):                                          # prose: a gap is removed and noted, never shown
        if isinstance(v, str) and "[GAP" in v:
            gaps.extend(m.group(1).strip() for m in gap_re.finditer(v))
            return re.sub(r"\s+([,.;:])", r"\1", gap_re.sub("", v)).strip()
        if isinstance(v, list):
            return [scrub(x) for x in v]
        if isinstance(v, dict):
            return {k: (x if k in ("table", "chart", "prov", "notes") else scrub(x)) for k, x in v.items()}
        return v
    for k in list(s):
        if k not in ("table", "chart", "prov", "notes", "source", "footnotes"):
            s[k] = scrub(s[k])
    if gaps:
        s["notes"] = (s.get("notes", "") + "\n[QA] Not available in the sources, left out of the text: "
                      + "; ".join(g for g in gaps if g)).strip()
    for k in ("title", "title_right"):
        if isinstance(s.get(k), str) and s[k].rstrip().endswith(".") and not s[k].rstrip().endswith("..."):
            s[k] = s[k].rstrip()[:-1]
    if isinstance(s.get("footnotes"), str):
        s["footnotes"] = [s["footnotes"]]
    return s


REQUIRED = {"closing": ["steps"], "next_steps": ["steps"], "challenge_matrix": ["columns", "rows"], "exhibit_argument": ["chart", "blocks"],
            "table_focus": ["table"], "bar_takeaway": ["chart"], "column_trend": ["chart"], "line_trend": ["chart"],
            "waterfall_bridge": ["chart"], "stacked_composition": ["chart"]}


def chart_problems(c: dict) -> list[str]:
    """Charts that would mislead: one bar, mixed units in one series, ranks drawn as quantities, short lines."""
    if not c or c.get("kind") == "bubble":
        return []
    cats = [str(x) for x in c.get("categories") or []]
    series = [se for se in c.get("series") or [] if isinstance(se, dict)]
    out = []
    vals = [float(v) for se in series for v in se.get("values") or [] if isinstance(v, (int, float))]
    units = {m.group(0).lower() for cat in cats for m in re.finditer(r"\(([^)]*)\)|%|\$|thousand|million|share", cat, re.I)}
    bad = [v for se in series for v in se.get("values") or []
           if not isinstance(v, (int, float)) and not (v is None and c.get("kind") == "line")]
    if bad:
        out.append(f"{len(bad)} chart values are not numbers (gap markers or text); chart only the points the facts "
                   "support, or show the numbers you have as a stat stack")
    if c.get("kind") == "waterfall" and len(series) == 1:
        v = [x for x in series[0].get("values") or [] if isinstance(x, (int, float))]
        if len(v) >= 3 and abs(v[0] + sum(v[1:-1]) - v[-1]) > max(0.015 * abs(v[-1]), 0.05):
            out.append(f"the bridge does not add up: {v[0]:g} plus the steps ({sum(v[1:-1]):g}) is "
                       f"{v[0] + sum(v[1:-1]):g}, not the end value {v[-1]:g}; a waterfall needs a start total, "
                       "signed steps in the same unit, and an end total")
    if len(cats) < 2 and c.get("kind") != "waterfall":
        out.append("a chart with one bar shows nothing; give >= 3 comparable categories or use a stat stack")
    sunits = {bool(re.search(r"%|cagr|share|rate|growth", str(se.get("name", "")), re.I)) for se in series}
    if len(series) > 1 and len(sunits) > 1:
        out.append("the series mix a percentage with an absolute measure; one chart, one unit")
    if len(units) > 1 or (len(series) == 1 and vals and min(abs(v) for v in vals) > 0
                          and max(abs(v) for v in vals) / min(abs(v) for v in vals) > (10 if len(cats) <= 3 else 40)
                          and len(cats) <= 5
                          and c.get("kind") != "waterfall"):
        out.append("the chart mixes different measures or units in one series; show one measure in one unit")
    if len(series) == 1 and vals and sorted(vals) == [float(i) for i in range(1, len(vals) + 1)]:
        out.append("the values are ranks 1..n, not quantities; rank in a table or ordered list instead")
    if c.get("kind") == "line" and len(cats) < 4:
        out.append("a line needs >= 4 periods; use columns for fewer")
    return out


def _as_written(v: float, text: str) -> str | None:
    """The figure as the slide's own text writes it ($65.37 per share -> $65.37/sh, $83.0bn, 35%)."""
    if not text:
        return None
    forms = {f"{v:,.2f}", f"{v:,.1f}", f"{v:,.0f}" if v == int(v) else None, f"{v:g}"} - {None}
    for f in sorted(forms, key=len, reverse=True):
        m = re.search(r"(\$?)" + re.escape(f) + r"(?![\d.])\s?(bn|tn|m\b|%|x\b|/sh|per share)?", text)
        if m and (m.group(1) or m.group(2)):
            unit = {"per share": "/sh"}.get(m.group(2) or "", m.group(2) or "")
            return f"{m.group(1)}{f}{unit}"
    return None


def stat_stack(c: dict, text: str = "") -> dict:
    """Unrelated headline numbers as big emphasised figures with labels (the consulting way). Each figure keeps its
    unit and precision, taken from how the slide's own text writes it when it does: $48.42/sh, $83.0bn, 35%."""
    items = []
    cunit = str(c.get("unit", ""))
    dec = c.get("decimals")
    for se in c.get("series") or []:
        for cat, v in zip(c.get("categories") or [], se.get("values") or []):
            if not isinstance(v, (int, float)):
                continue
            ctx_ = f"{cat} {se.get('name', '')}"
            pct = bool(re.search(r"%|share of|percent|premium|margin|growth", ctx_, re.I)) and abs(v) <= 100
            cur = "$" if re.search(r"\$|dollar|price|value|cost|revenue|debt|capex", ctx_ + " " + cunit, re.I) and not pct else ""
            per_share = bool(re.search(r"/\s*sh|per share|price", ctx_, re.I))
            mag = ("" if per_share or pct else "bn" if re.search(r"\$?bn|billion", ctx_ + " " + cunit, re.I)
                   else "m" if re.search(r"\$m\b|million", ctx_ + " " + cunit, re.I) else "")
            d = dec if isinstance(dec, int) else (2 if per_share and v != int(v) else (1 if v != int(v) else 0))
            if per_share:
                d = 2
            num = f"{v:,.{d}f}"
            tail = "/sh" if per_share else ""
            lab = re.sub(r"^\s*\d+[.)]\s*", "", re.sub(r"\s*\([^)]*\)", "", cat)).strip()
            if len(c.get("series") or []) > 1:
                lab = f"{lab}: {se.get('name', '')}"
            written = _as_written(v, text)
            items.append({"value": written or f"{cur}{num}{mag}{'%' if pct else ''}{tail}", "label": lab})
    if len(items) > 6:                                  # keep the first and the last (start and end of a story)
        items = items[:5] + items[-1:]
    return {"kind": "stats", "items": items}


def _stats(ctx, sl, n, c, x, y, w, h):
    """Big figures with labels, spread over the whole exhibit area with hairlines between them."""
    items = c.get("items") or []
    k = max(1, len(items))
    rh = min(1.2, h / k)
    y += (h - rh * k) / 2                                   # a short stack sits centred, not stranded at the top
    big = rh >= 1.25
    vsize, lsize = (40, 16) if big else (28, 14)
    vw = 2.6 if big else 1.9
    for i, it in enumerate(items):
        yy = y + i * rh
        _write(_box(sl, x, yy + 0.02, vw, rh - 0.1, f"role_subtitle_Stat{i + 1}", MSO_ANCHOR.MIDDLE),
               [{"text": it["value"], "size": vsize, "bold": True, "heading": True, "numbers": False,
                 "color": TC.ACCENT_1}])
        _write(_box(sl, x + vw + 0.1, yy + 0.02, w - vw - 0.1, rh - 0.1, f"StatLabel{i + 1}", MSO_ANCHOR.MIDDLE),
               [{"text": it["label"], "size": lsize, "numbers": False}])
        if i < k - 1:
            _rule(sl, x, yy + rh, w, TC.ACCENT_5, 0.5, f"StatRule{i + 1}")


def schema_errors(s: dict) -> list[str]:
    """Precise, fixable messages for the repair loop."""
    a = s.get("archetype")
    errs = [f"'{k}' is missing or empty (required for {a})" for k in REQUIRED.get(a, []) if not s.get(k)]
    t = s.get("table")
    if a in ("table_focus", "table_highlight") and isinstance(t, dict):
        if len(t.get("rows") or []) < 3:
            errs.append("a table needs at least 3 rows of sourced data; with fewer, choose another archetype")
        w = len(t.get("columns") or [])
        cells = [str(c) for r in t.get("rows") or [] for c in r[1:]]
        if cells and sum(1 for c in cells if "[GAP" in c or c.strip().lower() in ("n/a", "")) > 0.3 * len(cells):
            errs.append("over 30% of the table's values are missing; show only rows and columns the facts support")
        if any(len(r) != w for r in t.get("rows") or []):
            errs.append(f"every table row needs {w} cells (one per column)")
    if a == "challenge_matrix":
        rows = s.get("rows") or []
        if not 3 <= len(rows) <= 5:
            errs.append(f"challenge_matrix needs 3-5 rows, got {len(rows)}")
        for i, r in enumerate(rows, 1):
            cells = r.get("cells") or []
            if len(cells) != 2 or not all(c.get("lead") for c in cells if isinstance(c, dict)):
                errs.append(f"row {i}: needs exactly 2 cells, each with a 'lead' sentence and 1-3 bullets")
    if a == "exhibit_argument" and not s.get("title_right"):
        errs.append("'title_right' is missing: the right half's title continues the left title's thought")
    c = s.get("chart") or {}
    if c and c.get("kind") not in ("bubble", "stats") and (not c.get("categories") or not c.get("series")):
        errs.append("chart needs 'categories' and 'series' [{name, values}] with one value per category")
    errs += chart_problems(c) if c.get("kind") != "stats" else []
    return errs


def fallback(s: dict) -> dict:
    """When a slide cannot satisfy its archetype, render the evidence it has in the simplest faithful layout."""
    s = dict(s)
    c = s.get("chart") or {}
    if c and chart_problems(c):                        # a misleading chart becomes a stat stack, layout kept
        st = stat_stack(c, json.dumps({k: v for k, v in s.items() if k not in ("chart", "table")}, ensure_ascii=False))
        if len(st["items"]) < 2:                       # one number is not an exhibit: full-width argument instead
            s["chart"] = None
            if s.get("title_right"):
                s["title"] = s["title"].rstrip(" .…") + " " + s["title_right"].lstrip(" .…")
            s["archetype"] = "argument_only"
            if not s.get("blocks"):
                s["blocks"] = [{"text": "", "bullets": list(s.get("bullets") or [])}]
            return s                                        # the callout stays a callout (so-what band)
        s["chart"] = st
        if s.get("archetype") in ("exhibit_argument",) and s.get("blocks") and s.get("title_right"):
            return s
        s["archetype"] = "bar_takeaway"
        return s
    if s.get("archetype") in ("table_focus", "table_highlight") and isinstance(s.get("table"), dict):
        t = s["table"]
        keep = [r for r in t.get("rows") or [] if not any("[GAP" in str(c) for c in r[1:])]
        s["blocks"] = [{"text": (s.get("takeaway") or {}).get("text", ""), "bullets": [
            {"text": f"**{r[0]}**: " + "; ".join(f"{h} {v}" for h, v in zip(t["columns"][1:], r[1:]))} for r in keep[:6]]}]
        s["archetype"] = "argument_only"
        return s
    if s.get("archetype") == "challenge_matrix" and s.get("rows"):
        s["blocks"] = [{"text": f"**{_txt(r.get('label'))}**", "bullets": [
            b for cell in r.get("cells") or [] for b in ([{"text": cell.get("lead", "")}] if cell.get("lead") else [])
            + list(cell.get("bullets") or [])]} for r in s["rows"]]
        s["archetype"] = "argument_only"
        return s
    if s.get("chart") and (s["chart"].get("categories") or s["chart"].get("points")):
        s["archetype"] = "bar_takeaway"
        if not s.get("bullets"):
            s["bullets"] = [b for blk in s.get("blocks") or [] for b in (blk.get("bullets") or [])][:4]
        return s
    bullets = [b for blk in s.get("blocks") or [] for b in (blk.get("bullets") or [])] + list(s.get("bullets") or [])
    bullets += [b for b in (s.get("callout") or {}).get("bullets", [])]
    s["archetype"] = "argument_only"
    s["blocks"] = s.get("blocks") or [{"text": "", "bullets": bullets}]
    return s


def a_argument_only(ctx, s, sl, n):
    _header(ctx, s, sl, n)
    blocks = []
    for blk in s.get("blocks") or []:
        if blk.get("text"):
            blocks.append({"text": blk["text"], "size": ARGUMENT["intro"], "numbers": False, "space_before": 10})
        for b in blk.get("bullets") or []:
            b = b if isinstance(b, dict) else {"text": b}
            blocks.append({"text": b["text"], "size": ARGUMENT["bullet"] + 2, "bullet": True, "space_before": 4,
                           "prov": b.get("prov")})
    x, w = span(0, 8)
    top_h = BODY_B - BODY_Y
    co = [b["text"] if isinstance(b, dict) else str(b) for b in (s.get("callout") or {}).get("bullets") or []]
    if co:                                               # so-what band across the foot of the body
        cbl = [{"text": t, "size": ARGUMENT["callout"] + 1, "bullet": True, "space_before": 3,
                "color": TC.BACKGROUND_1} for t in co[:3]]
        cw = RIGHT - LEFT - 0.5
        ch = min(1.6, _need(cbl, cw) + 0.25)
        cy = BODY_B - ch
        band = _fill(sl, LEFT, cy, RIGHT - LEFT, ch, TC.TEXT_2, "ov_SoWhat")
        _shape_text(band, cbl, ctx, n, (LEFT + 0.25, cy + 0.06, cw, ch - 0.1), MSO_ANCHOR.MIDDLE)
        top_h = cy - 0.2 - BODY_Y
    blocks, dropped = _trim_to_fit(_grow(blocks, w, top_h), w, top_h, 11)
    if dropped:
        ctx.miss(n, "the argument", 10)
        _cut_note(s, dropped)
    _write(_box(sl, x, BODY_Y, w, top_h, "Argument"), blocks, ctx, n)


# ------------------------------------------------------------------ archetypes
def a_cover(ctx, s, sl, n):
    """Opening (and end) slide: short accent bar, large serif title, subtitle, hairline, date line, firm label.
    The title stays in the layout's title placeholder so outline view and accessibility tools see it."""
    clean = lambda t: re.sub(r"\*\*|\^\w+", "", t or "")
    x, w = LEFT, colx24(17) - LEFT
    from .common import font_file, wrap_lines
    f, _ = font_file("Georgia", True, False, (("Georgia", "Gelasio"),))
    size = next((z for z in (36, 32, 28) if wrap_lines(clean(s["title"]), w - 0.2, z, f, 0.97) <= 3), 28)
    lines = wrap_lines(clean(s["title"]), w - 0.2, size, f, 0.97)
    th = lines * _pitch(size, 1.05) / 72 + 0.3
    ty = 3.35 - th                                         # title block ends on a fixed line, whatever its length
    _fill(sl, x, ty - 0.32, 0.9, 0.06, TC.ACCENT_1, "CoverBar")
    t = sl.shapes.title
    t.left, t.top, t.width, t.height = Inches(x), Inches(ty), Inches(w + 0.5), Inches(th)   # slack past the wrap width
    tf = t.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.BOTTOM
    tf.margin_left = tf.margin_right = Inches(0)
    tf.text = clean(s["title"])
    for p in tf.paragraphs:
        p.alignment = PP_ALIGN.LEFT
        p.line_spacing = Pt(_pitch(size, 1.05))
        for r in p.runs:
            _set_font(r, size, TC.TEXT_1, True, heading=True)
    for ph in list(sl.placeholders):
        if ph.placeholder_format.idx == 1:
            if s.get("subtitle"):
                ph.left, ph.top, ph.width, ph.height = Inches(x), Inches(3.5), Inches(w), Inches(1.0)
                ph.text_frame.word_wrap = True
                ph.text_frame.vertical_anchor = MSO_ANCHOR.TOP
                ph.text_frame.margin_left = Inches(0)
                ph.text = clean(s["subtitle"])
                for p in ph.text_frame.paragraphs:
                    p.alignment = PP_ALIGN.LEFT
                    for r in p.runs:
                        _set_font(r, 16, TC.TEXT_2)
            else:
                ph._element.getparent().remove(ph._element)
    _rule(sl, LEFT, 6.55, RIGHT - LEFT, TC.ACCENT_5, 0.5, "CoverRule")
    if s.get("date"):
        _write(_box(sl, LEFT, 6.65, 6.0, 0.3, "Date"), [{"text": clean(s["date"]), "size": 11, "numbers": False,
                                                         "color": TC.TEXT_2}])
    _write(_box(sl, colx(9), 6.65, RIGHT - colx(9), 0.3, "Firm"),
           [{"text": s.get("firm", FIRM), "size": 11, "bold": True, "numbers": False, "align": PP_ALIGN.RIGHT}])


def a_closing(ctx, s, sl, n):
    """Closing slide: the next steps as numbered rows (action, detail, owner/timing) spread over the body."""
    _header(ctx, s, sl, n)
    steps = [st if isinstance(st, dict) else {"action": str(st)} for st in (s.get("steps") or [])][:6]
    has_when = any(st.get("owner") or st.get("timing") for st in steps)
    nx = LEFT
    ax, aw = span24(1, 7)
    dx, dw = (span24(8, 18) if has_when else span24(8, 23))
    wx, ww = span24(19, 23)
    y0 = TITLE_RULE_Y + 0.06
    heads = [(ax, aw, s.get("columns", ["Action"])[0] if s.get("columns") else "Next step"),
             (dx, dw, "What it involves")] + ([(wx, ww, "Owner and timing")] if has_when else [])
    for x, w, t in heads:
        _write(_box(sl, x, y0, w, 0.3, f"role_subtitle_Head{len(t)}", MSO_ANCHOR.BOTTOM),
               [{"text": t, "size": T["head"], "bold": True, "numbers": False}])
    _rule(sl, LEFT, y0 + 0.36, RIGHT - LEFT, TC.TEXT_1, 0.75, "HeadRule")
    top, bottom = y0 + 0.46, FOOT_RULE_Y - 0.1
    notes = s.get("footnotes") or []
    if notes:
        notes_h = 0.14 * max(1, (len("    ".join(notes)) // 190) + 1) + 0.04
        bottom -= notes_h + 0.06
        ctx.notes_top = FOOT_RULE_Y - 0.03 - notes_h

    def cells(st, size):
        a = [{"text": st.get("action", ""), "size": size + (4 if size > T["body"] else 2), "bold": True, "numbers": False}]
        d = [{"text": st.get("detail", ""), "size": size, "prov": st.get("prov")}] if st.get("detail") else []
        d += [{"text": m if isinstance(m, str) else _txt(m), "size": size - 1, "bullet": True, "space_before": 3,
               "prov": st.get("prov")} for m in (st.get("milestones") or [])[:3]]
        wh = [{"text": " · ".join(v for v in (st.get("owner"), st.get("timing")) if v), "size": size,
               "numbers": False, "color": TC.TEXT_2}] if has_when else []
        return a, d, wh
    size = T["body"] + (2 if len(steps) <= 3 else 0)          # few steps: larger type before larger gaps
    keep_ms = 3
    while True:
        steps_ = [dict(st, milestones=(st.get("milestones") or [])[:keep_ms]) for st in steps]
        laid = [cells(st, size) for st in steps_]
        need = [max(_need(a, aw), _need(d, dw) if d else 0, _need(wh, ww) if wh else 0, 0.4) + 0.12 for a, d, wh in laid]
        if sum(need) <= bottom - top:
            break
        if size > 10:
            size -= 1
        elif keep_ms > 0:                                    # at the floor: milestones go to the notes, not off-page
            keep_ms -= 1
        else:
            break
    if keep_ms < 3:
        cut = [f"{st.get('action')}: " + "; ".join((st.get("milestones") or [])[keep_ms:]) for st in steps
               if (st.get("milestones") or [])[keep_ms:]]
        s["notes"] = (s.get("notes", "") + "\nMilestones not shown: " + " | ".join(cut)).strip()
    if sum(need) > bottom - top:
        ctx.miss(n, "the next steps", int((sum(need) - (bottom - top)) * 30))
    gap = min(0.35, max(0.0, (bottom - top - sum(need)) / max(1, len(steps))))  # thin steps stay flagged (P20)
    y = top
    for i, ((a, d, wh), h) in enumerate(zip(laid, need)):
        rh = h + gap
        _write(_box(sl, nx, y, 0.5, 0.5, f"role_numeral_{i + 1}"),
               [{"text": str(i + 1), "size": T["num"], "bold": True, "heading": True, "numbers": False,
                 "color": TC.ACCENT_1}])
        _write(_box(sl, ax, y + 0.04, aw, rh - 0.1, f"Action{i + 1}"), a, ctx, n)
        if d:
            _write(_box(sl, dx, y + 0.04, dw, rh - 0.1, f"Detail{i + 1}"), d, ctx, n)
        if wh:
            _write(_box(sl, wx, y + 0.04, ww, rh - 0.1, f"When{i + 1}"), wh, ctx, n)
        y += rh
        if i < len(steps) - 1:
            _rule(sl, ax, y - 0.02, RIGHT - ax, TC.ACCENT_5, 0.5, f"StepRule{i + 1}")


def a_challenge_matrix(ctx, s, sl, n):
    """Numbered rows (serif numeral, line icon, two-weight label) x 2 columns of lead + bullets, hairlines."""
    _header(ctx, s, sl, n)
    lx, lw = span24(0, 5)
    ax, aw = span24(6, 14)
    bx, bw = span24(15, 23)
    heads = (s.get("columns") or ["", "", ""]) + ["", "", ""]
    y0 = TITLE_RULE_Y + 0.06                         # the reference starts the column heads right under the rule
    for x, w, t in ((lx, lw, heads[0]), (ax, aw, heads[1]), (bx, bw, heads[2])):
        _write(_box(sl, x, y0, w, 0.3, f"role_subtitle_Head{len(t)}", MSO_ANCHOR.BOTTOM),
               [{"text": t, "size": DENSE["head"], "bold": True, "numbers": False}])
    _rule(sl, LEFT, y0 + 0.36, RIGHT - LEFT, TC.TEXT_1, 0.75, "HeadRule")
    rows = s.get("rows") or []

    def blocks(c):
        bl = []
        if c.get("lead"):
            bl.append({"text": c["lead"], "size": DENSE["lead"], "bold": True, "numbers": False, "prov": c.get("prov")})
        for b in c.get("bullets") or []:
            b = b if isinstance(b, dict) else {"text": b}
            bl.append({"text": b["text"], "size": DENSE["cell"], "bullet": True, "space_before": 3, "prov": b.get("prov")})
        return bl
    top = y0 + 0.42
    avail = FOOT_RULE_Y - 0.06 - top                 # rows run to the source rule; footnotes sit in the label column
    notes_len = len(" ".join(s.get("footnotes") or []))
    if s.get("footnotes") and notes_len <= 90:
        ctx.label_col_notes = (lx, lw)
    elif s.get("footnotes"):                         # long notes take a full-width band above the source rule
        avail -= 0.14 * (notes_len // 200 + 1) + 0.06
        ctx.notes_top = FOOT_RULE_Y - 0.03 - (0.14 * (notes_len // 200 + 1) + 0.04)

    def fit_rows(rs, size):
        out = []
        for r in rs:
            cells = (r.get("cells") or []) + [{}, {}]
            ba, bb = blocks(cells[0]), blocks(cells[1])
            for b in ba + bb:
                b["size"] = size
            lab = [{"text": r.get("label", ""), "size": size}]
            out.append((ba, bb, max(_need(ba, aw), _need(bb, bw), _need(lab, lw - 0.85), 0.45) + 0.08))
        return out
    size = DENSE["cell"]
    laid = fit_rows(rows, size)
    while sum(x[2] for x in laid) > avail and size > s.get("min_size", 10):
        size -= 1
        laid = fit_rows(rows, size)
    while size < T["cell"]:                                # short content: larger type before larger gaps
        bigger = fit_rows(rows, size + 1)
        if sum(x[2] for x in bigger) > avail * 0.97:
            break
        size, laid = size + 1, bigger
    keep = len(rows)
    while keep > 1 and sum(x[2] for x in laid[:keep]) > avail:
        keep -= 1                                          # carry the rest to a continuation slide
    if keep < len(rows):
        ctx.carry = dict(s, rows=rows[keep:], title=_cont_title(s["title"]), number_from=s.get("number_from", 1) + keep,
                         _cont=True)
    gap = min(0.3, max(0.0, (avail - sum(x[2] for x in laid[:keep])) / max(1, keep)))   # never airy rows
    y = top
    first = s.get("number_from", 1)
    for i, (r, (ba, bb, need)) in enumerate(zip(rows[:keep], laid[:keep])):
        rh = need + gap
        _write(_box(sl, lx, y + 0.0, 0.42, 0.5, f"role_numeral_{i + 1}"),
               [{"text": str(first + i), "size": DENSE["num"], "bold": True, "heading": True, "numbers": False}])
        if r.get("icon"):
            _icon(sl, r["icon"], lx + 0.36, y + 0.03, 0.36, re.sub(r"\*\*", "", r.get("label", "")))
        lab_h = min(rh - 0.1, _need([{"text": r.get("label", ""), "size": size}], lw - 0.85) + 0.06)
        _write(_box(sl, lx + 0.85, y + 0.04, lw - 0.85, lab_h, f"Label{i + 1}"),
               [{"text": r.get("label", ""), "size": size, "numbers": False}])
        _write(_box(sl, ax, y + 0.06, aw, rh - 0.1, f"A{i + 1}"), ba, ctx, n)
        _write(_box(sl, bx, y + 0.06, bw, rh - 0.1, f"B{i + 1}"), bb, ctx, n)
        y += rh
        if i < keep - 1:
            _rule(sl, lx + 0.85, y, RIGHT - lx - 0.85, TC.ACCENT_5, 0.5, f"RowRule{i + 1}")


def a_exhibit_argument(ctx, s, sl, n):
    """Exhibit on white (left); the argument on a grey half-panel (right) ending in a navy 'so what' with an arrow."""
    lxx, lw = span24(0, 12)
    split = colx24(13) + 0.05
    panel = _fill(sl, split, 0, 13.333 - split, 7.5, TC.BACKGROUND_2, "ov_ArgumentPanel")
    _header(ctx, s, sl, n, title_w=lw)
    rx, rw = span24(14, 23)
    tr = s.get("title_right", "")
    _write(_box(sl, rx, 0.42, rw, 0.92, "role_title_Right", MSO_ANCHOR.BOTTOM),
           [{"text": tr, "size": _title_size(tr, rw) or 20, "bold": True, "heading": True, "numbers": False,
             "line_spacing": 1.0}])
    _rule(sl, rx, TITLE_RULE_Y, rw, TC.TEXT_1, 0.75, "TitleRuleRight")
    ex = s.get("exhibit") or {}
    y = BODY_Y - 0.05
    if ex.get("title"):
        tb_ = [{"text": ex["title"], "size": ARGUMENT["exhibit"], "bold": True, "numbers": False}]
        if _need(tb_, lw) > 0.4:                          # a long exhibit title steps down before it wraps
            tb_[0]["size"] = ARGUMENT["exhibit"] - 2
        th_ = max(0.32, _need(tb_, lw) + 0.04)
        _write(_box(sl, LEFT, y, lw, th_, "role_subtitle_ExhibitTitle"), tb_)
        y += th_
    if ex.get("unit"):
        ub_ = [{"text": ex["unit"], "size": ARGUMENT["unit"], "numbers": False}]
        uh_ = max(0.26, _need(ub_, lw) + 0.02)
        _write(_box(sl, LEFT, y, lw, uh_, "role_subtitle_ExhibitUnit"), ub_)
        y += uh_ + 0.02
    foot = 0.35 if s.get("footnotes") else 0
    _chart(ctx, sl, n, s["chart"], LEFT, y, lw, BODY_B - y - foot)
    blocks = []
    for blk in s.get("blocks") or []:
        blocks.append({"text": blk["text"], "size": ARGUMENT["intro"], "numbers": False, "space_before": 8, "prov": blk.get("prov")})
        for b in blk.get("bullets") or []:
            b = b if isinstance(b, dict) else {"text": b}
            blocks.append({"text": b["text"], "size": ARGUMENT["bullet"], "bullet": True, "level": 1, "space_before": 3,
                           "prov": b.get("prov")})
    if blocks:
        blocks[0]["space_before"] = 0
    co = s.get("callout") or {}
    cb = [{"text": b["text"] if isinstance(b, dict) else b, "size": ARGUMENT["callout"], "bullet": True,
           "color": TC.BACKGROUND_1, "numbers": False, "space_before": 5,
           "prov": b.get("prov") if isinstance(b, dict) else None} for b in co.get("bullets") or []]
    if cb:
        cb, _ = _fit(cb, rw - 0.3, 1.3, 10)
    ch = (_need(cb, rw - 0.3) + 0.28) if cb else 0
    cy = BODY_B - foot - ch
    space = cy - BODY_Y - 0.12
    blocks, ok = _fit(blocks, rw - 0.1, space, 10)
    if not ok:
        ctx.miss(n, "the argument column", max(5, int(sum(len(b["text"].split()) for b in blocks) * 0.15)))
        blocks, dropped = _trim_to_fit(blocks, rw - 0.1, space, 10)
        _cut_note(s, dropped)
        ok = True
    fitted = min((b["size"] for b in blocks if b.get("bullet")), default=ARGUMENT["bullet"])
    for b in cb:
        b["size"] = min(b["size"], fitted)
    if cb:                                             # callout shrinks with the argument: re-measure and re-seat it
        ch = _need(cb, rw - 0.3) + 0.28
        cy = BODY_B - foot - ch
    if not ok:
        ctx.miss(n, "the argument column", max(5, int(sum(len(b["text"].split()) for b in blocks) * 0.15)))
    _shape_text(panel, blocks, ctx, n, (rx, BODY_Y - 0.05, rw, space))       # the argument lives in the panel
    if cb:
        arr = _fill(sl, rx - 0.95, cy + ch / 2 - 0.3, 0.98, 0.6, TC.TEXT_2, "ov_SoWhatArrow", MSO_SHAPE.PENTAGON)
        arr.rotation = 180
        box = _fill(sl, rx, cy, rw, ch, TC.TEXT_2, "ov_SoWhat")
        _shape_text(box, cb, ctx, n, (rx + 0.12, cy + 0.06, rw - 0.2, ch - 0.1), MSO_ANCHOR.MIDDLE)


_NUMCELL = re.compile(r"^\s*([+\-−]?)\s*([$€£]?)\s*(\d[\d,]*(?:\.\d+)?)\s*(%|pp|x|[KMBT]|bn|m)?\s*$", re.I)


def _table_as_chart(t: dict):
    """A small table whose value columns are numbers in one unit -> clustered bar/column chart data (or None)."""
    rows = t.get("rows") or []
    cols = t.get("columns") or []
    if not 2 <= len(rows) <= 6 or len(cols) < 2:
        return None
    units, series = None, []
    for ci in range(1, len(cols)):
        parsed = []
        for r in rows:
            m = _NUMCELL.match(_plain(r[ci]).replace("−", "-")) if ci < len(r) else None
            if not m:
                parsed = None
                break
            sign, cur, num, unit = m.groups()
            parsed.append(((-1 if sign in ("-", "−") else 1) * float(num.replace(",", "")), (cur or "") + (unit or "").lower()))
        if not parsed or len({u for _, u in parsed}) != 1:
            continue
        u = parsed[0][1]
        if units is None:
            units = u
        if u == units and len(series) < 3:
            series.append({"name": _plain(cols[ci]), "values": [v for v, _ in parsed]})
    if not series:
        return None
    cats = [_plain(r[0]) for r in rows]
    kind = "bar" if max(len(c) for c in cats) > 14 else "column"
    decimals = 1 if any(abs(v - round(v)) > 1e-9 for se in series for v in se["values"]) else 0
    hl = [cats[i] for i in t.get("highlight_rows") or [] if i < len(cats)] if len(series) == 1 else []
    return {"kind": kind, "categories": cats, "series": series, "unit": units, "decimals": decimals,
            "highlight_categories": hl, "alt": f"{kind} chart: " + "; ".join(se["name"] for se in series)}


def _trim_to_fit(blocks, w, h, floor):
    """Fit at or above `floor`; if the text still does not fit, drop trailing lines (bullets first) until it does.
    Returns (blocks, dropped texts). A finished slide never overflows; what was cut goes to the notes."""
    bl, ok = _fit(blocks, w, h, floor)
    dropped = []
    while not ok and len(bl) > 1:
        k = max((i for i, b in enumerate(bl) if b.get("bullet")), default=len(bl) - 1)
        dropped.insert(0, bl.pop(k)["text"])
        bl, ok = _fit(bl, w, h, floor)
    return bl, dropped


def _cut_note(s, dropped):
    if dropped:
        s["notes"] = (s.get("notes", "") + "\n[QA] Did not fit and was left out: " + " | ".join(dropped)).strip()


def _grow(blocks, w, h, max_bump=2):
    """Short text steps up the type scale (at most two points) instead of leaving the column half empty."""
    import copy
    best = blocks
    for bump in range(1, max_bump + 1):
        trial = copy.deepcopy(blocks)
        for b in trial:
            b["size"] = b.get("size", T["body"]) + bump
        if _need(trial, w) > h * 0.9:
            break
        best = trial
    return best


def a_table_focus(ctx, s, sl, n):
    _header(ctx, s, sl, n)
    t = s["table"]
    y = TITLE_RULE_Y + 0.12
    if s.get("legend"):
        _write(_box(sl, colx(6), y, RIGHT - colx(6), 0.3, "role_subtitle_Legend"),
               [{"text": s["legend"], "size": T["label"], "numbers": False, "align": PP_ALIGN.RIGHT}])
        y += 0.36
    tk = s.get("takeaway") or {}
    notes = s.get("footnotes") or []
    notes_h = (0.14 * max(1, (len("    ".join(notes)) // 190) + 1) + 0.04) if notes else 0
    tk_h = 0.34 if tk.get("text") else 0
    bottom = FOOT_RULE_Y - 0.04 - notes_h - tk_h
    ctx.carry_rows = None
    t = dict(t)
    bullets = [b if isinstance(b, dict) else {"text": b} for b in s.get("bullets") or []]
    natural = _table(ctx, sl, n, dict(t), LEFT, y, RIGHT - LEFT, bottom - y, dry=True)
    ctx.carry_rows = None
    hgt = FOOT_RULE_Y - 0.04 - notes_h - y
    tx, tw = span(0, 7)
    ax, aw = span(8, 11)
    as_chart = _table_as_chart(t) if bullets else None
    side = None
    if bullets and not as_chart and len(t.get("columns") or []) <= 4 and natural < 0.72 * (bottom - y):
        side = _table(ctx, sl, n, dict(t), tx, y, tw, hgt, dry=True)   # measured at the width it will be drawn
        ctx.carry_rows = None
        if side == float("inf"):
            side = None
    if as_chart or side:
        if as_chart:                                       # a small all-number table reads better as a chart
            _chart(ctx, sl, n, as_chart, tx, y, tw, hgt)
            ctx.track(n, " ".join(" ".join(map(str, r)) for r in t["rows"]), t.get("prov"))
        else:                                              # short table: table left, its argument right
            _table(ctx, sl, n, dict(t, _pad=0.16), tx, y, tw, hgt)
        blocks = ([{"text": tk["text"], "size": T["head"], "bold": True, "numbers": False, "prov": tk.get("prov")}]
                  if tk.get("text") else [])
        blocks += [{"text": b["text"], "size": T["body"], "bullet": True, "space_before": 6, "prov": b.get("prov")}
                   for b in bullets]
        _rule(sl, ax, y, aw, TC.TEXT_1, 0.75, "TakeawayRule")
        _write(_box(sl, ax, y + 0.1, aw, hgt - 0.1, "Takeaways"), _fit(_grow(blocks, aw, hgt - 0.1), aw, hgt - 0.1, 11)[0],
               ctx, n)
        ctx.notes_top = FOOT_RULE_Y - 0.03 - notes_h if notes else None
        return
    gf = _table(ctx, sl, n, t, LEFT, y, RIGHT - LEFT, bottom - y)
    if ctx.carry_rows:
        ctx.carry = dict(s, table=dict(s["table"], rows=ctx.carry_rows, highlight_rows=[]),
                         title=_cont_title(s["title"]), _cont=True)
    if tk.get("text"):
        _write(_box(sl, LEFT, min(bottom + 0.04, y + gf.rendered_h + 0.14), RIGHT - LEFT, 0.3,
                    "role_subtitle_Takeaway"),
               [{"text": tk["text"], "size": T["head"], "bold": True, "numbers": False, "align": PP_ALIGN.CENTER,
                 "prov": tk.get("prov")}], ctx, n)
    ctx.notes_top = FOOT_RULE_Y - 0.03 - notes_h if notes else None


def a_chart_takeaway(ctx, s, sl, n):
    """Chart with its takeaway. The text goes beside the chart when it fills that column; when it is short, the chart
    takes the full width and the takeaway sits in a band under it, so neither leaves a dead block."""
    _header(ctx, s, sl, n)
    tk = s.get("takeaway") or {}
    bullets = [b if isinstance(b, dict) else {"text": b} for b in s.get("bullets") or []]
    lead = ([{"text": tk["text"], "size": T["head"], "bold": True, "numbers": False, "prov": tk.get("prov")}]
            if tk.get("text") else [])
    bl = [{"text": b["text"], "size": T["body"], "bullet": True, "space_before": 6, "prov": b.get("prov")}
          for b in bullets]
    body_h = BODY_B - BODY_Y
    bx, bw = span(8, 11)
    long_series = len((s["chart"] or {}).get("categories") or []) >= 8
    if not long_series or _need(lead + bl, bw) >= 0.62 * body_h or not (lead or bl):
        # The split follows the text: a narrower column for less text (the chart gains the width), then a step up
        # the type scale; the renderer never adds gaps to fake fullness.
        import copy
        best = None
        for split in (8, 9):
            tx, tw = span(split, 11)
            for bump in (0, 1, 2):
                trial = copy.deepcopy(lead + bl)
                for b in trial:
                    b["size"] = b.get("size", T["body"]) + bump
                need = _need(trial, tw)
                if need > body_h - 0.1:
                    break
                best = (split, trial, need)
                if need >= 0.62 * body_h:
                    break
            if best and best[0] == split and best[2] >= 0.62 * body_h:
                break
        split, blocks_, _ = best or (8, lead + bl, 0)
        x, w = span(0, split - 1)
        bx, bw = span(split, 11)
        _chart(ctx, sl, n, s["chart"], x, BODY_Y, w, body_h)
        _rule(sl, bx, BODY_Y, bw, TC.TEXT_1, 0.75, "TakeawayRule")
        blocks_, dropped = _trim_to_fit(blocks_, bw, body_h - 0.1, 11)
        _cut_note(s, dropped)
        _write(_box(sl, bx, BODY_Y + 0.1, bw, body_h - 0.1, "Takeaways"), blocks_, ctx, n)
        return
    lx, lw = span(0, 3)                                     # band: lead on the left, bullets in one or two columns
    cols = [bl] if len(bl) < 3 else [bl[: (len(bl) + 1) // 2], bl[(len(bl) + 1) // 2:]]
    cw = (RIGHT - colx(4) - GUT * (len(cols) - 1)) / len(cols)
    band = max([_need(lead, lw)] + [_need(c, cw) for c in cols]) + 0.04
    band = min(band, 0.45 * body_h)
    cy = BODY_B - band
    _chart(ctx, sl, n, s["chart"], LEFT, BODY_Y, RIGHT - LEFT, cy - BODY_Y - 0.18)
    _rule(sl, LEFT, cy - 0.08, RIGHT - LEFT, TC.TEXT_1, 0.75, "TakeawayRule")
    if lead:
        _write(_box(sl, lx, cy, lw, band, "Takeaway"), _fit(lead, lw, band, 12)[0], ctx, n)
    for i, c in enumerate(cols):
        _write(_box(sl, colx(4) + i * (cw + GUT), cy, cw, band, f"Takeaways{i + 1}"), _fit(c, cw, band, 10)[0], ctx, n)


def a_waterfall_bridge(ctx, s, sl, n):
    a_chart_takeaway(ctx, dict(s, chart=dict(s["chart"], kind="waterfall")), sl, n)


ARCHETYPES = {"argument_only": a_argument_only, "cover": a_cover, "section_divider": a_cover, "challenge_matrix": a_challenge_matrix,
              "text_matrix": a_challenge_matrix, "exhibit_argument": a_exhibit_argument, "table_focus": a_table_focus,
              "table_highlight": a_table_focus, "bar_takeaway": a_chart_takeaway, "column_trend": a_chart_takeaway,
              "line_trend": a_chart_takeaway, "stacked_composition": a_chart_takeaway,
              "waterfall_bridge": a_waterfall_bridge, "closing": a_closing, "next_steps": a_closing, "end": a_cover}
DIVIDERS = {"cover", "section_divider", "appendix_divider", "end"}


def render(slides: list[dict], out_path: str, template: str | None = None, fix_labels: bool = True,
           carry: bool = True) -> dict:
    """Render, then (when LibreOffice is available) re-render up to 3 times moving only chart labels that collide.
    carry=False (a fixed slide count): content longer than its slide is never continued on an extra slide; what
    does not fit goes to the speaker notes and is reported, so the content model can cut or merge it."""
    res = _render(slides, out_path, template, carry)
    if not fix_labels:
        return res
    try:
        for _ in range(3):
            moved = _relabel(slides, out_path)
            if not moved:
                break
            res = _render(slides, out_path, template, carry)
    except Exception as e:                                   # QA aid only: never fail the build on it
        res["errors"].append(f"label check skipped: {type(e).__name__}")
    return res


_CYCLE = ["right", "left", "above", "below"]


def _relabel(slides, out_path) -> int:
    """Render to PDF, find chart labels whose text collides with other text, move each to its next position."""
    import tempfile
    from .common import soffice_convert
    from .validate_pptx import _pdf_spans
    with tempfile.TemporaryDirectory() as td:
        pages = _pdf_spans(str(soffice_convert(out_path, "pdf", td)))
    moved = 0
    for (pw, spans), s in zip(pages, slides):
        c = s.get("chart") or {}
        if c.get("kind") != "bubble":
            continue
        k = 13.333 / pw
        boxes = [(t, (b[0] * k, b[1] * k, b[2] * k, b[3] * k)) for t, b in spans]
        for p in c["points"]:
            lab = p.get("label")
            if not lab:
                continue
            first = lab.split()[0]
            mine = [bx for t, bx in boxes if t.strip().startswith(first)]
            hit = False
            for (x0, y0, x1, y1) in mine:
                for t, (a0, b0, a1, b1) in boxes:
                    if t.strip().startswith(first) or not t.strip():
                        continue
                    if min(x1, a1) - max(x0, a0) > 0.03 and min(y1, b1) - max(y0, b0) > 0.04:
                        hit = True
            if hit:
                cur = p.get("label_pos") or "right"
                nxt = _CYCLE[(_CYCLE.index(cur) + 1) % 4] if cur in _CYCLE else "right"
                tried = p.setdefault("_tried", [cur])
                if nxt not in tried:
                    p["label_pos"] = nxt
                    tried.append(nxt)
                    moved += 1
    return moved


def _render(slides: list[dict], out_path: str, template: str | None = None, carry: bool = True) -> dict:
    prs = Presentation(template or default_template())
    for sid in list(prs.slides._sldIdLst):
        prs.part.drop_rel(sid.rId)
        prs.slides._sldIdLst.remove(sid)
    ctx = Ctx({})
    errors = []
    queue = list(slides)
    n = 0
    while queue:
        s = normalize(queue.pop(0))
        n += 1
        ctx.carry = None
        if s.get("archetype") not in DIVIDERS and not s.get("_cont") and schema_errors(s):
            errors += [f"slide {n} ({s.get('archetype')}): {e}" for e in schema_errors(s)]
            s = fallback(s)
        arch = s.get("archetype")
        fn = ARCHETYPES.get(arch)
        if fn is None:
            errors.append(f"slide {n}: archetype '{arch}' is not in the library {sorted(ARCHETYPES)}")
            fn = a_chart_takeaway if s.get("chart") else a_challenge_matrix
        sl = prs.slides.add_slide(layout(prs, "Title Slide" if arch in DIVIDERS else "Title Only"))
        sl._element.find(qn("p:cSld")).set("name", f"arch:{arch} node:{s.get('node', '')}")
        try:
            fn(ctx, s, sl, n)
            if arch not in DIVIDERS:
                _footer(ctx, s, sl, n)
        except (KeyError, TypeError, IndexError, ValueError, AttributeError) as e:
            errors.append(f"slide {n} ({arch}): missing or malformed field: {type(e).__name__}: {e}")
            for shp in list(sl.shapes):                     # never leave a half-drawn slide: redraw it simply
                if not shp.is_placeholder:
                    shp._element.getparent().remove(shp._element)
            fb = fallback(dict(s, chart=None) if s.get("chart") else s)
            if fb.get("archetype") == arch:
                bl = [b for blk in s.get("blocks") or [] for b in [{"text": blk.get("text", "")}] + list(blk.get("bullets") or [])]
                fb = dict(s, archetype="argument_only", chart=None,
                          blocks=[{"text": "", "bullets": bl + list(s.get("bullets") or [])}])
            try:
                sl._element.find(qn("p:cSld")).set("name", f"arch:{fb['archetype']} node:{s.get('node', '')}")
                ARCHETYPES[fb["archetype"]](ctx, fb, sl, n)
                _footer(ctx, fb, sl, n)
            except Exception as e2:
                errors.append(f"slide {n}: fallback layout failed too: {type(e2).__name__}: {e2}")
        if s.get("notes"):
            sl.notes_slide.notes_text_frame.text = s["notes"]
        if ctx.carry and carry:
            queue.insert(0, ctx.carry)
            errors.append(f"slide {n}: rows carried to a continuation slide (content longer than one slide)")
        elif ctx.carry:                                     # fixed slide count: never an extra slide
            left = ctx.carry.get("rows") or (ctx.carry.get("table") or {}).get("rows") or []
            txt = "; ".join(_txt(r.get("label")) if isinstance(r, dict) else " | ".join(map(str, r)) for r in left)
            prev = sl.notes_slide.notes_text_frame.text if sl.has_notes_slide else ""
            sl.notes_slide.notes_text_frame.text = (prev + f"\n[QA] Did not fit on the slide and left out: {txt}").strip()
            errors.append(f"slide {n}: rows carried to a continuation slide (content longer than one slide); "
                          f"{len(left)} rows left out because the slide count is fixed")
    errors += ctx.overflow
    prs.save(out_path)
    prov_path = str(Path(out_path).with_suffix("")) + ".provenance.json"
    Path(prov_path).write_text(json.dumps({"slides": ctx.prov}, indent=1))
    return {"slides": n, "errors": errors, "provenance": prov_path}
