"""Deterministic deck renderer: spec JSON -> .pptx. Models never place shapes; this file owns layout."""
from __future__ import annotations

import re

import copy
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION, XL_LABEL_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from . import theme as T
from .spec import normalize, validate
from .textfit import fit_size, wrap

CHART_MAP = {
    "bar": XL_CHART_TYPE.BAR_CLUSTERED,
    "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "stacked_column": XL_CHART_TYPE.COLUMN_STACKED,
    "line": XL_CHART_TYPE.LINE_MARKERS,
    "pie": XL_CHART_TYPE.PIE,
    "doughnut": XL_CHART_TYPE.DOUGHNUT,
}


class RenderError(ValueError):
    pass


# ----------------------------------------------------------------- primitives
def _bg(slide, color):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = T.rgb(color)


def _rect(slide, x, y, w, h, color, line=None):
    s = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    s.fill.solid()
    s.fill.fore_color.rgb = T.rgb(color)
    if line:
        s.line.color.rgb = T.rgb(line)
        s.line.width = Pt(0.75)
    else:
        s.line.fill.background()
    s.shadow.inherit = False
    return s


def _set_bullet(p, char="•", level=0):
    pPr = p._p.get_or_add_pPr()
    indent = 0.28 + 0.3 * level
    pPr.set("marL", str(int(Inches(indent))))
    pPr.set("indent", str(int(-Inches(0.24))))
    for tag in ("a:buNone", "a:buChar", "a:buFont", "a:buClr"):
        for el in pPr.findall(qn(tag)):
            pPr.remove(el)
    clr = etree.SubElement(pPr, qn("a:buClr"))
    etree.SubElement(clr, qn("a:srgbClr"), val=T.ACCENT)
    etree.SubElement(pPr, qn("a:buFont"), typeface=T.FONT)
    etree.SubElement(pPr, qn("a:buChar"), char=char)


def _no_bullet(p):
    pPr = p._p.get_or_add_pPr()
    etree.SubElement(pPr, qn("a:buNone"))


def _text(slide, x, y, w, h, paras, anchor=MSO_ANCHOR.TOP, align=PP_ALIGN.LEFT, name=None):
    """paras: list of dicts {text, size, bold, italic, color, space_before, bullet(level|None)}."""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    tf.auto_size = None
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    for i, pd in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = T.LINE_SPACING
        if pd.get("space_before"):
            p.space_before = Pt(pd["space_before"])
        r = p.add_run()
        r.text = pd["text"]
        f = r.font
        f.name = T.FONT
        f.size = Pt(pd["size"])
        f.bold = bool(pd.get("bold"))
        f.italic = bool(pd.get("italic"))
        f.color.rgb = T.rgb(pd.get("color", T.INK))
        if pd.get("bullet") is not None:
            _set_bullet(p, level=pd["bullet"])
        else:
            _no_bullet(p)
    return tb


def _style_of(pd):
    return "bold" if pd.get("bold") else ("italic" if pd.get("italic") else "regular")


def _fit(paras_at, w, h, pref, minimum, indent_in=0.0):
    """Largest size where paragraphs fit w x h. Raises if even the minimum overflows."""
    def as_tuples(size):
        return [(pd["text"], pd["size"], _style_of(pd), pd.get("space_before", 0)) for pd in paras_at(size)]
    size = fit_size(as_tuples, w - indent_in, h, pref, minimum, step=0.5)
    if size is None:
        raise RenderError(f"text does not fit {w:.2f}x{h:.2f}in even at {minimum}pt")
    return paras_at(size)


# ----------------------------------------------------------------- chrome
def _title(slide, text, top=T.TITLE_TOP, width=T.BODY_W):
    paras = _fit(lambda s: [{"text": text, "size": s, "bold": True, "color": T.INK}],
                 width, T.TITLE_H, *T.TITLE_PT)
    _text(slide, T.MARGIN_X, top, width, T.TITLE_H, paras, anchor=MSO_ANCHOR.BOTTOM, name="Title")
    _rect(slide, T.MARGIN_X, top + T.TITLE_H + 0.12, 0.6, 0.05, T.ACCENT).name = "TitleRule"


