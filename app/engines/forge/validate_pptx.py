"""Validate a finished .pptx against the PowerPoint hard rules P1-P18 (rules.yaml). Deterministic; no model calls.

    python -m engines.forge.validate_pptx deck.pptx [--rules client.yaml] [--brief brief.json]
           [--provenance provenance.json] [--storyline storyline.json] [--no-render] [--out report.json]

Output: {"file", "sha256", "rules": {P1: pass|fail|warn|not_run, ...}, "failures": [...], "stats": {...}}
Each failure has a `message` an LLM can act on directly ("Slide 4, Body 2: text overflows by 3 lines at 14pt;
cut to 45 words.") and a `fix_scope` saying whether the content stage, the renderer or the template must fix it.
Exit code 1 when any error-severity failure remains.

Sidecars (written by the engine, optional for third-party decks):
  brief.json       {"slide_count": 12}
  provenance.json  {"slides": {"4": [{"text": "12.5%", "prov": "F12"}, ...]}, "supplied_images": ["image3.png"]}
  storyline.json   {"nodes": [{"id": "1.2", "title": "..."}]}
"""
from __future__ import annotations

import io
import itertools
import json
import math
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

from .common import load_rules, soffice_convert, summarize
from .common import failure as F
from .ooxml_check import check as ooxml_check
from .measure_pptx import measure, public

RULES = [f"P{i}" for i in range(0, 21)]

# a small verb lexicon + inflection rules: enough to tell "Revenue grew 12% on pricing" from "Revenue overview"
_VERBS = set("""
accelerate account achieve add affect allow assume avoid be become beat benefit boost break bring build buy can
capture change close come compete concentrate continue contribute convert could cover create cut decline decrease
deliver depend derive destroy double drive drop earn enable end enter erode exceed expand expect explain face fall
fail finance fit fix focus follow fund gain generate get give go grow halve have help hit hold improve include
increase invest is are was were will would lag lead leave lose lower make match may meet might miss move must need
offer offset open outpace outperform overtake own pay peak plan position present produce protect provide push raise
reach realise realize reduce reflect remain represent require reshape restore return rise run save scale secure see
sell shift should show shrink slow spend split stabilise stabilize stall start stay support sustain take target tie
trail transform trigger triple turn underperform unlock outgrow use vary widen win work yield lift narrow command
act adopt align allocate apply automate benchmark build cap centralise centralize consolidate cut defer deploy
digitise digitize divest double-down eliminate establish exit fund hire integrate launch merge modernise modernize
negotiate outsource partner pilot price prioritise prioritize prune redesign refocus reinvest renegotiate replace
reprice restructure rethink shut simplify standardise standardize stop streamline test upgrade
face shape matter drive lack outweigh hinge rest lie set keep put let
jump surge soar climb slip slide slump plunge tumble dip rebound recover edge outstrip top spike bottom flatten level
plateau cluster cost total average comprise outnumber dominate signal suggest indicate point mean imply contract
decelerate collapse explode balloon fade surpass eclipse rank remain persist concentrate account mark
compound span bracket weigh sit straddle clear""".split())
_AUX = {"is", "are", "was", "were", "be", "been", "has", "have", "had", "will", "would", "can", "could", "should",
        "must", "may", "might", "does", "do", "did"}


def _has_verb(title: str) -> bool:
    for w in re.findall(r"[A-Za-z']+", title.lower()):
        if w in _AUX or w in _VERBS:
            return True
        for suf, cut in (("ies", "y"), ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"), ("ing", ""), ("ing", "e"),
                         ("led", "l"), ("ped", "p"), ("ted", "t"), ("ned", "n"), ("ged", "g"), ("ped", "")):
            if w.endswith(suf) and w[: -len(suf)] + cut in _VERBS:
                return True
    irregular = {"grew", "fell", "rose", "won", "lost", "made", "drove", "led", "took", "gave", "held", "paid", "spent",
                 "sold", "built", "brought", "came", "went", "cut", "hit", "beat", "split", "set", "kept", "left", "met",
                 "drew", "sank", "shrank", "swung", "stood", "began", "became", "outgrew", "overtook", "underwent",
                 "saw", "found", "got", "ran", "spun", "slid", "bore"}
    return any(w in irregular for w in re.findall(r"[a-z]+", title.lower()))


_NUM = re.compile(r"(?<![\w.])[-+]?\$?\(?\d[\d,]*(?:\.\d+)?\)?\s?(?:%|pp|x|bn|b|m|mm|k|million|billion|thousand)?(?![\w])", re.I)


def _numbers(text: str, ignore: re.Pattern) -> list[tuple[str, float, str]]:
    """(raw, value, unit) for every number in text; years and small ordinals filtered by `ignore`."""
    out = []
    for m in _NUM.finditer(text):
        raw = m.group(0).strip()
        core = re.sub(r"[^\d.]", "", raw.split()[0] if " " in raw else raw)
        if not core or core == ".":
            continue
        if ignore.match(core) and not re.search(r"%|\$|bn|b$|m$|k$|x$|million|billion|thousand", raw, re.I):
            continue
        try:
            v = float(core)
        except ValueError:
            continue
        if raw.startswith("-") or raw.startswith("("):
            v = -v
        unit = (re.search(r"(%|pp|x|bn|b|mm|m|k|million|billion|thousand)\s*$", raw, re.I) or [None])[0]
        unit = unit.lower() if unit else ""
        out.append((raw, v, unit))
    return out


