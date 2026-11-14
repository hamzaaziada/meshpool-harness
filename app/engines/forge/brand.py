"""Company brand: learn a company's look from a few of its existing decks, keep it as a profile, build decks in it.

    from engines.forge import brand as B
    prof = B.learn(["Q3 board deck.pptx", "Strategy update.pptx"])     # colours, fonts, logo, name, master
    B.save(prof)                                                        # stored under <meshpool home>/brand
    B.active()                                                          # the profile new decks use (or None)
    B.render_kwargs(prof)                                               # theme / template / brand for render()

What is learned, and how:
- colours: the deck's theme colours and the colours its slides actually use (fills weigh more than text). They are
  mapped onto the forge colour roles (text, brand dark, emphasis, highlight, focus, secondary, rules, muted, panel)
  with contrast checks, so the renderer's grammar keeps working in any palette: numbers stay readable on white,
  white text stays readable on the brand dark, dark text stays readable on the highlight row;
- fonts: the theme heading and body fonts (and the faces the slides use most);
- logo: a picture on the slide master or layouts, else the same small picture on most slides;
- company name and footer: repeated footer text ("(c) 2025 Acme Capital | Strictly confidential"), then the file's
  Company property;
- slide master: a copy of the deck with its slides removed, usable as the template when it is 16:9 widescreen and its
  decoration stays out of the body area. Its theme colours are rewritten to the role-mapped colours and the master's
  own artwork is frozen to literal colours first, so the master looks as it did.

The learned profile is a starting point: every field can be edited (Settings > Company) before it is used.
"""
from __future__ import annotations

import colorsys
import hashlib
import io
import json
import posixpath
import re
import shutil
import uuid
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from lxml import etree

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"a": A, "p": P, "r": R}
EMU = 914400
WIDE = (12192000, 6858000)                       # 13.333 x 7.5 in, the size the forge grid is drawn for
OFFICE_ACCENTS = {"4472C4", "ED7D31", "A5A5A5", "FFC000", "5B9BD5", "70AD47", "4F81BD", "C0504D", "9BBB59",
                  "8064A2", "4BACC6", "F79646", "156082", "E97132", "196B24", "0F9ED5", "A02B93", "4EA72E"}
ROLES = [("dk1", "Text", "Body text and titles"),
         ("dk2", "Brand dark", "Callout boxes and bands with white text; the darkest heat-map cell"),
         ("accent1", "Emphasis", "Numbers in text, accent bars, the main chart series"),
         ("accent2", "Highlight", "The one highlighted table row (dark text on it)"),
         ("accent3", "Focus", "The focus item in a chart"),
         ("accent4", "Secondary", "Second chart series, heat-map middle"),
         ("accent5", "Rules", "Hairlines and gridlines"),
         ("accent6", "Muted text", "Labels, notes and secondary text"),
         ("lt2", "Panel", "Light panel and half-page backgrounds")]
SAFE_FONTS = {"arial", "georgia", "calibri", "cambria", "helvetica", "times new roman", "verdana", "tahoma",
              "segoe ui", "garamond", "century gothic", "franklin gothic book", "gill sans mt", "aptos",
              "aptos display", "trebuchet ms", "palatino linotype", "book antiqua", "corbel", "candara", "constantia"}
MASTER_VERSION = 3                               # bump when master_template() changes what it writes
LOGO_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif"}


# ------------------------------------------------------------------ colour arithmetic
def _rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _hex(rgb) -> str:
    return "".join(f"{max(0, min(255, round(c * 255))):02X}" for c in rgb)


def luminance(h: str) -> float:
    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _hls(h: str):
    return colorsys.rgb_to_hls(*_rgb(h))


def chroma(h: str) -> float:
    r, g, b = _rgb(h)
    return max(r, g, b) - min(r, g, b)


def _hue_gap(a: str, b: str) -> float:
    d = abs(_hls(a)[0] - _hls(b)[0]) * 360
    return min(d, 360 - d)


def mix(a: str, b: str, t: float) -> str:
    """a moved t of the way to b."""
    return _hex(tuple(x + (y - x) * t for x, y in zip(_rgb(a), _rgb(b))))


def _until(h: str, target: str, ok, steps: int = 20) -> str:
    """Move h toward target until ok(colour) holds (or it reaches the target)."""
    for i in range(steps + 1):
        c = mix(h, target, i / steps)
        if ok(c):
            return c
    return target


def _mods(h: str, el) -> str:
    """Apply lumMod / lumOff / tint / shade children of a colour element (the common ones Office writes)."""
    if el is None:
        return h
    hh, ll, ss = _hls(h)
    rgb = _rgb(h)
    for m in el:
        n, v = etree.QName(m).localname, int(m.get("val", "100000")) / 100000
        if n == "lumMod":
            ll *= v
        elif n == "lumOff":
            ll += v
        elif n == "tint":
            rgb = tuple(c + (1 - c) * (1 - v) for c in colorsys.hls_to_rgb(hh, ll, ss))
            hh, ll, ss = colorsys.rgb_to_hls(*rgb)
        elif n == "shade":
            rgb = tuple(c * v for c in colorsys.hls_to_rgb(hh, ll, ss))
            hh, ll, ss = colorsys.rgb_to_hls(*rgb)
    return _hex(colorsys.hls_to_rgb(hh, max(0, min(1, ll)), ss))


