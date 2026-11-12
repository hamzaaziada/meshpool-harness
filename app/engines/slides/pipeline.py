"""Token-efficient deck pipeline (no agent loop): facts (code) -> storyline (1 call) -> spec (1 call) -> deterministic
render + QA + writing + number checks -> targeted repair of failing slides only (small calls, slide-scoped context).

Typical cost: 2-4 model calls per deck, versus 15-30 turns for an agent loop.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from engines.slides import qa  # noqa: E402
from engines.slides.renderer import RenderError, render  # noqa: E402
from engines.slides.spec import TYPES  # noqa: E402
from meshpool_harness.core.readability import check as plain  # noqa: E402

MODEL = __import__("os").environ.get("MH_MODEL", "deepseek-v4.1-flash")


# ------------------------------------------------------------------ facts (model-free)
def facts_from_files(files: list[str], max_facts: int = 60) -> list[dict]:
    """Compute a compact, numbered fact sheet from tabular inputs. Every number in the deck must come from here."""
    import pandas as pd
    facts = []

    def add(text, value, unit=""):
        facts.append({"id": f"F{len(facts) + 1}", "text": text, "value": value, "unit": unit})

    for f in files:
        p = Path(f)
        try:
            if p.suffix.lower() in (".csv", ".tsv"):
                df = pd.read_csv(p, sep="\t" if p.suffix.lower() == ".tsv" else ",")
            elif p.suffix.lower() in (".xlsx", ".xls"):
                df = pd.read_excel(p)
            else:
                continue
        except Exception:
            continue
        add(f"{p.name}: rows", int(len(df)))
        num = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        cat = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c]) and 1 < df[c].nunique() <= 12]
        dates = [c for c in df.columns if re.search(r"date|month|period|year|quarter", str(c), re.I)]
        for n in num[:4]:
            add(f"total {n}", round(float(df[n].sum()), 2))
            add(f"average {n}", round(float(df[n].mean()), 2))
        for c in cat[:2]:
            for n in num[:2]:
                g = df.groupby(c)[n].sum().sort_values(ascending=False)
                tot = float(g.sum()) or 1.0
                for k, v in g.head(6).items():
                    add(f"{n} for {c}={k}", round(float(v), 2))
                    add(f"share of {n} for {c}={k}", round(100 * float(v) / tot, 1), "%")
        for d in dates[:1]:
            try:
                s = df.assign(_p=pd.to_datetime(df[d], errors="coerce").dt.to_period("M").astype(str))
                for n in num[:1]:
                    g = s.groupby("_p")[n].sum().sort_index()
                    for k, v in g.tail(8).items():
                        add(f"{n} in {k}", round(float(v), 2))
                    if len(g) >= 2 and g.iloc[0]:
                        ch = round(100 * (float(g.iloc[-1]) / float(g.iloc[0]) - 1), 1)
                        add(f"{n} {'ROSE' if ch >= 0 else 'FELL'} from {g.index[0]} to {g.index[-1]} by", abs(ch), "%")
                        pk = g.idxmax()
                        if pk not in (g.index[0], g.index[-1]):   # peak mid-period: say both moves, never one
                            add(f"{n} peaked in {pk} at", round(float(g.max()), 2))
                            add(f"{n} FELL from its {pk} peak to {g.index[-1]} by",
                                round(100 * (1 - float(g.iloc[-1]) / float(g.max())), 1), "%")
            except Exception:
                pass
        if len(facts) >= max_facts:
            break
    return facts[:max_facts]


# ------------------------------------------------------------------ model calls
STORY = """You are a senior strategy consultant. Plan a deck as a storyline before any slide is written.
Rules: lead with the answer. One idea per slide. Every content slide has an ACTION TITLE: a full sentence (max 15 words)
that states the takeaway, not a label. Plain words a 9th grader understands; no jargon. Use only the facts given
(cite their ids). Return JSON:
{"answer": "<the one-sentence answer>", "slides": [{"type": "<one of: %s>", "title": "<action title>",
 "facts": ["F1", ...], "purpose": "<what this slide proves, 1 short sentence>"}]}