_SCALE = {"": 1, "k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}


def _candidates(v: float, unit: str) -> list[float]:
    """A quoted number may be stated in the chart's own units or scaled: compare against all plausible readings."""
    if unit in ("%", "pp"):
        return [v, v / 100]
    s = _SCALE.get(unit, 1)
    return sorted({v, v * s, v * s / 1e3, v * s / 1e6, v * s / 1e9})


def _close(a: float, b: float, tol: float) -> bool:
    if a == b:
        return True
    return abs(a - b) <= tol * max(abs(a), abs(b)) or abs(a - b) < 0.051 and max(abs(a), abs(b)) < 10


def _tokens(t: str) -> Counter:
    stop = {"the", "a", "an", "of", "to", "in", "and", "for", "on", "by", "with", "is", "are", "our", "its", "we", "from",
            "at", "as", "that", "this"}
    w = [x for x in re.findall(r"[a-z0-9%]+", t.lower()) if x not in stop]
    return Counter(w + [" ".join(p) for p in zip(w, w[1:])])


def _cos(a: Counter, b: Counter) -> float:
    num = sum(a[k] * b[k] for k in a)
    den = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return num / den if den else 0.0


def _grid(m: dict, R: dict) -> tuple[list[float], tuple[float, float]]:
    """Allowed vertical edges: column edges of the content area, the content area, title box edges and the slide."""
    W, _ = m["slide_size_in"]
    L = R["layout"]
    left, right = L["margin_in"], W - L["margin_in"]
    tmpl = m.get("_template_body")
    if tmpl:
        left, right = tmpl
    cols, gut = L["grid_columns"], L["gutter_in"]
    cw = (right - left - gut * (cols - 1)) / cols
    edges = {0.0, W, left, right}
    for i in range(cols):
        a = left + i * (cw + gut)
        edges.update({a, a + cw})
    for e in m.get("_template_edges", []):
        edges.add(e)
    return sorted(edges), (left, right)


def _template_geometry(path: str, m: dict):
    """Body and title placeholder edges from the slide master: the template's own grid wins over config."""
    from pptx import Presentation
    prs = Presentation(path)
    edges, body = [], None
    for lay in prs.slide_masters[0].slide_layouts:      # every layout placeholder edge is a template grid line
        for ph in lay.placeholders:
            if ph.left is not None:
                edges += [round(ph.left / 914400, 3), round((ph.left + ph.width) / 914400, 3)]
    for ph in prs.slide_masters[0].placeholders:
        if ph.left is None:
            continue
        x0, x1 = ph.left / 914400, (ph.left + ph.width) / 914400
        edges += [round(x0, 3), round(x1, 3)]
        t = ph._element.find(".//{http://schemas.openxmlformats.org/presentationml/2006/main}ph").get("type", "obj")
        if t in ("body", "obj"):
            body = (x0, x1)
    W = prs.slide_width / 914400
    if body and body[1] < 0.8 * W:          # master geometry does not match the slide size: the renderer ignored it
        m["_template_mismatch"] = True
        return
    m["_template_edges"] = edges
    if body:
        m["_template_body"] = body


def _pdf_spans(pdf: str) -> list[tuple[float, list[tuple[str, tuple]]]]:
    """(page width pt, [(text, (x0, top, x1, bottom))]) per page, via pypdfium2 (BSD/Apache; already bundled)."""
    import pypdfium2 as pdfium
    out = []
    doc = pdfium.PdfDocument(pdf)
    for page in doc:
        w, h = page.get_size()
        tp = page.get_textpage()
        spans = []
        for i in range(tp.count_rects()):
            l, b, r, t = tp.get_rect(i)
            txt = tp.get_text_bounded(l, b, r, t).strip()
            if txt:
                spans.append((txt, (l, h - t, r, h - b)))
        out.append((w, spans))
    doc.close()
    return out


def _within(q: dict, p: dict, share: float) -> bool:
    """q (a shape with area) lies at least `share` inside p."""
    area = q["w"] * q["h"]
    if area <= 0.0025:
        return False
    ix = min(q["x"] + q["w"], p["x"] + p["w"]) - max(q["x"], p["x"])
    iy = min(q["y"] + q["h"], p["y"] + p["h"]) - max(q["y"], p["y"])
    return ix > 0 and iy > 0 and ix * iy >= share * area


def _render_check(path: str, m: dict, R: dict, failures: list, stats: dict):
    """P1: render with LibreOffice; every rendered text span must sit inside some text box on its slide."""
    with tempfile.TemporaryDirectory() as td:
        try:
            pdf = soffice_convert(path, "pdf", td, R["render"]["timeout_s"])
        except Exception as e:
            failures.append(F("P1", "render", f"The deck could not be rendered by LibreOffice: {e}",
                              fix_scope="renderer", status="not_run" if "not installed" in str(e) else "fail"))
            return
        try:
            pages = _pdf_spans(str(pdf))
        except ImportError:
            failures.append(F("P1", "render_geometry", "pypdfium2 is not installed; rendered geometry was not checked.",
                              fix_scope="renderer", status="not_run"))
            return
        stats["rendered_pages"] = len(pages)
        if len(pages) != len(m["slides"]):
            failures.append(F("P1", "render_pages", f"LibreOffice rendered {len(pages)} pages for "
                              f"{len(m['slides'])} slides.", fix_scope="renderer"))
        tol = R["render"]["box_tolerance_in"]
        W, H = m["slide_size_in"]
        for (pw, spans), s in zip(pages, m["slides"]):
            k = W / pw
            texts = [sh for sh in s["shapes"] if sh["x"] is not None and sh["text"]]
            frames = [sh for sh in s["shapes"] if sh["x"] is not None and sh["role"] in ("chart", "table")]
            norm = lambda t: re.sub(r"\s+", "", t)
            outside, collide, off = [], [], []
            boxes, tspans = [], []
            for text, (x0, y0, x1, y1) in spans:
                bx0, by0, bx1, by1 = x0 * k, y0 * k, x1 * k, y1 * k
                if bx1 > W + 0.02 or by1 > H + 0.02 or bx0 < -0.02 or by0 < -0.02:
                    off.append(text[:30])
                key = norm(text)[:14]
                cx_, cy_ = (bx0 + bx1) / 2, (by0 + by1) / 2
                in_table = any(f["role"] == "table" and f["x"] - tol <= cx_ <= f["x"] + f["w"] + tol and
                               f["y"] - tol <= cy_ <= f["y"] + f["h"] + tol for f in frames)
                in_chart = any(f["role"] == "chart" and f["x"] - tol <= cx_ <= f["x"] + f["w"] + tol and
                               f["y"] - tol <= cy_ <= f["y"] + f["h"] + tol for f in frames)
                if in_table:
                    tspans.append((bx0, by0, bx1, by1, text))
                    continue
                if in_chart:
                    continue                          # chart labels are laid out by their frame
                owners = [sh for sh in texts if len(key) >= 8 and key in norm(sh["text"])]
                if not owners:                       # short spans, chart labels: must sit inside some text box or frame
                    inside = any(f["x"] - tol <= (bx0 + bx1) / 2 <= f["x"] + f["w"] + tol and
                                 f["y"] - tol <= (by0 + by1) / 2 <= f["y"] + f["h"] + tol for f in frames + texts)
                    if not inside:
                        outside.append(text[:30])
                    boxes.append((bx0, by0, bx1, by1, text))
                    continue
                inside_own = [sh for sh in owners if sh["x"] - tol <= bx0 and bx1 <= sh["x"] + sh["w"] + tol
                              and sh["y"] - tol <= by0 and by1 <= sh["y"] + sh["h"] + tol]
                own = min(owners, key=lambda sh: abs(sh["x"] - bx0) + abs(sh["y"] - by0))
                if not inside_own and (by1 > own["y"] + own["h"] + tol or by0 < own["y"] - tol or
                                       bx1 > own["x"] + own["w"] + tol):
                    outside.append(f"{text[:30]} ({own['name']})")
                boxes.append((bx0, by0, bx1, by1, text))
            for f in frames:                          # a table must render inside its own frame
                if f["role"] != "table":
                    continue
                mine = [t for t in tspans if f["x"] - tol <= (t[0] + t[2]) / 2 <= f["x"] + f["w"] + tol and t[1] >= f["y"] - tol]
                if mine and max(t[3] for t in mine) > f["y"] + f["h"] + tol:
                    outside.append(f"table rows run {max(t[3] for t in mine) - f['y'] - f['h']:.2f}in past the table frame")
            for a in boxes:                           # and table text must not collide with any other text
                for b in tspans:
                    ix = min(a[2], b[2]) - max(a[0], b[0])
                    iy = min(a[3], b[3]) - max(a[1], b[1])
                    if ix > 0.03 and iy > 0.04:
                        collide.append(f"'{a[4][:20]}' x table '{b[4][:20]}'")
            for i in range(len(boxes)):
                a = boxes[i]
                for b in boxes[i + 1:]:
                    ix = min(a[2], b[2]) - max(a[0], b[0])
                    iy = min(a[3], b[3]) - max(a[1], b[1])
                    if ix > 0.03 and iy > 0.04:
                        collide.append(f"'{a[4][:20]}' x '{b[4][:20]}'")
            if outside:
                failures.append(F("P1", "render_outside_box",
                                  f"Slide {s['n']}: after rendering, text runs past its box: {'; '.join(outside[:3])}. "
                                  "Cut words in that box.", slide=s["n"], measured=len(outside)))
            if collide:
                failures.append(F("P1", "render_collision", f"Slide {s['n']}: rendered text overlaps other text: "
                                  f"{'; '.join(collide[:3])}.", slide=s["n"], measured=len(collide), fix_scope="renderer"))
            if off:
                failures.append(F("P1", "render_off_slide", f"Slide {s['n']}: text renders off the slide: "
                                  f"{'; '.join(off[:3])}.", slide=s["n"], measured=len(off)))
        _dead_space(str(pdf), m, R, failures, stats)


def _proportions(path: str, R: dict, failures: list):
    """P20, from the file: thin content must not be disguised. Table rows are not stretched far past their text, and
    bars and columns are not slabs."""
    from pptx import Presentation
    from pptx.util import Emu
    from .common import font_file, wrap_lines
    Wd = R["whitespace"]
    f, _ = font_file("Arial", False, False, (("Arial", "Liberation Sans"),))
    prs = Presentation(path)
    for n, sl in enumerate(prs.slides, 1):
        for sh in sl.shapes:
            if sh.has_table:
                tbl = sh.table
                cw = [Emu(c.width).inches for c in tbl.columns]
                airy = 0
                for r_i, row in enumerate(tbl.rows):
                    if r_i == 0:
                        continue
                    need = 0.0
                    for ci, cell in enumerate(row.cells):
                        txt = cell.text_frame.text
                        sz = next((r.font.size.pt for p in cell.text_frame.paragraphs for r in p.runs if r.font.size), 12)
                        lines = wrap_lines(txt, max(0.3, cw[ci] - 0.12), sz, f, 0.97) if txt.strip() else 1
                        need = max(need, lines * sz * 1.15 / 72 + 0.045)
                    if Emu(row.height).inches > need + Wd["max_row_air_in"]:
                        airy += 1
                if airy:
                    failures.append(F("P20", "stretched_rows", f"Slide {n}, {sh.name}: {airy} table rows are stretched "
                                      f"more than {Wd['max_row_air_in']}in past their text. A short table needs a "
                                      "different composition (beside its argument), not taller rows.", slide=n,
                                      shape=sh.name, fix_scope="renderer"))
            if sh.name.startswith("ov_Panel") and sh.has_text_frame and sh.text_frame.text.strip():
                tf = sh.text_frame                         # the so-what panel fills the body; its text must too
                iw = Emu(sh.width).inches - Emu(tf.margin_left or 0).inches - Emu(tf.margin_right or 0).inches
                ih = Emu(sh.height).inches - Emu(tf.margin_top or 0).inches - Emu(tf.margin_bottom or 0).inches
                need = 0.0
                for p in tf.paragraphs:
                    if not p.text.strip():
                        continue
                    sz = 12.0                              # substance measured at body size: grown type is not
                    need += wrap_lines(p.text, max(0.3, iw - 0.25), sz, f, 0.97) * sz * 1.2 / 72 + 0.06  # content
                if ih > 1.2 and need < Wd.get("min_panel_fill", 0.2) * ih:
                    failures.append(F("P20", "dead_space", f"Slide {n}, {sh.name}: the takeaway panel is "
                                      f"{need / ih:.0%} text; the slide is thin. Add the evidence it is missing (a "
                                      "breakdown, a comparison, the implication) or merge it with its neighbour.",
                                      slide=n, shape=sh.name, measured=round(need / ih, 2)))
            if sh.has_chart:
                ch = sh.chart
                try:
                    ncat = len(ch.plots[0].categories)
                    ctype = str(ch.chart_type)
                except Exception:
                    continue
                if not ncat:
                    continue
                horiz = "BAR" in ctype
                if not (horiz or "COLUMN" in ctype):
                    continue
                gap = ch.plots[0].gap_width if ch.plots[0].gap_width is not None else 150
                nser = len(list(ch.plots[0].series)) if "CLUSTERED" in ctype else 1
                dim = (Emu(sh.height).inches if horiz else Emu(sh.width).inches) * 0.85
                thick = dim / ncat / (1 + gap / 100) / max(1, nser)
                limit = Wd["max_bar_in"] if horiz else Wd["max_column_in"]
                if thick > limit:
                    failures.append(F("P20", "oversized_bars", f"Slide {n}, {sh.name}: {'bars' if horiz else 'columns'} "
                                      f"about {thick:.1f}in thick (limit {limit}in) read as slabs. Size the chart to its "
                                      "data.", slide=n, shape=sh.name, fix_scope="renderer", measured=round(thick, 2),
                                      limit=limit))


def _dead_space(pdf_path: str, m: dict, R: dict, failures: list, stats: dict):
    """P20: no large empty block in a content slide's body (measured on the render)."""
    from .whitespace import page_empty, where
    import pypdfium2 as pdfium
    Wd = R["whitespace"]
    W, H = m["slide_size_in"]
    doc = pdfium.PdfDocument(pdf_path)
    worst = []
    exempt = set(Wd["exempt_archetypes"])
    for i, s in enumerate(m["slides"]):
        if i >= len(doc) or s.get("archetype") in exempt or s["n"] in stats.get("inferred_divider_slides", []):
            continue
        if not s.get("archetype") and _looks_divider(s):
            continue
        charts = [(sh["x"], sh["y"], sh["w"], sh["h"]) for sh in s["shapes"] if sh["role"] == "chart" and sh["x"] is not None]
        e = page_empty(doc[i], W, H, Wd["body_top"], Wd["body_bottom"], filled=charts)
        worst.append(round(e["share"], 2))
        limit = (Wd.get("max_empty_share_by_density") or {}).get(s.get("density"), Wd["max_empty_share"])
        if e["share"] > limit:
            failures.append(F("P20", "dead_space", f"Slide {s['n']}: {e['share']:.0%} of the body is one empty block "
                              f"({e['w_in']:.1f} x {e['h_in']:.1f} in, {where(e, W, H)}). Fill it with substance the "
                              "slide is missing (more evidence, a breakdown, a comparison, the implication), or merge "
                              "this slide with its neighbour. Never pad with filler.", slide=s["n"],
                              measured=round(e["share"], 2), limit=limit))
    stats["empty_share_by_slide"] = worst
    doc.close()


def _opens_cleanly(path, failures):
    """P0: the file opens in Office without a repair prompt (schema + cross-part checks, ooxml_check.py)."""
    try:
        r = ooxml_check(path)
    except Exception as e:  # a file we cannot even unzip
        failures.append(F("P0", "package", f"Not a readable Office package: {e}", fix_scope="renderer"))
        return
    for pr in r["problems"][:20]:
        failures.append(F("P0", pr["check"], f"{pr['part']}: {pr['detail']}. Office would offer to repair "
                          "this file.", fix_scope="renderer"))
    if r["schema_validation"] == "not_run":
        failures.append(F("P0", "schema", "Office schemas not installed; only package and relationship checks "
                          "ran (python -m engines.forge.ooxml_check --install-schemas).", status="not_run",
                          severity="info", fix_scope="renderer"))


def slide_density(s: dict, R: dict, override: str | None = None) -> str:
    """The density mode a slide is checked against: the caller's override, else the slide's own tag
    ("density:present" in its name), else the rules' default ("read": today's dense caps)."""
    D = R["density"]
    if override:
        return override
    m = re.search(D.get("mode_regex") or r"density:([a-z]+)", s.get("name") or "")
    mode = m.group(1) if m else None
    return mode if mode in (D.get("modes") or {}) or mode == "read" else D.get("default_mode", "read")


def density_caps(R: dict, mode: str) -> dict:
    """P10 caps for a density mode: the base (read) caps with the mode's keys merged over them."""
    from .common import _merge
    D = R["density"]
    over = (D.get("modes") or {}).get(mode)
    return _merge(D, over) if over else D


def validate(path: str, rules: dict | None = None, brief: dict | None = None, provenance: dict | None = None,
             storyline: dict | None = None, render: bool | None = None, density: str | None = None) -> dict:
    """density: check every slide against this mode's P10 caps and P20 threshold ("present", "balanced", "read");
    by default each slide's own density tag decides."""
    rules = rules or load_rules()
    R = rules["pptx"]
    failures: list[dict] = []
    _opens_cleanly(path, failures)
    try:
        m = measure(path, rules)
    except Exception as e:  # the package is too broken to read: report P0 and stop
        failures.append(F("P0", "package", f"python-pptx cannot read the deck ({type(e).__name__}: {e}).",
                          fix_scope="renderer"))
        status = {r: "not_run" for r in RULES}                # nothing else could be evaluated
        status["P0"] = "fail"
        return {"file": str(path), "sha256": None, "rules": status, "failures": failures,
                "stats": {}, "evidence": [], "measurement": {}}
    try:
        _template_geometry(path, m)
    except Exception:
        pass
    stats = dict(m["summary"])
    W, H = m["slide_size_in"]
    slides = m["slides"]
    theme_faces = {m["theme"]["major"], m["theme"]["minor"]} - {None}
    allowed_faces = theme_faces | set(R["fonts"]["allowlist"])
    title_exempt_arch = set(R["titles"]["exempt_archetypes"])
    exempt_layout = re.compile(R["titles"]["exempt_layouts"])
    ignore = re.compile(R["provenance"]["ignore_numbers"])
    prov = (provenance or {}).get("slides", {})
    supplied = set((provenance or {}).get("supplied_images", []))

    # ---------------- P1 render
    if render is None:
        render = R["render"]["enabled"]
    for s in slides:
        s["density"] = slide_density(s, R, density)
    if render:
        _render_check(path, m, R, failures, stats)
    else:
        failures.append(F("P1", "render", "Render check skipped (--no-render).", fix_scope="renderer", status="not_run"))
    try:
        _proportions(path, R, failures)
    except Exception as e:  # never let a proportion check break validation
        failures.append(F("P20", "proportions", f"Proportion check could not run: {e}", status="not_run",
                          severity="info", fix_scope="renderer"))

    for s in slides:
        n = s["n"]
        texts = [sh for sh in s["shapes"] if sh["text"]]
        is_title_exempt = (s["archetype"] in title_exempt_arch) or bool(exempt_layout.search(s["layout"] or ""))
        if not s["archetype"] and not is_title_exempt and _looks_divider(s):
            is_title_exempt = True          # undeclared cover/divider in a third-party deck: inferred, reported in stats
            stats.setdefault("inferred_divider_slides", []).append(n)

        # ---------------- P2 charts
        for sh in s["shapes"]:
            if sh["role"] == "picture" and re.search(R["charts"]["picture_name_hints"], f"{sh['name']} {sh['descr']}"):
                failures.append(F("P2", "chart_as_picture", f"Slide {n}, {sh['name']}: a picture stands in for a chart. "
                                  "Supply the chart data so it renders as a native chart.", slide=n, shape=sh["name"]))
        for c in s["charts"]:
            emb = c["embedded"]
            if not emb or not emb.get("blob"):
                failures.append(F("P2", "chart_no_workbook", f"Slide {n}, {c['shape']}: chart has no embedded data "
                                  "workbook.", slide=n, shape=c["shape"], fix_scope="renderer"))
                continue
            _chart_refs(n, c, failures)

        # ---------------- P3 tables
        for sh in s["shapes"]:
            if sh["role"] == "picture" and re.search(R["tables"]["picture_name_hints"], f"{sh['name']} {sh['descr']}") \
                    and sh["name"] not in supplied:
                failures.append(F("P3", "table_as_picture", f"Slide {n}, {sh['name']}: a picture stands in for a table. "
                                  "Supply rows and columns so it renders as a native table.", slide=n, shape=sh["name"]))
        if any(sh["role"] == "picture" for sh in s["shapes"]):
            failures.append(F("P3", "image_text_ocr", f"Slide {n}: images were not OCR-checked for embedded text "
                              "(no OCR engine configured).", slide=n, fix_scope="renderer", status="not_run",
                              severity="info"))

        # ---------------- P5 overflow and minimum sizes
        for sh in texts:
            ov = sh.get("overflow") or {}
            scale = sh.get("font_scale", 1.0)
            if ov.get("overflow_lines"):
                size = min(p["size"] for p in sh["paras"]) * scale
                words = len(sh["text"].split())
                keep = max(1, int(words * ov["avail_in"] / ov["need_in"]) - 1) if ov["need_in"] else words
                failures.append(F("P5", "text_overflow", f"Slide {n}, {sh['name']}: text overflows by "
                                  f"{ov['overflow_lines']} line{'s' if ov['overflow_lines'] > 1 else ''} at {size:g}pt; "
                                  f"cut to {keep} words.", slide=n, shape=sh["name"], measured=words, limit=keep))
            if ov.get("grows_to_in") and ov["grows_to_in"] > H - 0.05:
                failures.append(F("P5", "autogrow_off_slide", f"Slide {n}, {sh['name']}: the box grows past the slide "
                                  f"bottom ({ov['grows_to_in']:.2f}in of {H:.2f}in); cut the text.", slide=n,
                                  shape=sh["name"]))
            tf = R["text_fit"]
            body_floor = tf.get("min_dense_pt", tf["min_body_pt"]) if s["archetype"] in tf.get("dense_archetypes", []) \
                else tf["min_body_pt"]
            floor = {"title": tf["min_title_pt"], "title_inferred": tf["min_title_pt"], "footnote": tf["min_footnote_pt"],
                     "footer": tf["min_footnote_pt"]}.get(sh["role"], body_floor)
            small = [p["size"] * scale for p in sh["paras"] if p["size"] * scale < floor - 0.01]
            if small:
                failures.append(F("P5", "font_below_minimum", f"Slide {n}, {sh['name']}: text is {min(small):g}pt, below "
                                  f"the {floor}pt minimum for {sh['role']} text; cut words so it fits at full size.",
                                  slide=n, shape=sh["name"], measured=min(small), limit=floor))

        # ---------------- P6 colours
        bad = [v for v in s["srgb_values"] if v.upper() not in {e.upper() for e in R["colors"]["srgb_exceptions"]}]
        if R["colors"]["check_charts"]:
            bad += [v for c in s["charts"] for v in c["srgb_values"]
                    if v.upper() not in {e.upper() for e in R["colors"]["srgb_exceptions"]}]
        if bad:
            failures.append(F("P6", "hardcoded_color", f"Slide {n}: {len(bad)} hardcoded colours "
                              f"({', '.join(sorted(set(bad))[:5])}); use theme colour slots.", slide=n,
                              measured=len(bad), limit=0, fix_scope="renderer"))

        # ---------------- P7 master usage and grid snap
        if not is_title_exempt or not re.search(R["layout"]["title_exempt_layouts"], s["layout"] or ""):
            tph = [sh for sh in s["shapes"] if sh["role"] == "title" and sh["ph_type"] is not None]
            if not tph:
                failures.append(F("P7", "no_title_placeholder", f"Slide {n}: no filled title placeholder (layout "
                                  f"'{s['layout']}').", slide=n, fix_scope="renderer"))
        edges, (cl, cr) = _grid(m, R)
        tol = R["layout"]["snap_tolerance_in"]
        for sh in s["shapes"]:
            if sh["depth"] or sh["x"] is None or sh["role"] in ("footer", "line") or \
                    sh["name"].startswith(R["layout"]["overlap_declare_prefix"]):
                continue                      # declared overlays (labels, icons, callouts) sit where the data puts them
            if sh["ph_type"] is not None and not sh.get("own_xfrm"):
                continue                      # geometry inherited from the layout: on the template by definition
            off = []
            if "left" in R["layout"]["edge_check"] and min(abs(sh["x"] - e) for e in edges) > tol:
                off.append(f"left {sh['x']:.2f}in")
            if "right" in R["layout"]["edge_check"] and min(abs(sh["x"] + sh["w"] - e) for e in edges) > tol:
                off.append(f"right {sh['x'] + sh['w']:.2f}in")
            if off:
                failures.append(F("P7", "off_grid", f"Slide {n}, {sh['name']}: edge off the grid ({', '.join(off)}).",
                                  slide=n, shape=sh["name"], fix_scope="renderer"))

        # ---------------- P8 spacing and overlaps
        if len(s["left_edges"]) > R["layout"]["grid_columns"]:
            failures.append(F("P8", "left_edges", f"Slide {n}: {len(s['left_edges'])} distinct left edges, more than "
                              f"{R['layout']['grid_columns']} grid columns.", slide=n, fix_scope="renderer"))
        top = [sh for sh in s["shapes"] if sh["depth"] == 0 and sh["x"] is not None]
        for sh in top:
            if sh["role"] in ("footer",) or (sh["x"] <= 0.01 and sh["w"] >= W - 0.02) or \
                    sh["name"].startswith(R["layout"]["overlap_declare_prefix"]) or sh["x"] + sh["w"] >= W - 0.01:
                continue           # full-bleed bands and declared overlays (labels, callouts) are allowed
            if sh["x"] < cl - tol or sh["x"] + sh["w"] > cr + tol:
                if sh["role"] not in ("picture",) and not (sh["x"] <= 0.01 or sh["x"] + sh["w"] >= W - 0.01):
                    failures.append(F("P8", "outside_margin", f"Slide {n}, {sh['name']}: outside the safe margins.",
                                      slide=n, shape=sh["name"], fix_scope="renderer"))
        mn = R["layout"]["overlap_min_in"]
        for a, b in itertools.combinations(top, 2):
            if a["name"].startswith(R["layout"]["overlap_declare_prefix"]) or b["name"].startswith(R["layout"]["overlap_declare_prefix"]):
                continue
            ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            if ox <= mn or oy <= mn:
                continue
            if R["layout"]["allow_panel_containment"]:
                def contains(p, q):
                    return (not p["text"] and p["x"] <= q["x"] + 0.01 and p["y"] <= q["y"] + 0.01 and
                            p["x"] + p["w"] >= q["x"] + q["w"] - 0.01 and p["y"] + p["h"] >= q["y"] + q["h"] - 0.01)
                if contains(a, b) or contains(b, a):
                    continue
            failures.append(F("P8", "overlap", f"Slide {n}: {a['name']} overlaps {b['name']} "
                              f"({ox:.2f}in x {oy:.2f}in) without a declared overlay.", slide=n,
                              shape=f"{a['name']} x {b['name']}", fix_scope="renderer"))

        # ---------------- P9 icons and decoration
        icons = s["icons"]
        icap = (R["icons"].get("max_per_slide_by_archetype") or {}).get(s["archetype"], R["icons"]["max_per_slide"])
        if len(icons) > icap:
            failures.append(F("P9", "too_many_icons", f"Slide {n}: {len(icons)} icons; keep at most "
                              f"{icap}, only where they carry meaning.", slide=n, measured=len(icons), limit=icap))
        for ic in icons:
            if ic["set"] not in R["icons"]["allowed_sets"]:
                failures.append(F("P9", "icon_set", f"Slide {n}, {ic['shape']}: icon is not from the approved set "
                                  f"({', '.join(R['icons']['allowed_sets'])}).", slide=n, shape=ic["shape"],
                                  fix_scope="renderer"))
        if R["icons"]["forbid_gradients"] and s["gradients"]:
            failures.append(F("P9", "gradient", f"Slide {n}: {s['gradients']} gradient fills; use flat theme colours.",
                              slide=n, fix_scope="renderer"))
        if R["icons"]["forbid_decorative_lines"] and s["decorative_lines"]:
            failures.append(F("P9", "decorative_line", f"Slide {n}: {s['decorative_lines']} unconnected lines; remove "
                              "decorative lines.", slide=n, fix_scope="renderer"))
        if R["icons"]["forbid_emoji"] and s["emoji"]:
            failures.append(F("P9", "emoji", f"Slide {n}: remove {s['emoji']} emoji from the text.", slide=n))

        # ---------------- P10 density (caps per density mode: the slide's "density:<mode>" tag, or the call's)
        D = density_caps(R, slide_density(s, R, density))
        cap = (D.get("by_archetype") or {}).get(s["archetype"], D["max_body_words"])
        if s["body_words"] > cap:
            failures.append(F("P10", "body_words", f"Slide {n}: {s['body_words']} words of body text; cut to "
                              f"{cap} and move detail to the notes or appendix.", slide=n,
                              measured=s["body_words"], limit=cap))
        bcap = (D.get("bullets_by_archetype") or {}).get(s["archetype"], D["max_bullets"])
        if s["bullets"] > bcap:
            failures.append(F("P10", "bullets", f"Slide {n}: {s['bullets']} bullets; keep {bcap} or fewer.",
                              slide=n, measured=s["bullets"], limit=bcap))
        if s["bullet_levels"] > D["max_bullet_levels"]:
            failures.append(F("P10", "bullet_levels", f"Slide {n}: {s['bullet_levels']} bullet levels; flatten to "
                              f"{D['max_bullet_levels']}.", slide=n, measured=s["bullet_levels"],
                              limit=D["max_bullet_levels"]))

        # ---------------- P11 action titles
        T = R["titles"]
        if not is_title_exempt:
            t = s["title"]
            tsh = next((sh for sh in s["shapes"] if sh["role"] in ("title", "title_inferred")), None)
            if not t:
                failures.append(F("P11", "no_title", f"Slide {n}: no title. Write a one-sentence takeaway title.", slide=n))
            else:
                if s["title_words"] > T["max_words"]:
                    failures.append(F("P11", "title_words", f"Slide {n}: title has {s['title_words']} words; cut to "
                                      f"{T['max_words']}.", slide=n, measured=s["title_words"], limit=T["max_words"]))
                if T["require_verb"] and not _has_verb(t):
                    failures.append(F("P11", "title_no_verb", f"Slide {n}: title \"{t[:80]}\" is a topic label. "
                                      "Rewrite it as a sentence that states the takeaway, with a verb.", slide=n))
                if tsh and (tsh.get("overflow") or {}).get("lines", 0) > T["max_lines"]:
                    failures.append(F("P11", "title_lines", f"Slide {n}: title runs to {tsh['overflow']['lines']} lines; "
                                      f"keep it to {T['max_lines']}.", slide=n, measured=tsh["overflow"]["lines"],
                                      limit=T["max_lines"]))

        # ---------------- P13 provenance and source line
        P = R["provenance"]
        sourced = {str(e.get("text")).strip() for e in prov.get(str(n), []) if e.get("prov")}
        sourced_vals = []
        for e in prov.get(str(n), []):
            for _, v, u in _numbers(str(e.get("text", "")), ignore):
                sourced_vals += _candidates(v, u)
        nums = []
        for sh in s["shapes"]:
            if sh["role"] in ("footer", "footnote") or not sh["text"]:
                continue
            nums += _numbers(sh["text"], ignore)
        unsourced = [raw for raw, v, u in nums if raw not in sourced and not any(
            _close(c, sv, 1e-9) for c in _candidates(v, u) for sv in sourced_vals)]
        if nums and provenance is None:
            failures.append(F("P13", "no_provenance", f"Slide {n}: {len(nums)} numbers and no provenance map; every "
                              "number must trace to a source.", slide=n, status="not_run", severity="error"))
        elif unsourced:
            failures.append(F("P13", "unsourced_number", f"Slide {n}: numbers without a source: "
                              f"{', '.join(unsourced[:6])}. Cite a provenance ID or replace with a gap marker.",
                              slide=n, measured=len(unsourced), limit=0))
        gaps = sum(sh["text"].count(P["gap_marker"]) for sh in s["shapes"] if sh["text"])
        if gaps:
            failures.append(F("P13", "gap_marker", f"Slide {n}: {gaps} blocked number(s) shown as gap markers; supply "
                              "a sourced value.", slide=n, measured=gaps))
        is_data = bool(s["charts"] or s["tables"] or nums)
        if P["require_source_line"] and is_data and not is_title_exempt:
            sl = s["source_line"]
            if not sl["present"]:
                failures.append(F("P13", "no_source_line", f"Slide {n}: data slide without a source line. Add "
                                  "'Source: ...' naming where the numbers come from.", slide=n))
            elif sl["y"] is not None and sl["y"] < H * P["source_band_top_frac"]:
                failures.append(F("P13", "source_position", f"Slide {n}: the source line is not at the bottom of the "
                                  "slide.", slide=n, fix_scope="renderer"))

        # ---------------- P14 text agrees with chart
        if s["charts"]:
            vals = [v for c in s["charts"] for se in c["series"] for v in se["values"]]
            derived = set(vals)
            if R["agreement"]["allow_derived"]:
                for a, b in itertools.permutations(vals, 2):
                    derived.add(a - b)
                    if b:
                        derived.update({(a - b) / b, (a - b) / b * 100, a / b, a / b * 100})
                for c in s["charts"]:
                    for se in c["series"]:
                        tot = sum(se["values"])
                        if len(se["values"]) >= 3:      # totals and shares only for compositions, not 2-bar pairs
                            derived.add(tot)
                        if tot and len(se["values"]) >= 3:
                            derived.update({v / tot * 100 for v in se["values"]} | {v / tot for v in se["values"]})
                        if len(se["values"]) >= 2 and se["values"][0] > 0 and se["values"][-1] > 0:
                            k = len(se["values"]) - 1
                            cagr = (se["values"][-1] / se["values"][0]) ** (1 / k) - 1
                            derived.update({cagr, cagr * 100})
            tol = R["agreement"]["rel_tolerance"]
            quoted = []
            for sh in s["shapes"]:
                if sh["role"] in ("title", "title_inferred", "body", "subtitle") and sh["text"]:
                    quoted += _numbers(sh["text"], ignore)
            mismatch = [raw for raw, v, u in quoted
                        if not any(_close(c, d, tol) for c in _candidates(abs(v), u) for d in {abs(x) for x in derived})]
            if mismatch:
                failures.append(F("P14", "text_chart_mismatch", f"Slide {n}: {', '.join(mismatch[:5])} in the text "
                                  "do not match the chart data. Quote the chart's values, rounded as shown.", slide=n,
                                  measured=len(mismatch), limit=0))

        # ---------------- P17 editability
        if s["max_group_depth"] > R["editability"]["max_group_depth"]:
            failures.append(F("P17", "group_depth", f"Slide {n}: groups nested {s['max_group_depth']} levels deep; "
                              f"flatten to {R['editability']['max_group_depth']}.", slide=n, fix_scope="renderer"))
        for lk in s["locks"]:
            bad_l = [x for x in lk["locks"] if x in R["editability"]["forbidden_locks"]]
            if bad_l:
                failures.append(F("P17", "locked", f"Slide {n}, {lk['shape']}: locked ({', '.join(bad_l)}).", slide=n,
                                  shape=lk["shape"], fix_scope="renderer"))

        boxes = [sh for sh in s["shapes"] if sh["depth"] == 0 and sh["x"] is not None]
        for tb in boxes:                                 # text laid over a box instead of written into it
            if not tb["text"].strip() or tb["filled"] or tb["role"] in ("title", "footer", "footnote", "table", "chart"):
                continue
            for bx in boxes:
                if bx is tb or not bx["filled"] or bx["text"].strip() or bx["role"] in ("picture", "icon", "chart"):
                    continue
                if bx["w"] * bx["h"] > 0.6 * W * H:          # a full-slide background is not a text container
                    continue
                ix = min(tb["x"] + tb["w"], bx["x"] + bx["w"]) - max(tb["x"], bx["x"])
                iy = min(tb["y"] + tb["h"], bx["y"] + bx["h"]) - max(tb["y"], bx["y"])
                if ix > 0 and iy > 0 and ix * iy >= R["editability"]["text_in_shape_overlap"] * tb["w"] * tb["h"]:
                    if any(o is not bx and o is not tb and _within(o, bx, R["editability"]["text_in_shape_overlap"])
                           for o in boxes if o["role"] != "line"):
                        continue                         # a card holding several elements, not one text's shape
                    failures.append(F("P17", "text_over_shape", f"Slide {n}: '{tb['name']}' is a text box laid over "
                                      f"'{bx['name']}'. Write the text into the shape itself so it moves and edits "
                                      "with it.", slide=n, shape=tb["name"], fix_scope="renderer"))
                    break

        # ---------------- P18 accessibility
        for sh in s["shapes"]:
            if sh["role"] in ("picture", "icon", "chart") and (not sh["descr"].strip() or re.search(
                    r"(?i)^[\w ./-]+\.(png|jpe?g|gif|svg|emf|wmf|bmp|tiff?)$", sh["descr"].strip())):
                failures.append(F("P18", "alt_text", f"Slide {n}, {sh['name']}: no alt text. Describe what it shows "
                                  "in one sentence.", slide=n, shape=sh["name"]))
            if sh["role"] == "icon" and sh["descr"].startswith(R["icons"]["tag_prefix"]) and sh["descr"].count(":") < 3:
                pass  # icon tag alone is a set label, not a description; covered when the renderer adds ":<meaning>"
        ordered = [sh for sh in s["shapes"] if sh["depth"] == 0 and sh["text"] and sh["role"] not in ("footer", "footnote")]
        first_title = next((i for i, sh in enumerate(ordered) if sh["role"] in ("title", "title_inferred")), None)
        inv = 0
        if first_title not in (None, 0):
            inv += 1
        for a, b in itertools.combinations(ordered, 2):
            if a["x"] is None or b["x"] is None:
                continue
            h_overlap = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]) > 0.1
            if h_overlap and b["y"] + b["h"] <= a["y"] - 0.05:
                inv += 1
        if inv > R["accessibility"]["max_reading_order_inversions"]:
            failures.append(F("P18", "reading_order", f"Slide {n}: {inv} reading-order inversions (title must come "
                              "first, then top to bottom).", slide=n, measured=inv, fix_scope="renderer"))

    if m.get("_template_mismatch"):
        failures.append(F("P7", "master_ignored", "The slide master's placeholders do not match the slide size; slides "
                          "are drawn outside the template's layouts.", fix_scope="renderer"))

    # ---------------- P4 fonts (deck level)
    faces = {f for f in m["typefaces"] if f}
    off = sorted(faces - allowed_faces) if allowed_faces else []
    if off:
        failures.append(F("P4", "font_not_allowed", f"Fonts outside the theme/allowlist: {', '.join(off)}.",
                          fix_scope="renderer"))
    if len(faces) > R["fonts"]["max_families"]:
        failures.append(F("P4", "font_families", f"{len(faces)} font families ({', '.join(sorted(faces))}); use at most "
                          f"{R['fonts']['max_families']}.", fix_scope="renderer", measured=len(faces),
                          limit=R["fonts"]["max_families"]))
    # sizes per role are counted per density mode: a forge slide tagged "density:<mode>" (or the call's override) is
    # held to that mode's cap; untagged slides (reference / hand-made decks) share the base cap
    tag_re = re.compile(R["density"].get("mode_regex") or r"density:([a-z]+)")
    by_mode: dict = {}
    for s in slides:
        mode = s["density"] if (density or tag_re.search(s.get("name") or "")) else None
        for role, c in (s.get("font_sizes") or {}).items():
            by_mode.setdefault(mode, {}).setdefault(role, set()).update(c)
    mode_caps = R["fonts"].get("max_sizes_per_role_by_density") or {}
    for mode, roles in by_mode.items():
        for role, sizes in roles.items():
            cap = (mode_caps.get(mode) or {}).get(role, R["fonts"]["max_sizes_per_role"])
            if len(sizes) > cap:
                hist = [str(z) for z in sorted(sizes)]
                where = f" on {mode}-density slides" if mode else ""
                failures.append(F("P4", "font_sizes", f"{role} text uses {len(hist)} sizes{where} ({', '.join(hist)}pt); "
                                  f"use at most {cap}.", fix_scope="renderer", measured=len(hist), limit=cap))
    if R["fonts"]["require_embedding"] and not m["embedded_fonts"]:
        failures.append(F("P4", "fonts_not_embedded", "Fonts are not embedded.", fix_scope="template"))

    # ---------------- P12 storyline
    S = R["storyline"]
    content = [s for s in slides if s["title"] and s["archetype"] not in title_exempt_arch
               and not exempt_layout.search(s["layout"] or "")]
    if S["require_outline_node"]:
        nodes = {nd["id"] for nd in (storyline or {}).get("nodes", [])}
        for s in content:
            if not s["node"]:
                failures.append(F("P12", "no_outline_node", f"Slide {s['n']}: not mapped to an outline node.",
                                  slide=s["n"], fix_scope="renderer"))
            elif nodes and s["node"] not in nodes:
                failures.append(F("P12", "unknown_node", f"Slide {s['n']}: node {s['node']} is not in the approved "
                                  "outline.", slide=s["n"]))
    for a, b in itertools.combinations(content, 2):
        sim = _cos(_tokens(a["title"]), _tokens(b["title"]))
        if sim > S["max_title_similarity"]:
            failures.append(F("P12", "repeated_point", f"Slides {a['n']} and {b['n']} make the same point (title "
                              f"similarity {sim:.2f}). Merge them or sharpen the second title.", slide=b["n"],
                              measured=round(sim, 2), limit=S["max_title_similarity"]))

    # ---------------- P15 archetypes
    A = R["archetypes"]
    run, prev = 0, None
    for s in slides:
        if not s["archetype"]:
            failures.append(F("P15", "no_archetype", f"Slide {s['n']}: no declared archetype.", slide=s["n"],
                              fix_scope="renderer"))
        run = run + 1 if s["archetype"] and s["archetype"] == prev else 1
        prev = s["archetype"]
        if s["archetype"] and run > A["max_consecutive_same"]:
            failures.append(F("P15", "archetype_repeat", f"Slide {s['n']}: archetype '{s['archetype']}' used {run} times "
                              "in a row; pick the archetype that best proves this title.", slide=s["n"]))

    # ---------------- P16 slide count
    want = (brief or {}).get("slide_count")
    app_re = re.compile(R["slide_count"]["appendix_section_regex"])
    appendix_ids = {i for sec in m["sections"] if app_re.search(sec["name"] or "") for i in sec["slide_ids"]}
    main = [s for s in slides if s["slide_id"] not in appendix_ids]
    stats["main_slides"] = len(main)
    if want is None:
        failures.append(F("P16", "slide_count", "No brief slide count supplied; count not checked.", status="not_run",
                          severity="info"))
    elif len(main) != want:
        failures.append(F("P16", "slide_count", f"The main section has {len(main)} slides; the brief asks for {want}. "
                          "Move backup material to the appendix section.", measured=len(main), limit=want))
    B = R["bookends"]
    if (brief or {}).get("opening") and slides and slides[0].get("archetype") not in B["opening_archetypes"]:
        failures.append(F("P16", "opening_slide", "The brief asks for an opening (title) slide and slide 1 is not one. "
                          "Add a cover slide first: title, subtitle, date.", slide=1))
    if (brief or {}).get("closing") and slides and slides[-1].get("archetype") not in B["closing_archetypes"]:
        failures.append(F("P16", "closing_slide", "The brief asks for a closing slide and the last slide is not one. "
                          "End with a 'closing' slide: the next steps (action, detail, owner/timing) or the close the "
                          "brief names.", slide=len(slides)))

    # ---------------- P19 cross-slide consistency
    K = R["consistency"]
    body_slides = [s for s in content if s.get("consistency") and s["n"] not in stats.get("inferred_divider_slides", [])]
    if len(body_slides) >= K["min_slides"]:
        tol = K["geometry_tol_in"]

        def spread(key, idx=None):
            vals = [(s["n"], s["consistency"][key] if idx is None else s["consistency"][key][idx])
                    for s in body_slides if s["consistency"].get(key) is not None]
            if len(vals) < 2:
                return None, []
            mode = Counter(round(v, 2) for _, v in vals).most_common(1)[0][0]
            return mode, [n for n, v in vals if abs(v - mode) > tol]
        for i, name in enumerate(("left edge",)):      # top, width and height vary with subtitle and archetype
            mode, odd = spread("title_box", i)
            if odd:
                failures.append(F("P19", "title_position", f"Title {name} differs from the deck's {mode:.2f}in on slides "
                                  f"{', '.join(map(str, odd[:8]))}. Every title sits in the same place.",
                                  slide=odd[0], measured=len(odd), limit=0, fix_scope="renderer"))
        sizes = Counter(s["consistency"]["title_size"] for s in body_slides if s["consistency"]["title_size"])
        if len(sizes) > K["title_sizes_max"]:
            mode = sizes.most_common(1)[0][0]
            odd = [s["n"] for s in body_slides if s["consistency"]["title_size"] not in (None, mode)]
            failures.append(F("P19", "title_size", f"Titles use {len(sizes)} sizes ({', '.join(f'{x:g}' for x in sizes)}pt); "
                              f"slides {', '.join(map(str, odd[:8]))} differ from {mode:g}pt. Shorten those titles "
                              "instead of shrinking them.", slide=odd[0] if odd else None, measured=len(sizes),
                              limit=K["title_sizes_max"]))
        tfaces = {s["consistency"]["title_face"] for s in body_slides if s["consistency"]["title_face"]}
        if len(tfaces) > K["title_faces_max"]:
            failures.append(F("P19", "title_face", f"Titles use {len(tfaces)} typefaces ({', '.join(sorted(tfaces))}).",
                              fix_scope="renderer", measured=len(tfaces), limit=K["title_faces_max"]))
        bfaces = {f for s in body_slides for f in s["consistency"]["body_faces"]}
        if len(bfaces) > K["body_faces_max"]:
            failures.append(F("P19", "body_face", f"Body text uses {len(bfaces)} typefaces ({', '.join(sorted(bfaces))}).",
                              fix_scope="renderer", measured=len(bfaces), limit=K["body_faces_max"]))
        mode, odd = spread("content_left")
        if odd:
            failures.append(F("P19", "content_margin", f"Content starts at a different left margin from the deck's "
                              f"{mode:.2f}in on slides {', '.join(map(str, odd[:8]))}.", slide=odd[0],
                              measured=len(odd), limit=0, fix_scope="renderer"))
        mode, odd = spread("source_y")
        if odd:
            failures.append(F("P19", "source_position", f"Source line sits at a different height from the deck's "
                              f"{mode:.2f}in on slides {', '.join(map(str, odd[:8]))}.", slide=odd[0],
                              fix_scope="renderer"))
        cl = {x for s in body_slides for x in s["consistency"]["chart_label_sizes"]}
        if len(cl) > K["chart_label_sizes_max"]:
            failures.append(F("P19", "chart_labels", f"Chart labels use {len(cl)} sizes ({', '.join(f'{x:g}' for x in sorted(cl))}pt).",
                              fix_scope="renderer", measured=len(cl), limit=K["chart_label_sizes_max"]))
    else:
        failures.append(F("P19", "consistency", f"Only {len(body_slides)} content slides; cross-slide consistency not "
                          "checked.", status="not_run", severity="info"))

    rules_status = summarize(failures, RULES)
    return {"file": str(path), "sha256": m["sha256"], "rules": rules_status, "failures": failures, "stats": stats,
            "evidence": m["evidence"], "measurement": public(m)}