# ------------------------------------------------------------------ reading a deck
def _theme_of(zf: zipfile.ZipFile) -> tuple[dict, dict, str]:
    """(colours by slot, fonts {major, minor}, theme part name) of the first master's theme."""
    names = zf.namelist()
    mrels = "ppt/slideMasters/_rels/slideMaster1.xml.rels"
    part = "ppt/theme/theme1.xml"
    if mrels in names:
        x = etree.fromstring(zf.read(mrels))
        for rel in x:
            if rel.get("Type", "").endswith("/theme"):
                part = "ppt/" + rel.get("Target").replace("../", "")
    t = etree.fromstring(zf.read(part))
    colors = {}
    for el in t.find(".//a:clrScheme", NS) if t.find(".//a:clrScheme", NS) is not None else []:
        v, s = el.find("a:srgbClr", NS), el.find("a:sysClr", NS)
        colors[etree.QName(el).localname] = (v.get("val") if v is not None else s.get("lastClr") if s is not None
                                             else None)
    fonts = {}
    for k in ("major", "minor"):
        lat = t.find(f".//a:{k}Font/a:latin", NS)
        fonts[k] = lat.get("typeface") if lat is not None else None
    return {k: (v or "").upper() for k, v in colors.items() if v}, fonts, part


def _clrmap(zf) -> dict:
    try:
        x = etree.fromstring(zf.read("ppt/slideMasters/slideMaster1.xml"))
        cm = x.find("p:clrMap", NS)
        return dict(cm.attrib) if cm is not None else {}
    except KeyError:
        return {}


def _resolve(el, theme: dict, cmap: dict) -> str | None:
    """Hex of a colour element (srgbClr / schemeClr / sysClr), modifiers applied."""
    n = etree.QName(el).localname
    if n == "srgbClr":
        return _mods(el.get("val", "000000").upper(), el)
    if n == "sysClr":
        return _mods((el.get("lastClr") or "000000").upper(), el)
    if n == "schemeClr":
        v = el.get("val")
        v = cmap.get(v, {"tx1": "dk1", "bg1": "lt1", "tx2": "dk2", "bg2": "lt2"}.get(v, v))
        if v in theme:
            return _mods(theme[v], el)
    return None


def _usage(zf, theme: dict, cmap: dict, slide_parts: list[str]) -> Counter:
    """Colours the slides use, weighted: shape and table fills 3, lines 1, text 1, chart series fills 2."""
    use = Counter()
    for part in slide_parts:
        x = etree.fromstring(zf.read(part))
        for sf in x.iter(f"{{{A}}}solidFill"):
            par = etree.QName(sf.getparent()).localname
            w = 3 if par in ("spPr", "tcPr", "bgPr") else 1 if par in ("ln", "rPr", "defRPr", "endParaRPr") else 2
            for c in sf:
                h = _resolve(c, theme, cmap)
                if h:
                    use[h] += w
    return use


def _rels(zf, part: str) -> dict:
    folder, name = posixpath.split(part)                  # zip member names always use '/', on Windows too
    rp = posixpath.join(folder, "_rels", name + ".rels")
    out = {}
    if rp in zf.namelist():
        for rel in etree.fromstring(zf.read(rp)):
            tgt = rel.get("Target", "")
            if rel.get("TargetMode") == "External":
                continue
            out[rel.get("Id")] = _norm(tgt.lstrip("/") if tgt.startswith("/") else f"{folder}/{tgt}")
    return out


def _norm(path: str) -> str:
    parts = []
    for seg in path.split("/"):
        if seg == "..":
            parts and parts.pop()
        elif seg and seg != ".":
            parts.append(seg)
    return "/".join(parts)


def _pictures(zf, part: str):
    """(image part, x, y, w, h in inches) of the pictures in a slide / layout / master part."""
    rels = _rels(zf, part)
    x = etree.fromstring(zf.read(part))
    for pic in x.iter(f"{{{P}}}pic"):
        blip = pic.find(".//a:blip", NS)
        off, ext = pic.find(".//a:xfrm/a:off", NS), pic.find(".//a:xfrm/a:ext", NS)
        if blip is None or off is None or ext is None:
            continue
        rid = blip.get(f"{{{R}}}embed")
        if rid in rels:
            yield rels[rid], int(off.get("x")) / EMU, int(off.get("y")) / EMU, int(ext.get("cx")) / EMU, \
                int(ext.get("cy")) / EMU


