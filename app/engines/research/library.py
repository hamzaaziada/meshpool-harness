"""Research library: collect sources, keep them as passages with stable ids, rank them, extract numbers, and export
what was found to reports, decks and models with passage-level citations.

    from engines.research import library as R
    c = R.Corpus(".")                                   # sources.json in the working folder (loaded if present)
    c.search_and_read(["nvidia data center revenue fy2026", "hyperscaler capex 2026 guidance"], per_query=6)
    c.add_url("https://www.sec.gov/..."); c.add_file("cim.pdf"); c.add_text("Call notes", text, publisher="Client")
    print(c.overview())                                 # id, tier, publisher, date, title, passages, duplicates
    for p in c.find("data center revenue growth", k=8): print(p.cite, p.source.label, p.text[:300])
    for n in c.numbers("data center revenue", k=20): print(n)    # values with unit, scale and passage id
    c.save()
    R.finalize("report.md", c)                          # appends the Sources list, writes report.docx

Citations in a report are passage ids in square brackets: [S3.p12] or [S3.p12; S7.p2]. Every number must appear in
a cited passage (scale-aware: "$4.2bn" matches "$4,200 million"), or be a calculation written as
[calc: 4.2/3.1-1] whose inputs appear in the cited passages. Quotes must be verbatim. verify.check() enforces it.

Authority tiers (1 is best): 1 primary (filings, regulators, statistics agencies, central banks, issuer releases
and investor-relations pages), 2 reputable secondary (wire services and financial press, rating agencies, academic
and major research institutions), 3 other (trade press, consultancies' blogs, encyclopaedias, company blogs),
4 low (forums, user-generated content, content farms). Duplicates (syndicated copies) are marked and not counted as
independent confirmation.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

STORE = "sources.json"
PASSAGE_CHARS = (300, 900)                 # merge paragraphs to at least / split above (characters)

def _hosts(*names: str) -> str:
    """Host regex matching whole domain labels: _hosts("ft.com") matches ft.com and www.ft.com, not microsoft.com."""
    return r"(^|\.)(" + "|".join(re.escape(n) for n in names) + r")$"


TIER_RULES: list[tuple[int, str, str]] = [  # (tier, kind, host regex), first match wins; hosts are www-stripped
    (1, "filing", _hosts("sec.gov", "companieshouse.gov.uk", "esma.europa.eu", "disclosure.edinet-fsa.go.jp",
                         "filings.xbrl.org")),
    (1, "regulator / statistics", r"\.gov(\.[a-z]{2})?$|\.gouv\.fr$|\.gob\.[a-z]{2}$|\.go\.jp$|" + _hosts(
        "europa.eu", "federalreserve.gov", "bankofengland.co.uk", "bis.org", "imf.org", "worldbank.org", "oecd.org",
        "un.org", "iea.org", "fred.stlouisfed.org", "stlouisfed.org", "ons.gov.uk", "ecb.europa.eu")),
    (1, "issuer release", _hosts("prnewswire.com", "businesswire.com", "globenewswire.com") +
        r"|^(ir|investors?|investor-relations|newsroom)\.[^.]+\."),     # ir.<company>.<tld>, not investors.com
    (2, "financial press / wire", _hosts(
        "reuters.com", "bloomberg.com", "ft.com", "wsj.com", "economist.com", "nytimes.com", "apnews.com",
        "cnbc.com", "barrons.com", "nikkei.com", "asia.nikkei.com", "handelsblatt.com", "lesechos.fr",
        "theinformation.com", "axios.com", "marketwatch.com", "fortune.com", "forbes.com")),
    (2, "rating agency / data", _hosts("spglobal.com", "moodys.com", "fitchratings.com", "morningstar.com",
                                       "msci.com", "factset.com", "pitchbook.com", "gartner.com", "idc.com")),
    (2, "bank / broker research", _hosts(
        "goldmansachs.com", "gs.com", "morganstanley.com", "jpmorgan.com", "jpmorganchase.com", "ubs.com",
        "bofa.com", "bankofamerica.com", "ml.com", "citi.com", "citigroup.com", "db.com", "barclays.com",
        "barclays.co.uk", "credit-suisse.com", "jefferies.com", "lazard.com", "evercore.com", "bernstein.com",
        "alliancebernstein.com", "rbccm.com", "bmo.com", "hsbc.com", "nomura.com", "macquarie.com",
        "wellsfargo.com", "piper.com", "pipersandler.com", "williamblair.com", "cowen.com", "td.com",
        "kbw.com", "raymondjames.com", "stifel.com", "baird.com", "berenberg.com", "kepler-cheuvreux.com",
        "bnpparibas.com", "socgen.com", "natixis.com", "mizuhogroup.com", "daiwa.co.jp", "cicc.com")),
    (2, "academic / research institution", r"\.edu$|\.ac\.[a-z]{2}$|" + _hosts(
        "nber.org", "ssrn.com", "arxiv.org", "nature.com", "sciencedirect.com", "jstor.org", "mckinsey.com",
        "bcg.com", "bain.com")),
    (4, "user-generated / forum", _hosts(
        "reddit.com", "quora.com", "medium.com", "tumblr.com", "stackexchange.com", "wallstreetoasis.com",
        "fool.com", "investorplace.com", "benzinga.com", "tipranks.com", "wordpress.com", "blogspot.com",
        "substack.com")),
    (3, "encyclopaedia / explainer", _hosts("wikipedia.org", "investopedia.com", "britannica.com",
                                            "corporatefinanceinstitute.com", "statista.com", "seekingalpha.com")),
]
ISSUER_PATH = re.compile(r"/(investor|investors|investor-relations|ir|annualreport|annual-report|earnings)(/|$|[-_])",
                         re.I)
# Market-data sites, aggregators and blogs whose URLs carry /earnings-..., /investors/... pages about OTHER companies:
# the issuer-path rule above only counts on a company's own domain.
NOT_ISSUER = _hosts(
    "nasdaq.com", "nyse.com", "marketbeat.com", "investing.com", "yahoo.com", "zacks.com", "marketscreener.com",
    "stocktitan.net", "streetinsider.com", "gurufocus.com", "macrotrends.net", "stockanalysis.com", "finviz.com",
    "simplywall.st", "earningswhispers.com", "wallstreethorizon.com", "thestreet.com", "insidermonkey.com",
    "advfn.com", "investorshub.com", "msn.com", "google.com", "tradingview.com", "alphaspread.com",
    "companiesmarketcap.com", "investors.com", "kiplinger.com", "quiverquant.com", "fintel.io", "barchart.com",
    "cnn.com", "businessinsider.com", "linkedin.com", "x.com", "twitter.com", "youtube.com", "facebook.com")
JUNK = re.compile(r"request (has been|was) blocked|access denied|are you a robot|verify you are (a )?human|"
                  r"enable javascript|captcha|sign in or create|403 forbidden|404 not found|page not found|"
                  r"subscribe to (continue|read)|this content is for subscribers", re.I)
TIER_WEIGHT = {1: 1.35, 2: 1.2, 3: 1.0, 4: 0.6}


def classify(url: str | None, publisher: str | None = None) -> tuple[int, str]:
    """(tier, kind) for a URL. Unknown hosts are tier 3. A user's own file is tier 1 (it is the primary document)."""
    if not url:
        return (1, "provided document") if publisher else (3, "unknown")
    p = urllib.parse.urlparse(url if "://" in url else "https://" + url)
    host = p.netloc.lower().split("@")[-1].split(":")[0].removeprefix("www.")
    for tier, kind, rx in TIER_RULES:
        if re.search(rx, host):
            return tier, kind
    if ISSUER_PATH.search(p.path) and not re.search(NOT_ISSUER, host):   # a company's own investor-relations pages
        return 1, "issuer release"
    return 3, "other"


