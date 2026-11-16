"""The company dossier: research that runs BEFORE a deck or a model is planned.

A model built from the last annual report alone misses what changed since: a hospital group that moved to an
asset-light model in 2025 (sale-and-leaseback, management contracts) has different capex, depreciation, lease costs
and margins from the history in its statements. The dossier finds that first.

    d = build_dossier(brief, facts, client=client, models=["deepseek-v4.1-flash"], out_dir=out)
    facts += d["facts"]                  # sourced facts (F ids), each with its passage and URL
    d["changes"]                         # what changed in the business, with evidence and what it affects
    d["open"]                            # checklist questions research could not answer (shown, never invented)

Steps: (1) identify the company or companies the brief is about (name, ticker, listing, country, investor
relations site, a market data symbol); (2) the question list: an analyst's standard checklist (strategy and business
model changes, latest results and earnings release, guidance, M&A and disposals, financing, capex plan, capital
returns, management and shareholders, regulation and macro, competitors, recent news) plus the questions this brief
raises; (3) answer every question from the web, filings and investor-relations documents with
engines/forge/research.investigate (facts only from passages; every number in a fact appears in its passage);
(4) market data for listings outside the SEC's list; (5) what changed: structural changes that make history a poor
guide, each with its evidence and the model lines and slides it affects. Everything is written to dossier.json and
dossier.md in out_dir.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

CHECKLIST = [
    ("strategy", "What changed in {co}'s strategy or business model in the last 24 months (asset-light shift, "
                 "sale and leaseback, management contracts, divestitures, spin-offs, new segments or markets)?", ""),
    ("results", "What did {co} report in its latest earnings release or quarterly results (revenue, EBITDA, net "
                "income, margins, growth)?", "year"),
    ("annual", "What does {co}'s latest annual report or investor presentation say about segments, capacity and "
               "strategy?", ""),
    ("guidance", "What guidance or outlook has {co}'s management given for the current and next year?", "year"),
    ("deals", "Which acquisitions, disposals, partnerships or asset sales has {co} announced since {y2}?", ""),
    ("financing", "How is {co} financed: debt, refinancing, capital raises, leases and credit ratings since {y2}?", ""),
    ("capex", "What is {co}'s capital expenditure plan and expansion pipeline (new facilities, beds, stores, "
              "capacity)?", ""),
    ("returns", "What is {co}'s dividend and share buyback policy and recent payouts?", ""),
    ("people", "Have there been management changes, major shareholder moves or takeover offers at {co} since "
               "{y2}?", "year"),
    ("macro", "Which regulation, pricing, currency or inflation factors in {country} affect {co} now?", "year"),
    ("peers", "Who are {co}'s main competitors and how does {co} compare on size, growth and margins?", ""),
    ("news", "What are the most important news items about {co} in the last six months?", "month"),
]


def _chat_json(client, models, system, user, task_id, max_tokens=2500) -> dict | None:
    for m in models or []:
        try:
            r = client.chat(model=m, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                            max_tokens=max_tokens, temperature=0.1, task_id=task_id,
                            extra={"response_format": {"type": "json_object"}})
            txt = (((r or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            if "{" in txt:
                return json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
        except Exception:  # noqa: BLE001 - the next model, then nothing
            continue
    return None


IDENTIFY = """You read a brief for a consulting deck or a financial model and say which companies it is about and
what the research must find before anyone plans the work. Return ONLY JSON:
{"companies": [{"name": "full legal or common name", "short": "the name as people search for it",
  "ticker": "exchange ticker or empty", "exchange": "e.g. NASDAQ, EGX, LSE, or private", "country": str,
  "market_symbol": "Yahoo Finance symbol for a listed company (e.g. COST, CLHO.CA, VOD.L) or empty",
  "ir_site": "investor relations domain if you know it, else empty"}],
 "decision": "the decision the work serves, one line",
 "questions": ["up to 6 research questions this specific brief raises that a standard company checklist would
 not cover"]}
Only companies the brief is about (the subject, a target, the main peer named). Never invent a ticker: leave it
empty if unsure."""


def identify(brief: str, client, models, task_id: str = "dossier") -> dict:
    got = _chat_json(client, models, IDENTIFY, brief[:12000], f"{task_id}-identify") or {}
    cos = [c for c in got.get("companies") or [] if isinstance(c, dict) and (c.get("name") or c.get("short"))]
    return {"companies": cos[:3], "decision": str(got.get("decision") or ""),
            "questions": [str(q) for q in got.get("questions") or [] if str(q).strip()][:6]}


def questions_for(ident: dict, now: str | None = None, limit: int = 14) -> list[dict]:
    """The checklist for the main company plus the brief's own questions, as investigate() question dicts."""
    year = int((now or time.strftime("%Y"))[:4])
    cos = ident.get("companies") or []
    if not cos:
        return [{"question": q, "why": "the brief", "kind": "brief"} for q in ident.get("questions") or []][:limit]
    main = cos[0]
    co = main.get("short") or main.get("name")
    out = [{"question": t.format(co=co, y2=year - 2, country=main.get("country") or "its markets"),
            "why": "analyst checklist", "kind": k, "recency": rec, "company": co}
           for k, t, rec in CHECKLIST]
    out += [{"question": q, "why": "the brief", "kind": "brief", "company": co} for q in ident.get("questions") or []]
    return out[:limit]


def _queries(q: dict, ident: dict) -> list[str]:
    """Search queries a person would type: the company name in quotes, the topic words, the year; the IR site and
    filetype:pdf for reports."""
    co = q.get("company") or ""
    main = (ident.get("companies") or [{}])[0]
    name = f'"{co}"' if co else ""
    year = time.strftime("%Y")
    topic = {
        "strategy": ["asset-light strategy", "sale and leaseback", "business model change"],
        "results": [f"results {year} revenue EBITDA", "earnings release"],
        "annual": ["annual report pdf", "investor presentation"],
        "guidance": ["guidance outlook", "management expects"],
        "deals": ["acquisition", "disposal sale stake"],
        "financing": ["loan refinancing debt", "bond rating"],
        "capex": ["capex expansion new hospital beds" if "hospital" in (co or "").lower() else "capex expansion plan"],
        "returns": ["dividend", "share buyback"],
        "people": ["CEO appointed", "stake acquisition offer"],
        "macro": [f"{main.get('country') or ''} regulation prices inflation".strip()],
        "peers": ["competitors market share"],
        "news": ["news"],
    }.get(q.get("kind"), [])
    out = [f"{name} {t}".strip() for t in topic[:2]]
    if q.get("kind") in ("annual", "results") and main.get("ir_site"):
        out.append(f"{name} {topic[0]} site:{main['ir_site']}".strip())
    if q.get("kind") == "brief" or not out:
        out.append(q["question"][:120])
    return out[:3]


CHANGES = """You are a senior analyst. From the sourced findings below, list the structural changes in the
company's business that make its historical financial statements a poor guide to the future (e.g. an asset-light
shift, a sale and leaseback, a big acquisition or disposal, a new segment, a change in ownership or financing, a
regulatory or currency shock). For each: what changed, when, the fact ids that show it, and which model lines and
slides it affects (revenue mix, margins, capex, depreciation, leases, working capital, debt, tax, share count).
Use ONLY the findings; never infer a change they do not state. Return ONLY JSON:
{"changes": [{"change": str, "when": str, "evidence": ["F12"], "affects": ["capex", "depreciation"],
  "so_what": "one line: how the model or deck must reflect it"}]}"""


def build_dossier(brief: str, facts: list[dict], *, client, models: list[str], out_dir: str, rules: dict | None = None,
                  now: str | None = None, search_fn=None, fetch_fn=None, task_id: str = "dossier",
                  max_questions: int | None = None) -> dict:
    """Research the brief's company before planning. Never raises: a failed step is recorded and skipped."""
    from engines.forge import research as RS
    t0 = time.time()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ident = identify(brief, client, models, task_id)
    qs = questions_for(ident, now, int(max_questions or os.environ.get("MH_DOSSIER_QUESTIONS") or 14))
    for q in qs:
        q["queries"] = _queries(q, ident)
    r = dict(rules or {})
    caps = dict(RS.caps(rules), max_questions=len(qs), time_budget_s=float(
        os.environ.get("MH_DOSSIER_SECONDS") or 420), facts_per_question=3, max_sources_per_question=4)
    r["review"] = dict((r.get("review") or {}), research=caps)
    names = [c.get("short") or c.get("name") for c in ident.get("companies") or []]
    try:
        res = RS.investigate(qs, facts, client=client, models=models, out_dir=str(out), rules=r,
                             task_id=f"{task_id}-research", search_fn=search_fn, fetch_fn=fetch_fn,
                             names=names)
    except Exception as e:  # noqa: BLE001
        res = {"facts": [], "questions": [{"question": q["question"], "status": f"failed: {str(e)[:120]}",
                                           "facts": []} for q in qs]}
    new = list(res.get("facts") or [])
    have = " ".join(f.get("text", "") for f in facts + new)
    tics = [(c.get("name") or c.get("short"), c["market_symbol"]) for c in ident.get("companies") or []
            if c.get("market_symbol") and f"({c['market_symbol']}) share price" not in have]
    if tics and search_fn is None and os.environ.get("MH_RESEARCH_MARKET", "1") != "0":
        try:
            new += RS.market_facts(tics, RS.next_fact_no(facts + new), rates="Treasury yield" not in have)
        except Exception:  # noqa: BLE001
            pass
    found = [f for f in new if f.get("research")]
    changes = []
    if found:
        got = _chat_json(client, models, CHANGES, "\n".join(f"{f['id']}: {f['text']}" for f in found)[:24000],
                         f"{task_id}-changes", 2500) or {}
        ids = {f["id"] for f in found}
        for c in got.get("changes") or []:
            ev = [e for e in c.get("evidence") or [] if e in ids] if isinstance(c, dict) else []
            if ev and c.get("change"):                # a change with no evidence among the findings is dropped
                changes.append({"change": str(c["change"]), "when": str(c.get("when") or ""), "evidence": ev,
                                "affects": [str(a) for a in c.get("affects") or []],
                                "so_what": str(c.get("so_what") or "")})
    log = res.get("questions") or []
    open_ = [q["question"] for q in log if not q.get("facts")]
    d = {"identity": ident, "questions": log, "facts": new, "changes": changes, "open": open_,
         "seconds": round(time.time() - t0, 1), "failures": res.get("failures") or []}
    (out / "dossier.json").write_text(json.dumps(d, indent=1, default=str))
    (out / "dossier.md").write_text(to_markdown(d))
    return d


def to_markdown(d: dict) -> str:
    cos = ", ".join(c.get("name") or c.get("short") for c in d["identity"].get("companies") or []) or "the brief"
    lines = [f"# Research dossier: {cos}", "", f"Decision: {d['identity'].get('decision') or 'n/a'}", ""]
    if d["changes"]:
        lines += ["## What changed (history is a poor guide here)", ""]
        lines += [f"- **{c['change']}** ({c['when']}; {', '.join(c['evidence'])}). Affects: "
                  f"{', '.join(c['affects']) or 'n/a'}. {c['so_what']}" for c in d["changes"]] + [""]
    lines += ["## Questions and findings", ""]
    byid = {f["id"]: f for f in d["facts"]}
    for q in d["questions"]:
        lines.append(f"### {q['question']}")
        lines += [f"- {fid}: {byid[fid]['text']}" for fid in q.get("facts") or [] if fid in byid] or \
                 [f"- {q.get('status') or 'nothing found'}"]
        lines.append("")
    other = [f for f in d["facts"] if not f.get("question")]
    if other:
        lines += ["## Filed and market figures", ""] + [f"- {f['id']}: {f['text']}" for f in other] + [""]
    if d["open"]:
        lines += ["## Open questions (not answered by any source found)", ""] + [f"- {q}" for q in d["open"]]
    return "\n".join(lines) + "\n"


def brief_block(d: dict) -> str:
    """What the planner must see: the structural changes and the open questions, short."""
    parts = []
    if d.get("changes"):
        parts.append("What changed in the business (from research; plan and model around it, cite the evidence):\n"
                     + "\n".join(f"- {c['change']} ({c['when']}; evidence {', '.join(c['evidence'])}): {c['so_what']}"
                                 for c in d["changes"]))
    if d.get("open"):
        parts.append("Research could not answer (say so where it matters; never fill with an estimate):\n"
                     + "\n".join(f"- {q}" for q in d["open"][:8]))
    return "\n\n".join(parts)
