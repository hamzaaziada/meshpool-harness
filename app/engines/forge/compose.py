"""Composition for text-led slides: every slide carries a visual, and no block leaves dead space.

A slide whose evidence is words (argument_only, an exhibit_argument whose chart could not be drawn, the text
fallbacks) is laid out from the content it already has, never padded:
- 2-4 blocks that each lead with a sourced headline figure -> stat columns (the figure big, its sentence under it);
- blocks that are sequential steps (Step 1, Phase 2, Week 3, Q1 2027 ...) -> a process: numbered chevrons over
  the columns;
- otherwise -> one column per block (or one row per item), each marked by an icon chosen by its meaning
  (render.icon_name / render._icon), with hairlines between them.
The type steps up the scale until the blocks use the body; the so-what band follows the blocks at one gap and is as
tall as its text. `relayout` is the deterministic re-layout the build applies when a slide still shows dead space or
no visual after the content repairs.
"""
from __future__ import annotations

import re

from pptx.enum.dml import MSO_THEME_COLOR as TC
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

from . import render as R

# meaning -> a Lucide icon that exists in the bundled set; the first family whose words appear in the text wins
MEANINGS = (
    (r"\brisks?\b|threat|downside|exposure|conflict|critic|break the case", "triangle-alert"),
    (r"antitrust|regulat|clearance|approval|legal|lawsuit|compliance|governance", "scale"),
    (r"\beps\b|accreti|dilut|earnings per share", "chart-line"),
    (r"synerg", "combine"),
    (r"valuation|multiple|\bdcf\b|comps?\b|precedent|ev\s*/|premium|price per share|\bprice\b|offer", "gauge"),
    (r"debt|leverage|interest|financ|loan|bond|credit", "landmark"),
    (r"\bcash\b|capital|capex|fund|invest|cheque|check size|equity", "banknote"),
    (r"revenue|sales|backlog|bookings|pipeline|demand", "trending-up"),
    (r"decline|fall|drop|loss|shrink|dilutive", "trending-down"),
    (r"cost|margin|expense|spend", "receipt"),
    (r"customer|client|hyperscaler|user|tenant", "users"),
    (r"partner|deal\b|acqui|merger|takeover|buy\b|stake|ownership|shares", "handshake"),
    (r"integrat|combine|structure|licen", "puzzle"),
    (r"\bgw\b|power|energy|capacity|data cent|compute|gpu|chip|infrastructure", "zap"),
    (r"\btime|timing|week|month|quarter|year|date|deadline|schedule|by \d", "clock"),
    (r"confirm|verify|diligence|check|review|test", "search-check"),
    (r"plan|next step|action|roadmap|prepare|run\b", "list-checks"),
    (r"growth|scale|expand|grow", "trending-up"),
    (r"people|team|talent|hire|workforce|staff", "users"),
    (r"goal|target|aim|objective|recommend|decision|approve", "target"),
    (r"market|competit|rival|peer", "store"),
    (r"technology|software|platform|\bai\b|model", "cpu"),
)
SEQ = re.compile(r"(?i)^\W*(\*\*)?\s*(step\s*\d|phase\s*\d|stage\s*\d|wave\s*\d|week\s*\d|month\s*\d|day\s*\d|"
                 r"q[1-4]\b|h[12]\b|(19|20)\d\d\b|\d+[.)]\s|first\b|second\b|third\b|then\b|next\b|finally\b)")
MAX_ICONS = 4                     # rules.yaml icons.max_per_slide


def plain(t) -> str:
    return re.sub(r"\*\*|\^\w+", "", "" if t is None else str(t)).strip()


def meaning_icon(text: str, taken=()) -> str | None:
    """The icon for a block's meaning, or None when nothing in the text names one (no meaningless dots). Icons
    already used on the slide (`taken`) are skipped, so each block gets its own."""
    t = plain(text).lower()
    for pat, name in MEANINGS:
        if re.search(pat, t) and R.icon_name(name) not in taken:
            return R.icon_name(name)
    return None


def item_icons(items) -> list[str | None]:
    """One distinct icon per item: chosen by its lead first, then by its detail."""
    out: list[str | None] = []
    for it in items:
        ic = meaning_icon(it["lead"], out) or meaning_icon(" ".join(b["text"] for b in it["bullets"]), out)
        out.append(ic)
    return out