def _texts_near_bottom(zf, part: str, h_in: float):
    x = etree.fromstring(zf.read(part))
    for sp in x.iter(f"{{{P}}}sp"):
        ph = sp.find(".//p:nvPr/p:ph", NS)
        off = sp.find(".//a:xfrm/a:off", NS)
        footerish = ph is not None and ph.get("type") in ("ftr",)
        low = off is not None and int(off.get("y")) / EMU > h_in * 0.86
        if not (footerish or low):
            continue
        t = " ".join("".join(r.text or "" for r in p.iter(f"{{{A}}}t")) for p in sp.iter(f"{{{A}}}p")).strip()
        if t:
            yield re.sub(r"\s+", " ", t)


def _slide_parts(zf) -> list[str]:
    return sorted([n for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)],
                  key=lambda n: int(re.findall(r"\d+", n)[-1]))


def _layout_parts(zf) -> list[str]:
    return [n for n in zf.namelist() if re.fullmatch(r"ppt/slide(Masters/slideMaster|Layouts/slideLayout)\d+\.xml", n)]


def _ct(zf, part: str) -> str:
    x = etree.fromstring(zf.read("[Content_Types].xml"))
    ext = part.rsplit(".", 1)[-1].lower()
    for o in x:
        if o.get("PartName", "").lstrip("/") == part:
            return o.get("ContentType")
    for d in x:
        if d.get("Extension", "").lower() == ext:
            return d.get("ContentType")
    return ""


def _fonts_used(zf, slide_parts) -> Counter:
    c = Counter()
    for part in slide_parts:
        x = etree.fromstring(zf.read(part))
        for lat in x.iter(f"{{{A}}}latin"):
            tf = lat.get("typeface") or ""
            if tf and not tf.startswith("+"):
                c[tf] += 1
    return c


def read_deck(path: str) -> dict:
    """Everything learn() needs from one .pptx / .potx: theme, colour usage, fonts, pictures, footer text, size."""
    with zipfile.ZipFile(path) as zf:
        pres = etree.fromstring(zf.read("ppt/presentation.xml"))
        sz = pres.find("p:sldSz", NS)
        size = (int(sz.get("cx")), int(sz.get("cy"))) if sz is not None else WIDE
        theme, fonts, _ = _theme_of(zf)
        cmap = _clrmap(zf)
        slides = _slide_parts(zf)
        layouts = _layout_parts(zf)
        h_in = size[1] / EMU
        pics_master = [(p, x, y, w, h, part) for part in layouts for p, x, y, w, h in _pictures(zf, part)]
        pics_slides = defaultdict(list)
        for part in slides:
            for p, x, y, w, h in _pictures(zf, part):
                pics_slides[p].append((x, y, w, h))
        foot = Counter()
        for part in slides + layouts:
            for t in set(_texts_near_bottom(zf, part, h_in)):
                foot[t] += 1
        company = None
        if "docProps/app.xml" in zf.namelist():
            m = re.search(rb"<Company>([^<]+)</Company>", zf.read("docProps/app.xml"))
            company = m.group(1).decode("utf-8", "replace").strip() if m else None
        images = {}
        for p, *_ in pics_master + [(k,) for k in pics_slides]:
            if p in zf.namelist() and p not in images:
                images[p] = (_ct(zf, p), hashlib.sha1(zf.read(p)).hexdigest())
        return {"path": str(path), "name": Path(path).name, "size": size, "theme": theme, "fonts": fonts,
                "cmap": cmap, "usage": _usage(zf, theme, cmap, slides + layouts), "slides": len(slides),
                "fonts_used": _fonts_used(zf, slides), "pics_master": pics_master, "pics_slides": dict(pics_slides),
                "footer": foot, "company": company, "images": images, "h_in": h_in, "w_in": size[0] / EMU}


