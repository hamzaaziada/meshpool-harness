"""Append the slides of one deck to another, keeping the target's own slides exactly as they are.

Used to add forge-built slides to a deck the user already has: forge renders the new slides with that deck's look
(its master and theme, learned by brand.py), and this copies them in after the existing slides. Text, tables,
pictures, native charts (with their data) and links come across; each new slide uses the target's layout of the same
name, else its blank layout.
"""
from __future__ import annotations

import copy
import io
import re
from pathlib import Path

from pptx import Presentation
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.opc.package import Part

_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _layout(prs, name: str):
    for lay in prs.slide_layouts:
        if lay.name == name:
            return lay
    for lay in prs.slide_layouts:
        if lay.name.lower() in ("blank", "leer", "vide", "en blanco"):
            return lay
    return prs.slide_layouts[len(prs.slide_layouts) - 1]


def append(target: str, source: str, out: str) -> int:
    """Write `out`: every slide of `target`, then every slide of `source`. Returns how many slides were added."""
    dst, src = Presentation(target), Presentation(source)
    pkg = dst.part.package
    pin = _pinner(src)       # theme colours and fonts as the source drew them, whatever the target deck's theme
    # A deck of another shape (4:3): the new slides are scaled evenly to fit and centred, text included
    f = min(dst.slide_width / src.slide_width, dst.slide_height / src.slide_height)
    dx = (dst.slide_width - src.slide_width * f) / 2
    dy = (dst.slide_height - src.slide_height * f) / 2
    added = 0
    for s in src.slides:
        new = dst.slides.add_slide(_layout(dst, s.slide_layout.name))
        for ph in list(new.placeholders):                        # the layout's empty placeholders: not on the copy
            ph._element.getparent().remove(ph._element)
        ids = {}
        for rel in s.part.rels.values():
            if rel.reltype in (RT.SLIDE_LAYOUT, RT.NOTES_SLIDE):
                continue
            if rel.is_external:
                ids[rel.rId] = new.part.relate_to(rel.target_ref, rel.reltype, is_external=True)
            elif rel.reltype == RT.IMAGE:
                _, rid = new.part.get_or_add_image_part(io.BytesIO(rel.target_part.blob))
                ids[rel.rId] = rid
            elif rel.reltype == RT.CHART:
                ids[rel.rId] = new.part.relate_to(_copy_chart(rel.target_part, pkg, pin, f), RT.CHART)
            else:                                                # anything else: carried over as it is
                ids[rel.rId] = new.part.relate_to(rel.target_part, rel.reltype)
        tree = new.shapes._spTree
        for el in s.shapes._spTree.iterchildren():
            if el.tag.endswith("}nvGrpSpPr") or el.tag.endswith("}grpSpPr"):
                continue
            el = copy.deepcopy(el)
            _inherit(el, s)                                      # before it stops being a placeholder
            if abs(f - 1) > 0.002:
                _scale(el, f, dx, dy)
            _renumber(el, len(dst.slides))
            pin(el)
            _unplaceholder(el)
            for node in el.iter():
                for k, v in list(node.attrib.items()):
                    if k.startswith(_R) and v in ids:
                        node.set(k, ids[v])
            tree.append(el)
        bg = s._element.cSld.bg
        if bg is not None:                                       # the slide's own background, if it has one
            new._element.cSld.insert(0, copy.deepcopy(bg))
        if s.has_notes_slide and s.notes_slide.notes_text_frame is not None:
            new.notes_slide.notes_text_frame.text = s.notes_slide.notes_text_frame.text
        added += 1
    dst.save(out)
    return added


_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
# what may follow a:latin inside a run's properties (the schema's order): latin is inserted before the first of these
_AFTER_LATIN = {_A + t for t in ("ea", "cs", "sym", "hlinkClick", "hlinkMouseOver", "rtl", "extLst")}


def _pinner(prs):
    """A function that rewrites theme references in copied XML to the colours and fonts of `prs`'s own theme:
    schemeClr becomes srgbClr (its tint and shade children kept), +mj-lt / +mn-lt become the font names, and text
    with no font of its own gets the body font. Without it the slides took the target deck's theme (stock Office:
    Calibri and purple)."""
    from lxml import etree
    master = prs.slide_masters[0]
    theme = next(r.target_part for r in master.part.rels.values() if r.reltype == RT.THEME)
    t = etree.fromstring(theme.blob)
    colors = {}
    for slot in t.find(f".//{_A}clrScheme"):
        name = slot.tag.rsplit("}", 1)[-1]
        rgb = slot.find(f"{_A}srgbClr")
        sysc = slot.find(f"{_A}sysClr")
        colors[name] = rgb.get("val") if rgb is not None else (sysc.get("lastClr") if sysc is not None else None)
    cmap = master._element.find("{http://schemas.openxmlformats.org/presentationml/2006/main}clrMap")
    alias = dict(cmap.attrib) if cmap is not None else {"bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2"}
    major = t.find(f".//{_A}majorFont/{_A}latin").get("typeface")
    minor = t.find(f".//{_A}minorFont/{_A}latin").get("typeface")

    def pin(root):
        for sc in list(root.iter(f"{_A}schemeClr")):
            hexv = colors.get(alias.get(sc.get("val"), sc.get("val")))
            if hexv:
                sc.tag = f"{_A}srgbClr"
                sc.set("val", hexv)
        for lat in root.iter(f"{_A}latin"):
            lat.set("typeface", {"+mj-lt": major, "+mn-lt": minor}.get(lat.get("typeface"), lat.get("typeface")))
        for tag in ("rPr", "defRPr", "endParaRPr"):
            for rp in root.iter(f"{_A}{tag}"):
                if rp.find(f"{_A}latin") is None:
                    lat = etree.Element(f"{_A}latin", typeface=minor)
                    nxt = next((c for c in rp if c.tag in _AFTER_LATIN), None)
                    nxt.addprevious(lat) if nxt is not None else rp.append(lat)
    return pin


