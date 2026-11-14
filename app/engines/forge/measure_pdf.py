"""Measure a reference deck delivered as PDF (public consulting decks), to calibrate rules.yaml thresholds.

    python -m engines.forge.measure_pdf deck.pdf [--pages 53-76] [--out m.json]

A PDF has no masters, placeholders or chart parts, so only the visible properties are measured: text density,
title length and form, source lines, type scale, fonts, left edges, images and vector drawings. Heuristics are
named as such in the output (`method`), and every metric carries an evidence ID.
"""
from __future__ import annotations

import json
import re
import statistics as st
from collections import Counter
from pathlib import Path

from .common import evidence_id, sha256
from .validate_pptx import _has_verb

EN_STOP = {"the", "and", "of", "to", "in", "is", "for", "on", "are", "with", "by", "that", "from", "as", "be"}
SRC = re.compile(r"^\s*(source|sources|kilde|kilder|note|notes)\s*:", re.I)


def _pages(spec: str | None, n: int) -> list[int]:
    if not spec:
        return list(range(n))
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a) - 1, int(b or a)))
    return [p for p in out if 0 <= p < n]


def _mode_share(vals, tol=None):
    """Share of pages that match the most common value (within tol inches for positions): 1.0 = fully consistent."""
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    if tol is None:
        return round(Counter(vals).most_common(1)[0][1] / len(vals), 2)
    best = max(sum(1 for v in vals if abs(v - c) <= tol) for c in vals)
    return round(best / len(vals), 2)


def measure(path: str, pages: str | None = None) -> dict:
    import pymupdf as fitz
    doc = fitz.open(path)
    sha = sha256(path)
    per = []
    fonts, sizes = Counter(), {"title": Counter(), "body": Counter(), "footnote": Counter()}
    english_hits = total_words = 0
    for i in _pages(pages, doc.page_count):
        pg = doc[i]
        W, H = pg.rect.width, pg.rect.height
        spans = []
        for b in pg.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                for s in ln.get("spans", []):
                    if s["text"].strip():
                        spans.append(s)
        if not spans:
            per.append({"page": i + 1, "words": 0, "empty": True})
            continue
        top = [s for s in spans if s["bbox"][1] < H * 0.22]
        tmax = max((s["size"] for s in top), default=0)
        title_sp = [s for s in top if s["size"] >= tmax * 0.85 and s["size"] >= 14] if tmax else []
        title = " ".join(s["text"].strip() for s in title_sp)
        words = 0
        lefts = set()
        src = False
        for s in spans:
            role = "title" if s in title_sp else ("footnote" if s["size"] <= 9.5 else "body")
            sizes[role][round(s["size"])] += 1
            fonts[re.sub(r"^[A-Z]{6}\+", "", s["font"]).split("-")[0].split(",")[0]] += len(s["text"])
            if role == "body":
                words += len(s["text"].split())
            lefts.add(round(s["bbox"][0] / 72 / 0.05) * 0.05)
            if s["bbox"][1] > H * 0.8 and SRC.match(s["text"]):
                src = True
        text = " ".join(s["text"] for s in spans).lower()
        toks = re.findall(r"[a-zæøå]+", text)
        english_hits += sum(1 for w in toks if w in EN_STOP)
        total_words += len(toks)
        area = sum(abs((r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1])) for r in pg.get_image_info())
        raster = area > 0.3 * W * H
        all_words = sum(len(s["text"].split()) for s in spans)
        tsz = max((s["size"] for s in title_sp), default=None)
        ty = min((s["bbox"][1] for s in title_sp), default=None)
        tx = min((s["bbox"][0] for s in title_sp), default=None)
        tfont = re.sub(r"^[A-Z]{6}\+", "", title_sp[0]["font"]).split("-")[0] if title_sp else None
        per.append({"page": i + 1, "title_size": round(tsz, 1) if tsz else None,
                    "title_top_in": round(ty / 72, 2) if ty is not None else None,
                    "title_left_in": round(tx / 72, 2) if tx is not None else None, "title_font": tfont,
                    "raster_exhibit": raster, "all_words": all_words, "aspect": round(W / H, 3), "title": title[:200], "title_words": len(title.split()),
                    "title_has_verb": _has_verb(title) if title else None, "words": words,
                    "distinct_left_edges": len(lefts), "source_line": src, "images": len(pg.get_images()),
                    "drawings": len(pg.get_drawings())})
    content = [p for p in per if not p.get("empty") and p["title_words"] and (p["all_words"] >= 15 or p["raster_exhibit"])]
    english = english_hits > 0.06 * max(1, total_words)

    def stat(key, pages_):
        xs = [p[key] for p in pages_ if p.get(key) is not None]
        if not xs:
            return None
        xs.sort()
        return {"median": st.median(xs), "mean": round(st.mean(xs), 1), "p90": xs[int(0.9 * (len(xs) - 1))],
                "max": xs[-1]}
    titled = [p for p in content if p["title_words"]]
    summary = {
        "pages": len(per), "content_pages": len(content), "language_english": english,
        "aspect": Counter(p.get("aspect") for p in per).most_common(1)[0][0] if per else None,
        "body_words": stat("words", content), "title_words": stat("title_words", titled),
        "title_with_verb_share": (round(sum(1 for p in titled if p["title_has_verb"]) / len(titled), 2)
                                  if titled and english else None),
        "raster_exhibit_share": round(sum(1 for p in content if p["raster_exhibit"]) / len(content), 2) if content else None,
        "source_line_share": round(sum(1 for p in content if p["source_line"]) / len(content), 2) if content else None,
        "distinct_left_edges": stat("distinct_left_edges", content),
        "images_per_page": stat("images", content), "drawings_per_page": stat("drawings", content),
        "fonts_top": [f for f, _ in fonts.most_common(4)],
        "title_font_mode_share": _mode_share([p["title_font"] for p in content]),
        "title_size_mode_share": _mode_share([p["title_size"] for p in content]),
        "title_top_mode_share": _mode_share([p["title_top_in"] for p in content], 0.05),
        "title_left_mode_share": _mode_share([p["title_left_in"] for p in content], 0.05),
        "title_sizes": [s for s, _ in sizes["title"].most_common(3)],
        "body_sizes": [s for s, _ in sizes["body"].most_common(4)],
        "footnote_sizes": [s for s, _ in sizes["footnote"].most_common(3)],
    }
    method = {"title": "largest spans (>=85% of max, >=14pt) in the top 22% of the page",
              "body_words": "words in spans that are not title and >9.5pt", "content_pages": "has a title and >=15 words of any size, or a raster exhibit covering >30% of the page",
              "raster_exhibit": "embedded images cover >30% of the page; text inside them (incl. source lines) is invisible to this measurement, so text metrics are lower bounds",
              "source_line": "span in bottom 20% starting 'Source:'/'Note:' (or Danish 'Kilde:')",
              "left_edges": "span x0 rounded to 0.05in", "verb": "lexicon + inflection heuristic, English only"}
    return {"file": str(path), "sha256": sha, "page_range": pages, "summary": summary, "method": method,
            "evidence": {k: evidence_id(sha, "pdf_" + k) for k in summary}, "pages": per}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages")
    ap.add_argument("--out")
    a = ap.parse_args()
    r = measure(a.pdf, a.pages)
    txt = json.dumps(r, indent=1)
    if a.out:
        Path(a.out).write_text(txt)
    print(json.dumps({"file": r["file"], "summary": r["summary"]}, indent=1))
