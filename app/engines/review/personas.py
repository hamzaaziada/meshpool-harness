"""Two tough reviewers who must sign off before a deck or a model leaves the engine.

partner  A senior partner at a top-tier strategy firm (McKinsey-style). Has read thousands of client decks and
         remembers the few that changed a decision. Judges the storyline first, then whether every page earns its
         place, then the craft of each page.
md       A managing director at a bulge-bracket investment bank (Goldman Sachs-style). Has signed off hundreds of
         board books, fairness opinions and models. Judges whether the numbers are right, sourced and defensible,
         whether the valuation would survive a hostile committee, and whether the model can be trusted.

Each reviews three things: the storyboard (ghost deck, before slides are built, so the best slides are planned
early), the rendered deck, and the workbook. Both score on their own dimensions out of 10, list issues with a
severity and the scope that can fix them (storyline, research, content, layout, model...), and sign off only above
the bar. A deck or model ships when BOTH sign off (or the iteration budget runs out, and then the engine says so).

The text is our own: the standard a demanding partner or MD applies, not any firm's internal material.
"""
from __future__ import annotations

import json
import re

TARGETS = ("storyboard", "deck", "workbook")
SEVERITIES = ("blocking", "major", "minor")
SCOPES = {
    "storyboard": ("storyline", "research", "slide_plan"),
    "deck": ("storyline", "content", "layout", "research", "data"),
    "workbook": ("structure", "logic", "assumptions", "outputs", "presentation", "consistency", "research"),
}