def _looks_divider(s: dict) -> bool:
    """Cover or section divider without a declared archetype: little text, no chart or table, one large headline."""
    if s["charts"] or s["tables"] or s["body_words"] > 30:
        return False
    sizes = [p["size"] for sh in s["shapes"] for p in sh["paras"]]
    return bool(sizes) and max(sizes) >= 32


def _chart_refs(n: int, c: dict, failures: list):
    """Every series formula must point at cells in the embedded workbook, and the cached values must match them."""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(c["embedded"]["blob"]), data_only=True)
    except Exception as e:
        failures.append(F("P2", "chart_workbook_unreadable", f"Slide {n}, {c['shape']}: embedded workbook unreadable "
                          f"({type(e).__name__}).", slide=n, shape=c["shape"], fix_scope="renderer"))
        return
    for se in c["series"]:
        if se["literal"] or not se["ref"]:
            failures.append(F("P2", "series_literal", f"Slide {n}, {c['shape']}: series '{se['name']}' holds literal "
                              "values, not cell references.", slide=n, shape=c["shape"], fix_scope="renderer"))
            continue
        ref = se["ref"].replace("$", "")
        sheet, _, rng = ref.rpartition("!")
        sheet = sheet.strip("'")
        if sheet not in wb.sheetnames:
            failures.append(F("P2", "series_ref", f"Slide {n}, {c['shape']}: series '{se['name']}' points at missing "
                              f"sheet '{sheet}'.", slide=n, shape=c["shape"], fix_scope="renderer"))
            continue
        try:
            cells = [x for row in wb[sheet][rng] for x in (row if isinstance(row, tuple) else (row,))] \
                if ":" in rng else [wb[sheet][rng]]
        except Exception:
            failures.append(F("P2", "series_ref", f"Slide {n}, {c['shape']}: series ref {se['ref']} does not resolve.",
                              slide=n, shape=c["shape"], fix_scope="renderer"))
            continue
        got = [x.value for x in cells]
        if len(got) != len(se["values"]) or any(
                not isinstance(g, (int, float)) or abs(g - v) > 1e-9 * max(1, abs(v)) for g, v in zip(got, se["values"])):
            failures.append(F("P2", "series_cache_mismatch", f"Slide {n}, {c['shape']}: series '{se['name']}' cached "
                              f"values differ from the embedded cells {se['ref']}.", slide=n, shape=c["shape"],
                              fix_scope="renderer"))


