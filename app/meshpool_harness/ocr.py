"""OCR for images and scanned PDFs, offline, with open-source models (RapidOCR on ONNX Runtime, PaddleOCR models).

Languages
- Built in through PP-OCRv6 (multilingual): Chinese (simplified and traditional), English, Japanese and about 50
  Latin-script languages (French, German, Spanish, Portuguese, Italian, Dutch, Turkish, Vietnamese, Indonesian, ...).
- Extra PP-OCRv5 models shipped in app/vendor/ocr: Arabic (also Persian and Urdu script), Cyrillic (Russian,
  Ukrainian, ...), Korean, Devanagari (Hindi, Marathi, Nepali, ...), Thai and Greek.

lang="auto" reads with the multilingual model, then also tries Arabic and Cyrillic when the result looks weak, and
keeps the best reading. Engines are cached per process.
"""
from __future__ import annotations

import io
import os
import threading
from pathlib import Path

from meshpool_harness import config as C

EXTRA = {"arabic": "arabic", "ar": "arabic", "fa": "arabic", "ur": "arabic", "persian": "arabic", "urdu": "arabic",
         "cyrillic": "cyrillic", "ru": "cyrillic", "uk": "cyrillic", "be": "cyrillic", "bg": "cyrillic",
         "russian": "cyrillic", "ukrainian": "cyrillic", "sr": "cyrillic", "kk": "cyrillic", "mn": "cyrillic",
         "korean": "korean", "ko": "korean", "kr": "korean",
         "devanagari": "devanagari", "hi": "devanagari", "mr": "devanagari", "ne": "devanagari", "hindi": "devanagari",
         "thai": "th", "th": "th", "greek": "el", "el": "el", "eslav": "eslav"}
_ENGINES: dict[str, object] = {}
_LOCK = threading.Lock()


def models_dir() -> Path:
    for p in (C.APP / "vendor" / "ocr", C.APP.parent / "ocr-models"):
        if p.is_dir():
            return p
    return C.APP / "vendor" / "ocr"


def available() -> bool:
    try:
        import onnxruntime  # noqa: F401
        import rapidocr  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _engine(script: str):
    with _LOCK:
        if script in _ENGINES:
            return _ENGINES[script]
        import logging
        from rapidocr import RapidOCR
        logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        params = {"Global.log_level": "warning"}
        if script != "multi":
            from rapidocr.utils.typings import LangRec, ModelType, OCRVersion
            path = models_dir() / f"{script}_PP-OCRv5_rec_mobile.onnx"
            if not path.is_file():
                raise FileNotFoundError(f"the {script} OCR model is not in this install")
            params.update({"Rec.lang_type": LangRec(script), "Rec.ocr_version": OCRVersion.PPOCRV5,
                           "Rec.model_type": ModelType.MOBILE, "Rec.model_path": str(path)})
        e = RapidOCR(params=params)
        _ENGINES[script] = e
        return e


def _run(script: str, img) -> tuple[list[str], float, list]:
    r = _engine(script)(img)
    txts = list(r.txts or [])
    scores = list(r.scores or [])
    boxes = list(r.boxes) if r.boxes is not None else []
    mean = sum(scores) / len(scores) if scores else 0.0
    return txts, mean, boxes


def _lines(txts: list[str], boxes: list) -> str:
    """Join detected text pieces into reading-order lines (top to bottom, then left to right)."""
    if not boxes or len(boxes) != len(txts):
        return "\n".join(txts)
    items = []
    for t, b in zip(txts, boxes):
        ys = [p[1] for p in b]
        xs = [p[0] for p in b]
        items.append((min(ys), max(ys), min(xs), t))
    items.sort(key=lambda x: (x[0], x[2]))
    lines, cur, cur_bottom = [], [], None
    for top, bottom, left, t in items:
        if cur and top > cur_bottom - (bottom - top) * 0.5:
            lines.append(cur)
            cur = []
        cur.append((left, t))
        cur_bottom = bottom if not cur_bottom or len(cur) == 1 else max(cur_bottom, bottom)
    if cur:
        lines.append(cur)
    return "\n".join("  ".join(t for _, t in sorted(line)) for line in lines)