def headline_figure(text: str) -> tuple[str, str] | None:
    """The first bold phrase with a number: ("$132.5B", "backlog"). The figure as written, the phrase's words as
    its caption."""
    from .common import numbers as number_tokens
    for m in re.finditer(r"\*\*([^*]+)\*\*", text or ""):
        phrase = m.group(1)
        toks = number_tokens(phrase)
        if not toks:
            continue
        raw = toks[0][0]
        if len(raw) > 11:
            continue
        cap = re.sub(r"\s+", " ", phrase.replace(raw, " ")).strip(" ,:;-–—(")
        cap = re.sub(r"^(of|in|a|an|the|at|for)\s+", "", cap, flags=re.I)
        return raw, cap
    return None


def _items(s: dict) -> tuple[str, list[dict]]:
    """(intro, items): the blocks as items {lead, bullets, prov}; a single block's bullets become the items, its
    text the intro line."""
    blocks = [b for b in s.get("blocks") or [] if isinstance(b, dict) and (b.get("text") or b.get("bullets"))]
    norm = lambda b: b if isinstance(b, dict) else {"text": str(b)}  # noqa: E731
    if len(blocks) == 1 and len(blocks[0].get("bullets") or []) >= 2:
        intro = " ".join(x for x in (s.get("intro"), blocks[0].get("text")) if x)
        return intro, [{"lead": norm(b)["text"], "bullets": [], "prov": norm(b).get("prov")}
                       for b in blocks[0]["bullets"]]
    out = []
    for b in blocks:
        bl = [norm(x) for x in b.get("bullets") or []]
        lead = b.get("text") or (bl.pop(0)["text"] if bl else "")
        out.append({"lead": lead, "bullets": bl, "prov": b.get("prov") or next((x.get("prov") for x in bl
                                                                                 if x.get("prov")), None)})
    return s.get("intro") or "", out


def plan_markers(items: list[dict]) -> str:
    """'figures' when every item (2-4) leads with a sourced headline figure, 'process' when the items are
    sequential steps (3+), else 'icons'."""
    if 2 <= len(items) <= 4 and all(headline_figure(it["lead"]) and it.get("prov") for it in items):
        return "figures"
    if len(items) >= 3 and all(SEQ.match(it["lead"] or "") for it in items):
        return "process"
    return "icons"


def _band(ctx, s, sl, n, y, texts, dry=False) -> float:
    """The navy so-what band across the body, as tall as its text."""
    if not texts:
        return 0.0
    cbl = [{"text": t, "size": R.ARGUMENT["callout"] + 1, "bullet": len(texts) > 1, "space_before": 3,
            "color": TC.BACKGROUND_1} for t in texts[:3]]
    cw = R.RIGHT - R.LEFT - 0.5
    ch = min(1.7, R._need(cbl, cw) + 0.26)
    if not dry:
        band = R._fill(sl, R.LEFT, y, R.RIGHT - R.LEFT, ch, TC.TEXT_2, "ov_SoWhat")
        R._shape_text(band, cbl, ctx, n, (R.LEFT + 0.25, y + 0.06, cw, ch - 0.12), MSO_ANCHOR.MIDDLE)
    return ch


SIZES = ((0, 0), (3, 2), (3, 5), (5, 5))         # (lead, bullet) steps over the mode's intro/bullet sizes


def _col_blocks(it, z, figure):
    lead = it["lead"]
    out = []
    if lead:
        out.append({"text": lead, "size": R.ARGUMENT["intro"] + SIZES[z][0], "numbers": False, "prov": it.get("prov")})
    for b in it["bullets"]:
        out.append({"text": b["text"], "size": R.ARGUMENT["bullet"] + SIZES[z][1], "bullet": True,
                    "space_before": 4, "prov": b.get("prov")})
    return out