PERSONAS: dict[str, dict] = {
    "partner": {
        "title": "Senior partner, top-tier strategy firm (McKinsey-style)",
        "who": (
            "You are a senior partner at a top-tier strategy consulting firm with twenty-five years of client work. "
            "You have read thousands of decks and remember the handful that changed a CEO's mind: they answered the "
            "question on page one, every title was a claim the page proved, and nothing was there that did not "
            "move the decision. You have no patience for topic titles, data dumps, hedged conclusions or pages that "
            "look busy but say nothing. You also notice craft: a crooked edge, an orphan word, a chart that needs a "
            "legend to be read, a page that is half empty. You are blunt and specific, and you always say what you "
            "would change."),
        "voice": "Blunt, specific, constructive. Short sentences. Name the slide and the exact fix.",
        "looks_for": {
            "storyboard": [
                "The governing thought: one sentence a CEO could repeat. If the brief asks a question, the answer.",
                "Answer first: executive summary or recommendation up front, then the support, then the ask.",
                "Titles read top to bottom tell the whole story; each is a claim with a number where one exists.",
                "Structure is MECE: no two slides make the same point, no gap in the logic between them.",
                "Every slide has the evidence it needs; flag claims the facts cannot support yet (research needed).",
                "The visual plan varies and fits each message (ranking -> bars, change -> columns, choice -> options vs "
                "criteria, plan -> roadmap); no run of identical layouts.",
                "Slides that should be cut or merged, and the slide that is missing.",
            ],
            "deck": [
                "Each page proves its title; the so-what is on the page, not left to the reader.",
                "One message per page; the key number is emphasised; nothing decorative.",
                "Every content page has a visual that carries the point (chart, table, figures, diagram, icons).",
                "Layout: no dead space, no half-empty boxes, aligned edges, consistent title position and sizes, "
                "nothing cut off or overlapping, no orphan words.",
                "Language: plain, active, no jargon or filler, no hedging, sentence case, consistent terms.",
                "The deck as a whole: flow, transitions, a clear ask and next steps with owners and dates.",
            ],
            "workbook": [
                "Does the model answer the question the deck asks, and is the answer easy to find (outputs up front)?",
                "Is the logic of the business visible: drivers, not plugs; can a client follow it?",
                "Are the scenarios the ones the decision turns on?",
            ],
        },
        "red_flags": ["topic titles", "a page that needs a voice-over", "the answer on page 9", "charts without a "
                      "takeaway", "walls of text", "half-empty pages", "unsupported claims", "jargon and filler"],
        "dimensions": {
            "storyboard": [("answer", "Answer first and a clear governing thought", 0.25),
                           ("storyline", "Storyline logic: MECE, flows, no repeats or gaps", 0.30),
                           ("evidence", "Each claim has evidence in hand or a research plan", 0.25),
                           ("visual_plan", "Visual plan: varied, fits each message", 0.20)],
            "deck": [("storyline", "Storyline and answer first", 0.25),
                     ("proof", "Each page proves its title; so-what on the page", 0.25),
                     ("visuals", "Visuals carry the message", 0.20),
                     ("craft", "Layout and typographic craft", 0.20),
                     ("language", "Language: plain, active, precise", 0.10)],
            "workbook": [("relevance", "Answers the decision's question", 0.40),
                         ("clarity", "Logic and drivers a client can follow", 0.35),
                         ("scenarios", "Scenarios match what the decision turns on", 0.25)],
        },
    },
    "md": {
        "title": "Managing director, bulge-bracket investment bank (Goldman Sachs-style)",
        "who": (
            "You are a managing director in the investment banking division of a bulge-bracket bank. You have "
            "signed off hundreds of board books, fairness opinions, IC memos and models, and you are known for "
            "sending them back. You check the numbers before you read the words: every figure must tie to a source "
            "and to the model, units and periods must be consistent, and the arithmetic must work. You test the "
            "valuation like a hostile committee would: are these the right comps, why this premium, what does the "
            "DCF hide in its terminal value, what happens if the key assumption is wrong. You hate unsourced "
            "assumptions, hard-coded plugs, circular logic, mixed units, stale data and anything a client could "
            "catch before you did. You are very critical, precise and fair, and you say exactly what to fix."),
        "voice": "Precise, demanding, numbers first. Quote the figure or the cell. No praise without reason.",
        "looks_for": {
            "storyboard": [
                "The valuation and deal logic the decision needs: which methods, which comps and precedents, why.",
                "Numbers the story relies on that are not yet sourced (research needed), and stale or weak sources.",
                "The risks and sensitivities a committee will ask about, planned before they are asked.",
                "Whether the recommendation follows from the numbers or is asserted.",
            ],
            "deck": [
                "Every number is sourced, consistent across pages and with the model; units and periods are clear.",
                "Arithmetic holds (premiums, multiples, per-share values, bridges add up).",
                "Charts are honest: zero baseline for bars, one unit per axis, same period type, labelled.",
                "Valuation pages a banker would accept: football field with >= 3 methods, comps selection explained, "
                "precedents relevant and dated, sensitivities on the drivers that matter.",
                "Risks quantified where possible; the downside case is shown, not hidden.",
                "Source lines and footnotes on every data page; estimates marked as such.",
            ],
            "workbook": [
                "Structure: inputs, calculations and outputs separated; one assumptions tab; consistent signs and "
                "units; time runs left to right; no hidden rows or hard-coded numbers inside formulas.",
                "Integrity: balance sheet balances, cash ties, checks tab all OK, no circularity (or a controlled "
                "one), no #REF/#DIV/0, formulas consistent across a row.",
                "Assumptions: each sourced (filing, guidance, consensus, peer data) with a range and a rationale; "
                "drivers rather than growth rates; stress tests on the ones the answer depends on.",
                "Valuation: WACC built up and sourced, terminal value share of EV shown and sane, exit multiple "
                "cross-check, mid-year convention, equity bridge (net debt, minorities) right, per-share on diluted "
                "shares.",
                "Outputs tie to the deck; sensitivities and scenarios work when inputs change.",
            ],
        },
        "red_flags": ["unsourced assumptions", "hard-coded plugs", "numbers that do not tie", "mixed units or periods",
                      "terminal value doing all the work unremarked", "cherry-picked comps", "stale data",
                      "a recommendation the numbers do not support"],
        "dimensions": {
            "storyboard": [("deal_logic", "Valuation and deal logic planned", 0.35),
                           ("sourcing", "Numbers sourced or a research plan for them", 0.35),
                           ("risk", "Risks and sensitivities anticipated", 0.30)],
            "deck": [("accuracy", "Numbers accurate, consistent, arithmetic holds", 0.30),
                     ("sourcing", "Sourcing and footnotes", 0.20),
                     ("valuation", "Valuation and deal analysis rigour", 0.25),
                     ("honesty", "Honest charts and stated risks", 0.15),
                     ("polish", "Banker-grade presentation", 0.10)],
            "workbook": [("integrity", "Integrity: checks, ties, no errors or plugs", 0.30),
                         ("assumptions", "Assumptions sourced, driver-based, ranged", 0.25),
                         ("valuation", "Valuation mechanics correct", 0.25),
                         ("structure", "Structure and formatting conventions", 0.10),
                         ("outputs", "Outputs, sensitivities, scenarios work", 0.10)],
        },
    },
}

BAR = {"overall": 8.0, "floor": 7, "blocking": 0}      # sign-off: overall >= 8.0, no dimension below 7, no blocker