def report_text(res: dict, limit: int = 40) -> str:
    """Compact text for a repair prompt: only error/warn failures, content-scope first."""
    fs = [f for f in res["failures"] if f["status"] == "fail"]
    fs.sort(key=lambda f: (f["fix_scope"] != "content", f.get("slide") or 0))
    lines = [f"[{f['rule']}/{f['fix_scope']}] {f['message']}" for f in fs[:limit]]
    head = " ".join(f"{k}={v}" for k, v in res["rules"].items())
    return head + "\n" + "\n".join(lines)


def _load(p):
    return json.loads(Path(p).read_text()) if p else None


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("pptx")
    ap.add_argument("--rules")
    ap.add_argument("--brief")
    ap.add_argument("--provenance")
    ap.add_argument("--storyline")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--text", action="store_true", help="print the compact repair text instead of JSON")
    ap.add_argument("--density", choices=["present", "balanced", "read"],
                    help="check against this density mode (default: each slide's own density tag, else read)")
    a = ap.parse_args()
    res = validate(a.pptx, load_rules(a.rules), _load(a.brief), _load(a.provenance), _load(a.storyline),
                   render=False if a.no_render else None, density=a.density)
    res.pop("measurement")
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1, default=str))
    print(report_text(res) if a.text or a.out else json.dumps(res, indent=1, default=str))
    sys.exit(1 if any(v == "fail" for v in res["rules"].values()) else 0)
