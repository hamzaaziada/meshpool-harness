"""Consulting archetypes modelled on the reference decks (numbered insights + side panel, header-bar cards, phased
roadmaps, icon stat rows, executive summary rows, NOW/NEXT/LATER recommendation). All layout is deterministic;
icons are Lucide (ISC licence, engines/slides/icons). The model only supplies content."""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path

from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

from . import theme as T

ICONS = Path(__file__).resolve().parent / "icons"
CACHE = Path(__import__("tempfile").gettempdir()) / "mh_icon_cache"
_NAMES = None


def _names():
    global _NAMES
    if _NAMES is None:
        _NAMES = sorted(p.stem for p in ICONS.glob("*.svg"))
    return _NAMES


ALIASES = {"money": "banknote", "revenue": "banknote", "cost": "receipt", "growth": "trending-up", "decline": "trending-down",
           "customer": "users", "customers": "users", "people": "users", "team": "users", "risk": "shield-alert",
           "time": "clock", "calendar": "calendar", "market": "store", "retail": "shopping-cart", "data": "database",
           "analysis": "chart-column", "chart": "chart-column", "target": "target", "goal": "target", "idea": "lightbulb",
           "strategy": "compass", "partner": "handshake", "partners": "handshake", "law": "scale", "legal": "scale",
           "location": "map-pin", "map": "map", "country": "globe", "global": "globe", "process": "workflow",
           "build": "hammer", "factory": "factory", "product": "package", "delivery": "truck", "logistics": "truck",
           "service": "headset", "support": "headset", "energy": "zap", "heat": "thermometer-sun", "price": "tag",
           "check": "circle-check", "question": "circle-help", "warning": "triangle-alert", "security": "shield-check",
           "search": "search", "digital": "monitor-smartphone", "phone": "smartphone", "governance": "landmark",
           "finance": "landmark", "investment": "piggy-bank", "phase": "milestone", "launch": "rocket", "plan": "list-checks"}


def icon_name(key: str | None) -> str:
    k = (key or "").strip().lower().replace("_", "-").replace(" ", "-")
    names = _names()
    if k in names:
        return k
    if k in ALIASES:
        return ALIASES[k]
    for w in re.split(r"[-\s]+", k):
        if w in ALIASES:
            return ALIASES[w]
        hits = [n for n in names if n == w or n.startswith(w + "-")]
        if hits:
            return min(hits, key=len)
    return "circle-dot"


@lru_cache(maxsize=512)
def icon_png(key: str | None, color: str = T.ACCENT) -> str:
    import cairosvg
    name = icon_name(key)
    CACHE.mkdir(exist_ok=True)
    out = CACHE / f"{name}-{color}.png"
    if not out.exists():
        svg = (ICONS / f"{name}.svg").read_text().replace("currentColor", f"#{color}")
        cairosvg.svg2png(bytestring=svg.encode(), write_to=str(out), output_width=160, output_height=160)
    return str(out)


def _box(sl, x, y, w, h, fill, line=None, shape=MSO_SHAPE.RECTANGLE):
    s = sl.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    s.fill.solid()
    s.fill.fore_color.rgb = T.rgb(fill)
    if line:
        s.line.color.rgb = T.rgb(line)
        s.line.width = Pt(0.75)
    else:
        s.line.fill.background()
    s.shadow.inherit = False
    return s


def _badge(sl, x, y, n, d=0.36, fill=T.ACCENT, color="FFFFFF"):
    from .renderer import _text
    _box(sl, x, y, d, d, fill, shape=MSO_SHAPE.OVAL)
    _text(sl, x, y, d, d, [{"text": str(n), "size": 12, "bold": True, "color": color}], anchor=MSO_ANCHOR.MIDDLE,
          align=PP_ALIGN.CENTER, name=f"Badge{n}")


def _icon(sl, x, y, key, size=0.42, color=T.ACCENT):
    try:
        png = icon_png(key, color)
    except Exception:                       # Cairo library missing (common on Windows): skip the icon
        return
    sl.shapes.add_picture(png, Inches(x), Inches(y), Inches(size), Inches(size)).name = f"Icon-{icon_name(key)}"


def _fit_paras(paras_at, w, h, pref, minimum):
    from .renderer import _fit
    return _fit(paras_at, w, h, pref, minimum)


def _lead_text(label, text, size):
    """'Label - text' as two paragraphs: bold lead, regular body."""
    out = []
    if label:
        out.append({"text": label, "size": size, "bold": True, "color": T.INK})
    if text:
        out.append({"text": text, "size": size - 1.5, "color": T.MUTED, "space_before": 2})
    return out


