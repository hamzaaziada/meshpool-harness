"""Deck QA: geometry, fonts, overflow, contrast, slide count, and LibreOffice render to PDF/PNG."""
from __future__ import annotations

import itertools
import shutil
import subprocess
import tempfile
from pathlib import Path

from pptx import Presentation
from pptx.util import Emu

from . import theme as T
from .textfit import block_height_in

EMU_IN = 914400


def _in(v):
    return Emu(v).inches


def _lum(hexs):
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hexs[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _bg_hex(slide):
    try:
        return str(slide.background.fill.fore_color.rgb)
    except Exception:
        return "FFFFFF"


def check(pptx_path: str, requested_slides: int | None = None) -> list[dict]:
    """Returns a list of issues: {slide, check, detail}. Empty list == pass."""
    prs = Presentation(pptx_path)
    W, H = _in(prs.slide_width), _in(prs.slide_height)
    issues, fonts = [], set()
    if requested_slides is not None and len(prs.slides) != requested_slides:
        issues.append({"slide": 0, "check": "slide_count",
                       "detail": f"{len(prs.slides)} slides, {requested_slides} requested"})
    for n, sl in enumerate(prs.slides, 1):
        slide_bg = _bg_hex(sl)
        fills = []                                     # filled shapes: the real background under a text box
        for sh in sl.shapes:
            try:
                if sh.fill.type == 1:                   # solid
                    fills.append((_in(sh.left), _in(sh.top), _in(sh.width), _in(sh.height), str(sh.fill.fore_color.rgb), sh.shape_id))
            except Exception:
                pass

        def under(x, y, w, h, own_id):
            cx, cy = x + w / 2, y + h / 2
            hits = [f for f in fills if f[5] != own_id and f[0] <= cx <= f[0] + f[2] and f[1] <= cy <= f[1] + f[3]]
            return min(hits, key=lambda f: f[2] * f[3])[4] if hits else slide_bg
        boxes = []
        for sh in sl.shapes:
            x, y, w, h = _in(sh.left), _in(sh.top), _in(sh.width), _in(sh.height)
            if x < -0.01 or y < -0.01 or x + w > W + 0.01 or y + h > H + 0.01:
                issues.append({"slide": n, "check": "out_of_bounds", "detail": sh.name})
            if not sh.has_text_frame:
                if sh.shape_type is not None and "PLACEHOLDER" in str(sh.shape_type):
                    issues.append({"slide": n, "check": "empty_placeholder", "detail": sh.name})
                continue
            tf = sh.text_frame
            text = tf.text.strip()
            bg = under(x, y, w, h, sh.shape_id)
            if sh.is_placeholder and not text:
                issues.append({"slide": n, "check": "empty_placeholder", "detail": sh.name})
            if not text:
                continue
            paras = []
            for p in tf.paragraphs:
                runs = [r for r in p.runs if r.text]
                if not runs:
                    continue
                f = runs[0].font
                fonts.add(f.name or "theme")
                size = f.size.pt if f.size else 18
                style = "bold" if f.bold else ("italic" if f.italic else "regular")
                sb = p.space_before.pt if p.space_before is not None else 0
                paras.append(("".join(r.text for r in runs), size, style, sb))
                try:
                    col = str(f.color.rgb)
                except Exception:
                    col = None
                if col and contrast(col, bg) < 4.5 and size < 24 or (col and contrast(col, bg) < 3.0):
                    issues.append({"slide": n, "check": "low_contrast",
                                   "detail": f"{sh.name}: {col} on {bg} ({contrast(col, bg):.1f}:1)"})
            indent = 0.6 if any(p._p.pPr is not None and p._p.pPr.find(
                "{http://schemas.openxmlformats.org/drawingml/2006/main}buChar") is not None for p in tf.paragraphs) else 0
            need = block_height_in(paras, w - indent)
            if need > h + 0.05:
                issues.append({"slide": n, "check": "text_overflow",
                               "detail": f"{sh.name}: needs {need:.2f}in, box {h:.2f}in"})
            boxes.append((sh.name, x, y, w, h))
        for (na, ax, ay, aw, ah), (nb, bx, by, bw, bh) in itertools.combinations(boxes, 2):
            ox = min(ax + aw, bx + bw) - max(ax, bx)
            oy = min(ay + ah, by + bh) - max(ay, by)
            if ox > 0.05 and oy > 0.05:
                issues.append({"slide": n, "check": "overlap", "detail": f"{na} x {nb}"})
    if len(fonts - {"theme"}) > 2:
        issues.append({"slide": 0, "check": "font_families", "detail": sorted(fonts)})
    return issues


def render_images(pptx_path: str, out_dir: str, dpi: int = 60) -> list[str]:
    """pptx -> pdf (LibreOffice headless) -> png per slide. Returns png paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    from meshpool_harness.config import find_soffice
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice not installed")
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([soffice, f"-env:UserInstallation={Path(profile).as_uri()}", "--headless", "--convert-to", "pdf",
                        "--outdir", str(out), pptx_path], check=True, capture_output=True, timeout=180)
    pdf = out / (Path(pptx_path).stem + ".pdf")
    prefix = out / Path(pptx_path).stem
    subprocess.run(["pdftoppm", "-png", "-r", str(dpi), str(pdf), str(prefix)], check=True, timeout=180)
    return sorted(str(p) for p in out.glob(Path(pptx_path).stem + "-*.png"))


def contact_sheet(pngs: list[str], out_path: str, cols: int = 3) -> str:
    """One image with all slides, for a single vision review call (cheaper than one call per slide)."""
    from PIL import Image
    ims = [Image.open(p) for p in pngs]
    w, h = ims[0].size
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w + (cols + 1) * 10, rows * h + (rows + 1) * 10), "#9AA3B2")
    for i, im in enumerate(ims):
        sheet.paste(im, (10 + (i % cols) * (w + 10), 10 + (i // cols) * (h + 10)))
    sheet.save(out_path)
    return out_path
