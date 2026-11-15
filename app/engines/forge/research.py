"""Focused research for the deck loop: a few questions the reviewers asked, answered from sourced passages.

    res = investigate(["CoreWeave 2025 revenue backlog"], facts, client=client, out_dir=out)
    facts += res["facts"]        # new F ids, each text carrying its source and URL so the slide cites it

This is not the research engine's agent loop (engines/research/engine.py, dozens of turns per report): it uses the
same library (engines/research/library.py: web search, page reading, tiered sources, BM25 passages) in-process, one
search per question, a few pages each, under a question cap and a wall-clock budget. Facts come only from passages:
a model may pick and trim the sentences (a strong text model, when one answers), but every number in a fact must
appear in the passage it cites, or the fact is dropped. Without a model the sentences are picked deterministically.
A question that finds nothing is recorded as "nothing found"; nothing is ever invented.

Research is allowed only when the build allows it (allowed()): off with MH_RESEARCH=0, rules review.research.enabled
false, a brief that says not to research, stub clients (tests), or no network.
"""
from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

NO_RESEARCH_RE = re.compile(r"(?i)\b(no|without|skip|don'?t|do not)\s+(?:any\s+|web\s+|external\s+|outside\s+)?"
                            r"(research|web search|searching|browsing)\b|\b(?:use |using )?only the (?:facts|data|"
                            r"documents?|files?)(?: (?:provided|given|attached))?\b(?![^.]*\b(?:and|or|plus)\b[^.]*"
                            r"\b(?:research|sourced|sources?)\b)")       # "only the facts given and any sourced research" allows it

DEFAULTS = {"enabled": True, "max_questions": 6, "max_sources_per_question": 3, "time_budget_s": 240,
            "facts_per_question": 3, "max_research_steps": 3}

_NET: dict = {}


def caps(rules: dict | None) -> dict:
    return dict(DEFAULTS, **((rules or {}).get("review") or {}).get("research") or {})


def network_ok(timeout: float = 3.0) -> bool:
    """Can this machine reach the web? Through the HTTPS proxy when one is set (the proxy resolves names), else a
    direct connection to the search host. Cached for the process."""
    if "ok" in _NET:
        return _NET["ok"]
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    try:
        if proxy:
            u = urllib.parse.urlparse(proxy if "://" in proxy else "http://" + proxy)
            host, port = u.hostname, u.port or 80
        else:
            host, port = "html.duckduckgo.com", 443
        socket.create_connection((host, port), timeout=timeout).close()
        _NET["ok"] = True
    except OSError:
        _NET["ok"] = False
    return _NET["ok"]


def allowed(client, rules: dict | None, brief: str = "", option: bool | None = None) -> tuple[bool, str]:
    """(allowed, why). An explicit option (True/False) wins over the defaults, never over MH_RESEARCH=0, the rules or
    the network."""
    if os.environ.get("MH_RESEARCH", "1") == "0":
        return False, "research off (MH_RESEARCH=0)"
    if not caps(rules).get("enabled", True):
        return False, "research off in rules.yaml"
    if option is False:
        return False, "research off for this request"
    if option is None and NO_RESEARCH_RE.search(brief or ""):
        return False, "the brief asks for no outside research"
    if option is None:
        try:
            from meshpool_harness.core.client import LiveClient
        except Exception:  # noqa: BLE001
            return False, "no live client"
        if not isinstance(client, LiveClient):
            return False, "research off for offline (stub) clients"
    if not network_ok():
        return False, "no network"
    return True, ""


def next_fact_no(facts: list[dict]) -> int:
    nums = [int(m.group(1)) for f in facts for m in [re.match(r"^F(\d+)$", str(f.get("id") or ""))] if m]
    return max(nums, default=0) + 1


