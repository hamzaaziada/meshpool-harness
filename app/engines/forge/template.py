"""The forge default template: a 16:9 master with real placeholders and a theme that carries every colour and font.

Built from python-pptx's default template (so it opens everywhere), then:
- slide size 13.333 x 7.5 in, every master/layout placeholder rescaled to it;
- theme fonts: serif headings (major) and sans body (minor), the pattern measured in the reference decks;
- theme colours: our own palette (not any firm's), one emphasis colour (accent1) and muted tones;
- title style: 24 pt, theme heading font, dk2, bottom-anchored so one- and two-line titles share a baseline.
A client .potx/.pptx can replace it: the renderer only needs a "Title Only" and a "Title Slide" layout.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.util import Emu, Inches

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS = {"a": A, "p": P}

FONTS = {"major": "Georgia", "minor": "Arial"}
# roles: dk1 text and title, dk2 navy (callouts, darkest heat), lt2 grey half-panel, accent1 emphasis blue (numbers),
# accent2 coral (the one highlighted row), accent3 cyan (the focus item in a chart), accent4 mid blue (heat),
# accent5 rule grey, accent6 muted text. Our own palette; roles follow the reference grammar.
COLORS = {"dk1": "0A1A2F", "lt1": "FFFFFF", "dk2": "0A2540", "lt2": "ECECEC", "accent1": "1E5BD8",
          "accent2": "F4A582", "accent3": "29B6E8", "accent4": "1A73B8", "accent5": "BFBFBF", "accent6": "6B6B6B",
          "hlink": "1E5BD8", "folHlink": "6B6B6B"}
W_IN, H_IN = 13.333, 7.5
CACHE = Path(__import__("tempfile").gettempdir()) / "forge_template_v2.pptx"


def _theme_part(prs):
    m = prs.slide_masters[0]
    return next(r.target_part for r in m.part.rels.values() if r.reltype.endswith("/theme"))


def _set_theme(prs):
    part = _theme_part(prs)
    x = etree.fromstring(part.blob)
    cs = x.find(".//a:clrScheme", NS)
    cs.set("name", "Forge")
    for el in cs:
        name = etree.QName(el).localname
        if name in COLORS:
            for c in list(el):
                el.remove(c)
            etree.SubElement(el, f"{{{A}}}srgbClr", val=COLORS[name])
    fs = x.find(".//a:fontScheme", NS)
    fs.set("name", "Forge")
    for kind in ("major", "minor"):
        lat = fs.find(f"a:{kind}Font/a:latin", NS)
        lat.set("typeface", FONTS[kind])
    part._blob = etree.tostring(x, xml_declaration=True, encoding="UTF-8", standalone=True)


def _scale_shapes(holder, sx: float):
    for sh in holder.shapes:
        if sh.left is not None:
            sh.left = Emu(int(sh.left * sx))
            sh.width = Emu(int(sh.width * sx))


def _style_master(prs):
    m = prs.slide_masters[0]
    txs = m._element.find(".//p:txStyles", NS)
    t1 = txs.find("p:titleStyle/a:lvl1pPr", NS)
    t1.set("algn", "l")
    ls = t1.find("a:lnSpc", NS)
    if ls is None:
        ls = etree.Element(f"{{{A}}}lnSpc")
        t1.insert(0, ls)
        etree.SubElement(ls, f"{{{A}}}spcPct", val="90000")
    d = t1.find("a:defRPr", NS)
    d.set("sz", "2500")
    d.set("b", "1")
    for c in list(d):
        if etree.QName(c).localname in ("solidFill",):
            d.remove(c)
    sf = etree.SubElement(d, f"{{{A}}}solidFill")
    etree.SubElement(sf, f"{{{A}}}schemeClr", val="tx1")
    d.insert(0, sf)
    body = txs.find("p:bodyStyle", NS)
    for lvl, sz in (("lvl1pPr", "1400"), ("lvl2pPr", "1200"), ("lvl3pPr", "1200"), ("lvl4pPr", "1200"), ("lvl5pPr", "1200")):
        el = body.find(f"a:{lvl}", NS)
        if el is not None:
            el.find("a:defRPr", NS).set("sz", sz)
    # title placeholder geometry on the master and every layout: same box everywhere, bottom-anchored
    for holder in [m] + list(m.slide_layouts):
        for ph in holder.placeholders:
            t = ph._element.find(".//p:ph", NS).get("type")
            if t == "title":
                ph.left, ph.top, ph.width, ph.height = Inches(0.6), Inches(0.3), Inches(12.133), Inches(0.95)
                bp = ph._element.find(".//a:bodyPr", NS)
                if bp is not None:
                    bp.set("anchor", "b")
            elif t in ("body", None) and holder is m:
                ph.left, ph.width = Inches(0.6), Inches(12.133)


@lru_cache(maxsize=1)
def default_template() -> str:
    """Path of the forge default template (built once per process into the temp folder)."""
    prs = Presentation()
    sx = Inches(W_IN) / prs.slide_width
    prs.slide_width, prs.slide_height = Inches(W_IN), Inches(H_IN)
    m = prs.slide_masters[0]
    _scale_shapes(m, sx)
    for lay in m.slide_layouts:
        _scale_shapes(lay, sx)
    _set_theme(prs)
    _style_master(prs)
    prs.save(CACHE)
    return str(CACHE)


def layout(prs, name: str):
    for lay in prs.slide_masters[0].slide_layouts:
        if lay.name.lower() == name.lower():
            return lay
    raise KeyError(f"template has no layout named {name!r}")