def _inherit(el, slide):
    """Write onto a placeholder shape what it inherits from its source layout and master (the title's bottom anchor,
    alignment, line spacing, bold), so it looks the same once it is a plain shape in another deck."""
    P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
    ph = next(el.iter(f"{P}ph"), None)
    body = next(el.iter(f"{_A}bodyPr"), None)
    if ph is None or body is None:
        return
    typ, idx = ph.get("type", "body"), ph.get("idx", "0")
    title = typ in ("title", "ctrTitle")
    sources = []                                             # nearest first: the layout's placeholder, then the master's
    for holder in (slide.slide_layout, slide.slide_layout.slide_master):
        for sp in holder.placeholders:
            pf = sp._element.find(f".//{P}ph")
            if pf is not None and (pf.get("idx", "0") == idx and not title
                                   or title and pf.get("type") in ("title", "ctrTitle")):
                sources.append(sp._element)
                break
    for src in sources:
        sb = next(src.iter(f"{_A}bodyPr"), None)
        for k, v in (sb.attrib.items() if sb is not None else ()):
            if k not in body.attrib:
                body.set(k, v)
    master = slide.slide_layout.slide_master._element
    style = master.find(f".//{P}{'titleStyle' if title else 'bodyStyle'}")
    lvl = []
    for src in sources:
        lst = next(src.iter(f"{_A}lstStyle"), None)
        if lst is not None and lst.find(f"{_A}lvl1pPr") is not None:
            lvl.append(lst.find(f"{_A}lvl1pPr"))
    if style is not None and style.find(f"{_A}lvl1pPr") is not None:
        lvl.append(style.find(f"{_A}lvl1pPr"))
    if not lvl:
        return
    for para in el.iter(f"{_A}p"):
        ppr = para.find(f"{_A}pPr")
        if ppr is None:
            ppr = copy.deepcopy(lvl[0])
            ppr.tag = f"{_A}pPr"
            for c in list(ppr):
                if c.tag.endswith("}defRPr"):
                    ppr.remove(c)
            para.insert(0, ppr)
        for lv in lvl:
            if "algn" in lv.attrib and "algn" not in ppr.attrib:
                ppr.set("algn", lv.get("algn"))
            ln = lv.find(f"{_A}lnSpc")
            if ln is not None and ppr.find(f"{_A}lnSpc") is None:
                ppr.insert(0, copy.deepcopy(ln))
        d = next((lv.find(f"{_A}defRPr") for lv in lvl if lv.find(f"{_A}defRPr") is not None), None)
        if d is not None:
            for r in para.iter(f"{_A}rPr"):
                for k in ("b", "i", "sz"):
                    if k in d.attrib and k not in r.attrib:
                        r.set(k, d.get(k))


def _unplaceholder(el):
    """A copied placeholder would take the target layout's formatting (a centred, middle-anchored title over the
    rule): when it has its own position it becomes a plain shape and keeps the look it was drawn with."""
    P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
    for nv in el.iter(f"{P}nvPr"):
        ph = nv.find(f"{P}ph")
        sp = nv.getparent().getparent() if nv.getparent() is not None else None
        has_xfrm = sp is not None and any(c.tag.endswith("}xfrm") for c in sp.iter())
        if ph is not None and has_xfrm:
            nv.remove(ph)


def _renumber(el, n: int):
    """forge writes the page number as text in its "Firm" footer box: give it the slide's place in this deck."""
    name = next((c.get("name") for c in el.iter() if c.tag.endswith("}cNvPr")), None)
    if name != "Firm":
        return
    ts = [t for t in el.iter() if t.tag.endswith("}t") and t.text]
    if ts and re.search(r"\d+\s*$", ts[-1].text):
        ts[-1].text = re.sub(r"\d+(\s*)$", lambda m: f"{n}{m.group(1)}", ts[-1].text)