@dataclass
class Source:
    id: str
    title: str
    url: str | None = None
    publisher: str | None = None
    date: str | None = None                 # published / as-of date, ISO where known
    retrieved: str | None = None
    tier: int = 3
    kind: str = "other"
    sha: str = ""
    duplicate_of: str | None = None
    n_passages: int = 0
    note: str | None = None

    @property
    def label(self) -> str:
        bits = [self.publisher or (urllib.parse.urlparse(self.url).netloc.removeprefix("www.") if self.url else None),
                self.title, self.date[:10] if self.date else None]
        return ", ".join(b for b in bits if b)

    def citation(self) -> str:
        """Bibliography line: [S3] Publisher, "Title", date. URL. Retrieved date. Tier."""
        s = f"[{self.id}] {self.publisher + ', ' if self.publisher else ''}\"{self.title}\""
        s += f", {self.date[:10]}" if self.date else ", undated"
        s += f". {self.url}" if self.url else ""
        s += f". Retrieved {self.retrieved[:10]}" if self.retrieved else ""
        s += f". Tier {self.tier} ({self.kind})"
        return s + (f"; duplicate of {self.duplicate_of}" if self.duplicate_of else "") + "."


@dataclass
class Passage:
    id: str                                 # "S3.p12"
    source_id: str
    text: str
    page: int | None = None
    section: str | None = None

    source: Source | None = field(default=None, repr=False, compare=False)
    score: float = field(default=0.0, repr=False, compare=False)

    @property
    def cite(self) -> str:
        return f"[{self.id}]"

    def to_json(self) -> dict:
        return {"id": self.id, "source_id": self.source_id, "text": self.text, "page": self.page,
                "section": self.section}