# ------------------------------------------------------------------ colour roles
def map_roles(theme: dict, usage: Counter) -> dict:
    """The forge colour roles from a deck's theme and the colours it uses (see the module docstring)."""
    white = "FFFFFF"
    stock = sum(1 for k in ("accent1", "accent2", "accent3") if theme.get(k) in OFFICE_ACCENTS) >= 2
    chrom = [(h, n) for h, n in usage.most_common() if chroma(h) > 0.18 and 0.08 < _hls(h)[1] < 0.92]
    used_theme = [theme.get(f"accent{i}") for i in range(1, 7) if theme.get(f"accent{i}")]
    # candidates by weight; theme accents count as used, ahead of unused ones, unless the theme is stock Office
    order = [h for h, _ in chrom]
    if not stock or not order:
        for h in used_theme:
            if h not in order and chroma(h) > 0.18:
                order.append(h)
    if not order:
        order = ["1E5BD8"]
    # emphasis: the most used brand colour, darkened until bold numbers read on white (3:1)
    a1 = _until(order[0], "000000", lambda c: contrast(c, white) >= 3.0)
    # brand dark: the darkest used colour with white text at 7:1, else the emphasis darkened to it
    darks = [h for h in order + [theme.get("dk2", "")] if h and contrast(h, white) >= 7 and chroma(h) > 0.08]
    dk2 = darks[0] if darks else _until(a1, "000000", lambda c: contrast(c, white) >= 7)
    if dk2 == a1:                                       # one brand colour: the dark role is a deeper shade of it
        dk2 = mix(a1, "000000", 0.35)
    # highlight: a second hue, light enough for dark text (dk1 at 4.5:1)
    dk1 = theme.get("dk1") if theme.get("dk1") and contrast(theme["dk1"], white) >= 12 else "111111"
    others = [h for h in order[1:] if _hue_gap(h, a1) > 35]
    base2 = others[0] if others else _hex(colorsys.hls_to_rgb((_hls(a1)[0] + 0.5) % 1, 0.6, 0.75))
    a2 = _until(base2, white, lambda c: contrast(c, dk1) >= 4.5 and _hls(c)[1] >= 0.62)
    # focus: a third used hue, else a lighter emphasis; secondary: a tint of the emphasis
    third = [h for h in others[1:] if _hue_gap(h, a2) > 25] or others[1:]
    a3 = third[0] if third else mix(a1, white, 0.35)
    a3 = _until(a3, "000000", lambda c: contrast(c, white) >= 2.2)
    a4 = mix(a1, white, 0.45)
    greys = [h for h, _ in usage.most_common() if chroma(h) <= 0.08]
    rules = next((g for g in greys if 1.25 <= contrast(g, white) <= 2.2), "BFBFBF")
    muted = next((g for g in greys if 4.5 <= contrast(g, white) <= 7.5), "6B6B6B")
    panel = next((h for h, _ in usage.most_common() if 1.03 <= contrast(h, white) <= 1.25), None)
    panel = panel or (theme.get("lt2") if not stock and theme.get("lt2") and 1.03 <= contrast(theme["lt2"], white) <= 1.3
                      else mix(a1, white, 0.92))
    return {"dk1": dk1, "lt1": "FFFFFF", "dk2": dk2, "lt2": panel, "accent1": a1, "accent2": a2, "accent3": a3,
            "accent4": a4, "accent5": rules, "accent6": muted, "hlink": a1, "folHlink": muted}


def check_roles(colors: dict) -> list[str]:
    """Readability problems in a role palette (empty when every role does its job)."""
    w, out = "FFFFFF", []
    if contrast(colors["dk1"], w) < 7:
        out.append("Text colour is too light to read on white (needs 7:1).")
    if contrast(colors["accent1"], w) < 3:
        out.append("Emphasis colour is too light for numbers on white (needs 3:1).")
    if contrast(colors["dk2"], w) < 4.5:
        out.append("Brand dark is too light for white text on it (needs 4.5:1).")
    if contrast(colors["accent2"], colors["dk1"]) < 4.5:
        out.append("Highlight is too dark for text on it (needs 4.5:1 against the text colour).")
    if contrast(colors["accent6"], w) < 4.5:
        out.append("Muted text is too light to read on white (needs 4.5:1).")
    return out


# ------------------------------------------------------------------ logo, name, master
def _logo(decks: list[dict]):
    """(deck, image part) of the most likely logo: on a master or layout first, else repeated on most slides."""
    best = None
    for d in decks:
        area = d["w_in"] * d["h_in"]
        for p, x, y, w, h, part in d["pics_master"]:
            if w * h > area * 0.12 or p not in d["images"]:          # a full-bleed picture is a background
                continue
            score = 2 + (part.endswith("slideMaster1.xml")) + (y < d["h_in"] * 0.2 or y > d["h_in"] * 0.8)
            if not best or score > best[0]:
                best = (score, d, p)
        for p, spots in d["pics_slides"].items():
            if p not in d["images"] or d["slides"] < 2 or len(spots) < max(2, d["slides"] * 0.5):
                continue
            x, y, w, h = spots[0]
            if w * h > area * 0.08:
                continue
            score = 1 + len(spots) / d["slides"]
            if not best or score > best[0]:
                best = (score, d, p)
    return (best[1], best[2]) if best else (None, None)


_COPY = re.compile(r"(?i)^\s*(?:©|\(c\)|copyright)\s*(?:\d{4}\s*[-–]?\s*\d{0,4})?\s*")
_NOISE = re.compile(r"(?i)\b(strictly\s+)?(private and\s+)?confidential\b|\bproprietary\b|\bdraft\b|\ball rights reserved\b"
                    r"|\bpage\s*\d+\b|^\d+$|\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b")


def _name_and_footer(decks: list[dict]) -> tuple[str | None, str | None]:
    foot = Counter()
    for d in decks:
        for t, n in d["footer"].items():
            if n >= max(2, (d["slides"] or 1) * 0.3):
                foot[t] += n
    footer = None
    name = None
    for t, _ in foot.most_common():
        if re.fullmatch(r"[\d\s/.:-]+", t):
            continue
        footer = footer or t
        for seg in re.split(r"\s*[|•·–—]\s*|\s{2,}", t):
            s = _COPY.sub("", seg).strip(" .,")
            s = _NOISE.sub("", s).strip(" .,|-")
            if 2 <= len(s) <= 60 and re.search(r"[A-Za-z]", s):
                name = s
                break
        if name:
            break
    if not name:
        name = next((d["company"] for d in decks if d.get("company")), None)
    return name, footer