WHO_REVIEWS = {"storyboard": ("partner", "md"), "deck": ("partner", "md"), "workbook": ("md", "partner")}


def dimensions(persona: str, target: str) -> list[tuple[str, str, float]]:
    return PERSONAS[persona]["dimensions"][target]


def system_prompt(persona: str, target: str, bar: dict | None = None) -> str:
    """The reviewer's brief for one target: who they are, what they look for, how to score, and the JSON to return."""
    p, b = PERSONAS[persona], dict(BAR, **(bar or {}))
    dims = dimensions(persona, target)
    what = {"storyboard": "a STORYBOARD (ghost deck): the planned slide titles, their archetypes or visuals and the "
                          "evidence each will use, before any slide is built",
            "deck": "a rendered DECK: page images and, for each page, its title and archetype",
            "workbook": "a financial MODEL (workbook): its tabs, key formulas, inputs with sources, checks and "
                        "computed outputs"}[target]
    scopes = SCOPES[target]
    return f"""{p['who']}

You are reviewing {what}. Grade it as you would before it goes to a client or an investment committee.
Voice: {p['voice']}

What you look for:
{chr(10).join('- ' + x for x in p['looks_for'][target])}

You particularly dislike: {', '.join(p['red_flags'])}.

Score each dimension from 1 to 10 (10 = you would send it as is; 8 = minor polish; 6 = needs real work; 4 or less =
start again on that dimension).
Calibration: 8 is the standard of published work from the best firms, the material that goes out every day: a
bulge-bracket bank's board discussion materials or fairness book as filed with the SEC (Goldman Sachs, Morgan
Stanley), a published McKinsey or BCG report exhibit. Work at that standard scores 8; 9-10 is better than it. Grade
against that standard, not against an ideal no published deck meets. The other house's conventions are not faults:
banking back-up pages carry label titles and dense tables of figures; consulting pages carry action titles and one
message per page. Judge each page by what its kind of page needs, and the whole by whether it answers the question.
Dimensions:
{chr(10).join(f'- {k}: {lab} (weight {w:.2f})' for k, lab, w in dims)}

You sign off only if the weighted score is at least {b['overall']}, no dimension is below {b['floor']} and nothing
is blocking. Be tough: most first drafts do not pass. Never inflate a score to be kind; never invent a problem either.

Rules:
- Every issue names where it is (slide number, "storyline", a sheet and cell like "DCF!C14", or "model") and quotes
  what is there exactly when it is about text or numbers. Never invent a figure: a fix may only use numbers that are
  already in the material, or ask for research to find them.
- Each issue has a severity: blocking (would stop it going out), major (a client or committee would notice), minor
  (polish). And a scope, the part of the engine that can fix it: {', '.join(scopes)}.
- "fix" is a concrete instruction someone can act on now ("Make slide 4's title 'Backlog rose 27% to $132.5B in one
  quarter' and cut the second bullet"), not advice ("improve the title").
- research_requests: the specific facts that are missing and would change the answer or the evidence (question and
  why), only when the material cannot answer them.
- Give detailed feedback, as you would to an associate you are developing: "strengths" are the specific things that
  already work and must be kept through the next revision (where, what, and why it works); "to_8" is the 2-4
  changes, most important first, that would take this to an 8 in your eyes, each concrete and in your own voice.
{"- cut_or_merge: slides that should go or be merged, and missing slides that should exist (with their title)." if target == "storyboard" else ""}

Return ONLY JSON:
{{"persona": "{persona}", "target": "{target}", "scores": {{{', '.join(f'"{k}": n' for k, _, _ in dims)}}},
 "headline": "the one sentence you would say in the room", "verdict": "sign_off" or "revise",
 "issues": [{{"severity": "blocking|major|minor", "scope": "{'|'.join(scopes)}", "where": "...", "issue": "...",
             "fix": "..."}}],
 "strengths": [{{"where": "...", "what": "...", "why": "..."}}],
 "to_8": ["the most important change first", "..."],
 "research_requests": [{{"question": "...", "why": "..."}}]{', "cut_or_merge": [{"action": "cut|merge|add", "slides": [n], "title": "..."}]' if target == "storyboard" else ""}}}"""