# ------------------------------------------------------------------------------------------------ text handling
_WORD = re.compile(r"[^\W_]+", re.U)
STOP = set("a an the and or of to in on for by with at from as is are was were be been it its this that these those "
           "into than then over under per vs via not no but if so such can may will would should could has have had "
           "do does did what which who whom how why when where".split())


def tokens(s: str) -> list[str]:
    return [w for w in (m.group(0).lower() for m in _WORD.finditer(s)) if w not in STOP and len(w) > 1]


def split_passages(text: str) -> list[tuple[str, int | None, str | None]]:
    """Paragraphs merged to 300-900 characters, keeping the page number (docread's '## Page N' markers) and the
    nearest heading. Returns [(text, page, section)]."""
    out: list[tuple[str, int | None, str | None]] = []
    page, sect, buf = None, None, ""

    def flush():
        nonlocal buf
        t = re.sub(r"[ \t]+", " ", buf).strip()
        while len(t) > PASSAGE_CHARS[1]:
            cut = t.rfind(". ", PASSAGE_CHARS[0], PASSAGE_CHARS[1])
            cut = cut + 1 if cut > 0 else PASSAGE_CHARS[1]
            out.append((t[:cut].strip(), page, sect))
            t = t[cut:].strip()
        if t:
            out.append((t, page, sect))
        buf = ""
    for para in re.split(r"\n\s*\n", text or ""):
        para = para.strip()
        if not para:
            continue
        m = re.match(r"^#+\s*Page\s+(\d+)\s*$", para, re.I)
        if m:
            flush()
            page = int(m.group(1))
            continue
        if re.match(r"^#{1,6}\s", para) and len(para) < 160:
            flush()
            sect = para.lstrip("#").strip()
            continue
        buf = (buf + "\n" + para) if buf else para
        if len(buf) >= PASSAGE_CHARS[0]:
            flush()
    flush()
    return out


def _shingles(text: str, k: int = 8) -> set[int]:
    w = tokens(text)[:6000]
    return {hash(" ".join(w[i:i + k])) for i in range(max(0, len(w) - k + 1))}


# ------------------------------------------------------------------------------------------------ numbers
SCALE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "mn": 1e6, "mm": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9,
         "b": 1e9, "trillion": 1e12, "tn": 1e12, "t": 1e12}
CURRENCY = {"$": "USD", "us$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP",
            "¥": "JPY", "jpy": "JPY", "chf": "CHF", "a$": "AUD", "c$": "CAD"}
NUM_RE = re.compile(
    r"(?P<cur>US\$|A\$|C\$|[$€£¥]|\b(?:USD|EUR|GBP|JPY|CHF)\s?)?"
    r"(?P<num>(?<![\w.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])-?\d+(?:\.\d+)?)"
    r"(?:\s?(?P<scale>thousand|million|billion|trillion|mn|mm|bn|tn|[kmbt])\b)?"
    r"\s?(?P<unit>%|percent\b|per cent\b|bps\b|basis points\b|x\b|times\b|pp\b|percentage points\b)?",
    re.I)


@dataclass
class Number:
    value: float                            # in base units (USD, %, x...) after scale
    raw: str
    unit: str                               # "USD", "%", "x", "bps", "" ...
    decimals: int                           # digits after the decimal point as written (for rounding tolerance)
    scale: float
    passage_id: str | None = None
    context: str = ""

    def __str__(self):
        return f"{self.raw!r} = {self.value:g} {self.unit} ({self.passage_id}): {self.context}"