def _decoration(prs) -> tuple[bool, str, float | None]:
    """Whether the master's artwork (on the master and its title-only layout) leaves room for the forge page:
    (ok, why not, left edge of artwork in the footer's right corner or None). The title band, the body and the
    source line must be clear; artwork in the bottom-right corner (a logo, a page number) is kept and the firm
    label moves left of it."""
    from .render import BODY_B, LEFT, RIGHT, SRC_Y, colx
    lay = find_layout(prs, "Title Only")
    zones = {"the title band": (LEFT, 0.2, RIGHT, 1.42), "the body area": (LEFT, 1.42, RIGHT, BODY_B),
             "the source line": (LEFT, SRC_Y, colx(10) - 0.1, SRC_Y + 0.3)}
    corner = (colx(10), SRC_Y - 0.1, RIGHT, 7.5)
    right = None
    for holder in (prs.slide_masters[0], lay):
        for sh in holder.shapes:
            if sh.is_placeholder or sh.width is None or sh.left is None:
                continue
            x0, y0 = sh.left / EMU, sh.top / EMU
            x1, y1 = x0 + sh.width / EMU, y0 + sh.height / EMU
            if (x1 - x0) * (y1 - y0) > 13.333 * 7.5 * 0.85:          # a full-slide background is fine
                continue
            for zname, (a0, b0, a1, b1) in zones.items():
                if min(x1, a1) - max(x0, a0) > 0.1 and min(y1, b1) - max(y0, b0) > 0.1:
                    return False, f"its master has artwork ({sh.name}) in {zname}", None
            a0, b0, a1, b1 = corner
            if min(x1, a1) - max(x0, a0) > 0 and min(y1, b1) - max(y0, b0) > 0:
                right = x0 if right is None else min(right, x0)
    return True, "", right


def find_layout(prs, name: str):
    """A layout by name, else by what it holds: 'Title Only' = a title and nothing but date / footer / number;
    'Title Slide' = a centred title (ctrTitle). Raises KeyError when neither is there."""
    lays = list(prs.slide_masters[0].slide_layouts)
    for lay in lays:
        if lay.name.lower() == name.lower():
            return lay

    def kinds(lay):
        return {(ph._element.find(f".//{{{P}}}ph").get("type") or "body") for ph in lay.placeholders}
    minor = {"dt", "ftr", "sldNum"}
    if name.lower() == "title only":
        hit = [lay for lay in lays if "title" in kinds(lay) and kinds(lay) - minor == {"title"}]
    elif name.lower() == "title slide":
        hit = [lay for lay in lays if "ctrTitle" in kinds(lay)] or [lay for lay in lays if "title" in kinds(lay)]
    else:
        hit = []
    if not hit:
        raise KeyError(f"template has no layout like {name!r}")
    return hit[0]


def _freeze_colors(prs, theme: dict, cmap: dict) -> None:
    """Replace scheme colours in the master and layouts (not placeholder text styles) by literal colours, so a new
    theme recolours the forge content but leaves the company's own artwork as it was."""
    from pptx.oxml.ns import qn
    for holder in [prs.slide_masters[0]] + list(prs.slide_masters[0].slide_layouts):
        root = holder._element
        targets = list(root.iter(qn("a:schemeClr")))
        for el in targets:
            if el.get("val") == "phClr":
                continue
            anc = el
            in_txstyles = False
            while anc is not None:
                if etree.QName(anc).localname in ("txStyles", "txBody"):
                    in_txstyles = True
                    break
                anc = anc.getparent()
            if in_txstyles:
                continue
            v = cmap.get(el.get("val"), {"tx1": "dk1", "bg1": "lt1", "tx2": "dk2", "bg2": "lt2"}.get(el.get("val"),
                                                                                                    el.get("val")))
            if v not in theme:
                continue
            new = etree.Element(qn("a:srgbClr"), val=theme[v])
            for ch in list(el):
                new.append(ch)
            el.getparent().replace(el, new)


def make_master(deck_path: str, out_path: str) -> tuple[bool, str, float | None]:
    """A template from a company deck: slides removed, size, layouts and artwork checked.
    (ok, why not, left edge of the master's artwork in the footer's right corner)."""
    from .common import open_pptx
    prs = open_pptx(deck_path)                            # a .potx too
    if abs(prs.slide_width - WIDE[0]) > WIDE[0] * 0.003 or abs(prs.slide_height - WIDE[1]) > WIDE[1] * 0.003:
        w, h = prs.slide_width / EMU, prs.slide_height / EMU
        return False, (f"its slides are {w:.2f} x {h:.2f} in; the forge layouts need 16:9 widescreen "
                       "(13.33 x 7.5 in)"), None
    try:
        find_layout(prs, "Title Only")
        find_layout(prs, "Title Slide")
    except KeyError as e:
        return False, str(e).strip("'\""), None
    ok, why, right = _decoration(prs)
    if not ok:
        return False, why, None
    for sid in list(prs.slides._sldIdLst):
        prs.part.drop_rel(sid.rId)
        prs.slides._sldIdLst.remove(sid)
    prs.save(out_path)
    return True, "", right