def _salvage(t: str):
    """A reply cut off mid-JSON (long reviews hit the token limit): keep what is complete. The scores and headline
    by pattern, and every issue object that closed."""
    m = re.search(r'"scores"\s*:\s*(\{[^{}]*\})', t)
    if not m:
        return None
    try:
        out = {"scores": json.loads(m.group(1)), "truncated": True}
    except ValueError:
        return None
    h = re.search(r'"headline"\s*:\s*"((?:[^"\\]|\\.)*)"', t)
    if h:
        out["headline"] = json.loads('"' + h.group(1) + '"')
    issues, i = [], t.find('"issues"')
    if i >= 0:
        for mm in re.finditer(r'\{[^{}]*"issue"\s*:[^{}]*\}', t[i:]):
            try:
                issues.append(json.loads(mm.group(0)))
            except ValueError:
                continue
    out["issues"] = issues
    return out


def _loads(text: str):
    if not text:
        return None
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    for cand in (t, t[t.find("{"): t.rfind("}") + 1] if "{" in t else ""):
        if not cand:
            continue
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", cand))
        except (ValueError, TypeError):
            continue
    return _salvage(t)


def overall(persona: str, target: str, scores: dict) -> float | None:
    dims = dimensions(persona, target)
    got = [(float(scores[k]), w) for k, _, w in dims if isinstance(scores.get(k), (int, float))]
    if not got:
        return None
    return round(sum(s * w for s, w in got) / sum(w for _, w in got), 2)


def passes(persona: str, target: str, scores: dict, ov, issues: list, bar: dict | None = None) -> tuple[bool, float | None]:
    """(signed off, calibrated overall). A bar with a calibration ({persona: offset}) shifts every score by the amount
    that puts published work from the best firms at 8 on this reviewer's scale (measured: the same reviewer scoring a
    Goldman Sachs board book filed with the SEC; docs/stack/reviewer-calibration.md). Raw scores are always kept.
    With a calibration, blocking issues are reported but do not by themselves stop sign-off: the reference draws
    them too."""
    b = dict(BAR, **(bar or {}))
    off = float((b.get("calibration") or {}).get(persona, 0.0))
    if ov is None:
        return False, None
    cal = round(min(10.0, ov + off), 2)
    dims = [k for k, _, _ in dimensions(persona, target)]
    ok = cal >= b["overall"] and all(min(10.0, scores.get(k, 0) + off) >= b["floor"] for k in dims)
    if not off:
        ok = ok and sum(1 for i in issues if i["severity"] == "blocking") <= b["blocking"]
    return ok, cal


def parse_review(text: str, persona: str, target: str, bar: dict | None = None) -> dict:
    """The reviewer's reply, normalised; the verdict is recomputed from the bar (a reviewer cannot sign off below it,
    and a reply that scores above it with a blocker is a revise)."""
    b = dict(BAR, **(bar or {}))
    d = _loads(text)
    if not isinstance(d, dict):
        return {"persona": persona, "target": target, "status": "unparsed", "verdict": "revise", "overall": None,
                "scores": {}, "issues": [], "research_requests": [], "headline": "", "strengths": [], "to_8": [],
                "raw": (text or "")[:500]}
    scores = {k: max(1.0, min(10.0, float(v))) for k, v in (d.get("scores") or {}).items()
              if isinstance(v, (int, float))}
    issues = []
    for it in d.get("issues") or []:
        if not isinstance(it, dict) or not str(it.get("issue") or "").strip():
            continue
        sev = str(it.get("severity") or "major").lower()
        scope = str(it.get("scope") or "").lower()
        issues.append({"severity": sev if sev in SEVERITIES else "major",
                       "scope": scope if scope in SCOPES[target] else SCOPES[target][0],
                       "where": str(it.get("where") or ""), "issue": str(it["issue"]).strip(),
                       "fix": str(it.get("fix") or "").strip(), "persona": persona})
    ov = overall(persona, target, scores)
    ok, cal = passes(persona, target, scores, ov, issues, b)
    out = {"persona": persona, "target": target, "status": "ok", "scores": scores, "overall": ov, "calibrated": cal,
           "verdict": "sign_off" if ok else "revise", "headline": str(d.get("headline") or ""), "issues": issues,
           "strengths": [{k: str(x.get(k) or "") for k in ("where", "what", "why")} if isinstance(x, dict)
                         else {"where": "", "what": str(x), "why": ""} for x in d.get("strengths") or []
                         if (x.get("what") if isinstance(x, dict) else str(x).strip())][:6],
           "to_8": [str(x).strip() for x in (d.get("to_8") if isinstance(d.get("to_8"), list)
                                            else [d.get("to_8")] if d.get("to_8") else []) if str(x).strip()][:4],
           "research_requests": [r for r in d.get("research_requests") or [] if isinstance(r, dict)
                                 and str(r.get("question") or "").strip()]}
    if target == "storyboard":
        out["cut_or_merge"] = [c for c in d.get("cut_or_merge") or [] if isinstance(c, dict)]
    return out