def questions_from(panel: dict, limit: int = 6) -> list[dict]:
    """The reviewers' research requests, then their issues of scope "research" turned into questions; duplicates
    (same words) kept once, capped."""
    out, seen = [], set()

    def add(q: str, why: str, who: str, where: str = ""):
        q = re.sub(r"\s+", " ", (q or "").strip())
        key = re.sub(r"\W+", " ", q.lower()).strip()[:80]
        if q and key not in seen:
            seen.add(key)
            out.append({"question": q[:300], "why": (why or "")[:300], "persona": who, "where": where})
    for r in panel.get("reviews") or []:
        for rq in r.get("research_requests") or []:
            add(str(rq.get("question") or ""), str(rq.get("why") or ""), r.get("persona", ""))
    for it in panel.get("issues") or []:
        if it.get("scope") == "research":
            add(it.get("fix") or it.get("issue"), it.get("issue", ""), it.get("persona", ""), it.get("where", ""))
    return out[:limit]


_STOP = set("""what which how much many is are does do did find get source confirm verify the a an please exactly latest
available of to in on for and or with by from at as that this these those its it be been was were will would should
could can current resulting correct including deal deck slide headline figure number value""".split())


def _keywords(q: str, n: int = 8) -> str:
    """A web query a search engine can use: the question's names, figures and key terms (<= n words), not its
    sentence. 'What is NVIDIA's exact current ownership of CoreWeave, including the 22.9M shares ...' ->
    'NVIDIA ownership CoreWeave 22.9M shares'."""
    words = re.findall(r"[A-Za-z][\w.&'-]*|\$?\d[\d.,]*[%MBK]?", q)
    keep = []
    for w in words:
        lw = w.lower().strip("'")
        if lw in _STOP or len(lw) < 3 and not w[0].isdigit():
            continue
        w = re.sub(r"'s$", "", w)
        if w.lower() not in (k.lower() for k in keep):
            keep.append(w)
    names = [w for w in keep if w[0].isupper() or w[0].isdigit() or w[0] == "$"]
    rest = [w for w in keep if w not in names]
    return " ".join((names + rest)[:n])