def _align_placeholders(prs) -> None:
    """Title and body placeholders of the master and its layouts on the forge margins (left 0.6 in, right 12.733 in),
    their heights and vertical positions kept: the forge grid and the page checks read the master's margins."""
    from pptx.util import Inches
    from .render import LEFT, RIGHT
    for holder in [prs.slide_masters[0]] + list(prs.slide_masters[0].slide_layouts):
        for ph in holder.placeholders:
            t = ph._element.find(f".//{{{P}}}ph").get("type") or "body"
            if t in ("title", "ctrTitle", "body", "subTitle", "obj") and ph.left is not None:
                top, h = ph.top, ph.height
                ph.left, ph.width = Inches(LEFT), Inches(RIGHT - LEFT)
                ph.top, ph.height = top, h


def _unique_ids(prs) -> None:
    """Shape ids unique within each master and layout part (copied artwork often repeats them)."""
    from pptx.oxml.ns import qn
    for holder in [prs.slide_masters[0]] + list(prs.slide_masters[0].slide_layouts):
        seen, nxt = set(), 1 + max([int(e.get("id", "0")) for e in holder._element.iter(qn("p:cNvPr"))] + [0])
        for e in holder._element.iter(qn("p:cNvPr")):
            if e.get("id") in seen:
                e.set("id", str(nxt))
                nxt += 1
            seen.add(e.get("id"))


def master_template(prof: dict) -> str | None:
    """The company master recoloured to the profile's roles (cached by content), or None when not used."""
    if prof.get("template_mode") != "master" or not prof.get("template"):
        return None
    src = Path(prof["template"])
    if not src.exists():
        return None
    from pptx import Presentation
    from .template import _set_theme
    key = hashlib.sha1(src.read_bytes() + json.dumps([prof["colors"], prof["fonts"]], sort_keys=True).encode())
    out = Path(__import__("tempfile").gettempdir()) / f"forge_brand{MASTER_VERSION}_{key.hexdigest()[:12]}.pptx"
    if out.exists():
        return str(out)
    with zipfile.ZipFile(src) as zf:
        theme, _, _ = _theme_of(zf)
        cmap = _clrmap(zf)
    prs = Presentation(str(src))
    _freeze_colors(prs, theme, cmap)
    _set_theme(prs, {"colors": prof["colors"], "fonts": prof["fonts"]})
    _align_placeholders(prs)
    _unique_ids(prs)
    prs.save(out)
    return str(out)


# ------------------------------------------------------------------ learning
def _installed(face: str) -> bool:
    from .common import font_file
    return bool(face) and font_file(face)[1]