def _footer(slide, source, number):
    if source:
        txt = source if source.lower().startswith("source") else f"Source: {source}"
        lines = wrap(txt, T.BODY_W - 1.0, T.FOOTER_PT)
        if len(lines) > 1:
            txt = lines[0].rstrip(" ,;") + "…"
        _text(slide, T.MARGIN_X, T.FOOTER_TOP, T.BODY_W - 1.0, T.FOOTER_H,
              [{"text": txt, "size": T.FOOTER_PT, "color": T.FAINT}], anchor=MSO_ANCHOR.MIDDLE, name="Source")
    _text(slide, T.SLIDE_W - T.MARGIN_X - 0.8, T.FOOTER_TOP, 0.8, T.FOOTER_H,
          [{"text": str(number), "size": T.FOOTER_PT, "color": T.FAINT}],
          anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.RIGHT, name="PageNumber")


def _bullet_paras(bullets, size):
    sub = max(T.SUB_BULLET_PT[1], size - 3)
    out = []
    for i, b in enumerate(bullets):
        text = b if isinstance(b, str) else b.get("text", "")
        out.append({"text": text, "size": size, "color": T.INK, "bullet": 0, "bold": bool(isinstance(b, dict) and b.get("bold")),
                    "space_before": 0 if i == 0 else size * 0.6})
        if isinstance(b, dict):
            for sb in b.get("sub", [])[:3]:
                out.append({"text": sb, "size": sub, "color": T.MUTED, "bullet": 1, "space_before": 3})
    return out


# ----------------------------------------------------------------- slide types
def s_title(prs, s, n):
    sl = prs.slides.add_slide(prs.slide_layouts[6])
    _bg(sl, T.ACCENT_DARK)
    _rect(sl, T.MARGIN_X, 2.35, 0.9, 0.07, T.SERIES[3])
    paras = _fit(lambda z: [{"text": s["title"], "size": z, "bold": True, "color": "FFFFFF"}],
                 10.5, 2.0, *T.COVER_TITLE_PT)
    _text(sl, T.MARGIN_X, 2.6, 10.5, 2.0, paras, name="Title")
    y = 4.75
    if s.get("subtitle"):
        p = _fit(lambda z: [{"text": s["subtitle"], "size": z, "color": "C9D3EE"}], 10.5, 0.9, *T.SUBTITLE_PT)
        _text(sl, T.MARGIN_X, y, 10.5, 0.9, p, name="Subtitle")
    if s.get("meta"):
        _text(sl, T.MARGIN_X, 6.55, 10.5, 0.4, [{"text": s["meta"], "size": 12, "color": "C9D3EE"}], name="Meta")
    return sl


def s_section(prs, s, n):
    sl = prs.slides.add_slide(prs.slide_layouts[6])
    _bg(sl, T.ACCENT_DARK)
    if s.get("number") is not None:
        _text(sl, T.MARGIN_X, 2.3, 3, 0.9, [{"text": f"{int(s['number']):02d}", "size": 40, "bold": True,
                                              "color": T.SERIES[3]}], name="SectionNumber")
    paras = _fit(lambda z: [{"text": s["title"], "size": z, "bold": True, "color": "FFFFFF"}], 10.5, 1.8, 36, 24)
    _text(sl, T.MARGIN_X, 3.25, 10.5, 1.8, paras, name="Title")
    if s.get("subtitle"):
        _text(sl, T.MARGIN_X, 5.15, 10.5, 0.8, [{"text": s["subtitle"], "size": 16, "color": "C9D3EE"}], name="Subtitle")
    return sl