# ------------------------------------------------------------------ archetypes
def s_exec_summary(prs, s, n):
    from .renderer import _content_slide, _text
    sl = _content_slide(prs, s)
    pts = s["points"][:5]
    stat = s.get("stat")
    w = T.BODY_W - (4.2 if stat else 0)
    top, bottom = T.BODY_TOP, T.BODY_BOTTOM
    rh = (bottom - top) / len(pts)
    for i, p in enumerate(pts):
        y = top + i * rh
        _box(sl, T.MARGIN_X, y + 0.08, 0.1, rh - 0.24, T.SERIES[i % len(T.SERIES)])
        paras = _fit_paras(lambda z, p=p: _lead_text(p.get("label"), p.get("text"), z), w - 0.4, rh - 0.2, 16, 11)
        _text(sl, T.MARGIN_X + 0.3, y + 0.06, w - 0.4, rh - 0.12, paras, anchor=MSO_ANCHOR.MIDDLE, name=f"Point{i + 1}")
        if i < len(pts) - 1:
            _box(sl, T.MARGIN_X, y + rh - 0.02, w, 0.012, T.RULE)
    if stat:
        x = T.MARGIN_X + w + 0.4
        _box(sl, x, top, 3.8, bottom - top, T.PANEL)
        _text(sl, x + 0.3, top + 1.2, 3.2, 1.3, [{"text": str(stat["value"]), "size": 48, "bold": True,
                                                  "color": T.ACCENT_DARK}], anchor=MSO_ANCHOR.BOTTOM, name="StatValue")
        lp = _fit_paras(lambda z: [{"text": stat.get("label", ""), "size": z, "color": T.INK}], 3.2, 1.6, 16, 11)
        _text(sl, x + 0.3, top + 2.7, 3.2, 1.6, lp, name="StatLabel")
    return sl


def s_numbered(prs, s, n):
    """Numbered insights (badge + bold lead + text, optional icon) with an optional dark side panel."""
    from .renderer import _content_slide, _text
    sl = _content_slide(prs, s)
    items = s["items"][:7]
    panel = s.get("panel")
    w = T.BODY_W - (4.3 if panel else 0)
    top, bottom = T.BODY_TOP, T.BODY_BOTTOM
    rh = (bottom - top) / len(items)
    has_icon = any(it.get("icon") for it in items)
    for i, it in enumerate(items, 1):
        y = top + (i - 1) * rh
        _badge(sl, T.MARGIN_X, y + (rh - 0.36) / 2, i)
        x = T.MARGIN_X + 0.55
        if has_icon:
            _icon(sl, x, y + (rh - 0.38) / 2, it.get("icon") or it.get("label"), 0.38)
            x += 0.55
        tw = T.MARGIN_X + w - x
        paras = _fit_paras(lambda z, it=it: _lead_text(it.get("label"), it.get("text"), z), tw, rh - 0.1, 15, 10.5)
        _text(sl, x, y + 0.05, tw, rh - 0.1, paras, anchor=MSO_ANCHOR.MIDDLE, name=f"Item{i}")
        if i < len(items):
            _box(sl, T.MARGIN_X + 0.55, y + rh - 0.01, w - 0.55, 0.01, T.RULE)
    if panel:
        x = T.MARGIN_X + w + 0.3
        pw = T.BODY_W - w - 0.3
        _box(sl, x, top, pw, bottom - top, T.ACCENT_DARK)
        hp = _fit_paras(lambda z: [{"text": panel.get("heading", ""), "size": z, "bold": True, "color": "FFFFFF"}],
                        pw - 0.5, 0.8, 18, 13)
        _text(sl, x + 0.25, top + 0.25, pw - 0.5, 0.8, hp, name="PanelHeading")
        pts = panel.get("items", [])[:5]
        ph = (bottom - top - 1.3) / max(1, len(pts))
        for j, p in enumerate(pts, 1):
            yy = top + 1.15 + (j - 1) * ph
            _badge(sl, x + 0.25, yy + 0.04, j, 0.32, fill=T.SERIES[3], color=T.INK)
            pp = _fit_paras(lambda z, p=p: [{"text": p, "size": z, "color": "FFFFFF"}], pw - 0.95, ph - 0.1, 13, 10)
            _text(sl, x + 0.7, yy, pw - 0.95, ph - 0.1, pp, name=f"PanelItem{j}")
    return sl