def parse_numbers(text: str, passage_id: str | None = None, keep_years: bool = False) -> list[Number]:
    """Numbers with currency, scale and unit. Plain years (1900-2100 without a unit) are skipped unless keep_years."""
    out = []
    for m in NUM_RE.finditer(text or ""):
        num = m.group("num")
        cur = (m.group("cur") or "").strip().lower()
        sc = (m.group("scale") or "").lower()
        unit = (m.group("unit") or "").lower()
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        # single-letter scales only count after a currency (avoids "5 m" distance, "3 t" weights)
        if sc in ("k", "m", "b", "t") and not cur:
            sc = ""
        if not keep_years and not cur and not sc and not unit and "," not in num and "." not in num \
                and 1900 <= v <= 2100:
            continue
        mult = SCALE.get(sc, 1.0)
        u = CURRENCY.get(cur, "")
        if unit in ("%", "percent", "per cent"):
            u = "%"
        elif unit in ("pp", "percentage points"):
            u = "pp"
        elif unit in ("bps", "basis points"):
            u = "bps"
        elif unit in ("x", "times"):
            u = "x"
        dec = len(num.split(".")[1]) if "." in num else 0
        s, e = max(0, m.start() - 70), min(len(text), m.end() + 70)
        out.append(Number(v * mult, m.group(0).strip(), u, dec, mult, passage_id, text[s:e].replace("\n", " ")))
    return out


def same_number(a: Number, b: Number, rel: float = 0.006) -> bool:
    """a (as written in a report) matches b (in a source): equal units (a bare number matches any unit) and equal
    within rounding of a's written precision or 0.6%."""
    if a.unit and b.unit and a.unit != b.unit and not ({a.unit, b.unit} <= {"pp", "%"}):
        return False
    if b.value == a.value:
        return True
    tol = max(abs(b.value) * rel, 0.5 * 10 ** (-a.decimals) * a.scale)
    return abs(a.value - b.value) <= tol