def _content_slide(prs, s):
    sl = prs.slides.add_slide(prs.slide_layouts[6])
    _bg(sl, T.BG)
    if s.get("section_tag"):
        _text(sl, T.MARGIN_X, 0.18, 8, 0.3, [{"text": s["section_tag"], "size": 10, "bold": True, "color": T.ACCENT}],
              name="SectionTag")
    _title(sl, s["title"])
    return sl


def s_bullets(prs, s, n):
    sl = _content_slide(prs, s)
    w = T.BODY_W - (3.9 if s.get("callout") else 0)
    h = T.BODY_BOTTOM - T.BODY_TOP
    paras = _fit(lambda z: _bullet_paras(s["bullets"], z), w, h, *T.BODY_PT, indent_in=0.6)
    _text(sl, T.MARGIN_X, T.BODY_TOP, w, h, paras, name="Body")
    if s.get("callout"):
        x = T.MARGIN_X + w + 0.4
        cw = T.BODY_W - w - 0.4
        _rect(sl, x, T.BODY_TOP, cw, h, T.PANEL).name = "CalloutPanel"
        _rect(sl, x, T.BODY_TOP, 0.06, h, T.ACCENT).name = "CalloutRule"
        cp = _fit(lambda z: [{"text": s["callout"], "size": z, "bold": True, "color": T.ACCENT_DARK}],
                  cw - 0.6, h - 0.6, 20, 13)
        _text(sl, x + 0.3, T.BODY_TOP + 0.3, cw - 0.6, h - 0.6, cp, anchor=MSO_ANCHOR.MIDDLE, name="Callout")
    return sl


def s_two_column(prs, s, n):
    sl = _content_slide(prs, s)
    cw = (T.BODY_W - T.GUTTER) / 2
    h = T.BODY_BOTTOM - T.BODY_TOP
    # shared size so both columns look identical
    def both(z):
        return [_col_paras(s["left"], z), _col_paras(s["right"], z)]
    size = T.BODY_PT[0]
    while size >= T.SUB_BULLET_PT[1]:
        try:
            for side in both(size):
                _fit(lambda _z, side=side: side, cw, h - 0.1, size, size, indent_in=0.6)
            break
        except RenderError:
            size -= 0.5
    else:
        raise RenderError("two_column content does not fit; shorten bullets")
    for i, key in enumerate(("left", "right")):
        x = T.MARGIN_X + i * (cw + T.GUTTER)
        _text(sl, x, T.BODY_TOP, cw, h, _col_paras(s[key], size), name=f"Column{i + 1}")
    return sl


def _col_paras(col, z):
    out = []
    if col.get("heading"):
        out.append({"text": col["heading"], "size": z + 2, "bold": True, "color": T.ACCENT})
    bp = _bullet_paras(col["bullets"], z)
    if bp and out:
        bp[0]["space_before"] = z * 0.7
    return out + bp