def _scale(el, f: float, dx: float, dy: float):
    """Scale one top-level shape by f and move it by (dx, dy): positions, sizes, table grids and font sizes."""
    top_off = None
    xfrm = next((c for c in el.iter() if c.tag.endswith("}xfrm")), None)
    if xfrm is not None:
        top_off = next((c for c in xfrm if c.tag.endswith("}off")), None)
    for node in el.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in ("off", "chOff") and "x" in node.attrib:
            shift = node is top_off
            node.set("x", str(int(int(node.get("x")) * f + (dx if shift else 0))))
            node.set("y", str(int(int(node.get("y")) * f + (dy if shift else 0))))
        elif tag in ("ext", "chExt") and "cx" in node.attrib:
            node.set("cx", str(int(int(node.get("cx")) * f)))
            node.set("cy", str(int(int(node.get("cy")) * f)))
        elif tag == "gridCol" and "w" in node.attrib:
            node.set("w", str(int(int(node.get("w")) * f)))
        elif tag == "tr" and "h" in node.attrib:
            node.set("h", str(int(int(node.get("h")) * f)))
        elif tag in ("rPr", "defRPr", "endParaRPr") and "sz" in node.attrib:
            node.set("sz", str(max(600, int(int(node.get("sz")) * f))))
        elif tag == "spcPts" and "val" in node.attrib:          # line spacing and space before/after, in points
            node.set("val", str(int(int(node.get("val")) * f)))
        elif tag == "bodyPr":                                    # text box padding, in EMU
            for k in ("lIns", "tIns", "rIns", "bIns"):
                if k in node.attrib:
                    node.set(k, str(int(int(node.get(k)) * f)))


def _copy_chart(part, pkg, pin, f: float = 1.0):
    """A chart part and its embedded workbook, copied into pkg under fresh names (so nothing collides)."""
    from lxml import etree
    from pptx.parts.chart import ChartPart
    root = etree.fromstring(part.blob)
    pin(root)
    C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
    seen: dict[str, str] = {}                                  # negative axis ids (python-pptx) make Office repair
    for el in root.iter(f"{C}axId", f"{C}crossAx"):
        v = el.get("val")
        if v and v.startswith("-"):
            el.set("val", seen.setdefault(v, str(50000 + 1000 * len(seen) + abs(int(v)) % 1000)))
    if abs(f - 1) > 0.002:                                       # the chart's own text, on a smaller slide
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1] in ("rPr", "defRPr", "endParaRPr") and "sz" in node.attrib:
                node.set("sz", str(max(600, int(int(node.get("sz")) * f))))
    blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    new = ChartPart.load(pkg.next_partname("/ppt/charts/chart%d.xml"), part.content_type, pkg, blob)
    for rel in list(part.rels.values()):
        if rel.is_external:
            rid = new.relate_to(rel.target_ref, rel.reltype, is_external=True)
        else:
            sub = rel.target_part
            ext = Path(str(sub.partname)).suffix
            folder = str(sub.partname).rsplit("/", 1)[0]
            copied = Part.load(pkg.next_partname(f"{folder}/Microsoft_Excel_Worksheet%d{ext}" if ext == ".xlsx"
                                                 else f"{folder}/item%d{ext}"), sub.content_type, pkg, sub.blob)
            rid = new.relate_to(copied, rel.reltype)
        if rid != rel.rId:
            _fix_rid(new, rid, rel.rId)
    return new


def _fix_rid(part, got: str, want: str):
    """The copied chart XML names its parts by the source's ids: give the new relationship that id."""
    rels = part.rels._rels
    rel = rels.pop(got)
    rel._rId = want
    rels[want] = rel


def insert(target: str, source: str, out: str, at: int) -> int:
    """Write `out`: `target` with every slide of `source` placed before its slide number `at` (1-based; past the end
    appends). The new slides take the target's look as in append(); every forge page number in the result is
    renumbered to its place. Returns how many slides were added."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = str(Path(d) / "appended.pptx")
        added = append(target, source, tmp)
        prs = Presentation(tmp)
        lst = prs.slides._sldIdLst
        ids = list(lst)
        new = ids[len(ids) - added:]
        pos = max(0, min(int(at) - 1, len(ids) - added))
        for el in new:
            lst.remove(el)
        for k, el in enumerate(new):
            lst.insert(pos + k, el)
        renumber(prs)
        prs.save(out)
    return added


def consolidate(decks: list[str], out: str) -> int:
    """Write `out`: the first deck, then each other deck's slides in order, all in the first deck's look, page numbers
    renumbered. Returns the total slide count."""
    import shutil
    import tempfile
    if not decks:
        raise ValueError("no decks to consolidate")
    with tempfile.TemporaryDirectory() as d:
        cur = str(Path(d) / "c0.pptx")
        shutil.copyfile(decks[0], cur)
        for i, src in enumerate(decks[1:], 1):
            nxt = str(Path(d) / f"c{i}.pptx")
            append(cur, src, nxt)
            cur = nxt
        prs = Presentation(cur)
        renumber(prs)
        prs.save(out)
        return len(prs.slides)


def renumber(prs) -> None:
    """Every forge "Firm" footer gets its slide's place in this deck."""
    for i, sl in enumerate(prs.slides, 1):
        for el in sl.shapes._spTree.iterchildren():
            _renumber(el, i)
