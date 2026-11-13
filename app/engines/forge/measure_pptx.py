"""Deck anatomy: measure how a .pptx is built (step 1 inventory + step 2 anatomy of the reverse-engineering brief).

    python -m engines.forge.measure_pptx deck.pptx [--out anatomy.json]

Every top-level metric carries an evidence ID (EV-<file sha8>-<metric>) so findings can cite it. The validator
(validate_pptx.py) reads this same structure, so a measured deck and a validated deck are described the same way.
Format-aware only: everything comes from parsing the OOXML parts, never from file names.
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from collections import Counter
from hashlib import sha256 as _sha
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.oxml.ns import qn

from .common import EMU_PER_IN, evidence_id, font_file, load_rules, sha256, wrap_lines

NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
      "p14": "http://schemas.microsoft.com/office/powerpoint/2010/main",
      "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
      "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
      "dc": "http://purl.org/dc/elements/1.1/", "dcterms": "http://purl.org/dc/terms/",
      "ct": "http://schemas.openxmlformats.org/package/2006/content-types"}
CHART_URI = "http://schemas.openxmlformats.org/drawingml/2006/chart"
TABLE_URI = "http://schemas.openxmlformats.org/drawingml/2006/table"
SVG_EXT = "{96DAC541-7B7A-43D3-8B79-37D633B846F1}"
EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0001F900-\U0001F9FF]")
TITLE_TYPES = {"title", "ctrTitle"}
FOOTER_TYPES = {"ftr", "sldNum", "dt", "hdr"}
BODY_TYPES = {"body", "obj", None, "subTitle"}


def _in(emu) -> float:
    return round((emu or 0) / EMU_PER_IN, 4)


# ------------------------------------------------------------------ package inventory (step 1)
def inventory(path: str) -> dict:
    z = zipfile.ZipFile(path)
    members = []
    for info in z.infolist():
        members.append({"name": info.filename, "size": info.file_size, "sha256": _sha(z.read(info)).hexdigest()})
    ct = Counter()
    try:
        root = etree.fromstring(z.read("[Content_Types].xml"))
        for o in root.findall("ct:Override", NS):
            ct[o.get("ContentType").split("/")[-1]] += 1
    except KeyError:
        pass
    app, core = {}, {}
    if "docProps/app.xml" in z.namelist():
        a = etree.fromstring(z.read("docProps/app.xml"))
        for tag in ("Application", "AppVersion", "Company", "Template", "PresentationFormat", "Slides", "Notes",
                    "HiddenSlides"):
            el = a.find(f"ep:{tag}", NS)
            if el is not None:
                app[tag] = el.text
    if "docProps/core.xml" in z.namelist():
        c = etree.fromstring(z.read("docProps/core.xml"))
        for el in c:
            core[etree.QName(el).localname] = el.text
    return {"members": members, "member_order": [m["name"] for m in members], "content_types": dict(ct),
            "generator": app, "core": core}


# ------------------------------------------------------------------ inheritance helpers
def _theme(prs) -> dict:
    master = prs.slide_masters[0]
    theme_part = next(r.target_part for r in master.part.rels.values() if r.reltype.endswith("/theme"))
    t = etree.fromstring(theme_part.blob)
    colors = {}
    cs = t.find(".//a:clrScheme", NS)
    for el in (cs if cs is not None else []):
        v = el.find("a:srgbClr", NS)
        s = el.find("a:sysClr", NS)
        colors[etree.QName(el).localname] = (v.get("val") if v is not None else (s.get("lastClr") if s is not None else None))
    major = t.find(".//a:majorFont/a:latin", NS)
    minor = t.find(".//a:minorFont/a:latin", NS)
    return {"name": t.get("name"), "colors": colors, "major": major.get("typeface") if major is not None else None,
            "minor": minor.get("typeface") if minor is not None else None}


def _ph(sh):
    try:
        pf = sh.placeholder_format
        t = pf.type
        return (str(t).split(".")[-1].split(" ")[0].lower() if t is not None else None), pf.idx
    except Exception:
        return None, None


_PH_TYPE_XML = {"title": "title", "center_title": "ctrTitle", "subtitle": "subTitle", "body": "body",
                "object": "obj", "footer": "ftr", "slide_number": "sldNum", "date": "dt", "header": "hdr",
                "chart": "chart", "table": "tbl", "picture": "pic"}


def _ph_xml(sh):
    """Placeholder (type, idx) as written in XML: type defaults to obj when absent."""
    ph = sh._element.find(".//" + qn("p:nvPr") + "/" + qn("p:ph"))
    if ph is None:
        return None, None
    return ph.get("type", "obj"), int(ph.get("idx", "0"))


def _inherit_chain(sh, slide, level: int, pres_el) -> list:
    """lvlNpPr elements in priority order for a paragraph at `level` in shape `sh`."""
    tag = f"a:lvl{level + 1}pPr"
    chain = []

    def add(lst):
        if lst is not None:
            el = lst.find(tag, NS)
            if el is not None:
                chain.append(el)
    add(sh._element.find(".//a:lstStyle", NS))
    ptype, pidx = _ph_xml(sh)
    style = "otherStyle"
    if ptype is not None:
        layout, master = slide.slide_layout, slide.slide_layout.slide_master
        for holder in (layout, master):
            for lp in holder.placeholders:
                lt, li = _ph_xml(lp)
                same = (lt == ptype) if holder is master or ptype in TITLE_TYPES else (li == pidx)
                if holder is layout and not same and lt == ptype:
                    same = True
                if same:
                    add(lp._element.find(".//a:lstStyle", NS))
                    break
        style = "titleStyle" if ptype in TITLE_TYPES else ("otherStyle" if ptype in FOOTER_TYPES else "bodyStyle")
        txs = master._element.find(".//p:txStyles", NS)
        if txs is not None:
            add(txs.find(f"p:{style}", NS))
    else:
        if pres_el is not None:
            add(pres_el.find("p:defaultTextStyle", NS))
    return chain


def _first_attr(els, child: str, attr: str):
    for el in els:
        if el is None:
            continue
        c = el.find(child, NS) if child else el
        if c is not None and c.get(attr) is not None:
            return c.get(attr)
    return None


def _bullet(els) -> bool:
    for el in els:
        if el is None:
            continue
        if el.find("a:buNone", NS) is not None:
            return False
        if el.find("a:buChar", NS) is not None or el.find("a:buAutoNum", NS) is not None or el.find("a:buBlip", NS) is not None:
            return True
    return False


def _resolve_face(face, theme):
    if face in (None, ""):
        return None
    if face.startswith("+mj"):
        return theme["major"]
    if face.startswith("+mn"):
        return theme["minor"]
    return face


# ------------------------------------------------------------------ shape walk
def _walk(shapes, depth=0, xform=None):
    """Yield (shape, depth, abs_box) with group transforms applied."""
    for sh in shapes:
        x, y, w, h = sh.left, sh.top, sh.width, sh.height
        if xform and None not in (x, y, w, h):
            ox, oy, sx, sy, cx, cy = xform
            x, y, w, h = ox + (x - cx) * sx, oy + (y - cy) * sy, w * sx, h * sy
        box = None if None in (x, y, w, h) else (x, y, w, h)
        yield sh, depth, box
        if sh.shape_type is not None and "GROUP" in str(sh.shape_type):
            xf = sh._element.find(qn("p:grpSpPr") + "/" + qn("a:xfrm"))
            if xf is not None and box:
                off, ext = xf.find(qn("a:off")), xf.find(qn("a:ext"))
                choff, chext = xf.find(qn("a:chOff")), xf.find(qn("a:chExt"))
                if None not in (off, ext, choff, chext) and int(chext.get("cx")) and int(chext.get("cy")):
                    sx = box[2] / int(chext.get("cx"))
                    sy = box[3] / int(chext.get("cy"))
                    yield from _walk(sh.shapes, depth + 1, (box[0], box[1], sx, sy, int(choff.get("x")), int(choff.get("y"))))
                    continue
            yield from _walk(sh.shapes, depth + 1, xform)


def _chart_info(gf, slide_part) -> dict:
    rid = gf._element.find(".//" + qn("c:chart")).get(qn("r:id"))
    part = slide_part.related_part(rid)
    x = etree.fromstring(part.blob)
    plot = x.find(".//c:plotArea", NS)
    types = []
    series = []
    for pt in (plot if plot is not None else []):
        ln = etree.QName(pt).localname
        if not ln.endswith("Chart"):
            continue
        bd = pt.find("c:barDir", NS)
        gp = pt.find("c:grouping", NS)
        types.append(ln + (f":{bd.get('val')}" if bd is not None else "") + (f":{gp.get('val')}" if gp is not None else ""))
        for ser in pt.findall("c:ser", NS):
            name = "".join(ser.xpath(".//c:tx//c:v/text()", namespaces=NS)) or None
            for key in ("c:val", "c:yVal", "c:bubbleSize"):
                v = ser.find(key, NS)
                if v is None:
                    continue
                ref = v.find("c:numRef/c:f", NS)
                lit = v.find("c:numLit", NS) is not None
                vals = [float(p.text) for p in v.findall(".//c:pt/c:v", NS) if _isnum(p.text)]
                series.append({"name": name, "field": key[2:], "ref": ref.text if ref is not None else None,
                               "literal": lit, "values": vals})
    ext = x.find(".//c:externalData", NS)
    embedded = None
    if ext is not None:
        try:
            emb = part.related_part(ext.get(qn("r:id")))
            embedded = {"partname": str(emb.partname), "content_type": emb.content_type, "blob": emb.blob}
        except Exception:
            embedded = {"partname": None, "error": "externalData relationship does not resolve"}
    label_sizes = [int(s) / 100 for s in x.xpath(".//c:txPr//a:defRPr/@sz", namespaces=NS)]
    return {"partname": str(part.partname), "types": types, "series": series, "embedded": embedded,
            "label_sizes": label_sizes, "srgb": len(x.findall(".//a:srgbClr", NS)),
            "scheme": len(x.findall(".//a:schemeClr", NS)), "srgb_values": x.xpath(".//a:srgbClr/@val", namespaces=NS)}


def _isnum(t):
    try:
        float(t)
        return True
    except (TypeError, ValueError):
        return False


def _locks(el) -> list[str]:
    out = []
    for lk in el.iter():
        if etree.QName(lk).localname.endswith("Locks"):
            out += [k for k, v in lk.attrib.items() if v in ("1", "true")]
    return out


def _body_pr(sh):
    bp = sh._element.find(".//" + qn("a:bodyPr"))
    if bp is None:
        return {}, None
    auto = "none"
    scale = 1.0
    if bp.find(qn("a:normAutofit")) is not None:
        auto = "normAutofit"
        fs = bp.find(qn("a:normAutofit")).get("fontScale")
        scale = int(fs) / 100000 if fs else 1.0
    elif bp.find(qn("a:spAutoFit")) is not None:
        auto = "spAutoFit"
    ins = {k: int(bp.get(k)) if bp.get(k) is not None else d for k, d in
           (("lIns", 91440), ("rIns", 91440), ("tIns", 45720), ("bIns", 45720))}
    return {"autofit": auto, "font_scale": scale, "insets": ins, "wrap": bp.get("wrap", "square")}, bp


def measure(path: str, rules: dict | None = None) -> dict:
    rules = rules or load_rules()
    R = rules["pptx"]
    file_sha = sha256(path)
    prs = Presentation(path)
    W, H = _in(prs.slide_width), _in(prs.slide_height)
    theme = _theme(prs)
    pres_el = prs.part._element
    metric_map = tuple(sorted(R["fonts"]["metric_files"].items()))
    inv = inventory(path)

    sections = []
    for sec in pres_el.iter("{%s}section" % NS["p14"]):
        sections.append({"name": sec.get("name"), "slide_ids": [int(s.get("id")) for s in sec.iter("{%s}sldId" % NS["p14"])]})
    embedded_fonts = [e.find("p:font", NS).get("typeface") for e in pres_el.findall(".//p:embeddedFont", NS)
                      if e.find("p:font", NS) is not None]

    layouts = []
    for m in prs.slide_masters:
        for lay in m.slide_layouts:
            layouts.append(lay.name)

    deck_faces, size_hist = Counter(), {"title": Counter(), "body": Counter(), "footnote": Counter(), "chart_labels": Counter()}
    scheme_n = srgb_n = 0
    srgb_vals = Counter()
    shape_name_patterns = Counter()
    slides = []
    src_re = re.compile(R["provenance"]["source_regex"])
    tag_re, node_re = re.compile(R["archetypes"]["tag_regex"]), re.compile(R["archetypes"]["node_regex"])

    for n, slide in enumerate(prs.slides, 1):
        sx = etree.fromstring(slide.part.blob)
        s_scheme = len(sx.findall(".//a:schemeClr", NS))
        s_srgb_vals = sx.xpath(".//a:srgbClr/@val", namespaces=NS)
        cname = slide._element.find(qn("p:cSld")).get("name") or ""
        rec = {"n": n, "slide_id": slide.slide_id, "layout": slide.slide_layout.name, "name": cname,
               "archetype": (tag_re.search(cname).group(1) if tag_re.search(cname) else None),
               "node": (node_re.search(cname).group(1) if node_re.search(cname) else None),
               "shapes": [], "charts": [], "tables": 0, "table_words": 0, "pictures": 0, "icons": [],
               "gradients": len(sx.findall(".//a:gradFill", NS)), "max_group_depth": 0, "decorative_lines": 0,
               "emoji": 0, "scheme_colors": s_scheme, "srgb_colors": len(s_srgb_vals),
               "srgb_values": s_srgb_vals, "slide_number": False, "locks": []}
        scheme_n += s_scheme
        srgb_n += len(s_srgb_vals)
        srgb_vals.update(s_srgb_vals)
        tree_order = 0
        for sh, depth, box in _walk(slide.shapes):
            tree_order += 1
            rec["max_group_depth"] = max(rec["max_group_depth"], depth)
            shape_name_patterns[re.sub(r"[\d\s;]+$", "", sh.name or "")] += 1
            ptype, pidx = _ph_xml(sh)
            el = sh._element
            kind = etree.QName(el).localname
            gd = el.find(".//" + qn("a:graphicData"))
            uri = gd.get("uri") if gd is not None else None
            descr = (el.find(".//" + qn("p:cNvPr")).get("descr") or "") if el.find(".//" + qn("p:cNvPr")) is not None else ""
            x, y, w, h = (_in(v) for v in box) if box else (None, None, None, None)
            srec = {"id": sh.shape_id, "name": sh.name, "kind": kind, "depth": depth, "order": tree_order,
                    "ph_type": ptype, "ph_idx": pidx, "x": x, "y": y, "w": w, "h": h, "descr": descr,
                    "text": "", "paras": [], "role": None, "filled": False,
                    "own_xfrm": el.find(".//" + qn("a:xfrm")) is not None}
            lk = _locks(el)
            if lk:
                rec["locks"].append({"shape": sh.name, "locks": lk})
            try:
                srec["filled"] = sh.fill.type is not None and str(sh.fill.type).split(".")[-1].split(" ")[0] in ("SOLID", "GRADIENT", "PATTERNED", "PICTURE")
            except Exception:
                pass
            if ptype == "sldNum" or el.findall(".//a:fld[@type='slidenum']", NS):
                rec["slide_number"] = True
            if kind == "cxnSp" or (el.find(".//" + qn("a:prstGeom")) is not None and
                                   el.find(".//" + qn("a:prstGeom")).get("prst") in ("line", "straightConnector1")):
                connected = el.find(".//" + qn("a:stCxn")) is not None or el.find(".//" + qn("a:endCxn")) is not None
                if not connected:
                    rec["decorative_lines"] += 1
                srec["role"] = "line"
            if uri == CHART_URI:
                ci = _chart_info(sh, slide.part)
                ci.update({"shape": sh.name, "descr": descr})
                rec["charts"].append(ci)
                for s in ci["label_sizes"]:
                    size_hist["chart_labels"][s] += 1
                srgb_n += ci["srgb"]
                scheme_n += ci["scheme"]
                srgb_vals.update(ci["srgb_values"])
                srec["role"] = "chart"
            elif uri == TABLE_URI:
                rec["tables"] += 1
                words = sum(len(c.text.split()) for r in sh.table.rows for c in r.cells)
                rec["table_words"] += words
                srec["role"] = "table"
                srec["text"] = " | ".join(c.text for r in sh.table.rows for c in r.cells)
            elif kind == "pic":
                rec["pictures"] += 1
                svg = SVG_EXT in etree.tostring(el).decode()
                small = w is not None and w <= R["icons"]["max_icon_size_in"] and h <= R["icons"]["max_icon_size_in"]
                tagged = descr.startswith(R["icons"]["tag_prefix"])
                if tagged or (small and svg) or (small and w <= 0.75 and h <= 0.75):
                    iset = descr.split(":")[1] if tagged and descr.count(":") >= 2 else None
                    rec["icons"].append({"shape": sh.name, "set": iset, "svg": svg})
                    srec["role"] = "icon"
                else:
                    srec["role"] = "picture"
                try:
                    srec["image_ext"] = sh.image.ext
                except Exception:
                    srec["image_ext"] = None
            if sh.has_text_frame and sh.text_frame.text.strip():
                bp, _ = _body_pr(sh)
                srec.update(bp)
                paras = []
                for p in sh.text_frame.paragraphs:
                    txt = "".join(r.text for r in p.runs) if p.runs else p.text
                    if not txt.strip():
                        continue
                    lvl = p.level
                    chain = _inherit_chain(sh, slide, lvl, pres_el)
                    ppr = p._p.find(qn("a:pPr"))
                    run = next((r for r in p.runs if r.text.strip()), None)
                    rpr = run._r.find(qn("a:rPr")) if run is not None else None
                    sz = (rpr.get("sz") if rpr is not None else None) or _first_attr(chain, "a:defRPr", "sz")
                    size = int(sz) / 100 if sz else 18.0
                    face = None
                    if rpr is not None and rpr.find(qn("a:latin")) is not None:
                        face = rpr.find(qn("a:latin")).get("typeface")
                    face = _resolve_face(face or _first_attr([c.find("a:defRPr", NS) for c in chain], "a:latin", "typeface"),
                                         theme)
                    if face is None:
                        face = theme["major"] if ptype in TITLE_TYPES else theme["minor"]
                    bold = (rpr is not None and rpr.get("b") == "1") or _first_attr(chain, "a:defRPr", "b") == "1"
                    for r in p.runs:
                        rp = r._r.find(qn("a:rPr"))
                        if rp is not None and rp.find(qn("a:latin")) is not None and r.text.strip():
                            deck_faces[_resolve_face(rp.find(qn("a:latin")).get("typeface"), theme)] += 1
                    deck_faces[face] += 1
                    marl = int(_first_attr([ppr] + chain, None, "marL") or 0)
                    sb = _first_attr([ppr] + chain, "a:spcBef/a:spcPts", "val")
                    sa = _first_attr([ppr] + chain, "a:spcAft/a:spcPts", "val")
                    ls = _first_attr([ppr] + chain, "a:lnSpc/a:spcPct", "val")
                    paras.append({"text": txt, "size": size, "face": face, "bold": bool(bold), "level": lvl,
                                  "bullet": _bullet([ppr] + chain) if ptype is not None or ppr is not None else False,
                                  "marL_in": _in(marl), "space_before_pt": int(sb) / 100 if sb else 0.0,
                                  "space_after_pt": int(sa) / 100 if sa else 0.0,
                                  "line_spacing": int(ls) / 100000 if ls else 1.0})
                    rec["emoji"] += len(EMOJI.findall(txt))
                srec["paras"] = paras
                srec["text"] = "\n".join(p["text"] for p in paras)
                if srec["role"] is None:
                    if ptype in TITLE_TYPES:
                        srec["role"] = "title"
                    elif ptype in FOOTER_TYPES:
                        srec["role"] = "footer"
                    elif src_re.match(srec["text"]) or (y is not None and y >= H * R["provenance"]["source_band_top_frac"]
                                                         and max(p["size"] for p in paras) <= 11):
                        srec["role"] = "footnote"
                    elif ptype == "subTitle":
                        srec["role"] = "subtitle"
                    else:
                        srec["role"] = "body"
                role_key = {"title": "title", "body": "body", "subtitle": "body", "footnote": "footnote",
                            "footer": "footnote"}.get(srec["role"])
                if role_key:
                    for p in paras:
                        size_hist[role_key][p["size"] * srec.get("font_scale", 1.0)] += 1
                srec["overflow"] = _overflow(srec, metric_map, R)
            rec["shapes"].append(srec)

        texts = [s for s in rec["shapes"] if s["text"]]
        title = next((s for s in texts if s["role"] == "title"), None)
        rec["title_in_placeholder"] = title is not None
        if title is None:                      # no title placeholder: the topmost, largest text in the top band
            cands = [s for s in texts if s["role"] == "body" and s["y"] is not None and s["y"] < H * 0.25 and s["depth"] == 0]
            if cands:
                title = max(cands, key=lambda s: (max(p["size"] for p in s["paras"]), -s["y"]))
                title["role"] = "title_inferred"
        rec["title"] = title["text"].replace("\n", " ").strip() if title else ""
        rec["title_words"] = len(rec["title"].split())
        body = [s for s in texts if s["role"] in ("body",)]
        rec["body_words"] = sum(len(s["text"].split()) for s in body)
        bparas = [p for s in body for p in s["paras"]]
        rec["bullets"] = sum(1 for p in bparas if p["bullet"])
        rec["bullet_levels"] = (max(p["level"] for p in bparas if p["bullet"]) + 1) if rec["bullets"] else 0
        src = [s for s in texts if s["role"] == "footnote" and src_re.match(s["text"])]
        rec["source_line"] = {"present": bool(src), "y": src[0]["y"] if src else None,
                              "text": src[0]["text"][:160] if src else None}
        rec["notes_words"] = (len(slide.notes_slide.notes_text_frame.text.split())
                              if slide.has_notes_slide and slide.notes_slide.notes_text_frame else 0)
        rec["notes_text"] = (slide.notes_slide.notes_text_frame.text if slide.has_notes_slide and
                             slide.notes_slide.notes_text_frame else "")
        top = [s for s in rec["shapes"] if s["depth"] == 0 and s["x"] is not None and s["role"] not in ("footer",)]
        rec["left_edges"] = sorted({round(s["x"], 2) for s in top})
        rec["autofit"] = dict(Counter(s.get("autofit", "none") for s in texts))
        slides.append(rec)

    out = {
        "file": str(path), "sha256": file_sha, "slide_size_in": [W, H], "inventory": inv,
        "masters": len(prs.slide_masters), "layouts": layouts, "theme": theme, "sections": sections,
        "embedded_fonts": embedded_fonts, "typefaces": dict(deck_faces),
        "font_size_hist": {k: {str(s): c for s, c in sorted(v.items())} for k, v in size_hist.items()},
        "colors": {"scheme_refs": scheme_n, "srgb_refs": srgb_n,
                   "scheme_share": round(scheme_n / (scheme_n + srgb_n), 3) if scheme_n + srgb_n else None,
                   "srgb_values": dict(srgb_vals.most_common(20))},
        "shape_name_patterns": dict(shape_name_patterns.most_common(25)),
        "slides": slides,
    }
    out["summary"] = _summary(out)
    out["evidence"] = {k: evidence_id(file_sha, k) for k in out["summary"]}
    return out


def _overflow(s: dict, metric_map: tuple, R: dict) -> dict | None:
    """Estimate text height with real font metrics against the box. Returns None when the box has no geometry."""
    if s["w"] is None or not s["paras"]:
        return None
    ins = s.get("insets") or {"lIns": 91440, "rIns": 91440, "tIns": 45720, "bIns": 45720}
    tf = R["text_fit"]
    width = s["w"] - (ins["lIns"] + ins["rIns"]) / EMU_PER_IN
    height = s["h"] - (ins["tIns"] + ins["bIns"]) / EMU_PER_IN
    scale = s.get("font_scale", 1.0)
    need = 0.0
    lines = 0
    exact = True
    line_h = 0
    for p in s["paras"]:
        size = p["size"] * scale
        path, ok = font_file(p["face"] or "Calibri", p["bold"], False, metric_map)
        exact = exact and ok
        n = wrap_lines(p["text"], width - p["marL_in"], size, path, tf["wrap_safety"]) if s.get("wrap") != "none" else 1
        lines += n
        line_h = size * tf["line_height"] * p["line_spacing"] / 72
        need += n * line_h + (p["space_before_pt"] + p["space_after_pt"]) / 72
    over_in = need - height
    over_lines = max(0, round(over_in / line_h + 0.49)) if line_h and over_in > tf["overflow_tolerance_in"] else 0
    return {"need_in": round(need, 3), "avail_in": round(height, 3), "lines": lines,
            "overflow_lines": over_lines if s.get("autofit") != "spAutoFit" else 0,
            "grows_to_in": round(s["y"] + need + (ins["tIns"] + ins["bIns"]) / EMU_PER_IN, 3) if s.get("autofit") == "spAutoFit" else None,
            "metrics_exact": exact}


def _summary(m: dict) -> dict:
    sl = m["slides"]
    n = len(sl) or 1
    titles = [s for s in sl if s["title"]]
    return {
        "generator": m["inventory"]["generator"].get("Application"),
        "generator_version": m["inventory"]["generator"].get("AppVersion"),
        "slides": len(sl), "masters": m["masters"], "layouts_available": len(m["layouts"]),
        "layouts_used": dict(Counter(s["layout"] for s in sl)),
        "body_in_placeholders_share": _share([sh for s in sl for sh in s["shapes"] if sh["role"] == "body"],
                                             lambda sh: sh["ph_type"] is not None),
        "theme_fonts": [m["theme"]["major"], m["theme"]["minor"]],
        "typefaces": m["typefaces"], "embedded_fonts": m["embedded_fonts"],
        "scheme_color_share": m["colors"]["scheme_share"],
        "native_charts": sum(len(s["charts"]) for s in sl),
        "charts_with_embedded_workbook": sum(1 for s in sl for c in s["charts"] if c["embedded"] and c["embedded"].get("blob")),
        "chart_types": dict(Counter(t for s in sl for c in s["charts"] for t in c["types"])),
        "native_tables": sum(s["tables"] for s in sl), "pictures": sum(s["pictures"] for s in sl),
        "icons": sum(len(s["icons"]) for s in sl),
        "avg_body_words": round(sum(s["body_words"] for s in sl) / n, 1),
        "max_body_words": max((s["body_words"] for s in sl), default=0),
        "avg_bullets": round(sum(s["bullets"] for s in sl) / n, 1),
        "max_bullet_levels": max((s["bullet_levels"] for s in sl), default=0),
        "titles_in_placeholder_share": round(sum(1 for s in sl if s["title_in_placeholder"]) / n, 2),
        "avg_title_words": round(sum(s["title_words"] for s in titles) / max(1, len(titles)), 1),
        "source_line_share": round(sum(1 for s in sl if s["source_line"]["present"]) / n, 2),
        "autofit": dict(sum((Counter(s["autofit"]) for s in sl), Counter())),
        "overflow_shapes": sum(1 for s in sl for sh in s["shapes"] if (sh.get("overflow") or {}).get("overflow_lines")),
        "notes_share": round(sum(1 for s in sl if s["notes_words"]) / n, 2),
        "slide_numbers_share": round(sum(1 for s in sl if s["slide_number"]) / n, 2),
        "sections": len(m["sections"]), "max_group_depth": max((s["max_group_depth"] for s in sl), default=0),
        "avg_distinct_left_edges": round(sum(len(s["left_edges"]) for s in sl) / n, 1),
        "font_size_hist": m["font_size_hist"],
    }


def _share(items, pred):
    return round(sum(1 for i in items if pred(i)) / len(items), 2) if items else None


def public(m: dict) -> dict:
    """Drop binary blobs so the measurement serialises to JSON."""
    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if k != "blob"}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x
    return strip(m)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("pptx")
    ap.add_argument("--out")
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()
    res = public(measure(a.pptx))
    doc = {"file": res["file"], "sha256": res["sha256"], "summary": res["summary"], "evidence": res["evidence"]} if a.summary else res
    txt = json.dumps(doc, indent=1, default=str)
    if a.out:
        Path(a.out).write_text(txt)
    else:
        sys.stdout.write(txt + "\n")