def s_cards(prs, s, n):
    """2-4 equal cards: colored header bar with icon + label, bullets in the body."""
    from .renderer import _bullet_paras, _content_slide, _text
    sl = _content_slide(prs, s)
    cards = s["cards"][:4]
    gap = 0.2
    cw = (T.BODY_W - gap * (len(cards) - 1)) / len(cards)
    top, bottom = T.BODY_TOP, T.BODY_BOTTOM - (1.1 if s.get("takeaway") else 0)
    hh = 0.62
    from .textfit import block_height_in as _bh
    size = None
    for z in (14, 13, 12, 11):          # one shared body size so cards look identical; same width rule as QA
        if all(_bh([(p["text"], p["size"], "bold" if p.get("bold") else "regular", p.get("space_before", 0))
                    for p in _bullet_paras(c.get("bullets", [])[:5], z)], cw - 1.0) <= bottom - top - hh - 0.4
               for c in cards):
            size = z
            break
    if size is None:
        from .renderer import RenderError
        raise RenderError("card text does not fit; use at most 4 bullets of max 12 words per card")
    from .textfit import block_height_in
    need = max(block_height_in([(p["text"], p["size"], "regular", p.get("space_before", 0))
                                for p in _bullet_paras(c.get("bullets", [])[:5], size)], cw - 1.0) for c in cards)
    bottom = min(bottom, top + hh + need + 0.6)          # card height follows content (no half-empty cards)
    bottom = max(bottom, top + hh + 1.6)
    for i, c in enumerate(cards):
        x = T.MARGIN_X + i * (cw + gap)
        col = [T.ACCENT, T.ACCENT_DARK, T.INK, "17616A"][i % 4]
        _box(sl, x, top, cw, bottom - top, "FFFFFF", line=T.RULE)
        _box(sl, x, top, cw, hh, col)
        _icon(sl, x + 0.15, top + 0.11, c.get("icon") or c.get("header"), 0.4, color="FFFFFF")
        hp = _fit_paras(lambda z, c=c: [{"text": c.get("header", ""), "size": z, "bold": True, "color": "FFFFFF"}],
                        cw - 0.75, hh - 0.1, 15, 11)
        _text(sl, x + 0.65, top + 0.05, cw - 0.75, hh - 0.1, hp, anchor=MSO_ANCHOR.MIDDLE, name=f"CardHeader{i + 1}")
        bp = _bullet_paras(c.get("bullets", [])[:5], size)
        _text(sl, x + 0.2, top + hh + 0.2, cw - 0.4, bottom - top - hh - 0.35, bp, name=f"CardBody{i + 1}")
    if s.get("takeaway"):
        _box(sl, T.MARGIN_X, bottom + 0.25, T.BODY_W, 0.75, T.PANEL)
        _box(sl, T.MARGIN_X, bottom + 0.25, 0.08, 0.75, T.ACCENT)
        tp = _fit_paras(lambda z: [{"text": s["takeaway"], "size": z, "bold": True, "color": T.ACCENT_DARK}],
                        T.BODY_W - 0.5, 0.65, 16, 12)
        _text(sl, T.MARGIN_X + 0.3, bottom + 0.3, T.BODY_W - 0.5, 0.65, tp, anchor=MSO_ANCHOR.MIDDLE, name="Takeaway")
    return sl


