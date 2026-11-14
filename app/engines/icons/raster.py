"""Rasterise an icon SVG to a PNG without Cairo (Pillow only), for the PNG fallback beside an embedded SVG.

The Cairo library CairoSVG needs is missing on most Mac and Windows installs, so the forge renderer would otherwise
have no PNG for icons of the bundled sets. This covers what the bundled icon SVGs use: path (all commands, arcs
included), circle, ellipse, rect (rounded), line, polyline, polygon, g, transforms, fill / stroke / opacity,
fill-rule, round or square line ends. Defs, clip paths and masks are skipped (the icons only use full-size clips).
Drawn at 4x and scaled down, so edges are smooth.

    png_bytes(svg_text, "0A1A2F", size=192) -> PNG bytes
"""
from __future__ import annotations

import io
import math
import re
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image, ImageChops, ImageDraw

SS = 4                                  # supersampling factor
_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_SKIP = {"defs", "clipPath", "mask", "title", "desc", "style", "metadata"}


def _mul(a, b):
    """Affine matrices as (a, b, c, d, e, f): x' = a x + c y + e, y' = b x + d y + f."""
    return (a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1], a[0] * b[2] + a[2] * b[3],
            a[1] * b[2] + a[3] * b[3], a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5])


def _transform(text: str | None):
    m = (1, 0, 0, 1, 0, 0)
    for name, args in re.findall(r"([a-zA-Z]+)\s*\(([^)]*)\)", text or ""):
        v = [float(x) for x in _NUM.findall(args)]
        if name == "matrix" and len(v) == 6:
            t = tuple(v)
        elif name == "translate" and v:
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif name == "scale" and v:
            t = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate" and v:
            a = math.radians(v[0])
            t = (math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0, 0)
            if len(v) == 3:
                t = _mul(_mul((1, 0, 0, 1, v[1], v[2]), t), (1, 0, 0, 1, -v[1], -v[2]))
        else:
            continue
        m = _mul(m, t)
    return m


def _arc(x1, y1, rx, ry, phi, large, sweep, x2, y2):
    """Points along an SVG elliptical arc (endpoint parameterisation), excluding the start point."""
    if rx == 0 or ry == 0:
        return [(x2, y2)]
    rx, ry, p = abs(rx), abs(ry), math.radians(phi)
    cp, sp = math.cos(p), math.sin(p)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = x1p ** 2 / rx ** 2 + y1p ** 2 / ry ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    den = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    co = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        co = -co
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx, cy = cp * cxp - sp * cyp + (x1 + x2) / 2, sp * cxp + cp * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a
    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(4, int(abs(dt) / (math.pi / 16)))
    out = []
    for i in range(1, n + 1):
        t = t1 + dt * i / n
        out.append((cx + rx * math.cos(t) * cp - ry * math.sin(t) * sp, cy + rx * math.cos(t) * sp + ry * math.sin(t) * cp))
    return out


