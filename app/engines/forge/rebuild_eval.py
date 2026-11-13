"""One-shot rebuild test: can the engine rebuild a consulting reference deck from its context and idea alone?

    python -m engines.forge.rebuild_eval REF.pdf OUT_DIR [--pages 53-76] [--level idea|content] [--slides N]

1. Brief (no images). From the reference PDF's text layer only:
   - level "content": the text of every page, in order, without layout (the hardest thing we would ever get);
   - level "idea" (default, the real test): one model call condenses that text into the context, the governing
     idea and a fact list. No slide titles, no page structure, no images reach the builder.
2. Build. The deck engine gets the brief and the reference's content-page count, and builds once.
3. Score, the same way for both decks:
   - forge validators (P1-P18) on the rebuilt .pptx;
   - measure_pdf on the reference AND on the rebuilt deck's LibreOffice render (apples to apples);
   - storyline coverage: for every reference title, the best title similarity in the rebuild;
   - fact coverage: share of the reference's numbers that appear in the rebuild;
   - side-by-side contact sheets (reference pages | rebuilt slides) for a blind vision review.
Writes OUT_DIR/brief.md, rebuild.pptx, rebuild.pdf, report.json, sheet-ref.png, sheet-rebuild.png.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from engines.forge.common import load_rules, soffice_convert  # noqa: E402
from engines.forge.measure_pdf import _pages, measure as measure_pdf  # noqa: E402
from engines.forge.validate_pptx import _cos, _numbers, _tokens, validate  # noqa: E402

IDEA = """You are given the raw text of a consulting presentation (no images, no layout). Write a brief that a
different team could use to build the deck from scratch WITHOUT seeing the original:
- Context: who the client/audience is and the situation (2-3 sentences).
- The governing idea: the one-sentence answer the deck argues.
- The argument: 3-6 supporting points in the order the original argues them (one line each, not slide titles).
- Facts: every quantitative fact you can find, one per line, as "<what> = <number><unit> (<year/period>, <source if stated>)".
Do not invent anything. Plain text, no JSON."""


def page_texts(pdf: str, pages: str | None) -> list[dict]:
    import pymupdf as fitz
    doc = fitz.open(pdf)
    out = []
    for i in _pages(pages, doc.page_count):
        out.append({"page": i + 1, "text": doc[i].get_text().strip()})
    return out


def make_brief(pdf: str, pages: str | None, level: str, client, task_id: str) -> str:
    texts = page_texts(pdf, pages)
    raw = "\n\n".join(f"[page {t['page']}]\n{t['text']}" for t in texts if t["text"])
    if level == "content":
        return "Source material (text of the original, without layout or images):\n\n" + raw[:24000]
    r = client.chat(model=_model(), messages=[{"role": "system", "content": IDEA},
                                              {"role": "user", "content": raw[:30000]}],
                    task_id=task_id + "-brief", max_tokens=3000, temperature=0.2)
    return _content(r)


def _model():
    import os
    return os.environ.get("MH_MODEL", "deepseek-v4.1-flash")


def _content(r) -> str:
    if isinstance(r, dict):
        ch = (r.get("choices") or [{}])[0].get("message", {})
        return ch.get("content") or ""
    return getattr(r, "content", "") or str(r)


def contact_sheet(pngs: list[str], out: str, cols: int = 4, width: int = 480) -> str | None:
    from PIL import Image
    if not pngs:
        return None
    ims = []
    for p in pngs:
        im = Image.open(p).convert("RGB")
        ims.append(im.resize((width, int(im.height * width / im.width))))
    h = max(i.height for i in ims)
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * width + (cols + 1) * 8, rows * h + (rows + 1) * 8), "#9AA3B2")
    for k, im in enumerate(ims):
        sheet.paste(im, (8 + (k % cols) * (width + 8), 8 + (k // cols) * (h + 8)))
    sheet.save(out)
    return out


def _png(pdf: str, prefix: str, pages: list[int] | None = None, dpi: int = 50) -> list[str]:
    import pymupdf as fitz
    doc = fitz.open(pdf)
    out = []
    for i in (pages if pages is not None else range(doc.page_count)):
        p = f"{prefix}-{i + 1:03d}.png"
        doc[i].get_pixmap(dpi=dpi).save(p)
        out.append(p)
    return out


def score(ref_pdf: str, ref_pages: str | None, rebuild_pptx: str, out_dir: Path, brief_slides: int | None) -> dict:
    rules = load_rules()
    ref = measure_pdf(ref_pdf, ref_pages)
    reb_pdf = soffice_convert(rebuild_pptx, "pdf", str(out_dir))
    reb = measure_pdf(str(reb_pdf))
    val = validate(rebuild_pptx, rules, brief={"slide_count": brief_slides} if brief_slides else None, render=False)
    ref_titles = [p["title"] for p in ref["pages"] if p.get("title")]
    reb_titles = [s["title"] for s in val["measurement"]["slides"] if s["title"]]
    cover = [max((_cos(_tokens(a), _tokens(b)) for b in reb_titles), default=0) for a in ref_titles]
    ign = re.compile(rules["pptx"]["provenance"]["ignore_numbers"])
    ref_text = "\n".join(t["text"] for t in page_texts(ref_pdf, ref_pages))
    reb_text = "\n".join(sh["text"] for s in val["measurement"]["slides"] for sh in s["shapes"] if sh["text"])
    reb_text += "\n" + " ".join(str(v) for s in val["measurement"]["slides"] for c in s["charts"]
                                for se in c["series"] for v in se["values"])
    ref_nums = {round(v, 2) for _, v, _ in _numbers(ref_text, ign)}
    reb_nums = {round(v, 2) for _, v, _ in _numbers(reb_text, ign)}
    keys = ["content_pages", "body_words", "title_words", "title_with_verb_share", "source_line_share",
            "images_per_page", "drawings_per_page", "fonts_top", "title_sizes", "body_sizes", "footnote_sizes"]
    val.pop("measurement")
    return {
        "reference": {k: ref["summary"].get(k) for k in keys} | {"file": ref_pdf, "sha256": ref["sha256"]},
        "rebuild": {k: reb["summary"].get(k) for k in keys} | {"file": rebuild_pptx},
        "storyline_coverage": {"mean_best_title_similarity": round(sum(cover) / len(cover), 3) if cover else None,
                               "ref_titles_matched_over_0_5": sum(1 for c in cover if c > 0.5), "ref_titles": len(cover)},
        "fact_coverage": {"ref_numbers": len(ref_nums), "found_in_rebuild": len(ref_nums & reb_nums),
                          "share": round(len(ref_nums & reb_nums) / len(ref_nums), 3) if ref_nums else None,
                          "rebuild_numbers_not_in_reference": sorted(reb_nums - ref_nums)[:40]},
        "validator": {"rules": val["rules"], "failures": [f for f in val["failures"] if f["status"] == "fail"][:60],
                      "stats": {k: val["stats"].get(k) for k in ("slides", "native_charts", "native_tables",
                                                                    "avg_body_words", "avg_title_words",
                                                                    "source_line_share", "scheme_color_share")}},
        "rebuild_pdf": str(reb_pdf),
    }


def run(ref_pdf: str, out_dir: str, pages: str | None = None, level: str = "idea", slides: int | None = None,
        client=None) -> dict:
    from engines.slides.pipeline import build_deck
    from meshpool_harness.core.client import LiveClient
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    client = client or LiveClient("forge_rebuild")
    tid = "rebuild-" + Path(ref_pdf).stem
    if slides is None:
        slides = min(15, measure_pdf(ref_pdf, pages)["summary"]["content_pages"])
    brief = make_brief(ref_pdf, pages, level, client, tid)
    (out / "brief.md").write_text(brief)
    request = (f"Build a {slides}-slide consulting deck for the client described in brief.md. Argue the governing "
               "idea with action titles, charts for the numbers, and a source line on every data slide.")
    res = build_deck(request, [str(out / "brief.md")], str(out / "rebuild.pptx"), client, tid)
    rep = {"reference": ref_pdf, "pages": pages, "level": level, "requested_slides": slides,
           "build": {k: res.get(k) for k in ("ok", "calls", "slides", "reason")}}
    if (out / "rebuild.pptx").exists():
        rep["score"] = score(ref_pdf, pages, str(out / "rebuild.pptx"), out, slides)
        import pymupdf as fitz
        n = fitz.open(ref_pdf).page_count
        contact_sheet(_png(ref_pdf, str(out / "ref"), _pages(pages, n)[:16]), str(out / "sheet-ref.png"))
        contact_sheet(_png(rep["score"]["rebuild_pdf"], str(out / "reb"))[:16], str(out / "sheet-rebuild.png"))
    (out / "report.json").write_text(json.dumps(rep, indent=1, default=str))
    return rep


if __name__ == "__main__":
    import argparse
    import warnings
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser()
    ap.add_argument("ref_pdf")
    ap.add_argument("out_dir")
    ap.add_argument("--pages")
    ap.add_argument("--level", default="idea", choices=["idea", "content"])
    ap.add_argument("--slides", type=int)
    a = ap.parse_args()
    r = run(a.ref_pdf, a.out_dir, a.pages, a.level, a.slides)
    s = r.get("score", {})
    print(json.dumps({"build": r["build"], "storyline": s.get("storyline_coverage"), "facts": {
        k: v for k, v in (s.get("fact_coverage") or {}).items() if k != "rebuild_numbers_not_in_reference"},
        "rules": (s.get("validator") or {}).get("rules"), "reference": s.get("reference"), "rebuild": s.get("rebuild")},
        indent=1, default=str))
