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
                ids[rel.rId] = new.part.relate_to(_copy_chart(rel.target_part, pkg), RT.CHART)
            else:                                                # anything else: carried over as it is
                ids[rel.rId] = new.part.relate_to(rel.target_part, rel.reltype)
        tree = new.shapes._spTree
        for el in s.shapes._spTree.iterchildren():
            if el.tag.endswith("}nvGrpSpPr") or el.tag.endswith("}grpSpPr"):
                continue
            el = copy.deepcopy(el)
            if abs(f - 1) > 0.002:
                _scale(el, f, dx, dy)
            _renumber(el, len(dst.slides))
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


def _copy_chart(part, pkg):
    """A chart part and its embedded workbook, copied into pkg under fresh names (so nothing collides)."""
    from pptx.parts.chart import ChartPart
    new = ChartPart.load(pkg.next_partname("/ppt/charts/chart%d.xml"), part.content_type, pkg, part.blob)
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