def learn(paths: list[str], name: str | None = None, folder: str | None = None) -> dict:
    """A brand profile from one or more decks (.pptx / .potx). The logo and the master copy are written into
    `folder` (default: a new folder under the brand store). Nothing is saved as the active profile."""
    paths = [str(p) for p in paths if str(p).lower().endswith((".pptx", ".potx", ".pptm"))]
    if not paths:
        raise ValueError("Add at least one PowerPoint file (.pptx or .potx).")
    decks, notes = [], []
    for p in paths:
        try:
            decks.append(read_deck(p))
        except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError) as e:
            notes.append(f"{Path(p).name} could not be read ({type(e).__name__}); it was skipped.")
    if not decks:
        raise ValueError("None of the files could be read as a PowerPoint deck.")
    pid = uuid.uuid4().hex[:10]
    out = Path(folder) if folder else store_dir() / pid
    out.mkdir(parents=True, exist_ok=True)
    usage, theme_votes = Counter(), Counter()
    for d in decks:
        usage.update(d["usage"])
        theme_votes[json.dumps(d["theme"], sort_keys=True)] += max(1, d["slides"])
    theme = json.loads(theme_votes.most_common(1)[0][0])
    colors = map_roles(theme, usage)
    # fonts: the theme's, unless the slides overrule it with one face most of the time
    fonts = dict(next((d["fonts"] for d in decks if d["fonts"].get("major")), {"major": "Georgia", "minor": "Arial"}))
    used = Counter()
    for d in decks:
        used.update(d["fonts_used"])
    if used:
        top, n = used.most_common(1)[0]
        if n >= sum(used.values()) * 0.6:
            fonts["minor"] = fonts["minor"] if fonts["minor"] == top else top
    fonts = {k: v or {"major": "Georgia", "minor": "Arial"}[k] for k, v in fonts.items()}
    for k, v in fonts.items():
        if not _installed(v) and v.lower() not in SAFE_FONTS:
            notes.append(f"The {'heading' if k == 'major' else 'body'} font {v} is not installed here, so text is "
                         "fitted with similar metrics; it shows in {v} on computers that have it.".replace("{v}", v))
    # logo
    logo = None
    ld, part = _logo(decks)
    if part:
        ctype = ld["images"][part][0]
        if ctype in LOGO_TYPES:
            with zipfile.ZipFile(ld["path"]) as zf:
                data = zf.read(part)
            logo = str(out / ("logo" + LOGO_TYPES[ctype]))
            Path(logo).write_bytes(data)
        else:
            notes.append(f"A logo was found in {ld['name']} but it is a {ctype.split('/')[-1]} file; upload it as PNG.")
    else:
        notes.append("No logo was found on the slide master or repeated on the slides; upload one.")
    nm, footer = _name_and_footer(decks)
    # master: the deck with the most slides that can serve as the template
    template, why, tdeck, corner = None, "", None, None
    for dd in sorted(decks, key=lambda x: -x["slides"]):
        tp = out / "master.pptx"
        ok, why, corner = make_master(dd["path"], str(tp))
        if ok:
            template, tdeck = str(tp), dd
            break
    if not template:
        notes.append(f"The slide master cannot be reused ({why}); decks use the forge layout with your colours, "
                     "fonts and logo.")
    logo_on_master = bool(logo and tdeck is ld and any(p == part for p, *_ in ld["pics_master"]))
    sizes = sorted({f"{dd['w_in']:.2f} x {dd['h_in']:.2f} in" for dd in decks})
    return {"id": out.name if folder else pid, "name": name or nm or "", "footer": footer or "", "colors": colors,
            "fonts": fonts, "logo": logo, "logo_on_master": logo_on_master, "template": template,
            "template_mode": "master" if template else "forge", "master_note": "" if template else why,
            "master_corner": corner,
            "sources": [{"file": dd["name"], "slides": dd["slides"]} for dd in decks],
            "found": {"theme": theme, "top_colors": [h for h, _ in usage.most_common(12)],
                      "fonts_used": [f for f, _ in used.most_common(6)], "slide_sizes": sizes},
            "notes": notes, "problems": check_roles(colors)}


# ------------------------------------------------------------------ the store
def store_dir(create: bool = True) -> Path:
    from meshpool_harness import config as C
    d = C.home() / "brand"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _index() -> dict:
    p = store_dir(create=False) / "profiles.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {"active": None, "profiles": {}}


def _write_index(ix: dict) -> None:
    p = store_dir() / "profiles.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(ix, indent=1))
    tmp.replace(p)


def clean(prof: dict) -> dict:
    """A profile with its editable fields checked: colours as 6-digit hex for every role, fonts as names."""
    from .template import COLORS
    p = dict(prof)
    cols = dict(COLORS)
    for k, v in (prof.get("colors") or {}).items():
        v = str(v).lstrip("#").upper()
        if k in cols and re.fullmatch(r"[0-9A-F]{6}", v):
            cols[k] = v
    cols["hlink"], cols["folHlink"] = cols["accent1"], cols["accent6"]
    p["colors"] = cols
    f = prof.get("fonts") or {}
    p["fonts"] = {"major": str(f.get("major") or "Georgia")[:60], "minor": str(f.get("minor") or "Arial")[:60]}
    p["name"] = str(prof.get("name") or "")[:80]
    p["footer"] = str(prof.get("footer") or "")[:120]
    if p.get("template_mode") not in ("master", "forge") or (p.get("template_mode") == "master" and not p.get("template")):
        p["template_mode"] = "forge"
    p["problems"] = check_roles(cols)
    return p


def profiles() -> dict:
    ix = _index()
    return {"active": ix.get("active"), "profiles": list(ix.get("profiles", {}).values())}


def get(pid: str) -> dict | None:
    return _index().get("profiles", {}).get(pid)


def save(prof: dict, activate: bool | None = None) -> dict:
    ix = _index()
    prof = clean(prof)
    if not prof.get("id"):
        prof["id"] = uuid.uuid4().hex[:10]
    ix.setdefault("profiles", {})[prof["id"]] = prof
    if activate or (activate is None and not ix.get("active")):
        ix["active"] = prof["id"]
    elif activate is False and ix.get("active") == prof["id"]:
        ix["active"] = None
    _write_index(ix)
    return prof


def delete(pid: str) -> None:
    ix = _index()
    ix.get("profiles", {}).pop(pid, None)
    if ix.get("active") == pid:
        ix["active"] = None
    _write_index(ix)
    d = store_dir(create=False) / pid
    if d.is_dir() and d.parent == store_dir(create=False):
        shutil.rmtree(d, ignore_errors=True)