Section slides name the topic in plain words (never "Argument 1", "Part 2" or similar labels).
Pick each slide's type from the message (consulting archetypes): the answer up front -> exec_summary;
3-7 parallel reasons, risks, questions or findings -> numbered (add a "panel" with what it means for us);
options, pillars, drivers to compare -> cards; a sequence or plan -> roadmap; headline metrics -> stat_row or kpi;
a ranking, trend or split -> chart; detailed comparisons -> table; the decision -> recommendation (now/next/later).
Never more than two bullets slides in a row; prefer the structured types. Titles: a full-sentence takeaway,
max 15 words, no period, sentence case.
Order: title slide, a summary (exec_summary or kpi), 1 section per argument with 1-3 evidence slides (chart/table when
there are numbers), and a closing recommendation slide. Respect the requested slide count exactly if one is given;
if none is given, use 7-10 slides. Use section dividers only in decks of 12+ slides. Never repeat the same numbers on two slides.
The title slide's title is the deck's answer in max 10 words (e.g. "West and Pro drive sales; June needs a fix"),
never a copy of the request wording. Short or vague requests still get a full, decision-ready deck.
State direction exactly as the fact says (ROSE/FELL); a fall after a peak is not a fall over the whole period.
Never sum a stock (active users, balances, headcount) across periods; use the latest value.
Insight over inventory: build the argument on the "analysis:" facts (trends, inflection points, per-unit ratios,
gaps), not on raw totals. Each recommended action names an owner and an expected impact with a number."""

SPEC = """You turn a storyline into a deck spec. A renderer owns all layout; you write the content.
Use each slide's type and title exactly as planned. Content rules: max 5 bullets, max 12 words each, plain
language (grade 8-9), no jargon, numbers only from the facts given. Round for readers: money to 3 significant
figures with K/M ($75.5K, $1.2M), percentages to 1 decimal, counts as whole numbers. Put the fact
source in "source" as a short phrase. Charts: numeric values only, categories and series lengths equal.
Field schemas: %s
Density: write like a consulting pre-read. Each item gets a bold short label plus one concrete sentence; cards
and roadmap phases get 3-5 substantive points (8-14 words each, with numbers where the facts allow). Every data
slide states its "so what" (takeaways, takeaway, or a panel). Add "section_tag" like "01 | Market" to content slides.
Use an "icon" keyword (e.g. users, growth, price, risk, store, partner, target, time) on items, cards and stats.
Return JSON: {"title": "...", "requested_slide_count": <int or null>, "slides": [ ... ]}"""

FIX = """Fix this one slide. Keep its type and the meaning of its title. Return the corrected slide as JSON only.
Problems to fix: %s"""


def _call(client, system, user, task_id, max_tokens=6000):
    import uuid
    user = user + f"\n<run id=\"{uuid.uuid4().hex[:8]}\"/>"   # bypasses gateway response caching; prefix cache unaffected
    r = client.chat(model=MODEL, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    max_tokens=max_tokens, temperature=0.2, task_id=task_id, role="worker",
                    extra={"response_format": {"type": "json_object"}})
    txt = r["choices"][0]["message"].get("content") or "{}"
    return json.loads(txt[txt.find("{"): txt.rfind("}") + 1])


ANALYST = """You are a senior data analyst preparing facts for a consulting deck. Write Python (pandas) that reads the
input files in the current directory and prints ONE JSON list of 12-25 insight facts to stdout:
[{"text": "<what it is, plain words, with the period or segment>", "value": <number>, "unit": "%"|"$"|""}]
Find what a partner would put on a slide, not just totals: trend first vs last and % change; peak and trough and when;
the inflection point (where a trend reverses or accelerates); per-unit ratios (revenue per user/order, cost per unit)
and how they moved; net figures (adds minus losses); the biggest gap between segments; concentration (top share);
anomalies. Also print every derived per-period series you compute (e.g. churn rate, revenue per user, net adds) as one
fact per period, text like "churn rate (%) 2026-03". Round: money to 2 decimals or whole dollars, rates to 1 decimal
(as %), counts to whole numbers. Up to 80 facts. Only print the JSON list. Return {"code": "<python source>"}"""


def analyst_facts(client, files: list[str], task_id: str, start: int) -> list[dict]:
    """One model call writes analysis code; it runs locally (60 s cap); its printed findings become extra facts."""
    import subprocess
    data = [f for f in files if f.lower().endswith((".csv", ".xlsx", ".xls"))]
    if not data:
        return []
    import pandas as pd
    desc = ""
    for f in data:
        try:
            df = pd.read_csv(f) if f.endswith(".csv") else pd.read_excel(f)
            desc += f"\n{Path(f).name}: {len(df)} rows; columns {dict(df.dtypes.astype(str))}\n{df.head(6).to_string()}\n"
        except Exception:
            pass
    msgs_user, out = desc, []
    for attempt in range(2):
        try:
            code = _call(client, ANALYST, msgs_user, f"{task_id}-analyst", 3000).get("code", "")
            r = subprocess.run([sys.executable, "-I", "-c", code], cwd=str(Path(data[0]).parent), capture_output=True,
                               text=True, timeout=60)
            txt = r.stdout.strip()
            items = json.loads(txt[txt.find("["): txt.rfind("]") + 1])
            for it in items[:80]:
                if isinstance(it, dict) and isinstance(it.get("value"), (int, float)) and it.get("text"):
                    out.append({"id": f"F{start + len(out) + 1}", "text": "analysis: " + str(it["text"])[:140],
                                "value": round(float(it["value"]), 2), "unit": str(it.get("unit") or "")[:3]})
            if out:
                return out
            msgs_user = desc + f"\nYour previous code printed nothing usable. stderr: {r.stderr[-600:]}"
        except Exception as e:
            msgs_user = desc + f"\nYour previous code failed: {type(e).__name__}: {str(e)[:400]}"
    return out


SHAPES = {   # exact field shapes for the structured types (field names alone led to malformed slides)
    "exec_summary": '{"points": [{"label": "Short label", "text": "One concrete sentence"}], "stat": {"value": "$394K", "label": "..."}}',
    "numbered": '{"items": [{"label": "...", "text": "...", "icon": "risk"}], "panel": {"heading": "What it means for us", "items": ["..."]}}',
    "cards": '{"cards": [{"header": "2-4 words", "icon": "partner", "bullets": ["...", "..."]}], "takeaway": "..."}',
    "roadmap": '{"phases": [{"name": "2-3 words", "duration": "Q1 2027", "items": ["...", "..."]}]}',
    "stat_row": '{"stats": [{"value": "28.2%", "label": "...", "icon": "growth"}], "takeaway": "..."}',
    "recommendation": '{"now": {"action": "Verb-first action, max 12 words", "why": ["reason with a number"]}, "next": {...}, "later": {...}, "risk": "..."}',
    "kpi": '{"kpis": [{"value": "$394K", "label": "..."}], "takeaway": "..."}',
    "chart": '{"chart": {"type": "bar", "categories": ["..."], "series": [{"name": "Sales ($K)", "values": [1.0]}]}, "takeaways": ["..."]}',
}


def _schema_text():
    base = "; ".join(f"{t}({', '.join(v['req'] + v['opt'])})" for t, v in TYPES.items())
    return base + "\nShapes:\n" + "\n".join(f"{k}: {v}" for k, v in SHAPES.items())


_KFIX = re.compile(r"(\$?)(\d{1,3}(?:,\d{3})+(?:\.\d+)?)\s?([KkMm])\b")


def _fix_units(x):
    """'$111,188.16K' (a full dollar value with a K suffix) -> '$111.2K'. Deterministic, applied to all deck text."""
    if isinstance(x, str):
        def sub(m):
            v = float(m.group(2).replace(",", ""))
            if m.group(3) in "Kk":
                return f"{m.group(1)}{v / 1000:,.1f}K" if v < 1e6 else f"{m.group(1)}{v / 1e6:,.1f}M"
            return f"{m.group(1)}{v / 1e6:,.1f}M"
        x = _KFIX.sub(sub, x)

        def money(m):                              # raw dollar amounts -> reader-friendly K/M (3 significant figures)
            v = float(m.group(1).replace(",", "") + (m.group(2) or ""))
            if v < 1000:
                return m.group(0)
            if v >= 1e6:
                return f"${v / 1e6:.2f}M" if v < 1e7 else f"${v / 1e6:.1f}M"
            return f"${v / 1000:.2f}K" if v < 1e4 else f"${v / 1000:.1f}K" if v < 1e5 else f"${v / 1000:.0f}K"
        x = re.sub(r"\$(\d{1,3}(?:,\d{3})+|\d{4,})(\.\d+)?(?![\d.]|,\d|\s?(?:[KkMmBb]\b|bn\b|%|million|billion))", money, x)
        # plain counts: 8031 -> 8,031 (not years); 669.25 users -> 669 users
        x = re.sub(r"(?<![\d.,$])(?!(?:19|20)\d\d\b)(\d{4,})(?![\d.,%])", lambda m: f"{int(m.group(1)):,}", x)
        return re.sub(r"(?<![\d.,$])(\d[\d,]*)\.\d+(\s+(?:users|customers|orders|people|units|signups|accounts))",
                      lambda m: f"{round(float(m.group(1).replace(',', ''))):,}{m.group(2)}", x)
    if isinstance(x, list):
        return [_fix_units(v) for v in x]
    if isinstance(x, dict):
        return {k: (v if k in ("chart", "type") else _fix_units(v)) for k, v in x.items()}
    return x


def _tidy(deck: dict, want) -> dict:
    """Deterministic deck hygiene: unit slips, divider overuse in short decks, section tags that match the sections."""
    deck = _fix_units(deck)
    sl = [s for s in deck.get("slides", []) if isinstance(s, dict)]
    if want is None and len(sl) < 12:
        sl = [s for s in sl if s.get("type") != "section"]
    k, cur = 0, None
    has_sections = any(s.get("type") == "section" for s in sl)
    for s in sl:
        if s.get("type") == "section":
            k += 1
            cur = f"{k:02d} | " + " ".join(str(s.get("title", "")).split()[:4])
            s["number"] = f"{k:02d}"
        elif s.get("type") not in ("title",):
            if has_sections:
                if cur:
                    s["section_tag"] = cur
                else:
                    s.pop("section_tag", None)
    deck["slides"] = sl
    return deck


def _numbers_ok(slide: dict, facts: list[dict]) -> list[str]:
    vals = [float(f["value"]) for f in facts if isinstance(f["value"], (int, float))]
    blob = json.dumps({k: v for k, v in slide.items() if k not in ("type", "notes")})
    bad = []
    for tok in re.findall(r"(?<![\w.])-?\d[\d,]*\.?\d*", blob):
        try:
            v = float(tok.replace(",", ""))
        except ValueError:
            continue
        if v in (0, 1, 2, 3, 4, 5, 10, 100) or 1900 <= v <= 2100 and float(v).is_integer():
            continue
        if not any(abs(v - x) <= max(0.051, 0.006 * abs(x)) or abs(v - round(x)) < 0.5 and abs(x) >= 20 for x in vals):
            bad.append(tok)
    return bad[:5]


def _chart_ok(slide: dict, facts: list[dict]) -> list[str]:
    """Chart values must BE fact values (rounded to the shown decimals). Stops interpolated or invented series."""
    c = slide.get("chart") if isinstance(slide.get("chart"), dict) else None
    if not c:
        return []
    vals = [float(f["value"]) for f in facts if isinstance(f["value"], (int, float))]
    bad = []
    for se in c.get("series", []) or []:
        for v in (se.get("values") or []) if isinstance(se, dict) else []:
            if not isinstance(v, (int, float)):
                continue
            s = repr(float(v))
            d = 0 if float(v).is_integer() else min(4, len(s.split(".")[1]))
            if not any(round(x, d) == round(float(v), d) or round(x / 1000, d) == round(float(v), d)
                       or round(x / 1e6, d) == round(float(v), d) for x in vals):
                bad.append(v)
    return [f"chart values not in the fact sheet: {bad[:6]}; plot only real per-period facts (never fill gaps)"] if bad else []


def _slide_problems(i: int, s: dict, facts) -> list[str]:
    from engines.slides.spec import validate
    errs, warns = validate({"slides": [s]})
    probs = [e.replace("slide 1", f"slide {i}") for e in errs] + [w.replace("slide 1", f"slide {i}") for w in warns]
    text = " ".join(str(v) for k, v in s.items() if k not in ("type", "notes", "chart", "columns", "rows"))
    probs += [f"{p['check']}: {p['detail']}" for p in plain(text, max_grade=9.5) if p["check"] != "reading_level"
              or len(text.split()) > 40]
    if s.get("type") not in ("title", "section"):
        bad = _numbers_ok(s, facts)
        if bad:
            probs.append(f"numbers not in the fact sheet: {bad}; use only fact values")
        probs += _chart_ok(s, facts)
    return probs


def build_deck(request: str, files: list[str], out_path: str, client, task_id: str = "deck") -> dict:
    facts = facts_from_files(files)
    brief = ""
    for f in files:                                   # text briefs: content goes to the planner, numbers become facts
        if f.lower().endswith((".txt", ".md")):
            txt = Path(f).read_text(errors="replace")[:6000]
            brief += f"\n{Path(f).name}:\n{txt}\n"
            for mm in re.finditer(r"(?<![\w.])(\d[\d,]*\.?\d*)\s*(%|million|billion)?", txt):
                try:
                    facts.append({"id": f"F{len(facts) + 1}", "text": f"brief: ...{txt[max(0, mm.start() - 50):mm.start()]}",
                                  "value": float(mm.group(1).replace(",", "")), "unit": mm.group(2) or ""})
                except ValueError:
                    pass
    try:                                            # real analysis first: trends, inflections, ratios (not only totals)
        facts += analyst_facts(client, files, task_id, len(facts))
    except Exception:
        pass
    m = re.search(r"\b(\d{1,2})[- ]slide", request)
    want = int(m.group(1)) if m else None
    fact_text = "\n".join(f"{f['id']}: {f['text']} = {f['value']}{f['unit']}" for f in facts) or "(no data files)"
    story = _call(client, STORY % ", ".join(TYPES), f"Request: {request}\nRequested slide count: {want}\n"
                                                      f"Facts:\n{fact_text}" + (f"\nBrief:{brief}" if brief else ""),
                  task_id + "-story", 3000)
    used = sorted({fid for s in story.get("slides", []) if isinstance(s, dict) for fid in s.get("facts", []) or []})
    sub = [f for f in facts if f["id"] in used] or facts
    deck = _call(client, SPEC % _schema_text(), "Storyline:\n" + json.dumps(story) + (f"\nBrief:{brief}" if brief else "") + "\nFacts:\n" +
                 "\n".join(f"{f['id']}: {f['text']} = {f['value']}{f['unit']}" for f in sub), task_id + "-spec", 12000)
    deck = _tidy(deck, want)
    deck["requested_slide_count"] = want
    calls = 2
    for round_ in range(2):                        # targeted repair: failing slides only, slide-scoped context
        def _probs(i, s):
            try:
                return _slide_problems(i, s, facts)
            except Exception as e:
                return [f"malformed slide: {type(e).__name__}: {e}"]
        deck["slides"] = [s if isinstance(s, dict) else {"type": "bullets", "title": str(s)[:90], "bullets": [str(s)]}
                          for s in deck.get("slides", [])]
        probs = {i: _probs(i, s) for i, s in enumerate(deck.get("slides", []), 1)}
        probs = {i: p for i, p in probs.items() if p}
        if not probs:
            break
        for i, p in list(probs.items())[:8]:
            s = deck["slides"][i - 1]
            ss = story.get("slides", [])
            rel = [f for f in facts if f["id"] in set(ss[i - 1].get("facts", []))] if i - 1 < len(ss) and isinstance(
                ss[i - 1], dict) else sub
            try:
                fixed = _call(client, FIX % "; ".join(p), json.dumps(s) + "\nFacts:\n" +
                              "\n".join(f"{f['id']}: {f['text']} = {f['value']}{f['unit']}" for f in (rel or sub)),
                              f"{task_id}-fix{i}", 2500)
                calls += 1
                if fixed.get("type") in TYPES:
                    deck["slides"][i - 1] = fixed
            except Exception:
                pass
    r = None
    for attempt in range(3):                       # render failures go through the same slide-scoped repair
        try:
            r = render(deck, out_path)
            break
        except RenderError as e:
            bad = {int(x) for x in re.findall(r"slide (\d+)", str(e))}
            if not bad or attempt == 2:
                if bad:                             # last resort: drop unrenderable slides rather than fail the deck
                    deck["slides"] = [s for i, s in enumerate(deck["slides"], 1) if i not in bad]
                    try:
                        r = render(deck, out_path)
                        break
                    except RenderError as e2:
                        return {"ok": False, "reason": f"render: {e2}", "calls": calls, "deck": deck}
                return {"ok": False, "reason": f"render: {e}", "calls": calls, "deck": deck}
            for i in bad:
                if i - 1 < len(deck["slides"]):
                    try:
                        fixed = _call(client, FIX % f"layout: {e}. Shorten the text that does not fit (values under "
                                                    "10 characters, titles under 12 words, fewer/shorter bullets).",
                                      json.dumps(deck["slides"][i - 1]), f"{task_id}-rfix{i}", 2500)
                        calls += 1
                        if fixed.get("type") in TYPES:
                            deck["slides"][i - 1] = fixed
                    except Exception:
                        pass
    for _ in range(2):                             # layout QA failures (overflow/overlap) -> repair those slides, re-render
        lay = [i for i in qa.check(out_path) if i["check"] in ("text_overflow", "overlap", "out_of_bounds")]
        bad = sorted({i["slide"] for i in lay if i["slide"]})
        if not bad or r is None:
            break
        for i in bad:
            if i - 1 < len(deck["slides"]):
                det = "; ".join(x["detail"] for x in lay if x["slide"] == i)
                try:
                    fixed = _call(client, FIX % f"layout: text does not fit ({det}). Cut words: fewer, shorter bullets "
                                                "(max 12 words), shorter takeaway (max 15 words).",
                                  json.dumps(deck["slides"][i - 1]), f"{task_id}-qfix{i}", 2500)
                    calls += 1
                    if fixed.get("type") in TYPES:
                        deck["slides"][i - 1] = _fix_units(fixed)
                except Exception:
                    pass
        try:
            r = render(deck, out_path)
        except RenderError:
            break
    fixed_deck = _fix_units(deck)                  # repairs can reintroduce raw numbers: final pass over every slide
    if fixed_deck != deck:
        try:
            r = render(fixed_deck, out_path)
            deck = fixed_deck
        except RenderError:
            render(deck, out_path)
    issues = qa.check(out_path, want)
    Path(out_path).with_suffix(".json").write_text(json.dumps(deck, indent=1))
    return {"ok": not issues, "issues": issues, "calls": calls, "slides": r["slides"], "path": out_path,
            "answer": story.get("answer", ""), "deck": deck}