def _bez(p0, p1, p2, p3, n=12):
    return [((1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * p1[0] + 3 * (1 - t) * t ** 2 * p2[0] + t ** 3 * p3[0],
             (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * p1[1] + 3 * (1 - t) * t ** 2 * p2[1] + t ** 3 * p3[1])
            for t in (i / n for i in range(1, n + 1))]


def _path(d: str):
    """Subpaths of a path's d attribute: [(points, closed)]."""
    toks = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|" + _NUM.pattern, d or "")
    subs, cur, closed = [], [], False
    x = y = sx = sy = 0.0
    last_c = last_q = None
    cmd, i = None, 0

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v
    while i < len(toks):
        if toks[i].isalpha():
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                if cur:
                    subs.append((cur, True))
                cur, x, y = [], sx, sy
                last_c = last_q = None
                continue
        elif cmd is None:
            break
        rel = cmd.islower()
        c = cmd.upper()
        try:
            if c == "M":
                nx, ny = num(), num()
                x, y = (x + nx, y + ny) if rel else (nx, ny)
                if cur:
                    subs.append((cur, False))
                cur, sx, sy = [(x, y)], x, y
                cmd = "l" if rel else "L"
                last_c = last_q = None
            elif c in "LHV":
                if c == "L":
                    nx, ny = num(), num()
                    x, y = (x + nx, y + ny) if rel else (nx, ny)
                elif c == "H":
                    v = num()
                    x = x + v if rel else v
                else:
                    v = num()
                    y = y + v if rel else v
                cur = cur or [(sx, sy)]
                cur.append((x, y))
                last_c = last_q = None
            elif c in "CS":
                if c == "C":
                    a = [num() for _ in range(6)]
                    if rel:
                        a = [a[0] + x, a[1] + y, a[2] + x, a[3] + y, a[4] + x, a[5] + y]
                    c1 = (a[0], a[1])
                    c2, end = (a[2], a[3]), (a[4], a[5])
                else:
                    a = [num() for _ in range(4)]
                    if rel:
                        a = [a[0] + x, a[1] + y, a[2] + x, a[3] + y]
                    c1 = (2 * x - last_c[0], 2 * y - last_c[1]) if last_c else (x, y)
                    c2, end = (a[0], a[1]), (a[2], a[3])
                cur = cur or [(sx, sy)]
                cur += _bez((x, y), c1, c2, end)
                last_c, last_q = c2, None
                x, y = end
            elif c in "QT":
                if c == "Q":
                    a = [num() for _ in range(4)]
                    if rel:
                        a = [a[0] + x, a[1] + y, a[2] + x, a[3] + y]
                    q, end = (a[0], a[1]), (a[2], a[3])
                else:
                    a = [num() for _ in range(2)]
                    if rel:
                        a = [a[0] + x, a[1] + y]
                    q = (2 * x - last_q[0], 2 * y - last_q[1]) if last_q else (x, y)
                    end = (a[0], a[1])
                cur = cur or [(sx, sy)]
                cur += _bez((x, y), (x + 2 / 3 * (q[0] - x), y + 2 / 3 * (q[1] - y)),
                            (end[0] + 2 / 3 * (q[0] - end[0]), end[1] + 2 / 3 * (q[1] - end[1])), end)
                last_q, last_c = q, None
                x, y = end
            elif c == "A":
                rx, ry, phi, large, sweep, nx, ny = (num() for _ in range(7))
                nx, ny = (x + nx, y + ny) if rel else (nx, ny)
                cur = cur or [(sx, sy)]
                cur += _arc(x, y, rx, ry, phi, int(large), int(sweep), nx, ny)
                x, y = nx, ny
                last_c = last_q = None
            else:
                i += 1
        except (IndexError, ValueError):
            break
    if cur:
        subs.append((cur, closed))
    return subs


def _ellipse(cx, cy, rx, ry, n=64):
    return [(cx + rx * math.cos(2 * math.pi * k / n), cy + ry * math.sin(2 * math.pi * k / n)) for k in range(n)]


def _shape(el, tag):
    f = lambda k, dv=0.0: float((_NUM.findall(el.get(k) or "") or [dv])[0])   # noqa: E731
    if tag == "path":
        return _path(el.get("d", ""))
    if tag == "circle":
        return [(_ellipse(f("cx"), f("cy"), f("r"), f("r")), True)]
    if tag == "ellipse":
        return [(_ellipse(f("cx"), f("cy"), f("rx"), f("ry")), True)]
    if tag == "line":
        return [([(f("x1"), f("y1")), (f("x2"), f("y2"))], False)]
    if tag in ("polyline", "polygon"):
        v = [float(n) for n in _NUM.findall(el.get("points", ""))]
        return [(list(zip(v[0::2], v[1::2])), tag == "polygon")]
    if tag == "rect":
        x, y, w, h = f("x"), f("y"), f("width"), f("height")
        rx = f("rx", f("ry"))
        ry = f("ry", rx)
        rx, ry = min(rx, w / 2), min(ry, h / 2)
        if not rx:
            return [([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True)]
        pts = []
        for cx, cy, a0 in ((x + w - rx, y + ry, -90), (x + w - rx, y + h - ry, 0), (x + rx, y + h - ry, 90),
                           (x + rx, y + ry, 180)):
            pts += [(cx + rx * math.cos(math.radians(a0 + 90 * k / 8)), cy + ry * math.sin(math.radians(a0 + 90 * k / 8)))
                    for k in range(9)]
        return [(pts, True)]
    return []


def _area(pts):
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
               for i in range(len(pts))) / 2


def png_bytes(svg: str, hexcolor: str = "000000", size: int = 192) -> bytes:
    """The SVG drawn in one colour (its own colours ignored, opacity kept) on a transparent square PNG."""
    root = ET.fromstring(svg.encode("utf-8") if isinstance(svg, str) else svg)
    vb = [float(v) for v in _NUM.findall(root.get("viewBox") or "0 0 24 24")]
    vx, vy, vw, vh = vb if len(vb) == 4 else (0, 0, 24, 24)
    px = size * SS
    k = px / max(vw, vh)
    base = (k, 0, 0, k, -vx * k + (px - vw * k) / 2, -vy * k + (px - vh * k) / 2)
    alpha = Image.new("L", (px, px), 0)

    def walk(el, style, m):
        tag = el.tag.split("}")[-1]
        if tag in _SKIP:
            return
        st = dict(style)
        for key in ("fill", "stroke", "stroke-width", "fill-rule", "stroke-linecap", "stroke-linejoin"):
            if el.get(key) is not None:
                st[key] = el.get(key)
        for key, val in re.findall(r"([a-z-]+)\s*:\s*([^;]+)", el.get("style") or ""):
            st[key] = val.strip()
        op = float(el.get("opacity") or 1) * float(st.get("_op", 1))
        st["_op"] = op
        m = _mul(m, _transform(el.get("transform")))
        if tag in ("svg", "g"):
            for ch in el:
                walk(ch, st, m)
            return
        subs = [([(m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]) for x, y in pts], closed)
                for pts, closed in _shape(el, tag) if len(pts) >= 2]
        if not subs:
            return
        layer = Image.new("L", (px, px), 0)
        fill = st.get("fill", "black")
        if fill != "none" and float(el.get("fill-opacity") or 1) > 0:
            polys = [p for p, _ in subs if len(p) >= 3]
            if polys:
                acc = np.zeros((px, px), dtype=np.int16)
                for p in polys:                     # winding number: each subpath adds +-1 inside it
                    one = Image.new("L", (px, px), 0)
                    ImageDraw.Draw(one).polygon(p, fill=1)
                    acc += np.asarray(one, dtype=np.int16) * (1 if _area(p) >= 0 else -1)
                inside = (acc % 2 != 0) if st.get("fill-rule") == "evenodd" else (acc != 0)
                layer = Image.fromarray((inside * 255).astype(np.uint8), "L")
        stroke = st.get("stroke", "none")
        if stroke != "none":
            w = float((_NUM.findall(st.get("stroke-width", "1")) or [1])[0]) * math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))
            dr = ImageDraw.Draw(layer)
            round_ = st.get("stroke-linecap", "butt") == "round" or st.get("stroke-linejoin") == "round"
            for pts, closed in subs:
                line = pts + [pts[0]] if closed else pts
                dr.line(line, fill=255, width=max(1, round(w)), joint="curve")
                if round_ or closed:
                    for qx, qy in (line if round_ else []):
                        dr.ellipse((qx - w / 2, qy - w / 2, qx + w / 2, qy + w / 2), fill=255)
        if op < 1:
            layer = layer.point(lambda v, o=op: int(v * o))
        nonlocal_alpha[0] = ImageChops.screen(nonlocal_alpha[0], layer)

    nonlocal_alpha = [alpha]
    walk(root, {"fill": root.get("fill") or "black"}, base)
    a = nonlocal_alpha[0].resize((size, size), Image.LANCZOS)
    im = Image.new("RGBA", (size, size), "#" + hexcolor.lstrip("#"))
    im.putalpha(a)
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()