def set_logo(pid: str, name: str, data: bytes) -> dict:
    prof = get(pid)
    if not prof:
        raise KeyError(pid)
    ext = Path(name).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".gif"):
        raise ValueError("Upload the logo as a PNG or JPEG.")
    from PIL import Image
    Image.open(io.BytesIO(data)).verify()                      # a real image, not a renamed file
    d = store_dir() / pid
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("logo.*"):
        old.unlink()
    p = d / ("logo" + (".jpg" if ext == ".jpeg" else ext))
    p.write_bytes(data)
    prof["logo"], prof["logo_on_master"] = str(p), False
    return save(prof)


def active() -> dict | None:
    ix = _index()
    return ix.get("profiles", {}).get(ix.get("active")) if ix.get("active") else None


def for_request(task: str) -> dict | None:
    """The profile a request asks for by company name ('in the Acme style', 'for Acme'), else the active one.
    'no brand' / 'without our template' opts out."""
    if re.search(r"(?i)\b(no|without)\s+(the\s+|our\s+|a\s+)?(brand(ing)?|company (look|template)|template)\b", task):
        return None
    t = task.lower()
    for p in _index().get("profiles", {}).values():
        nm = (p.get("name") or "").strip().lower()
        if len(nm) >= 3 and re.search(r"\b" + re.escape(nm) + r"\b", t):
            return p
    return active()


def render_kwargs(prof: dict | None) -> dict:
    """theme / template / brand arguments for forge render() and build()."""
    if not prof:
        return {}
    prof = clean(prof)
    theme = {"palette": "navy", **prof["colors"], "fonts": prof["fonts"]}
    out = {"theme": theme, "brand": {"firm": prof["name"] or None, "footer": prof["footer"] or None,
                                     "logo": prof["logo"] if prof.get("logo") and Path(prof["logo"]).exists() and
                                     not (prof["template_mode"] == "master" and prof.get("logo_on_master")) else None}}
    tpl = master_template(prof)
    if tpl:
        out["template"] = tpl
        if prof.get("master_corner"):
            out["brand"]["footer_right"] = prof["master_corner"]
    return out


SAMPLE = [
    {"id": "s1", "node": "0", "archetype": "cover", "title": "{name} sample deck",
     "subtitle": "Colours, fonts, logo and layout learned from your decks", "date": "Built by Meshpool"},
    {"id": "s2", "node": "1", "archetype": "stacked_composition",
     "title": "Revenue grew 18% a year as the new segment scaled", "subtitle": "Revenue by segment, $m",
     "chart": {"kind": "stacked_column", "unit": "$m", "categories": ["2021", "2022", "2023", "2024", "2025"],
               "series": [{"name": "Core", "values": [120, 132, 140, 151, 160]},
                          {"name": "New segment", "values": [10, 24, 45, 70, 98], "highlight": True}]},
     "takeaway": {"text": "The new segment is now 38% of revenue"},
     "bullets": [{"text": "**New segment** went from 8% to 38% of revenue in four years"},
                 {"text": "Core grew 7% a year, in line with the market"},
                 {"text": "Mix shift lifted gross margin by 3 points"}],
     "source": {"text": "Illustrative figures"}},
    {"id": "s3", "node": "2", "archetype": "table_highlight",
     "title": "Option B returns the most for the least execution risk", "subtitle": "Nine options compared",
     "table": {"columns": ["Option", "NPV ($m)", "IRR", "Payback (years)", "Capex ($m)", "Risk"],
               "rows": [["A: organic build", "140", "14%", "6.0", "220", "Low"],
                        ["B: bolt-on acquisition", "260", "19%", "4.5", "310", "Medium"],
                        ["C: joint venture", "180", "16%", "5.5", "150", "Medium"],
                        ["D: transformational deal", "310", "15%", "7.0", "900", "High"],
                        ["E: licensing", "90", "21%", "3.0", "40", "Low"],
                        ["F: minority stake", "70", "12%", "6.5", "120", "Low"],
                        ["G: greenfield plant", "200", "13%", "8.0", "640", "High"],
                        ["H: divest non-core", "60", "n/a", "1.0", "0", "Low"],
                        ["I: do nothing", "0", "n/a", "n/a", "0", "Medium"]],
               "highlight_rows": [1]},
     "takeaway": {"text": "Recommend option B: the best return of the large options"},
     "bullets": [{"text": "B pays back a year faster than the other large options"},
                 {"text": "D creates more value for 3x the capex"},
                 {"text": "E and F can run alongside B"}],
     "source": {"text": "Illustrative figures"}},
]


def sample_deck(prof: dict, out_path: str) -> dict:
    """Two slides in the profile's look, rendered without a model call, so the look can be checked in PowerPoint."""
    import copy
    from .render import render
    slides = copy.deepcopy(SAMPLE)
    slides[0]["title"] = slides[0]["title"].format(name=prof.get("name") or "Your company").strip()
    return render(slides, out_path, fix_labels=False, **render_kwargs(prof))