# ------------------------------------------------------------------------------------------------ the corpus
class Corpus:
    """Sources and passages for one research task, persisted as sources.json in the working folder."""

    def __init__(self, folder: str = ".", search_fn=None, fetch_fn=None, now: str | None = None):
        self.folder = Path(folder)
        self.path = self.folder / STORE
        self.sources: dict[str, Source] = {}
        self.passages: dict[str, Passage] = {}
        self.queries: list[str] = []
        self.failures: list[dict] = []
        self._search, self._fetch = search_fn, fetch_fn
        self.now = now or time.strftime("%Y-%m-%d")
        self._sh: dict[str, set[int]] = {}
        if self.path.exists():
            self.load()

    # ---- persistence
    def load(self):
        d = json.loads(self.path.read_text())
        self.sources = {s["id"]: Source(**s) for s in d.get("sources", [])}
        self.passages = {}
        for p in d.get("passages", []):
            pp = Passage(**p)
            pp.source = self.sources.get(pp.source_id)
            self.passages[pp.id] = pp
        self.queries = d.get("queries", [])
        self.failures = d.get("failures", [])
        for sid in self.sources:
            self._sh[sid] = _shingles(" ".join(p.text for p in self.passages.values() if p.source_id == sid))
        self._index = None

    def save(self) -> str:
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "created": self.now, "queries": self.queries, "failures": self.failures[-50:],
            "sources": [asdict(s) for s in self.sources.values()],
            "passages": [p.to_json() for p in self.passages.values()]}, indent=1))
        return str(self.path)

    # ---- adding sources
    def _next_id(self) -> str:
        return f"S{len(self.sources) + 1}"

    def add_text(self, title: str, text: str, url: str | None = None, publisher: str | None = None,
                 date: str | None = None, tier: int | None = None, kind: str | None = None,
                 note: str | None = None) -> Source | None:
        """Add a document's text. Returns the Source (an existing one if the same URL or identical text was added)."""
        text = (text or "").strip()
        if len(text) < 80:
            self.failures.append({"what": url or title, "why": "too little text"})
            return None
        if len(text) < 3000 and (JUNK.search(title or "") or JUNK.search(text[:1500])):
            self.failures.append({"what": url or title, "why": "blocked, login or error page, not content"})
            return None
        sha = hashlib.sha256(re.sub(r"\s+", " ", text).encode()).hexdigest()[:16]
        for s in self.sources.values():
            if (url and s.url == url) or s.sha == sha:
                return s
        t, k = classify(url, publisher)
        sid = self._next_id()
        src = Source(sid, (title or url or sid).strip()[:200], url, publisher, (date or None), self.now,
                     tier if tier is not None else t, kind or k, sha, note=note)
        sh = _shingles(text)
        for oid, osh in self._sh.items():                       # syndicated copies are not independent
            if sh and osh and len(sh & osh) / max(1, min(len(sh), len(osh))) > 0.6:
                src.duplicate_of = self.sources[oid].duplicate_of or oid
                break
        self._sh[sid] = sh
        parts = split_passages(text)
        for i, (pt, page, sect) in enumerate(parts, start=1):
            pid = f"{sid}.p{i}"
            self.passages[pid] = Passage(pid, sid, pt, page, sect, src)
        src.n_passages = len(parts)
        self.sources[sid] = src
        self._index = None
        return src

    def add_url(self, url: str, publisher: str | None = None) -> Source | None:
        fetch = self._fetch or _web().fetch
        try:
            page = fetch(url)
        except Exception as e:                                   # noqa: BLE001 - a dead source is recorded, not fatal
            self.failures.append({"what": url, "why": str(e)[:200]})
            return None
        return self.add_text(page.get("title") or url, page.get("text") or "", page.get("url") or url,
                             publisher or _publisher(page.get("url") or url), page.get("date"))

    def add_file(self, path: str, title: str | None = None, publisher: str | None = None,
                 date: str | None = None) -> Source | None:
        """A provided document (PDF, Word, Excel, PowerPoint, text). Pages are kept for PDF citations."""
        from meshpool_harness.docread import extract
        p = Path(path)
        try:
            text = extract(str(p))
            text = text if isinstance(text, str) else (text.get("text") if isinstance(text, dict) else str(text))
        except Exception as e:                                   # noqa: BLE001
            self.failures.append({"what": str(p), "why": str(e)[:200]})
            return None
        return self.add_text(title or p.name, text, None, publisher or "Provided document", date, tier=1,
                             kind="provided document")

    def search(self, query: str, n: int = 8, recency: str = "", site: str = "") -> list[dict]:
        search = self._search or _web().search
        self.queries.append(query + (f" site:{site}" if site else ""))
        try:
            return search(query, n, recency, "", site)
        except Exception as e:                                   # noqa: BLE001
            self.failures.append({"what": f"search: {query}", "why": str(e)[:200]})
            return []

    def search_and_read(self, queries: list[str], per_query: int = 6, recency: str = "", site: str = "",
                        max_new: int = 30, workers: int = 6) -> list[Source]:
        """Run the queries, open the top results (best first, interleaved across queries, skipping social sites and
        already-read URLs) in parallel, and add them. Returns the new sources."""
        rows = [self.search(q, per_query, recency, site) for q in queries]
        return self._read_rows(rows, max_new, workers)

    def _read_rows(self, rows: list[list[dict]], max_new: int = 30, workers: int = 6) -> list[Source]:
        seen = {s.url for s in self.sources.values() if s.url}
        order, keys = [], set()
        for i in range(max((len(r) for r in rows), default=0)):
            for r in rows:
                if i < len(r):
                    u = r[i].get("url")
                    k = _norm(u) if u else None
                    if not u or k in keys or u in seen or any(h in u for h in _skip_hosts()):
                        continue
                    keys.add(k)
                    order.append(r[i])
        order = order[:max_new]
        new = []
        fetch = self._fetch or _web().fetch
        with ThreadPoolExecutor(max(1, workers)) as ex:
            futs = {ex.submit(fetch, r["url"]): r for r in order}
            got = {}
            for f in as_completed(futs):
                r = futs[f]
                try:
                    got[r["url"]] = f.result()
                except Exception as e:                           # noqa: BLE001
                    self.failures.append({"what": r["url"], "why": str(e)[:200]})
        for r in order:                                          # add in rank order so ids follow relevance
            page = got.get(r["url"])
            if not page:
                continue
            s = self.add_text(page.get("title") or r.get("title") or r["url"], page.get("text") or "",
                              page.get("url") or r["url"], _publisher(page.get("url") or r["url"]),
                              page.get("date") or r.get("date"))
            if s is not None and s not in new:
                new.append(s)
        return new

    # ---- existing reports: a strong starting point, never the final word
    REPORT_QUERIES = [
        '{s} equity research report filetype:pdf', '{s} initiating coverage pdf', '{s} analyst report price target pdf',
        '{s} investor presentation pdf', '{s} annual report pdf', '{s} industry report pdf',
        '{s} market report filetype:pdf', '"{s}" report.pdf', '{s} deep dive pdf']

    def find_reports(self, subject: str, extra: list[str] | None = None, per_query: int = 5, max_new: int = 12,
                     pdf_only: bool = False) -> list[Source]:
        """Search for reports others already wrote about the subject (broker and equity research, industry and market
        reports, investor presentations, annual reports), mostly PDFs found with filetype:pdf / "report.pdf" queries.
        They are marked as existing reports: use their structure, drivers and conclusions as hypotheses, then verify
        each figure you rely on against primary sources (c.corroborate). Returns the new sources."""
        qs = [q.format(s=subject) for q in self.REPORT_QUERIES] + list(extra or [])
        rows = [self.search(q, per_query) for q in qs]
        keep = []
        for r in rows:
            for x in r:
                u = (x.get("url") or "").lower()
                is_pdf = u.split("?")[0].endswith(".pdf")
                if pdf_only and not is_pdf:
                    continue
                if is_pdf or re.search(r"report|research|presentation|outlook|initiat|coverage|annual", u + " "
                                       + (x.get("title") or "").lower()):
                    keep.append(x)
        new = self._read_rows([keep], max_new)
        for src in new:
            src.note = "existing report: a starting point; verify the figures you use against primary sources"
            if src.kind in ("other", "encyclopaedia / explainer"):
                src.kind = "existing report"
        return new

    def corroborate(self, number_or_pid, text: str | None = None, k: int = 8) -> list[dict]:
        """Independent confirmation of a figure. number_or_pid: a passage id ("S4.p2", checks every figure in it) or
        a figure as written ("$170 billion"); text: words around it (metric, period) to search with. Returns, per
        figure, the passages in OTHER independent sources that state the same value: [{figure, from, confirmed_by:
        [(passage id, tier)], best_tier}]. A figure only one secondary source states is a hypothesis, not a fact."""
        if isinstance(number_or_pid, str) and re.match(r"^S\d+\.p\d+$", number_or_pid.strip("[]")):
            p = self.passage(number_or_pid)
            if not p:
                raise KeyError(number_or_pid)
            figs, origin, query = parse_numbers(p.text, p.id), p.source_id, text or p.text
        else:
            figs = parse_numbers(str(number_or_pid), keep_years=False)
            origin, query = None, text or str(number_or_pid)
        root = lambda sid: (self.sources[sid].duplicate_of or sid) if sid in self.sources else sid  # noqa: E731
        cands = self.find(query, k=max(k, 20), per_source=4, include_duplicates=False)
        out = []
        for f in figs:
            hits = []
            for p in cands:
                if origin and root(p.source_id) == root(origin):
                    continue
                if any(same_number(f, n) for n in parse_numbers(p.text, p.id)):
                    hits.append((p.id, self.sources[p.source_id].tier))
            out.append({"figure": f.raw, "from": f.passage_id, "confirmed_by": hits,
                        "best_tier": min((t for _, t in hits), default=None)})
        return out

    # ---- SEC EDGAR full-text search (filings and exhibits since 2001)
    def edgar_search(self, query: str, forms: str = "", start: str = "", end: str = "", ciks: str = "",
                     n: int = 10) -> list[dict]:
        """EDGAR full-text search. query supports quotes, OR, NEAR(); forms e.g. "10-K,10-Q,8-K"; dates YYYY-MM-DD;
        ciks comma-separated. Returns [{url, title, form, filed, period, company, adsh}] (main documents first)."""
        import urllib.parse as up
        qs = {"q": query}
        if forms:
            qs["forms"] = forms
        if start or end:
            qs.update(dateRange="custom", startdt=start or "2001-01-01", enddt=end or self.now)
        if ciks:
            qs["ciks"] = ciks
        self.queries.append(f"EDGAR: {query} {forms}".strip())
        try:
            from engines.sheets.spreading import _get, default_user_agent
            d = json.loads(_get("https://efts.sec.gov/LATEST/search-index?" + up.urlencode(qs), default_user_agent()))
        except Exception as e:                                   # noqa: BLE001
            self.failures.append({"what": f"EDGAR search: {query}", "why": str(e)[:200]})
            return []
        out = []
        for h in d.get("hits", {}).get("hits", []):
            src = h.get("_source", {})
            adsh, _, fname = h.get("_id", "").partition(":")
            cik = (src.get("ciks") or ["0"])[0]
            company = re.sub(r"\s*\(CIK \d+\)\s*$", "", (src.get("display_names") or [""])[0]).strip()
            out.append({"url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{adsh.replace('-', '')}/{fname}",
                        "title": f"{src.get('form', '')} for period ending {src.get('period_ending') or 'n/a'} "
                                 f"({fname})",
                        "form": src.get("form"), "filed": src.get("file_date"), "period": src.get("period_ending"),
                        "company": re.sub(r"\s+", " ", company), "adsh": adsh,
                        "exhibit": not fname.lower().startswith(adsh.replace("-", "")[:10].lower())
                        and bool(re.search(r"ex|exhibit", fname, re.I))})
        out.sort(key=lambda r: r["exhibit"])
        return out[:n]

    def add_edgar(self, hit_or_url, title: str | None = None) -> Source | None:
        """Read one EDGAR document (a hit from edgar_search or a sec.gov URL) with the SEC User-Agent; the source
        is dated with the filing date and published as the company's filing (tier 1)."""
        hit = hit_or_url if isinstance(hit_or_url, dict) else {"url": hit_or_url}
        url = hit["url"]
        try:
            if self._fetch is not None:
                page = self._fetch(url)
            else:
                from engines.sheets.spreading import _get, default_user_agent
                page = _web().html_to_text(_get(url, default_user_agent()).decode("utf-8", "replace"), url)
        except Exception as e:                                   # noqa: BLE001
            self.failures.append({"what": url, "why": str(e)[:200]})
            return None
        return self.add_text(title or hit.get("title") or page.get("title") or url, page.get("text") or "", url,
                             hit.get("company") or "SEC EDGAR", hit.get("filed") or page.get("date"),
                             tier=1, kind=f"filing {hit.get('form') or ''}".strip())

    # ---- reading
    def overview(self) -> str:
        rows = ["id   tier  kind                       date        passages  publisher / title"]
        for s in self.sources.values():
            rows.append(f"{s.id:<5}{s.tier:<6}{s.kind[:26]:<27}{(s.date or '')[:10]:<12}{s.n_passages:<10}"
                        f"{(s.publisher or '')[:28]} | {s.title[:70]}" + (f"  (dup of {s.duplicate_of})"
                                                                           if s.duplicate_of else ""))
        if self.failures:
            rows.append(f"{len(self.failures)} sources could not be read (see failures in sources.json)")
        return "\n".join(rows)

    def _build_index(self):
        docs = {pid: tokens(p.text + " " + (p.section or "") + " " + (p.source.title if p.source else ""))
                for pid, p in self.passages.items()}
        df: dict[str, int] = {}
        for t in docs.values():
            for w in set(t):
                df[w] = df.get(w, 0) + 1
        avg = sum(len(t) for t in docs.values()) / max(1, len(docs))
        self._index = (docs, df, avg)

    def find(self, query: str, k: int = 8, per_source: int = 2, min_tier: int = 4, prefer_recent: bool = True,
             include_duplicates: bool = False) -> list[Passage]:
        """Best passages for a question: BM25 relevance x authority tier x recency, at most per_source passages per
        source, duplicates of an already-used source skipped."""
        if not self.passages:
            return []
        if self._index is None:
            self._build_index()
        docs, df, avg = self._index
        q = tokens(query)
        N = len(docs)
        scored = []
        for pid, toks in docs.items():
            p = self.passages[pid]
            s = p.source or self.sources.get(p.source_id)
            if s is None or s.tier > min_tier or (s.duplicate_of and not include_duplicates):
                continue
            tf: dict[str, int] = {}
            for w in toks:
                tf[w] = tf.get(w, 0) + 1
            sc = 0.0
            for w in set(q):
                if w in tf:
                    idf = math.log(1 + (N - df[w] + 0.5) / (df[w] + 0.5))
                    sc += idf * tf[w] * 2.2 / (tf[w] + 1.2 * (0.25 + 0.75 * len(toks) / max(avg, 1)))
            if sc <= 0:
                continue
            if len(q) > 1:                                      # phrase proximity: query word pairs side by side
                pairs = set(zip(toks, toks[1:]))
                hits = sum((a, b) in pairs for a, b in zip(q, q[1:]))
                sc *= 1 + 0.6 * hits / (len(q) - 1)
            sc *= TIER_WEIGHT.get(s.tier, 1.0)
            if prefer_recent and s.date:
                age = _age_years(s.date, self.now)
                if age is not None:
                    sc *= 1.0 if age <= 1 else max(0.7, 1 - 0.06 * (age - 1))
            scored.append((sc, p, s))                           # local: find() runs from many threads (grid)
        scored.sort(key=lambda x: -x[0])
        out, per, texts = [], {}, set()
        for sc, p, s in scored:
            key = re.sub(r"\W+", "", p.text.lower())[:300]
            if per.get(p.source_id, 0) >= per_source or key in texts:     # filings repeat risk factors verbatim
                continue
            texts.add(key)
            per[p.source_id] = per.get(p.source_id, 0) + 1
            out.append(replace(p, source=s, score=sc))          # a copy: the shared passage is never mutated
            if len(out) >= k:
                break
        return out

    def numbers(self, query: str, k: int = 20, unit: str | None = None) -> list[Number]:
        """Numbers in the passages that best match the query (to triangulate a figure across sources)."""
        out = []
        for p in self.find(query, k=max(k, 12), per_source=3):
            for n in parse_numbers(p.text, p.id):
                if unit is None or n.unit == unit:
                    out.append(n)
        return out[:k]

    def passage(self, pid: str) -> Passage | None:
        return self.passages.get(pid.strip("[]"))

    # ---- exports
    def bibliography(self, cited: set[str] | None = None) -> str:
        ids = [s for s in self.sources if cited is None or s in cited]
        return "\n".join(f"- {self.sources[s].citation()}" for s in ids)

    def facts(self, pids: list[str]) -> list[dict]:
        """Deck facts (forge format plus provenance): one per passage sentence that carries a number."""
        out = []
        for pid in pids:
            p = self.passage(pid)
            if not p:
                continue
            for sent in re.split(r"(?<=[.!?])\s+", p.text):
                if parse_numbers(sent):
                    s = self.sources[p.source_id]
                    out.append({"id": f"F{len(out) + 1}", "text": sent.strip(), "passage": p.id,
                                "source": s.label, "url": s.url, "page": p.page})
        return out

    def assumption_source(self, pid: str) -> dict:
        """Source and Evidence text for an assumption-book row, from a passage id."""
        p = self.passage(pid)
        if not p:
            raise KeyError(pid)
        s = self.sources[p.source_id]
        where = f", p.{p.page}" if p.page else ""
        return {"source": f"{s.label}{where}. {s.url or ''} [{p.id}]".strip(),
                "evidence": p.text[:400]}