def _queries_model(questions: list[str], client, models, task_id, dead) -> list[list[str]] | None:
    """Two or three short web queries per question, in one call for the whole step (cheap)."""
    if client is None or not models:
        return None
    sys_ = ("Turn each research question into 2-3 short web search queries (3-8 words each: company names, the "
            "metric, the period, the source type such as 10-K, investor relations, press release). Return ONLY JSON: "
            '{"queries": [["q1", "q2"], ...]} with one list per question, in order.')
    user = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
    for m in models:
        try:
            r = client.chat(model=m, messages=[{"role": "system", "content": sys_}, {"role": "user", "content": user}],
                            max_tokens=800, task_id=f"{task_id}-queries")
            txt = (((r or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            js = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
            qs = js.get("queries") or []
            if len(qs) == len(questions):
                return [[str(x) for x in (q or [])][:3] for q in qs]
        except Exception as e:  # noqa: BLE001
            if re.search(r"HTTP 40[13]\b|invalid or missing api key", str(e), re.I):
                dead.add("models")
                return None
            continue
    return None


def _query(q: str) -> str:
    """A search query from a reviewer's question: the question without filler, at most 14 words."""
    q = re.sub(r"(?i)^(what|which|how much|how many|is|are|does|do|find|get|source|confirm|verify)\b\s*", "", q)
    q = re.sub(r"(?i)\b(the|a|an|please|exactly|latest available)\b", " ", q)
    return " ".join(re.sub(r"[?\"']", " ", q).split()[:14])


_JUNK_LINE = re.compile(r"(?i)^\s*(retrieved|archived|- ?↑|↑|view more|see also|cite|edit)\b")


def _sentences(text: str) -> list[str]:
    """Readable sentences only: page furniture (widgets, menus, tables flattened to one value per line, reference
    lists) is dropped, citation markers like [43] are removed."""
    out = []
    for line in re.split(r"\n+", text or ""):
        line = re.sub(r"\[\d+\]", "", line).strip(" -•\t")
        if len(line.split()) < 6 or _JUNK_LINE.search(line):
            continue
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9$])", line):
            s = s.strip()
            words = s.split()
            letters = sum(c.isalpha() for c in s)
            if 30 <= len(s) <= 400 and len(words) >= 6 and letters >= 0.55 * len(s.replace(" ", "")):
                out.append(s)
    return out


def _holds(fact: str, passage: str) -> bool:
    """The fact is in the passage: every number in it is a number in the passage (the same value, any formatting the
    library accepts), and its words are the passage's (at least 80% of them)."""
    from engines.research.library import parse_numbers, same_number, tokens
    have = parse_numbers(passage, keep_years=True)
    want = parse_numbers(fact, keep_years=True)
    if not all(any(same_number(w, h) for h in have) for w in want):
        return False
    ft, pt = set(tokens(fact)), set(tokens(passage))
    return bool(ft) and len(ft & pt) / len(ft) >= 0.8


def _source_line(p) -> str:
    s = p.source
    bits = [s.publisher or "", (s.title or "")[:90], (s.date or "")[:10]]
    label = ", ".join(b for b in bits if b)
    return f"(Source: {label}{'; ' + s.url if s.url else ''})"


def _pick_plain(q: str, passages: list, k: int) -> list[tuple[str, object]]:
    """Deterministic: sentences with a number from the best passages, ranked by the question's words they share."""
    from engines.research.library import parse_numbers, tokens
    qt = {t for t in tokens(q) if not t.isdigit()}
    need = max(2, round(0.4 * len(qt)))
    cands = []
    for rank, p in enumerate(passages):
        for s in _sentences(p.text):
            if not parse_numbers(s):
                continue
            overlap = len(qt & set(tokens(s)))
            if overlap >= need:
                cands.append((overlap - 0.3 * rank, s, p))
    cands.sort(key=lambda x: -x[0])
    out, seen = [], set()
    for _, s, p in cands:
        if s not in seen:
            seen.add(s)
            out.append((s, p))
        if len(out) >= k:
            break
    return out


def _pick_model(q: str, passages: list, k: int, client, models: list[str], task_id: str, dead: set
                ) -> list[tuple[str, object]] | None:
    """A text model picks up to k sentences that answer the question, each quoted from one passage. None when no
    model answered (the caller falls back to the deterministic pick); a refused key marks `dead` so the remaining
    questions do not try again."""
    by_id = {p.id: p for p in passages}
    text = "\n\n".join(f"[{p.id}] {p.source.label if p.source else ''}\n{' '.join(_sentences(p.text))[:1500]}"
                         for p in passages)
    system = ("You extract sourced facts for a board deck. From the passages ONLY, return up to "
              f"{k} facts that answer the question. Each fact is one sentence copied from a passage (you may trim "
              "words, never change or add a number) and names that passage. If the passages do not answer it, return "
              'an empty list. Return ONLY JSON: {"facts": [{"passage": "S1.p3", "text": "..."}]}')
    for m in models:
        try:
            r = client.chat(model=m, messages=[{"role": "system", "content": system},
                                               {"role": "user", "content": f"Question: {q}\n\nPassages:\n{text}"}],
                            max_tokens=1500, temperature=0.0, task_id=task_id, role="worker",
                            extra={"response_format": {"type": "json_object"}})
            txt = ((r.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            d = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
            out = []
            for f in d.get("facts") or []:
                p = by_id.get(str(f.get("passage") or "").strip("[]"))
                t = str(f.get("text") or "").strip()
                if p is not None and t:
                    out.append((t, p))
            return out[:k]
        except Exception as e:  # noqa: BLE001 - next model, then the deterministic pick
            if re.search(r"HTTP 40[13]\b|invalid or missing api key", str(e), re.I):
                dead.add("models")                     # the gateway refuses the key: no model for the rest
                break
            continue
    return None


# SEC XBRL company facts: reported figures (latest filing) for the companies a question names, no search engine needed
XBRL_LINES = [("dei", "EntityCommonStockSharesOutstanding", "shares", "shares outstanding"),
              ("us-gaap", "LongTermDebt", "USD", "long-term debt"),
              ("us-gaap", "DebtCurrent", "USD", "current debt"),
              ("us-gaap", "Revenues", "USD", "revenue"),
              ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD", "revenue"),
              ("us-gaap", "NetIncomeLoss", "USD", "net income (loss)"),
              ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment", "USD", "capital expenditure"),
              ("us-gaap", "CashAndCashEquivalentsAtCarryingValue", "USD", "cash and cash equivalents"),
              ("us-gaap", "InterestExpense", "USD", "interest expense"),
              ("us-gaap", "NetCashProvidedByUsedInOperatingActivities", "USD", "operating cash flow"),
              ("us-gaap", "PaymentsOfDividends", "USD", "dividends paid"),
              ("us-gaap", "PaymentsOfDividendsCommonStock", "USD", "dividends paid"),
              ("us-gaap", "PaymentsForRepurchaseOfCommonStock", "USD", "share repurchases"),
              ("us-gaap", "LongTermDebtNoncurrent", "USD", "long-term debt (non-current)"),
              ("us-gaap", "OperatingLeaseLiability", "USD", "operating lease liabilities"),
              ("us-gaap", "CommonStockDividendsPerShareDeclared", "USD/shares", "dividends declared per share"),
              ("us-gaap", "EarningsPerShareDiluted", "USD/shares", "diluted EPS"),
              ("us-gaap", "OperatingIncomeLoss", "USD", "operating income"),       # return on capital: operating
              ("us-gaap", "StockholdersEquity", "USD", "shareholders' equity"),    # income against equity + debt
              ("us-gaap", "PropertyPlantAndEquipmentNet", "USD", "net property and equipment"),
              ("us-gaap", "IncomeTaxExpenseBenefit", "USD", "income tax expense")]


def _money(v: float, unit: str) -> str:
    if unit == "USD/shares":
        return f"${v:,.2f} per share"
    if unit == "shares":
        return f"{v / 1e6:,.1f}M shares"
    a = abs(v)
    s = f"${a / 1e9:,.2f}B" if a >= 1e9 else f"${a / 1e6:,.1f}M"
    return ("-" if v < 0 else "") + s


def _days_between(a: str, b: str) -> int:
    import datetime as _dt
    try:
        return abs((_dt.date.fromisoformat(b) - _dt.date.fromisoformat(a)).days)
    except ValueError:
        return 0


def xbrl_facts(names: list[str], no: int, ticker_map: dict | None = None, fetch=None) -> list[dict]:
    """Latest reported values for each named company that files with the SEC, as sourced facts (F ids from no).
    Each fact names the form, period end, filing date and the filing's URL."""
    from engines.sheets import spreading as SP
    out: list[dict] = []
    try:
        tick = ticker_map or json.loads(SP._get(SP.TICKERS, SP.default_user_agent()))
    except Exception:  # noqa: BLE001
        return out
    rows = list(tick.values()) if isinstance(tick, dict) else tick
    for name in dict.fromkeys(n.strip() for n in names if n.strip()):
        low = name.lower()
        hit = next((r for r in rows if str(r.get("ticker", "")).lower() == low), None) or next(
            (r for r in rows if str(r.get("title", "")).lower().startswith(low)), None)
        if not hit:
            continue
        cik = str(hit["cik_str"]).zfill(10)
        try:
            facts = fetch(cik) if fetch else SP.fetch_companyfacts(cik)
        except Exception:  # noqa: BLE001
            continue
        seen = set()
        ends = [u.get("end", "") for ns, tag, unit, _ in XBRL_LINES for u in
                ((facts.get("facts", {}).get(ns, {}).get(tag) or {}).get("units") or {}).get(unit) or []
                if u.get("form") in ("10-K", "10-Q")]
        latest = max(ends) if ends else ""
        for ns, tag, unit, label in XBRL_LINES:
            if label in seen:
                continue
            units = ((facts.get("facts", {}).get(ns, {}).get(tag) or {}).get("units") or {}).get(unit) or []
            pts = [u for u in units if u.get("form") in ("10-K", "10-Q", "10-K/A", "10-Q/A") and "val" in u]
            if not pts:
                continue
            u = max(pts, key=lambda x: (x.get("end", ""), x.get("filed", "")))
            if latest and _days_between(u.get("end", ""), latest) > 200:
                continue                                 # a tag the company stopped using: stale, not current
            if u.get("start") and unit != "shares":
                per = f"for the period {u['start']} to {u['end']}"
            else:
                per = f"at {u['end']}"
            url = SP._filing_url(cik, u.get("accn", ""))
            out.append({"id": f"F{no + len(out)}", "text": f"{hit['title']} {label} {_money(float(u['val']), unit)} "
                        f"{per} ({u.get('form')} filed {u.get('filed')}; SEC XBRL {tag}; {url})",
                        "source": url, "research": True, "company": hit["title"], "label": label,
                        "value": float(u["val"]), "annual": not u.get("start") or
                        _days_between(u["start"], u["end"]) > 330, "end": u.get("end", "")})
            seen.add(label)
    return out


YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval={iv}"


def _yahoo(sym: str, rng: str = "2y", iv: str = "1wk") -> dict:
    import urllib.request
    req = urllib.request.Request(YAHOO.format(sym=urllib.parse.quote(sym), rng=rng, iv=iv),
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())["chart"]["result"][0]


def _beta(stock: list, index: list) -> float | None:
    """Weekly-return beta: cov(stock, index) / var(index), on the weeks both closed."""
    pairs = [(a, b) for a, b in zip(stock, index) if a and b]
    rs = [(pairs[i][0] / pairs[i - 1][0] - 1, pairs[i][1] / pairs[i - 1][1] - 1) for i in range(1, len(pairs))]
    if len(rs) < 30:
        return None
    ms, mi = sum(r[0] for r in rs) / len(rs), sum(r[1] for r in rs) / len(rs)
    var = sum((r[1] - mi) ** 2 for r in rs)
    return sum((r[0] - ms) * (r[1] - mi) for r in rs) / var if var else None


def market_facts(tickers: list[tuple[str, str]], no: int, fetch=None, rates: bool = True) -> list[dict]:
    """Market data a valuation needs and a 10-K does not carry, as sourced facts (F ids from no): for each
    (company name, ticker) the last close, its 52-week range and a 2-year weekly beta against the S&P 500; and the
    US 10-year Treasury yield (the risk-free rate). fetch(symbol, range, interval) -> Yahoo chart result (tests)."""
    import datetime as _dt
    get = fetch or _yahoo
    out: list[dict] = []
    day = lambda t: _dt.datetime.fromtimestamp(int(t), _dt.timezone.utc).date().isoformat()   # noqa: E731

    def add(text, url, **kw):
        out.append({"id": f"F{no + len(out)}", "text": text, "source": url, "research": True, **kw})
    try:
        idx = get("^GSPC", "2y", "1wk")
    except Exception:  # noqa: BLE001
        idx = None
    for name, tic in dict.fromkeys(tickers):
        url = f"https://finance.yahoo.com/quote/{tic}"
        try:
            wk = get(tic, "2y", "1wk")
            dy = get(tic, "1y", "1d")
        except Exception:  # noqa: BLE001
            continue
        closes = [(t, c) for t, c in zip(dy.get("timestamp") or [], (dy["indicators"]["quote"][0].get("close") or []))
                  if c]
        if not closes:
            continue
        t, last = closes[-1]
        add(f"{name} ({tic}) share price ${last:,.2f} at close on {day(t)} (Yahoo Finance daily close; {url})", url,
            company=name, label="share price", value=float(last))
        hi, lo = max(c for _, c in closes), min(c for _, c in closes)
        add(f"{name} ({tic}) 52-week closing range ${lo:,.2f} to ${hi:,.2f} to {day(t)} (Yahoo Finance; {url})", url)
        if idx:
            sw = dict(zip(wk.get("timestamp") or [], wk["indicators"]["quote"][0].get("close") or []))
            iw = dict(zip(idx.get("timestamp") or [], idx["indicators"]["quote"][0].get("close") or []))
            common = sorted(set(sw) & set(iw))
            b = _beta([sw[k] for k in common], [iw[k] for k in common])
            if b is not None:
                add(f"{name} ({tic}) beta {b:.2f} against the S&P 500, from 2 years of weekly returns to {day(common[-1])} "
                    f"(computed from Yahoo Finance weekly closes; {url})", url)
    if rates and out:
        try:
            tn = get("^TNX", "5d", "1d")
            cl = [(t, c) for t, c in zip(tn.get("timestamp") or [], tn["indicators"]["quote"][0].get("close") or []) if c]
            if cl:
                add(f"US 10-year Treasury yield {cl[-1][1]:.2f}% at close on {day(cl[-1][0])} (Yahoo Finance, US 10-year Treasury yield index; "
                    "https://finance.yahoo.com/quote/%5ETNX)", "https://finance.yahoo.com/quote/%5ETNX",
                    label="treasury yield", value=float(cl[-1][1]))
        except Exception:  # noqa: BLE001
            pass
    return out


def _tickers(names: list[str], ticker_map: dict | None = None) -> list[tuple[str, str]]:
    """(SEC company title, ticker) for each name that matches a ticker or the start of a filer's title."""
    from engines.sheets import spreading as SP
    try:
        tick = ticker_map or json.loads(SP._get(SP.TICKERS, SP.default_user_agent()))
    except Exception:  # noqa: BLE001
        return []
    rows = list(tick.values()) if isinstance(tick, dict) else tick
    out = []
    for name in dict.fromkeys(n.strip() for n in names if n.strip()):
        low = name.lower()
        hit = next((r for r in rows if str(r.get("ticker", "")).lower() == low), None) or next(
            (r for r in rows if str(r.get("title", "")).lower().startswith(low)), None)
        if hit:
            out.append((str(hit["title"]), str(hit["ticker"])))
    return out


def economics_facts(facts: list[dict], no: int) -> list[dict]:
    """The capital-allocation arithmetic a banker checks first, computed here from filed and market facts (never by a
    model): free cash flow, market cap, earnings yield, after-tax return on capital, cash cover of capex, and what
    each $1B returned does (shares retired at the price, dividend per share, EPS after a buyback). Each fact states
    its formula and the F ids it uses. Only annual flows are used; a missing input skips that line."""
    out: list[dict] = []
    by: dict[str, dict] = {}
    for f in facts:
        if f.get("company") and f.get("label") and isinstance(f.get("value"), (int, float)) and f.get("annual", True):
            by.setdefault(f["company"], {}).setdefault(f["label"], f)
    def v(c, k):
        return c[k]["value"] if k in c else None
    def add(company, label, text, used):
        out.append({"id": f"F{no + len(out)}", "research": True, "company": company, "label": label,
                    "text": f"{company} {label} {text} (computed: " + "; ".join(f"{c[k]['label']} {c[k]['id']}" for c, k in used)
                    + ")", "derived": True})
    for co, c in by.items():
        ocf, capex, cash = v(c, "operating cash flow"), v(c, "capital expenditure"), v(c, "cash and cash equivalents")
        price, shares, eps = v(c, "share price"), v(c, "shares outstanding"), v(c, "diluted EPS")
        oi, tax, ni = v(c, "operating income"), v(c, "income tax expense"), v(c, "net income (loss)")
        eq = v(c, "shareholders' equity")
        debt = v(c, "long-term debt (non-current)") or v(c, "long-term debt")
        if ocf is not None and capex is not None:
            add(co, "free cash flow", f"{_money(ocf - capex, 'USD')} = operating cash flow {_money(ocf, 'USD')} less "
                f"capital expenditure {_money(capex, 'USD')}", [(c, "operating cash flow"), (c, "capital expenditure")])
        if cash is not None and capex:
            add(co, "cash cover of capex", f"{cash / capex:.1f}x = cash {_money(cash, 'USD')} / capital expenditure "
                f"{_money(capex, 'USD')}", [(c, "cash and cash equivalents"), (c, "capital expenditure")])
        if price and shares:
            add(co, "market capitalisation", f"{_money(price * shares, 'USD')} = share price ${price:,.2f} x "
                f"{shares / 1e6:,.1f}M shares", [(c, "share price"), (c, "shares outstanding")])
            add(co, "per $1B returned", f"a buyback retires {1e9 / price / 1e6:,.2f}M shares ({1e9 / price / shares:.2%} of "
                f"{shares / 1e6:,.1f}M) at ${price:,.2f}; a special dividend pays ${1e9 / shares:,.2f} per share",
                [(c, "share price"), (c, "shares outstanding")])
        if price and eps:
            add(co, "earnings yield", f"{eps / price:.2%} = diluted EPS ${eps:,.2f} / share price ${price:,.2f} (the "
                "return a buyback earns on each dollar, before lost interest on the cash)",
                [(c, "diluted EPS"), (c, "share price")])
        if price and eps and shares and ni:
            left = shares - 1e9 / price
            add(co, "diluted EPS after a $1B buyback", f"${eps * shares / left:,.2f} "
                f"= EPS ${eps:,.2f} x {shares / 1e6:,.1f}M / {left / 1e6:,.1f}M shares (before lost interest on the cash)",
                [(c, "diluted EPS"), (c, "shares outstanding"), (c, "share price")])
        if oi and tax is not None and ni and eq is not None:
            rate = tax / (ni + tax) if ni + tax else 0
            cap = eq + (debt or 0)
            if cap > 0:
                add(co, "after-tax return on capital", f"{oi * (1 - rate) / cap:.1%} = operating income {_money(oi, 'USD')} "
                    f"x (1 - {rate:.1%} effective tax rate) / (equity {_money(eq, 'USD')} + long-term debt "
                    f"{_money(debt or 0, 'USD')})", [(c, "operating income"), (c, "income tax expense"),
                                                     (c, "net income (loss)"), (c, "shareholders' equity")]
                    + ([(c, "long-term debt (non-current)")] if "long-term debt (non-current)" in c else []))
    return out


def investigate(questions: list[dict | str], facts: list[dict], *, client=None, models: list[str] | None = None,
                out_dir: str | None = None, rules: dict | None = None, task_id: str = "forge-research",
                search_fn=None, fetch_fn=None, names: list[str] | None = None) -> dict:
    """Answer each question from the web (capped and timed). Returns {"facts": [new facts], "questions": [{question,
    why, status, facts, sources}], "seconds", "budget"}. New fact ids continue after the highest F id in `facts`."""
    from engines.research.library import Corpus
    cp = caps(rules)
    t0 = time.time()
    qs = [q if isinstance(q, dict) else {"question": str(q), "why": ""} for q in questions][:int(cp["max_questions"])]
    folder = Path(out_dir or ".") / ".research"
    corpus = Corpus(str(folder), search_fn=search_fn, fetch_fn=fetch_fn)
    budget = float(cp["time_budget_s"])
    k = int(cp["facts_per_question"])
    n_src = int(cp["max_sources_per_question"])

    edgar_ok = search_fn is None and os.environ.get("MH_RESEARCH_EDGAR", "1") != "0"   # tests stub the web
    dead0: set = set()
    need = [q for q in qs if not q.get("queries")]       # a caller (the dossier) may bring its own queries
    mq = _queries_model([q["question"] for q in need], client, models, task_id, dead0) if need else None
    for i, q in enumerate(need):                         # short queries a search engine can use, not the sentence
        q["queries"] = [x for x in ((mq[i] if mq else []) + [_keywords(q["question"])]) if x.strip()][:3]

    def gather(q):                                     # search + read in parallel across questions
        if time.time() - t0 > budget:
            return q, None, "skipped: time budget used"
        try:
            got = corpus.search_and_read(q["queries"], per_query=max(4, n_src + 2), max_new=n_src,
                                         workers=n_src, recency=q.get("recency") or "")
            if edgar_ok:                                # primary filings: the place a banker checks first
                names = [w for w in _keywords(q["question"], 6).split() if w[:1].isupper() and len(w) > 2][:2]
                if names:
                    hits = corpus.edgar_search(" ".join(f'"{w}"' for w in names) + " " + " ".join(
                        w for w in _keywords(q["question"], 8).split() if w not in names)[:80],
                        forms="10-K,10-Q,8-K,S-1,DEF 14A", start=time.strftime("%Y-01-01", time.gmtime(
                            time.time() - 2 * 365 * 86400)), n=3)
                    for h in hits[:1]:
                        s_ = corpus.add_edgar(h)
                        if s_:
                            got = list(got or []) + [s_]
            return q, got, ""
        except Exception as e:  # noqa: BLE001
            return q, None, f"search failed: {str(e)[:120]}"
    with ThreadPoolExecutor(max(1, min(4, len(qs)))) as ex:
        gathered = list(ex.map(gather, qs))
    new_facts: list[dict] = []
    if edgar_ok:                                     # reported figures for every company the questions name
        names = list(names or [])
        for q in qs if not names else []:
            names += [w for w in _keywords(q["question"], 8).split() if w[:1].isupper() and len(w) > 2
                      and not re.match(r"^(EV|NTM|GW|DCF|WACC|EPS|IC|CEO|CFO|FY\d*|Q\d)$", w)][:3]
        known0 = {re.sub(r"\W+", "", f.get("text", "").lower())[:200]: f for f in facts}
        for f in xbrl_facts(names, next_fact_no(facts)):
            same = known0.get(re.sub(r"\W+", "", f["text"].lower())[:200])
            if same is None:
                new_facts.append(f)
            else:                                        # already a fact: give it the value so it can be computed on
                same.update({k: f[k] for k in ("company", "label", "value", "annual", "end") if k in f})
        if os.environ.get("MH_RESEARCH_MARKET", "1") != "0":   # price, range, beta, risk-free: what a valuation needs
            have = " ".join(f.get("text", "") for f in facts)
            tics = [t for t in _tickers(names) if f"({t[1]}) share price" not in have]
            for f in market_facts(tics, next_fact_no(facts + new_facts), rates="Treasury yield" not in have):
                new_facts.append(f)
        for i, f in enumerate(new_facts):              # one F sequence across both sources
            f["id"] = f"F{next_fact_no(facts) + i}"
        if os.environ.get("MH_RESEARCH_ECONOMICS", "1") != "0":   # after numbering: the texts cite the inputs' ids
            have = {(f.get("company"), f.get("label")) for f in facts if f.get("derived")}
            new_facts += [f for f in economics_facts(facts + new_facts, next_fact_no(facts + new_facts))
                          if (f["company"], f["label"]) not in have]
    dead: set = set()
    log = []
    no = next_fact_no(facts + new_facts)                # web facts continue after the filed and market ones
    known = {re.sub(r"\W+", "", f.get("text", "").split(" (Source:")[0].lower())[:200] for f in facts}
    # a fact's stored text carries its source line; an earlier research step's sentence is matched without it
    for q, got, why in gathered:
        entry = {"question": q["question"], "why": q.get("why", ""), "persona": q.get("persona", ""),
                 "where": q.get("where", ""), "facts": [], "sources": []}
        if got is None:
            entry["status"] = why
            log.append(entry)
            continue
        passages = corpus.find(q["question"], k=6, per_source=2, min_tier=3)
        picked = None
        if client is not None and models and passages and time.time() - t0 <= budget and "models" not in dead:
            picked = _pick_model(q["question"], passages, k, client, models, task_id, dead)
        if picked is None:
            picked = _pick_plain(q["question"], passages, k)
        for text, p in picked:
            if not _holds(text, p.text):                # a number or words the passage does not state: dropped
                continue
            key = re.sub(r"\W+", "", text.lower())[:200]
            if key in known:
                continue
            known.add(key)
            f = {"id": f"F{no}", "text": f"{text} {_source_line(p)}", "passage": p.id,
                 "source": p.source.label if p.source else "", "url": p.source.url if p.source else None,
                 "tier": p.source.tier if p.source else None, "research": True, "question": q["question"]}
            no += 1
            new_facts.append(f)
            entry["facts"].append(f["id"])
            if f["url"] and f["url"] not in entry["sources"]:
                entry["sources"].append(f["url"])
        entry["status"] = "found" if entry["facts"] else "nothing found"
        log.append(entry)
    try:
        corpus.save()
    except Exception:  # noqa: BLE001
        pass
    return {"facts": new_facts, "questions": log, "seconds": round(time.time() - t0, 1),
            "budget": {"max_questions": cp["max_questions"], "time_budget_s": budget,
                       "dropped_questions": max(0, len(questions) - len(qs))},
            "failures": corpus.failures[-10:]}