def s_chart(prs, s, n):
    sl = _content_slide(prs, s)
    c = s["chart"]
    take = s.get("takeaways") or []
    cw = T.BODY_W - (4.0 if take else 0)
    h = T.BODY_BOTTOM - T.BODY_TOP
    cats, series = [str(x) for x in c["categories"]], [dict(se) for se in c["series"]]
    _period = re.compile(r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|q[1-4]|h[12]|fy|\d{4}|week|wk|month|year)", re.I)
    is_time = sum(bool(_period.match(str(x).strip())) for x in cats) >= max(2, len(cats) // 2)
    if c["type"] == "bar" and len(series) == 1 and not is_time:   # ranking bars: largest at top; time stays in order
        order = sorted(range(len(cats)), key=lambda i: float(series[0]["values"][i]))
        cats = [cats[i] for i in order]
        series[0]["values"] = [series[0]["values"][i] for i in order]
    if c.get("number_format", "General") == "General":   # readable labels: no raw floats like 60712.19
        mx = max(abs(float(v)) for se in series for v in se["values"]) if series else 0
        c = dict(c, number_format='#,##0,"K"' if mx >= 10000 else ("#,##0" if mx >= 100 else "0.0"))
    data = CategoryChartData()
    data.categories = cats
    for se in series:
        data.add_series(se["name"], [float(v) for v in se["values"]])
    gf = sl.shapes.add_chart(CHART_MAP[c["type"]], Inches(T.MARGIN_X), Inches(T.BODY_TOP),
                             Inches(cw), Inches(h), data)
    from engines.forge.common import positive_axis_ids
    positive_axis_ids(gf.chart)
    gf.name = "Chart"
    ch = gf.chart
    ch.has_title = False
    ch.font.name = T.FONT
    ch.font.size = Pt(12)
    ch.font.color.rgb = T.rgb(T.MUTED)
    fmt = c.get("number_format", "General")
    multi = len(c["series"]) > 1
    if c["type"] in ("pie", "doughnut"):
        ch.has_legend = True
        ch.legend.position = XL_LEGEND_POSITION.RIGHT
        ch.legend.include_in_layout = False
        pts = ch.plots[0].series[0].points
        for i in range(len(c["categories"])):
            pts[i].format.fill.solid()
            pts[i].format.fill.fore_color.rgb = T.rgb(T.SERIES[i % len(T.SERIES)])
        pl = ch.plots[0]
        pl.has_data_labels = True
        pl.data_labels.number_format = fmt if fmt != "General" else "0%"
        pl.data_labels.number_format_is_linked = fmt == "General"
        pl.data_labels.show_percentage = fmt == "General"
        pl.data_labels.show_value = fmt != "General"
    else:
        ch.has_legend = multi
        if multi:
            ch.legend.position = XL_LEGEND_POSITION.TOP
            ch.legend.include_in_layout = False
        va = ch.value_axis
        va.has_major_gridlines = True
        va.major_gridlines.format.line.color.rgb = T.rgb(T.RULE)
        va.format.line.fill.background()
        va.tick_labels.number_format = fmt
        va.tick_labels.number_format_is_linked = fmt == "General"
        ca = ch.category_axis
        ca.format.line.color.rgb = T.rgb(T.RULE)
        ca.has_major_gridlines = False
        for i, se in enumerate(ch.plots[0].series):
            color = T.rgb(T.SERIES[i % len(T.SERIES)])
            if c["type"] == "line":
                se.format.line.color.rgb = color
                se.format.line.width = Pt(2.5)
                se.smooth = False
            else:
                se.format.fill.solid()
                se.format.fill.fore_color.rgb = color
        if c["type"] in ("bar", "column", "stacked_column"):
            ch.plots[0].gap_width = 60
            if c["type"] == "stacked_column":
                ch.plots[0].overlap = 100
            if not multi and len(c["categories"]) <= 12:
                pl = ch.plots[0]
                pl.has_data_labels = True
                pl.data_labels.number_format = fmt
                pl.data_labels.number_format_is_linked = fmt == "General"
                pl.data_labels.position = XL_LABEL_POSITION.OUTSIDE_END
                pl.data_labels.font.size = Pt(11)
                pl.data_labels.font.color.rgb = T.rgb(T.INK)
    if take:
        _side_takeaways(sl, take, T.MARGIN_X + cw + 0.4, 4.0 - 0.4, h)
    return sl


def _side_takeaways(sl, take, x, w, h):
    _rect(sl, x, T.BODY_TOP, w, h, T.PANEL).name = "TakeawayPanel"
    paras = lambda z: [{"text": "What this shows", "size": z - 2, "bold": True, "color": T.ACCENT}] + [
        dict(pd) for pd in _bullet_paras(take[:4], z)]
    def fixed(z):
        ps = paras(z)
        if len(ps) > 1:
            ps[1]["space_before"] = z * 0.6
        return ps
    p = _fit(fixed, w - 0.5, h - 0.5, 16, 11, indent_in=0.6)
    _text(sl, x + 0.25, T.BODY_TOP + 0.25, w - 0.5, h - 0.5, p, name="Takeaways")


def s_table(prs, s, n):
    sl = _content_slide(prs, s)
    cols, rows = s["columns"], s["rows"]
    take = s.get("takeaway")
    h_avail = T.BODY_BOTTOM - T.BODY_TOP - (0.75 if take else 0)
    nrows = len(rows) + 1
    size = 14 if nrows <= 7 else (12 if nrows <= 10 else 11)
    row_h = min(0.55, h_avail / nrows)
    # column widths from content length, clamped
    lens = [max([len(str(c))] + [len(str(r[i])) for r in rows]) for i, c in enumerate(cols)]
    weights = [max(4, l) * (1.0 if _col_numeric(rows, i) else 1.6) for i, l in enumerate(lens)]
    widths = [T.BODY_W * wt / sum(weights) for wt in weights]
    gf = sl.shapes.add_table(nrows, len(cols), Inches(T.MARGIN_X), Inches(T.BODY_TOP),
                             Inches(T.BODY_W), Inches(row_h * nrows))
    gf.name = "Table"
    tbl = gf.table
    tblPr = tbl._tbl.tblPr
    for attr in ("bandRow", "firstRow"):
        tblPr.set(attr, "0")
    style = tblPr.find(qn("a:tableStyleId"))
    if style is not None:
        style.text = "{5940675A-B579-460E-94D1-54222C63F5DA}"  # "No Style, Table Grid"
    for i, w in enumerate(widths):
        tbl.columns[i].width = Inches(w)
    hl = s.get("highlight_row")
    for r in range(nrows):
        tbl.rows[r].height = Inches(row_h)
        for c in range(len(cols)):
            cell = tbl.cell(r, c)
            val = cols[c] if r == 0 else rows[r - 1][c]
            text = str(val)
            numeric = r > 0 and _is_num(text)
            cell.margin_left = cell.margin_right = Inches(0.08)
            cell.margin_top = cell.margin_bottom = Inches(0.03)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            if r == 0:
                cell.fill.fore_color.rgb = T.rgb(T.INK)
            elif hl is not None and r - 1 == hl:
                cell.fill.fore_color.rgb = T.rgb("E3EAFB")
            else:
                cell.fill.fore_color.rgb = T.rgb("FFFFFF" if r % 2 else T.PANEL)
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.alignment = PP_ALIGN.RIGHT if (numeric or (r == 0 and c > 0 and _col_numeric(rows, c))) else PP_ALIGN.LEFT
            run = p.add_run()
            run.text = text
            f = run.font
            f.name = T.FONT
            f.size = Pt(size)
            f.bold = r == 0 or (hl is not None and r - 1 == hl)
            f.color.rgb = T.rgb("FFFFFF" if r == 0 else T.INK)
    if take:
        y = T.BODY_TOP + row_h * nrows + 0.25
        _text(sl, T.MARGIN_X, y, T.BODY_W, 0.5, [{"text": take, "size": 15, "bold": True, "color": T.ACCENT_DARK}],
              name="Takeaway")
    return sl


def _is_num(t):
    t = t.replace(",", "").replace("%", "").replace("$", "").replace("€", "").replace("£", "").strip()
    t = t.lstrip("+-").rstrip("xkmbKMB")
    try:
        float(t)
        return True
    except ValueError:
        return False


def _col_numeric(rows, c):
    return rows and all(_is_num(str(r[c])) for r in rows)


def s_kpi(prs, s, n):
    sl = _content_slide(prs, s)
    k = s["kpis"]
    take = s.get("takeaway")
    gap = 0.35
    w = (T.BODY_W - gap * (len(k) - 1)) / len(k)
    top, h = T.BODY_TOP + 0.2, 2.9
    vsize = min(_fit(lambda z, x=x: [{"text": str(x["value"]), "size": z, "bold": True}], w - 0.6, 1.0,
                     *T.KPI_VALUE_PT)[0]["size"] for x in k)
    for i, x in enumerate(k):
        xx = T.MARGIN_X + i * (w + gap)
        _rect(sl, xx, top, w, h, T.PANEL).name = f"KpiCard{i + 1}"
        _rect(sl, xx, top, w, 0.06, T.ACCENT).name = f"KpiRule{i + 1}"
        _text(sl, xx + 0.3, top + 0.35, w - 0.6, 1.0,
              [{"text": str(x["value"]), "size": vsize, "bold": True, "color": T.ACCENT_DARK}],
              anchor=MSO_ANCHOR.BOTTOM, name=f"KpiValue{i + 1}")
        lp = _fit(lambda z, x=x: [{"text": x["label"], "size": z, "color": T.INK}], w - 0.6, 0.8, 15, 11)
        _text(sl, xx + 0.3, top + 1.5, w - 0.6, 0.8, lp, name=f"KpiLabel{i + 1}")
        if x.get("delta"):
            d = str(x["delta"])
            col = T.NEG if d.strip().startswith(("-", "−", "▼")) else T.POS
            _text(sl, xx + 0.3, top + 2.35, w - 0.6, 0.35, [{"text": d, "size": 15, "bold": True, "color": col}],
                  name=f"KpiDelta{i + 1}")
    if take:
        tp = _fit(lambda z: [{"text": take, "size": z, "color": T.INK}], T.BODY_W, 1.4, 18, 13)
        _text(sl, T.MARGIN_X, top + h + 0.45, T.BODY_W, 1.4, tp, name="Takeaway")
    return sl


def s_timeline(prs, s, n):
    sl = _content_slide(prs, s)
    items = s["items"]
    y = 3.55
    _rect(sl, T.MARGIN_X, y, T.BODY_W, 0.04, T.RULE).name = "TimelineAxis"
    seg = T.BODY_W / len(items)
    for i, it in enumerate(items):
        cx = T.MARGIN_X + seg * i + seg / 2
        dot = sl.shapes.add_shape(MSO_SHAPE.OVAL, Inches(cx - 0.11), Inches(y - 0.09), Inches(0.22), Inches(0.22))
        dot.fill.solid()
        dot.fill.fore_color.rgb = T.rgb(T.ACCENT)
        dot.line.fill.background()
        dot.name = f"TimelineDot{i + 1}"
        tw = seg - 0.25
        _text(sl, cx - tw / 2, y - 0.85, tw, 0.55, [{"text": str(it.get("date", "")), "size": 15, "bold": True,
                                                    "color": T.ACCENT}], anchor=MSO_ANCHOR.BOTTOM,
              align=PP_ALIGN.CENTER, name=f"TimelineDate{i + 1}")
        paras = lambda z, it=it: [{"text": it.get("label", ""), "size": z, "bold": True, "color": T.INK}] + (
            [{"text": it["detail"], "size": z - 2, "color": T.MUTED, "space_before": 4}] if it.get("detail") else [])
        p = _fit(paras, tw, 2.6, 15, 11)
        _text(sl, cx - tw / 2, y + 0.35, tw, 2.6, p, align=PP_ALIGN.CENTER, name=f"TimelineText{i + 1}")
    return sl


def s_quote(prs, s, n):
    sl = prs.slides.add_slide(prs.slide_layouts[6])
    _bg(sl, T.BG)
    if s.get("title"):
        _title(sl, s["title"])
    _text(sl, T.MARGIN_X + 0.2, 2.2, 1.0, 1.95,
          [{"text": "“", "size": 96, "bold": True, "color": T.ACCENT}], name="QuoteMark")
    qp = _fit(lambda z: [{"text": s["quote"], "size": z, "italic": True, "color": T.INK}], 10.2, 3.0, *T.QUOTE_PT)
    _text(sl, T.MARGIN_X + 1.4, 2.6, 10.2, 3.0, qp, anchor=MSO_ANCHOR.MIDDLE, name="Quote")
    if s.get("attribution"):
        _text(sl, T.MARGIN_X + 1.4, 5.8, 10.2, 0.5, [{"text": "— " + s["attribution"], "size": 15,
                                                      "color": T.MUTED}], name="Attribution")
    return sl


def s_image_text(prs, s, n):
    sl = _content_slide(prs, s)
    h = T.BODY_BOTTOM - T.BODY_TOP
    iw = 6.4
    img = Path(s["image"])
    if not img.exists():
        raise RenderError(f"image not found: {img}")
    from PIL import Image
    with Image.open(img) as im:
        ar = im.width / im.height
    w, hh = (iw, iw / ar) if iw / ar <= h else (h * ar, h)
    sl.shapes.add_picture(str(img), Inches(T.MARGIN_X), Inches(T.BODY_TOP + (h - hh) / 2), Inches(w), Inches(hh)).name = "Image"
    x = T.MARGIN_X + iw + 0.5
    bw = T.BODY_W - iw - 0.5
    p = _fit(lambda z: _bullet_paras(s["bullets"], z), bw, h, *T.BODY_PT, indent_in=0.6)
    _text(sl, x, T.BODY_TOP, bw, h, p, name="Body")
    return sl


from .archetypes import ARCHETYPES as _ARCH  # noqa: E402

RENDER = {**{k: v[1] for k, v in _ARCH.items()}, "title": s_title, "section": s_section, "bullets": s_bullets, "two_column": s_two_column,
          "chart": s_chart, "table": s_table, "kpi": s_kpi, "timeline": s_timeline, "quote": s_quote,
          "image_text": s_image_text}


_LIST_KEYS = ("items", "cards", "phases", "stats", "points", "kpis", "bullets")
_DICT_KEYS = ("panel", "stat", "now", "next", "later")


def _coerce(s: dict) -> dict:
    """Plain strings where the archetype expects objects (common with short, vague requests) become objects."""
    s = dict(s)
    for k in _LIST_KEYS:
        v = s.get(k)
        if isinstance(v, (str, dict)) and k != "bullets":
            v = [v] if isinstance(v, dict) else [x.strip() for x in v.split(";") if x.strip()]
        if isinstance(v, list) and k != "bullets":
            s[k] = [x if isinstance(x, dict) else {"text": str(x), "header": str(x), "name": str(x), "label": str(x)}
                    for x in v]
    for k in _DICT_KEYS:
        if isinstance(s.get(k), str):
            s[k] = {"heading": s[k], "label": s[k], "action": s[k], "value": s[k]}
    return s


def render(deck: dict, out_path: str) -> dict:
    """Validate, normalize, render. Returns {"path", "slides", "warnings"}. Raises RenderError with all errors."""
    errs, warns = validate(deck)
    if errs:
        raise RenderError("; ".join(errs))
    deck = normalize(deck)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(T.SLIDE_W), Inches(T.SLIDE_H)
    prs.core_properties.title = deck.get("title", "")
    problems = []
    for i, s in enumerate(deck["slides"], 1):
        try:
            sl = RENDER[s["type"]](prs, _coerce(s), i)
        except RenderError as e:
            problems.append(f"slide {i}: {e}")
            continue
        except Exception as e:                     # malformed fields must reach slide repair, never crash the deck
            problems.append(f"slide {i}: malformed {s['type']} slide ({type(e).__name__}: {e})")
            continue
        if s["type"] not in ("title", "section"):
            _footer(sl, s.get("source"), i)
        if s.get("notes"):
            sl.notes_slide.notes_text_frame.text = s["notes"]
    if problems:
        raise RenderError("; ".join(problems))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    prs.save(out_path)
    return {"path": out_path, "slides": len(deck["slides"]), "warnings": warns}