def _coverage(txts: list[str], boxes: list) -> float:
    """Characters read compared with the characters the text boxes should hold (about one per box height)."""
    if not boxes or len(boxes) != len(txts):
        return 1.0
    expect = 0.0
    for b in boxes:
        xs, ys = [p[0] for p in b], [p[1] for p in b]
        h = max(1.0, max(ys) - min(ys))
        expect += (max(xs) - min(xs)) / h * 1.4
    got = sum(len(t.replace(" ", "")) for t in txts)
    return min(1.0, got / expect) if expect else 1.0


def _weak(txts: list[str], mean: float, boxes: list) -> bool:
    text = "".join(txts)
    letters = sum(ch.isalpha() for ch in text)
    return (not text or mean < 0.9 or _coverage(txts, boxes) < 0.45
            or (len(text) >= 4 and letters / len(text) < 0.35))


def _quality(r: tuple) -> float:
    _, txts, mean, boxes = r
    text = "".join(txts)
    letters = sum(ch.isalpha() for ch in text)
    return mean * (letters + 1) * (0.5 + _coverage(txts, boxes))


def read_image(img, lang: str = "auto") -> dict:
    """OCR one image (path, bytes or PIL image). Returns {"text", "script", "confidence"}."""
    if not available():
        raise RuntimeError("OCR is not included in this install.")
    from PIL import Image
    _heif()
    if isinstance(img, (str, Path)):
        pil = Image.open(img)
    elif isinstance(img, (bytes, bytearray)):
        pil = Image.open(io.BytesIO(img))
    else:
        pil = img
    pil = pil.convert("RGB")
    if max(pil.size) > 4000:                         # keep memory and time in check on huge scans
        pil.thumbnail((4000, 4000))
    import numpy as np
    arr = np.array(pil)[:, :, ::-1]                    # RGB -> BGR for OpenCV
    want = (lang or "auto").strip().lower()
    if want not in ("auto", "multi"):
        script = EXTRA.get(want, "multi")
        txts, mean, boxes = _run(script, arr)
        return {"text": _lines(txts, boxes), "script": script, "confidence": round(mean, 3)}
    best = ("multi",) + _run("multi", arr)
    if _weak(best[1], best[2], best[3]):
        for script in ("arabic", "cyrillic"):
            try:
                cand = (script,) + _run(script, arr)
            except FileNotFoundError:
                continue
            if _quality(cand) > _quality(best) * 1.05:
                best = cand
    script, txts, mean, boxes = best
    return {"text": _lines(txts, boxes), "script": script, "confidence": round(mean, 3)}


def describe(img, name: str = "image", lang: str = "auto") -> str:
    """Readable OCR result for an agent."""
    try:
        r = read_image(img, lang)
    except Exception as e:  # noqa: BLE001
        return f"[OCR of {name} failed: {e}]"
    if not r["text"].strip():
        return f"[No text found in {name}.]"
    return f"Text in {name} (OCR, {r['script']} model, confidence {r['confidence']:.2f}):\n{r['text']}"


def _heif():
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()               # iPhone photos (.heic, .heif)
    except Exception:  # noqa: BLE001
        pass


def pdf_page_images(path: str | Path, pages: list[int], dpi: int = 200) -> list[tuple[int, object]]:
    """Each page as an image: rendered with PDFium (works for any scan, any encoding), else the page's largest
    embedded image."""
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(path))
        out = []
        for i in pages:
            if 0 <= i < len(doc):
                out.append((i, doc[i].render(scale=dpi / 72).to_pil()))
        doc.close()
        return out
    except Exception:  # noqa: BLE001
        pass
    from pypdf import PdfReader
    r = PdfReader(str(path))
    out = []
    for i in pages:
        try:
            imgs = list(r.pages[i].images)
        except Exception:  # noqa: BLE001
            continue
        if imgs:
            big = max(imgs, key=lambda im: len(im.data))
            out.append((i, big.image if getattr(big, "image", None) is not None else big.data))
    return out


os.environ.setdefault("OMP_NUM_THREADS", str(max(1, min(4, (os.cpu_count() or 2) - 1))))