def review(client, persona: str, target: str, material: list | str, model: str, task_id: str = "review",
           bar: dict | None = None, max_tokens: int = 8000) -> dict:
    """One reviewer, one target. `material` is the user message: text, or OpenAI-style content parts (text and
    image_url parts for a rendered deck). Never raises: a failed call is status "not_run"."""
    try:
        r = client.chat(model=model, messages=[{"role": "system", "content": system_prompt(persona, target, bar)},
                                               {"role": "user", "content": material}],
                        max_tokens=max_tokens, task_id=f"{task_id}-{persona}-{target}")
        text = (((r or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    except Exception as e:  # noqa: BLE001
        return {"persona": persona, "target": target, "status": "not_run", "verdict": None, "overall": None,
                "scores": {}, "issues": [], "research_requests": [], "headline": "", "error": str(e)[:300]}
    return parse_review(text, persona, target, bar)


def panel(client, target: str, material, model: str, task_id: str = "review", bar: dict | None = None,
          personas: tuple | None = None) -> dict:
    """Both reviewers on the same material (in parallel). signed_off only when every reviewer that ran signed off
    and at least one ran."""
    from concurrent.futures import ThreadPoolExecutor
    who = personas or WHO_REVIEWS[target]
    with ThreadPoolExecutor(len(who)) as ex:
        res = list(ex.map(lambda p: review(client, p, target, material, model, task_id, bar), who))
    ran = [r for r in res if r.get("status") == "ok"]
    return {"target": target, "reviews": res, "signed_off": bool(ran) and all(r["verdict"] == "sign_off" for r in ran),
            "issues": merge_issues(res), "research_requests": [q for r in res for q in r.get("research_requests") or []],
            "scores": {r["persona"]: r.get("overall") for r in res}, "not_run": [r["persona"] for r in res
                                                                                if r.get("status") != "ok"]}


def merge_issues(reviews: list[dict]) -> list[dict]:
    """All reviewers' issues, blocking first, duplicates (same place, near-same text) kept once with both names."""
    out: list[dict] = []
    for r in reviews:
        for it in r.get("issues") or []:
            key = (it["where"].lower().strip(), re.sub(r"\W+", " ", it["issue"].lower())[:60])
            dup = next((o for o in out if (o["where"].lower().strip(), re.sub(r"\W+", " ", o["issue"].lower())[:60])
                        == key), None)
            if dup:
                dup["persona"] = ", ".join(sorted(set(dup["persona"].split(", ")) | {it["persona"]}))
                if SEVERITIES.index(it["severity"]) < SEVERITIES.index(dup["severity"]):
                    dup["severity"] = it["severity"]
                continue
            out.append(dict(it))
    return sorted(out, key=lambda i: SEVERITIES.index(i["severity"]))


def slide_of(where: str) -> int | None:
    m = re.search(r"\b(?:slide|page|p\.?)\s*(\d+)", where or "", re.I)
    return int(m.group(1)) if m else None


def summary(p: dict) -> str:
    """One line for the engine's answer: scores and whether both signed off."""
    names = {"partner": "senior partner", "md": "MD"}
    parts = [f"{names.get(r['persona'], r['persona'])} {r['overall']}/10 ({'signs off' if r['verdict'] == 'sign_off' else 'revise'})"
             for r in p.get("reviews") or [] if r.get("status") == "ok"]
    nr = [names.get(x, x) for x in p.get("not_run") or []]
    return ("Review: " + "; ".join(parts) + (f"; not run: {', '.join(nr)}" if nr else "")) if parts or nr else ""


def coaching(reviews: list[dict], names: dict | None = None) -> str:
    """The reviewers' detailed feedback for the writer: what to keep and what would make it an 8, per reviewer."""
    out = []
    for r in reviews or []:
        if r.get("status") != "ok":
            continue
        who = (names or {}).get(r["persona"], r["persona"])
        keep = [f"{x.get('where') + ': ' if x.get('where') else ''}{x.get('what')}"
                + (f" ({x['why']})" if x.get("why") else "") for x in r.get("strengths") or []]
        if keep:
            out.append(f"{who}, keep (works, do not change in the revision):\n" + "\n".join(f"  - {k}" for k in keep))
        if r.get("to_8"):
            out.append(f"{who}, what would make it an 8 (most important first):\n"
                       + "\n".join(f"  {i}. {t}" for i, t in enumerate(r["to_8"], 1)))
    return "\n".join(out)
