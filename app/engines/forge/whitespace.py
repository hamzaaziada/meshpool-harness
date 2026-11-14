"""P20: dead space. The largest empty rectangle in a slide's body, measured on the rendered page.

Calibrated on the reference decks (scratchpad measurement, 13 Nov 2026): McKinsey content pages have a median of 6%
of the body as their largest empty block (p90 12%, max 17% bar one page at 30%); Bain content pages 13-15%. Light
grey panels count as empty: a panel with nothing in it is still dead space.
"""
from __future__ import annotations

import numpy as np


def largest_empty(mask: np.ndarray) -> tuple[int, int, int, int, int]:
    """mask True = empty. Returns (area, top_row, left_col, rows, cols) of the largest all-empty rectangle."""
    R, C = mask.shape
    h = np.zeros(C, int)
    best = (0, 0, 0, 0, 0)
    for r in range(R):
        h = np.where(mask[r], h + 1, 0)
        st: list[tuple[int, int]] = []
        for c in range(C + 1):
            cur = int(h[c]) if c < C else 0
            start = c
            while st and st[-1][1] >= cur:
                s, hh = st.pop()
                if hh * (c - s) > best[0]:
                    best = (hh * (c - s), r - hh + 1, s, hh, c - s)
                start = s
            st.append((start, cur))
    return best


def page_empty(page, width_in: float, height_in: float, top: float, bottom: float, side: float = 0.04,
               dpi: int = 20, light: int = 225, filled: list | None = None) -> dict:
    """Largest empty block inside the body band [top, bottom] (fractions of page height) of a pypdfium2 page.
    `filled` boxes (x, y, w, h in inches; chart frames) count as content: white space inside a plot is data, not
    layout."""
    img = np.asarray(page.render(scale=dpi / 72).to_pil().convert("L"), dtype=int)
    H, W = img.shape
    for (bx, by, bw, bh) in filled or []:
        img[int(by / height_in * H):int((by + bh) / height_in * H), int(bx / width_in * W):int((bx + bw) / width_in * W)] = 0
    y0, y1, x0, x1 = int(top * H), int(bottom * H), int(side * W), int((1 - side) * W)
    body = img[y0:y1, x0:x1] > light
    area, r, c, rows, cols = largest_empty(body)
    ph, pw = height_in / H, width_in / W
    return {"share": area / body.size if body.size else 0.0, "w_in": cols * pw, "h_in": rows * ph,
            "x_in": (x0 + c) * pw, "y_in": (y0 + r) * ph}


def where(e: dict, W: float, H: float) -> str:
    cx, cy = e["x_in"] + e["w_in"] / 2, e["y_in"] + e["h_in"] / 2
    v = "upper" if cy < H * 0.45 else ("lower" if cy > H * 0.6 else "middle")
    hz = "left" if cx < W * 0.4 else ("right" if cx > W * 0.6 else "centre")
    return f"{v} {hz}" if not (v == "middle" and hz == "centre") else "centre"


def bottom_blank(page, width_in: float, height_in: float, top_in: float, rule_in: float, notes: bool = False,
                 left_in: float = 0.6, right_in: float | None = None, dpi: int = 50, light: int = 250) -> dict:
    """How far above its foot a slide's content ends: the blank band under the lowest content row of the body, as a
    share of the body height. The body runs from `top_in` to the source rule (`rule_in`), or to the first footnote
    line when the slide has footnotes; light grey panels count as content here (a panel that fits its text is not
    empty space), only white is blank. `right_in` limits the columns read (an argument half-panel that runs the full
    height is left out)."""
    img = np.asarray(page.render(scale=dpi / 72).to_pil().convert("L"), dtype=int)
    H, W = img.shape
    px = lambda inch: max(0, min(H - 1, int(inch * dpi)))  # noqa: E731
    x0 = int(left_in / width_in * W)
    x1 = int((right_in if right_in else width_in - left_in) / width_in * W)
    band = img[:, x0:int((width_in - left_in) / width_in * W)] < 235
    dark = band.any(axis=1)
    solid = band.mean(axis=1) > 0.5                       # a filled band, not a line of footnote text
    content = (img[:, x0:x1] < light).any(axis=1)
    limit = rule_in - 0.1
    if notes:                                             # walk up through the footnote lines (8 pt, small gaps)
        r = px(rule_in - 0.06)
        while r > px(top_in) and not dark[r]:
            r -= 1
        gap = 0
        while r > px(top_in) and r > px(rule_in - 0.6):  # three footnote lines at most
            if solid[r]:
                break
            gap = 0 if dark[r] else gap + 1
            if gap > 0.07 * dpi:
                break
            r -= 1
        limit = (r + gap) / dpi
    rows = np.where(content[px(top_in):px(limit)])[0]
    last = (rows.max() + px(top_in)) / dpi if len(rows) else top_in
    body = max(0.01, limit - top_in)
    return {"share": max(0.0, (limit - last) / body), "blank_in": max(0.0, limit - last), "content_to": last,
            "body_to": limit}