def s_roadmap(prs, s, n):
    """Phased roadmap: chevrons with phase names, a column of activities under each, a duration badge at the bottom."""
    from .renderer import _bullet_paras, _content_slide, _text
    sl = _content_slide(prs, s)
    phases = s["phases"][:5]
    gap = 0.08
    pw = (T.BODY_W - gap * (len(phases) - 1)) / len(phases)
    top = T.BODY_TOP
    for i, p in enumerate(phases):
        x = T.MARGIN_X + i * (pw + gap)
        shp = MSO_SHAPE.PENTAGON if i == 0 else MSO_SHAPE.CHEVRON
        ch = _box(sl, x, top, pw + 0.18, 0.72, [T.ACCENT_DARK, T.ACCENT, "3D6FE0", "5A86E6", "7B9FEA"][i % 5], shape=shp)
        ch.name = f"Chevron{i + 1}"
        hp = _fit_paras(lambda z, p=p: [{"text": f"Phase {i + 1}. {p.get('name', '')}", "size": z, "bold": True,
                                          "color": "FFFFFF"}], pw - 0.5, 0.62, 14, 10)
        lx = 0.3 if i == 0 else 0.75                       # clear the chevron notch
        _text(sl, x + lx, top + 0.05, pw - lx - 0.5, 0.62, hp, anchor=MSO_ANCHOR.MIDDLE, name=f"PhaseName{i + 1}")
        from .textfit import block_height_in
        need = max(block_height_in([(q["text"], q["size"], "regular", q.get("space_before", 0))
                                    for q in _bullet_paras(ph.get("items", [])[:6], 13)], pw - 0.9) for ph in phases)
        bh = min(T.BODY_BOTTOM - top - 0.95 - 0.55, max(1.6, need + 0.4))
        _box(sl, x, top + 0.9, pw, bh + 0.1, T.PANEL)
        bp = _fit_paras(lambda z, p=p: _bullet_paras(p.get("items", [])[:6], z), pw - 0.3, bh - 0.1, 13, 10)
        _text(sl, x + 0.15, top + 1.0, pw - 0.3, bh - 0.1, bp, name=f"PhaseItems{i + 1}")
        if p.get("duration"):
            yb = top + 0.9 + bh + 0.3
            _box(sl, x + pw / 2 - 0.75, yb, 1.5, 0.38, T.ACCENT_DARK, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
            _text(sl, x + pw / 2 - 0.75, yb, 1.5, 0.38,
                  [{"text": str(p["duration"]), "size": 11, "bold": True, "color": "FFFFFF"}],
                  anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.CENTER, name=f"Duration{i + 1}")
    return sl


def s_stat_row(prs, s, n):
    """3-5 icon tiles: icon, big value, short label (like a data-scope or KPI strip)."""
    from .renderer import _content_slide, _text
    sl = _content_slide(prs, s)
    st = s["stats"][:5]
    gap = 0.35
    w = (T.BODY_W - gap * (len(st) - 1)) / len(st)
    top = T.BODY_TOP + 0.4
    vsize = 34
    for i, x_ in enumerate(st):
        x = T.MARGIN_X + i * (w + gap)
        _box(sl, x, top, w, 3.1, T.PANEL)
        _icon(sl, x + w / 2 - 0.35, top + 0.3, x_.get("icon") or x_.get("label"), 0.7)
        vp = _fit_paras(lambda z, x_=x_: [{"text": str(x_["value"]), "size": z, "bold": True, "color": T.ACCENT_DARK}],
                        w - 0.3, 0.8, vsize, 16)
        _text(sl, x + 0.15, top + 1.15, w - 0.3, 0.8, vp, anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.CENTER,
              name=f"StatValue{i + 1}")
        lp = _fit_paras(lambda z, x_=x_: [{"text": x_.get("label", ""), "size": z, "color": T.MUTED}], w - 0.3, 0.9, 14, 10)
        _text(sl, x + 0.15, top + 2.0, w - 0.3, 0.9, lp, align=PP_ALIGN.CENTER, name=f"StatLabel{i + 1}")
    if s.get("takeaway"):
        tp = _fit_paras(lambda z: [{"text": s["takeaway"], "size": z, "color": T.INK}], T.BODY_W, 1.2, 17, 12)
        _text(sl, T.MARGIN_X, top + 3.45, T.BODY_W, 1.2, tp, name="Takeaway")
    return sl


def s_recommendation(prs, s, n):
    """NOW / NEXT / LATER: three labelled columns with a bold action and a short rationale each."""
    cards = []
    for k, ic in (("now", "rocket"), ("next", "milestone"), ("later", "flag")):
        part = s.get(k) or {}
        why = part.get("why", [])
        why = [w for w in (why if isinstance(why, list) else [why]) if w][:3]
        action = part.get("action", "")
        cards.append({"header": k.capitalize(), "icon": part.get("icon", ic),
                      "bullets": ([{"text": action, "bold": True}] if action else []) + why})
    return s_cards(prs, {"title": s["title"], "cards": cards, "takeaway": s.get("risk") and f"Key risk: {s['risk']}"}, n)


ARCHETYPES = {
    "exec_summary": ({"req": ["title", "points"], "opt": ["stat"]}, s_exec_summary),
    "numbered": ({"req": ["title", "items"], "opt": ["panel"]}, s_numbered),
    "cards": ({"req": ["title", "cards"], "opt": ["takeaway"]}, s_cards),
    "roadmap": ({"req": ["title", "phases"], "opt": []}, s_roadmap),
    "stat_row": ({"req": ["title", "stats"], "opt": ["takeaway"]}, s_stat_row),
    "recommendation": ({"req": ["title", "now", "next", "later"], "opt": ["risk"]}, s_recommendation),
}