def finalize(report_path: str, corpus: Corpus, docx: bool = True) -> dict:
    """Append a Sources section listing every cited source (and a note of what could not be read), save the
    corpus, and write a .docx next to the report. Returns {"report", "docx", "cited"}."""
    from .verify import citations_in, split_sources
    p = Path(report_path)
    text = split_sources(p.read_text())[0].rstrip() + "\n"
    cited = {c.split(".")[0] for c in citations_in(text)}
    text += "\n## Sources\n" + corpus.bibliography(cited) + "\n"
    p.write_text(text)
    corpus.save()
    out = {"report": str(p), "cited": sorted(cited, key=lambda x: int(x[1:]))}
    if docx:
        try:
            out["docx"] = _to_docx(text, p.with_suffix(".docx"))
        except Exception as e:                                  # noqa: BLE001 - the markdown report stands alone
            out["docx_error"] = str(e)
    return out


def _to_docx(md: str, path: Path) -> str:
    import docx
    d = docx.Document()
    for block in md.split("\n"):
        line = block.rstrip()
        if not line:
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            d.add_heading(m.group(2), level=len(m.group(1)))
        elif re.match(r"^\s*[-*]\s+", line):
            d.add_paragraph(re.sub(r"^\s*[-*]\s+", "", line), style="List Bullet")
        elif re.match(r"^\s*\d+\.\s+", line):
            d.add_paragraph(re.sub(r"^\s*\d+\.\s+", "", line), style="List Number")
        else:
            d.add_paragraph(line)
    d.save(str(path))
    return str(path)


# ------------------------------------------------------------------------------------------------ helpers
def _web():
    from meshpool_harness import web
    return web


def _skip_hosts():
    try:
        return _web().SKIP_HOSTS
    except Exception:                                           # noqa: BLE001
        return ("facebook.com", "instagram.com", "pinterest.", "tiktok.com")


def _norm(u: str) -> str:
    p = urllib.parse.urlparse(u)
    return (p.netloc.lower().removeprefix("www.") + p.path.rstrip("/")).lower()


def _publisher(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
    return host


def _age_years(date: str, now: str) -> float | None:
    m, n = re.match(r"(\d{4})(?:-(\d{2}))?", date or ""), re.match(r"(\d{4})(?:-(\d{2}))?", now or "")
    if not m or not n:
        return None
    return (int(n.group(1)) - int(m.group(1))) + (int(n.group(2) or 6) - int(m.group(2) or 6)) / 12
