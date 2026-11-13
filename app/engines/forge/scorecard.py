"""Score every file against every rule, side by side: scorecard.csv.

    python -m engines.forge.scorecard manifest.json out.csv

manifest.json: [{"label": "...", "group": "hebbia|comparator|reference|ours", "path": "...", "pages": "53-76"?,
                 "visual": {"hierarchy": 3, ...}?}]
.pptx -> validate_pptx (P1-P18); .xlsx -> validate_xlsx (X1-X13); .pdf -> the subset of deck rules a PDF can show
(P4 fonts, P10 density, P11 titles, P13 source lines), marked "pdf:" because PDFs have no masters, placeholders or
chart parts. A missing file is a row of "not_supplied", never a pass.
"""
from __future__ import annotations

import csv
import json
import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

P_RULES = [f"P{i}" for i in range(1, 20)]
X_RULES = [f"X{i}" for i in range(1, 14)]
VISUAL = ["hierarchy", "white_space", "grid_discipline", "chart_clarity", "consultant_made"]
METRICS = ["slides_or_pages", "body_words_median", "title_words_median", "title_verb_share", "source_line_share",
           "scheme_color_share", "native_charts", "fonts"]
SYMBOL_FONTS = re.compile(r"(?i)wingdings|symbol|webdings|opensymbol")


def _pdf_row(path, pages, rules):
    from .measure_pdf import measure
    m = measure(path, pages)
    s = m["summary"]
    R = rules["pptx"]
    content = [p for p in m["pages"] if p.get("title_words") and (p.get("all_words", 0) >= 15 or p.get("raster_exhibit"))]
    row = {r: "n/a-pdf" for r in P_RULES}
    fams = {re.sub(r"(MT|PS|-.*)$", "", f) for f in s["fonts_top"] if not SYMBOL_FONTS.search(f)}
    row["P4"] = "pdf:" + ("pass" if len(fams) <= R["fonts"]["max_families"] else "fail")
    if content:
        over = sum(1 for p in content if p["words"] > R["density"]["max_body_words"]) / len(content)
        row["P10"] = f"pdf:{'pass' if over <= 0.1 else 'fail'} ({over:.0%} pages >{R['density']['max_body_words']}w)"
        t_ok = [p for p in content if p["title_words"] <= R["titles"]["max_words"] and
                (p["title_has_verb"] or not s["language_english"])]
        share = len(t_ok) / len(content)
        row["P11"] = f"pdf:{'pass' if share >= 0.9 else 'fail'} ({share:.0%} action titles)" if s["language_english"] \
            else f"pdf:unknown (non-English; {sum(1 for p in content if p['title_words'] <= 15) / len(content):.0%} <=15w)"
        src = s["source_line_share"] or 0
        lb = " lower bound" if (s.get("raster_exhibit_share") or 0) > 0.2 else ""
        row["P13"] = f"pdf:{'pass' if src >= 0.8 else 'fail'} ({src:.0%} source lines{lb})"
    met = {"slides_or_pages": s["pages"], "body_words_median": (s["body_words"] or {}).get("median"),
           "title_words_median": (s["title_words"] or {}).get("median"), "title_verb_share": s["title_with_verb_share"],
           "source_line_share": s["source_line_share"], "scheme_color_share": "n/a-pdf", "native_charts": "n/a-pdf",
           "fonts": "/".join(s["fonts_top"][:2])}
    return row, met, m["sha256"]


def _pptx_row(path, rules, side):
    from .validate_pptx import validate
    r = validate(path, rules, **side)
    st = r["stats"]
    words = sorted(s["body_words"] for s in r["measurement"]["slides"])
    titles = sorted(s["title_words"] for s in r["measurement"]["slides"] if s["title_words"])
    from .validate_pptx import _has_verb
    tl = [s["title"] for s in r["measurement"]["slides"] if s["title"]]
    met = {"slides_or_pages": st["slides"], "body_words_median": words[len(words) // 2] if words else None,
           "title_words_median": titles[len(titles) // 2] if titles else None,
           "title_verb_share": round(sum(map(_has_verb, tl)) / len(tl), 2) if tl else None,
           "source_line_share": st["source_line_share"], "scheme_color_share": st["scheme_color_share"],
           "native_charts": st["native_charts"], "fonts": "/".join(list(st["typefaces"])[:2])}
    return r["rules"], met, r["sha256"]


def _xlsx_row(path, rules, side):
    from .validate_xlsx import validate
    r = validate(path, rules, **side)
    st = r["stats"]
    return r["rules"], {"slides_or_pages": len(st["sheets"]), "fonts": f"{st['formulas']} formulas / "
                        f"{st['constants']} constants"}, r["sha256"]


def build(manifest: list[dict], out_csv: str, rules=None) -> list[dict]:
    from .common import load_rules
    rules = rules or load_rules()
    rows = []
    for item in manifest:
        p = item.get("path")
        base = {"label": item["label"], "group": item["group"], "file": Path(p).name if p else "", "sha256": ""}
        rule_cells, met = {}, {}
        if not p or not Path(p).exists():
            rule_cells = {r: "not_supplied" for r in P_RULES + X_RULES}
        else:
            if p.endswith(".pdf"):
                rule_cells, met, base["sha256"] = _pdf_row(p, item.get("pages"), rules)
            elif p.endswith(".pptx"):
                side = {k: item[k] for k in ("brief", "provenance", "storyline") if k in item}
                side["render"] = item.get("render", True)
                rule_cells, met, base["sha256"] = _pptx_row(p, rules, side)
            elif p.endswith(".xlsx"):
                side = {"recompute": item["recompute"]} if "recompute" in item else {}
                rule_cells, met, base["sha256"] = _xlsx_row(p, rules, side)
        row = dict(base)
        for r in P_RULES + X_RULES:
            row[r] = rule_cells.get(r, "")
        for m_ in METRICS:
            row[m_] = met.get(m_, "")
        for v in VISUAL:
            row["visual_" + v] = (item.get("visual") or {}).get(v, "not_run")
        row["sha256"] = base["sha256"][:16]
        rows.append(row)
        print(f"scored {item['label']}", file=sys.stderr)
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return rows


if __name__ == "__main__":
    man = json.loads(Path(sys.argv[1]).read_text())
    build(man, sys.argv[2])