def argument_layout(ctx, s, sl, n):
    """argument_only, composed: a visual marker per block, the type grown to use the body, the band after it."""
    R._header(ctx, s, sl, n)
    intro, items = _items(s)
    co = [b["text"] if isinstance(b, dict) else str(b) for b in (s.get("callout") or {}).get("bullets") or []]
    notes = s.get("footnotes") or []
    notes_h = (0.14 * max(1, (len("    ".join(notes)) // 190) + 1) + 0.04) if notes else 0
    bottom = R.FOOT_RULE_Y - 0.06 - notes_h if notes else R.BODY_B
    if notes:
        ctx.notes_top = R.FOOT_RULE_Y - 0.03 - notes_h
    if not items:
        return
    band_h = _band(ctx, s, sl, n, 0, co, dry=True)
    y = R.BODY_Y + 0.05
    if intro:
        ib = [{"text": intro, "size": R.ARGUMENT["intro"] + 3, "numbers": False}]
        ih = R._need(ib, R.RIGHT - R.LEFT) + 0.05
        R._write(R._box(sl, R.LEFT, y, R.RIGHT - R.LEFT, ih, "Intro"), ib, ctx, n)
        y += ih + 0.15
    avail = bottom - y - (band_h + 0.3 if band_h else 0)
    mode = plan_markers(items)
    if 2 <= len(items) <= 4:
        used = _columns(ctx, s, sl, n, items, mode, y, avail)
    else:
        used = _rows(ctx, s, sl, n, items, y, avail)
    if band_h:
        _band(ctx, s, sl, n, min(y + used + 0.3, bottom - band_h), co)


def _spans(k):
    return {2: [(0, 11), (12, 23)], 3: [(0, 7), (8, 15), (16, 23)], 4: [(0, 5), (6, 11), (12, 17), (18, 23)]}[k]


def _columns(ctx, s, sl, n, items, mode, y, avail) -> float:
    """One column per item under its marker (figure, chevron or icon). Returns the height used."""
    k = len(items)
    cols = [R.span24(a, b) for a, b in _spans(k)]
    figs = [headline_figure(it["lead"]) for it in items] if mode == "figures" else [None] * k
    icons = item_icons(items)
    if mode == "icons" and sum(1 for i in icons if i) < k:
        mode = "badges" if sum(1 for i in icons if i) < 2 else "icons"   # one shared look per slide
    mark_h = {"figures": 0.95, "process": 0.62, "icons": 0.72, "badges": 0.62}[mode]
    best = None
    for z in range(len(SIZES)):
        need = max(R._need(_col_blocks(it, z, f), w) for it, f, (_, w) in zip(items, figs, cols))
        if need <= avail - mark_h or best is None:
            best = (z, need)
        if need > 0.9 * (avail - mark_h):
            break
    z, need = best
    body_h = min(avail - mark_h, need)
    for j, (it, (x, w)) in enumerate(zip(items, cols)):
        if mode == "figures":
            val, cap = figs[j]
            R._write(R._box(sl, x, y, w, 0.62, f"role_numeral_Figure{j + 1}", MSO_ANCHOR.BOTTOM),
                     [{"text": val, "size": 34 if R.DENSITY["mode"] != "present" else 40, "bold": True,
                       "heading": True, "numbers": False, "color": TC.ACCENT_1, "line_spacing": 1.0,
                       "prov": it.get("prov")}], ctx, n)
            if cap:
                R._write(R._box(sl, x, y + 0.62, w, 0.26, f"FigureLabel{j + 1}"),
                         [{"text": cap[:60], "size": R.T["label"], "bold": True, "numbers": False,
                           "color": TC.TEXT_2}])
        elif mode == "process":
            shp = R._fill(sl, x, y, w + (0.12 if j < k - 1 else 0), 0.46, TC.TEXT_2 if j == k - 1 else TC.ACCENT_4,
                          f"ov_Step{j + 1}", MSO_SHAPE.PENTAGON if j == 0 else MSO_SHAPE.CHEVRON)
            shp.adjustments[0] = 0.35
            R._shape_text(shp, [{"text": f"{j + 1}", "size": R.T["head"], "bold": True, "numbers": False,
                                 "color": TC.BACKGROUND_1, "align": PP_ALIGN.CENTER}], ctx, n,
                          (x + 0.2, y + 0.02, w - 0.3, 0.42), MSO_ANCHOR.MIDDLE)
        elif mode == "icons" and icons[j]:
            R._icon(sl, icons[j], x, y, 0.52, plain(it["lead"])[:60])
        else:
            d = 0.46
            b = R._fill(sl, x, y + 0.03, d, d, TC.TEXT_2, f"ov_Badge{j + 1}", MSO_SHAPE.OVAL)
            R._shape_text(b, [{"text": str(j + 1), "size": R.T["head"], "bold": True, "numbers": False,
                               "color": TC.BACKGROUND_1, "align": PP_ALIGN.CENTER}], ctx, n,
                          (x, y + 0.03, d, d), MSO_ANCHOR.MIDDLE)
        R._rule(sl, x, y + mark_h - 0.1, w, TC.TEXT_1, 0.75, f"ov_ColumnRule{j + 1}")
        bl, dropped = R._trim_to_fit(_col_blocks(it, z, figs[j]), w, body_h + 0.02, 11)
        R._cut_note(s, dropped)
        R._write(R._box(sl, x, y + mark_h, w, body_h + 0.05, f"Column{j + 1}"), bl, ctx, n)
    return mark_h + body_h


def _rows(ctx, s, sl, n, items, y, avail) -> float:
    """One row per item: its marker, its lead (bold) on the left, its detail on the right; hairlines between."""
    k = len(items)
    icons = item_icons(items)
    use_icons = k <= MAX_ICONS and all(icons)
    lx, lw = R.span24(1, 8)
    dx, dw = R.span24(9, 23)
    has_detail = any(it["bullets"] for it in items)
    if not has_detail:
        lx, lw = R.span24(1, 23)
    best = None
    for z in range(len(SIZES)):
        hs = []
        for it in items:
            lead = [{"text": it["lead"], "size": R.ARGUMENT["intro"] + SIZES[z][0], "numbers": False}]
            det = [{"text": b["text"], "size": R.ARGUMENT["bullet"] + SIZES[z][1], "bullet": True, "space_before": 3}
                   for b in it["bullets"]]
            hs.append(max(R._need(lead, lw), R._need(det, dw) if det else 0, 0.5) + 0.2)
        if sum(hs) <= avail or best is None:
            best = (z, hs)
        if sum(hs) > 0.9 * avail:
            break
    z, hs = best
    spare = max(0.0, avail - sum(hs))
    gap = min(0.3, spare / max(1, k))                      # a little air per row, never stretched rows
    ry = y
    for j, (it, h) in enumerate(zip(items, hs)):
        h += gap
        if use_icons:
            R._icon(sl, icons[j], R.LEFT, ry + 0.08, 0.44, plain(it["lead"])[:60])
        else:
            d = 0.4
            b = R._fill(sl, R.LEFT, ry + 0.08, d, d, TC.TEXT_2, f"ov_Badge{j + 1}", MSO_SHAPE.OVAL)
            R._shape_text(b, [{"text": str(j + 1), "size": R.T["body"], "bold": True, "numbers": False,
                               "color": TC.BACKGROUND_1, "align": PP_ALIGN.CENTER}], ctx, n,
                          (R.LEFT, ry + 0.08, d, d), MSO_ANCHOR.MIDDLE)
        lead = [{"text": it["lead"], "size": R.ARGUMENT["intro"] + SIZES[z][0], "numbers": False, "prov": it.get("prov")}]
        R._write(R._box(sl, lx, ry + 0.06, lw, h - 0.1, f"RowLead{j + 1}"), lead, ctx, n)
        if it["bullets"]:
            det = [{"text": b["text"], "size": R.ARGUMENT["bullet"] + SIZES[z][1], "bullet": True, "space_before": 3,
                    "prov": b.get("prov")} for b in it["bullets"]]
            det, dropped = R._trim_to_fit(det, dw, h - 0.1, 11)
            R._cut_note(s, dropped)
            R._write(R._box(sl, dx, ry + 0.06, dw, h - 0.1, f"RowDetail{j + 1}"), det, ctx, n)
        if j < k - 1:
            R._rule(sl, R.LEFT, ry + h, R.RIGHT - R.LEFT, TC.ACCENT_5, 0.5, f"ov_RowRule{j + 1}")
        ry += h
    return ry - y
