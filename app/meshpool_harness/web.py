"""Web search, page reading and multi-source research without a browser.

Search runs keyless on DuckDuckGo and Bing in parallel (Mojeek as a fallback) and merges the results. If the user
adds a search API key in settings (Brave, Tavily, Serper or a SearXNG address), that backend goes first.
Pages are fetched over plain HTTPS and reduced to readable text: the main article, headings and lists, with the
title and publish date. PDFs and Office files are read with docread. research() searches several queries, opens
the best sources in parallel and returns the passages that answer the question, with their links.
"""
from __future__ import annotations

import base64
import html
import http.client
import ipaddress
import json
import os
from concurrent.futures import TimeoutError as _FutTimeout
import re
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
HEAD = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, deflate",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8"}
RECENCY = {"day": ("d", 'ex1:"ez1"'), "week": ("w", 'ex1:"ez2"'), "month": ("m", 'ex1:"ez3"'), "year": ("y", "")}
SKIP_HOSTS = ("facebook.com", "instagram.com", "pinterest.", "tiktok.com", "accounts.google.", "login.")
DOC_EXT = (".pdf", ".docx", ".xlsx", ".xls", ".pptx", ".csv")
_CACHE: dict[str, tuple[float, dict]] = {}
_LOCK = threading.Lock()


class WebError(Exception):
    pass


# ---------- HTTP ---------------------------------------------------------------------------------------------------

def _private_allowed() -> bool:
    return os.environ.get("MH_ALLOW_PRIVATE_FETCH", "").strip().lower() in ("1", "true", "yes", "on")


_FAKE_IP = ipaddress.ip_network("198.18.0.0/15")   # answers from proxy apps' fake-IP DNS (Clash, Surge...)


def _refuse(url: str, host: str, ip) -> None:
    raise WebError(f"refused {url}: {host} is a local or private network address ({ip}). Only public web pages can "
                   "be read (set MH_ALLOW_PRIVATE_FETCH=1 to allow local addresses).")


def _check_ip(url: str, host: str, ip) -> None:
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip in _FAKE_IP:
        return                                   # a local proxy app stands in for a public host: it fetches the page
    if not ip.is_global or ip.is_multicast:
        _refuse(url, host, ip)


def check_public(url: str) -> None:
    """Refuse URLs that point at this computer or a private network (loopback, RFC 1918, link-local such as the
    169.254.169.254 cloud metadata address, reserved ranges). Agents call fetch() without asking, so a page or a prompt
    must not be able to make them read local services. Behind a system proxy (common on company Macs and PCs) the
    proxy resolves the name, so only literal addresses and local names are checked. MH_ALLOW_PRIVATE_FETCH=1 turns the
    check off."""
    if _private_allowed():
        return
    u = urllib.parse.urlparse(url)
    scheme = u.scheme.lower()
    if scheme not in ("http", "https"):
        raise WebError(f"refused {url}: only http and https links can be read")
    host = (u.hostname or "").rstrip(".").lower()
    if not host:
        raise WebError(f"refused {url}: no host name")
    try:
        _check_ip(url, host, ipaddress.ip_address(host.split("%", 1)[0]))
        return                                   # a literal address: checked without DNS
    except ValueError:
        pass
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")) or "." not in host:
        _refuse(url, host, "a local name")
    proxied = urllib.request.getproxies().get(scheme) and not urllib.request.proxy_bypass(host)
    try:
        port = u.port or (443 if scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError, OSError) as e:
        if proxied:
            return                               # only the proxy can resolve outside names here
        raise WebError(f"could not reach {host}: {e}")
    for info in infos:
        try:
            ip = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        except ValueError:
            raise WebError(f"refused {url}: unexpected address for {host}")
        _check_ip(url, host, ip)


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    """Check every redirect hop too: a public page must not bounce the request to a local address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public(urllib.parse.urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _inflate(body: bytes, wbits: int, limit: int) -> bytes:
    """Decompress at most limit bytes (a small gzip bomb must not fill memory); a cut-off stream gives what it has."""
    d = zlib.decompressobj(wbits)
    return d.decompress(body, limit)


def _get(url: str, data: bytes | None = None, headers: dict | None = None, timeout: float = 20,
         limit: int = 8_000_000, public_only: bool = False) -> tuple[int, dict, bytes, str]:
    req = urllib.request.Request(url, data=data, headers={**HEAD, **(headers or {})})
    open_ = urllib.request.urlopen
    if public_only:
        check_public(url)
        open_ = urllib.request.build_opener(_PublicRedirects).open
    try:
        with open_(req, timeout=timeout) as r:
            body, hdr, final = r.read(limit), {k.lower(): v for k, v in r.headers.items()}, r.geturl()
            status = r.status
    except urllib.error.HTTPError as e:
        try:
            body = e.read(200_000) if e.fp else b""
        except (OSError, http.client.HTTPException):
            body = b""
        hdr, final, status = {k.lower(): v for k, v in (e.headers or {}).items()}, url, e.code
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as e:
        raise WebError(f"could not reach {urllib.parse.urlparse(url).netloc or url}: {getattr(e, 'reason', e)}")
    enc = hdr.get("content-encoding", "")
    try:
        if "gzip" in enc:
            body = _inflate(body, 16 + zlib.MAX_WBITS, limit)
        elif "deflate" in enc:
            body = _inflate(body, -zlib.MAX_WBITS if body[:1] != b"\x78" else zlib.MAX_WBITS, limit)
    except (OSError, zlib.error, EOFError):
        pass
    return status, hdr, body, final


def _decode(body: bytes, ctype: str) -> str:
    m = re.search(r"charset=([\w-]+)", ctype or "", re.I) or re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', body[:4000], re.I)
    cs = m.group(1) if m else "utf-8"
    cs = cs.decode() if isinstance(cs, bytes) else cs
    try:
        return body.decode(cs, "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


_TAG = re.compile(r"<[^>]+>")


def _untag(s: str, repl: str = " ") -> str:
    """Remove tags in linear time. A bare `<[^>]+>` rescans to the end of the text from every '<' that has no '>'
    after it, which takes minutes on a hostile page; text after the last '>' cannot hold a tag, so leave it out."""
    end = s.rfind(">") + 1
    return _TAG.sub(repl, s[:end]) + s[end:]


def _strip(s: str) -> str:
    s = _untag(s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


# ---------- search -------------------------------------------------------------------------------------------------

def _ddg(q: str, n: int, recency: str = "", region: str = "") -> list[dict]:
    form = {"q": q, "kl": region or "wt-wt"}
    if recency in RECENCY:
        form["df"] = RECENCY[recency][0]
    try:
        st, hdr, body, _ = _get("https://html.duckduckgo.com/html/", data=urllib.parse.urlencode(form).encode(),
                                headers={"Content-Type": "application/x-www-form-urlencoded",
                                         "Referer": "https://html.duckduckgo.com/"}, timeout=12)
    except WebError:
        st, hdr, body, _ = _get("https://html.duckduckgo.com/html/?" + urllib.parse.urlencode(form), timeout=12)
    page = _decode(body, hdr.get("content-type", ""))
    if st != 200 or "result__a" not in page:
        raise WebError(f"DuckDuckGo asked for a check (HTTP {st})")
    out = []
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)',
                         page, re.S):
        href = html.unescape(m.group(1))
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg")
        link = urllib.parse.unquote(qs[0]) if qs else href
        if "duckduckgo.com/y.js" in link:          # ads
            continue
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', m.group(3), re.S)
        out.append({"title": _strip(m.group(2)), "url": link, "snippet": _strip(sn.group(1)) if sn else ""})
        if len(out) >= n:
            break
    return out


def _bing_url(href: str) -> str:
    if "bing.com/ck/a" in href:
        u = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("u", [""])[0]
        if u.startswith("a1"):
            try:
                return base64.urlsafe_b64decode(u[2:] + "=" * (-len(u[2:]) % 4)).decode("utf-8", "replace")
            except ValueError:
                pass
    return href


def _bing(q: str, n: int, recency: str = "", region: str = "") -> list[dict]:
    params = {"q": q, "setlang": "en", "count": str(min(n + 5, 30))}
    if recency in RECENCY and RECENCY[recency][1]:
        params["filters"] = RECENCY[recency][1]
    if region:
        params["cc"] = region.split("-")[0]
    st, hdr, body, _ = _get("https://www.bing.com/search?" + urllib.parse.urlencode(params),
                            headers={"Cookie": "SRCHHPGUSR=ADLT=MODERATE"})
    page = _decode(body, hdr.get("content-type", ""))
    if st != 200:
        raise WebError(f"Bing answered {st}")
    out = []
    for blk in re.findall(r'<li class="b_algo"(.*?)</li>\s*(?=<li class="b_|</ol>)', page, re.S):
        a = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', blk, re.S)
        if not a:
            continue
        sn = re.search(r'<p[^>]*>(.*?)</p>', blk, re.S) or re.search(r'class="b_caption"[^>]*>(.*?)</div>', blk, re.S)
        out.append({"title": _strip(a.group(2)), "url": _bing_url(html.unescape(a.group(1))),
                    "snippet": re.sub(r"^Web\s*", "", _strip(sn.group(1))) if sn else ""})
        if len(out) >= n:
            break
    return out


def _brave_html(q: str, n: int, recency: str = "", region: str = "") -> list[dict]:
    p = {"q": q, "source": "web"}
    if recency in RECENCY:
        p["tf"] = "p" + RECENCY[recency][0]
    st, hdr, body, _ = _get("https://search.brave.com/search?" + urllib.parse.urlencode(p), timeout=12)
    page = _decode(body, hdr.get("content-type", ""))
    if st != 200:
        raise WebError(f"Brave answered {st}")
    out = []
    for blk in page.split('data-type="web"')[1:]:
        a = re.search(r'<a href="(https?://[^"]+)"[^>]*target="_self"', blk)
        t = re.search(r'search-snippet-title[^"]*"[^>]*title="([^"]*)"', blk)
        sn = re.search(r'class="(?:generic-snippet|snippet-description)[^"]*"[^>]*>(?:<div[^>]*>)?(.*?)</div>', blk, re.S)
        if not a or not t:
            continue
        snip = _strip(sn.group(1)) if sn else ""
        out.append({"title": html.unescape(t.group(1)), "url": html.unescape(a.group(1)),
                    "snippet": re.sub(r"^\w{3} \d{1,2}, \d{4} -\s*", "", snip)})
        if len(out) >= n:
            break
    if not out:
        raise WebError("Brave returned no readable results")
    return out


def _relevant(rows: list[dict], q: str) -> list[dict]:
    """Drop a result set that does not match the query (some engines serve unrelated results to automated clients)."""
    terms = [w for w in re.findall(r"[^\W_]{3,}", q.lower()) if w not in ("site", "the", "and", "for", "with")]
    if not rows or not terms:
        return rows
    ok = [r for r in rows if any(t in (r["title"] + " " + r["snippet"] + " " + r["url"]).lower() for t in terms)]
    return ok if len(ok) >= max(1, len(rows) // 2) else []


def _api_backends() -> list:
    """Search APIs the user configured. Each returns the same rows as the keyless backends."""
    out = []
    if os.environ.get("BRAVE_SEARCH_API_KEY"):
        def brave(q, n, recency="", region=""):
            p = {"q": q, "count": str(min(n, 20))}
            if recency in RECENCY:
                p["freshness"] = "p" + RECENCY[recency][0]
            st, hdr, body, _ = _get("https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(p),
                                    headers={"X-Subscription-Token": os.environ["BRAVE_SEARCH_API_KEY"],
                                             "Accept": "application/json"})
            j = json.loads(body or b"{}")
            return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": _strip(r.get("description", "")),
                     "date": r.get("age", "")} for r in (j.get("web") or {}).get("results", [])][:n]
        out.append(("Brave", brave))
    if os.environ.get("TAVILY_API_KEY"):
        def tavily(q, n, recency="", region=""):
            d = {"query": q, "max_results": min(n, 20), "search_depth": "advanced"}
            if recency in RECENCY:
                d["time_range"] = recency
            st, hdr, body, _ = _get("https://api.tavily.com/search", data=json.dumps(d).encode(),
                                    headers={"Content-Type": "application/json", "Accept": "application/json",
                                             "Authorization": "Bearer " + os.environ["TAVILY_API_KEY"]})
            j = json.loads(body or b"{}")
            return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")[:400],
                     "date": r.get("published_date", "")} for r in j.get("results", [])][:n]
        out.append(("Tavily", tavily))
    if os.environ.get("SERPER_API_KEY"):
        def serper(q, n, recency="", region=""):
            d = {"q": q, "num": min(n, 20)}
            if recency in RECENCY:
                d["tbs"] = "qdr:" + RECENCY[recency][0]
            st, hdr, body, _ = _get("https://google.serper.dev/search", data=json.dumps(d).encode(),
                                    headers={"Content-Type": "application/json", "Accept": "application/json",
                                             "X-API-KEY": os.environ["SERPER_API_KEY"]})
            j = json.loads(body or b"{}")
            return [{"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", ""),
                     "date": r.get("date", "")} for r in j.get("organic", [])][:n]
        out.append(("Google (Serper)", serper))
    if os.environ.get("SEARXNG_URL"):
        def searx(q, n, recency="", region=""):
            p = {"q": q, "format": "json"}
            if recency in RECENCY:
                p["time_range"] = recency
            st, hdr, body, _ = _get(os.environ["SEARXNG_URL"].rstrip("/") + "/search?" + urllib.parse.urlencode(p),
                                    headers={"Accept": "application/json"})
            j = json.loads(body or b"{}")
            return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", ""),
                     "date": r.get("publishedDate") or ""} for r in j.get("results", [])][:n]
        out.append(("SearXNG", searx))
    return out


_COOL: dict[str, float] = {}       # engine -> time it may be tried again after it limited us


def _try(name: str, fn, *a):
    if time.time() < _COOL.get(name, 0):
        raise WebError(f"{name} is resting after a rate limit")
    try:
        return fn(*a)
    except WebError as e:
        if any(x in str(e) for x in ("429", "check", "403")):
            _COOL[name] = time.time() + 300
        raise


def _norm(u: str) -> str:
    p = urllib.parse.urlparse(u)
    return (p.netloc.lower().removeprefix("www.") + p.path.rstrip("/")).lower()


def search(query: str, n: int = 8, recency: str = "", region: str = "", site: str = "") -> list[dict]:
    """Merged results from the configured API (if any) and the keyless engines, best first, without duplicates."""
    query = (query or "").strip()
    if not query:
        raise WebError("query is empty")
    if site:
        query += f" site:{site.strip()}"
    n = max(1, min(int(n or 8), 20))
    apis = _api_backends()
    lists, errors = [], []
    if apis:
        for name, fn in apis:
            try:
                rows = fn(query, n, recency, region)
                if rows:
                    lists.append(rows)
                    break
            except (WebError, ValueError) as e:
                errors.append(f"{name}: {e}")
    if len(lists) == 0 or sum(len(x) for x in lists) < n:
        with ThreadPoolExecutor(2) as ex:
            futs = {ex.submit(_try, name, fn, query, n, recency, region): name
                    for name, fn in (("DuckDuckGo", _ddg), ("Brave", _brave_html))}
            got = {}
            for f in as_completed(futs):
                try:
                    got[futs[f]] = _relevant(f.result(), query)
                except (WebError, ValueError) as e:
                    errors.append(f"{futs[f]}: {e}")
            lists += [got[k] for k in ("DuckDuckGo", "Brave") if got.get(k)]
    if not any(lists):
        try:
            rows = _relevant(_bing(query, n, recency, region), query)
            if rows:
                lists.append(rows)
            else:
                errors.append("Bing: unrelated results")
        except (WebError, ValueError) as e:
            errors.append(f"Bing: {e}")
    seen, out = set(), []
    for i in range(max((len(x) for x in lists), default=0)):        # interleave: rank 1 of each, then rank 2, ...
        for rows in lists:
            if i < len(rows):
                r = rows[i]
                k = _norm(r["url"])
                if r["url"].startswith("http") and k not in seen:
                    seen.add(k)
                    out.append(r)
    if not out and errors:
        raise WebError("search failed: " + "; ".join(errors) + ". The free search engines are limiting this "
                       "computer. Add a search key in Settings, Web search, or try again in a minute.")
    return out[:n]


def format_results(rows: list[dict]) -> str:
    if not rows:
        return "No results. Try fewer or different words."
    return "\n".join(f"{i}. {r['title']}\n   {r['url']}" + (f"\n   {r['date']}" if r.get("date") else "")
                     + (f"\n   {r['snippet']}" if r.get("snippet") else "") for i, r in enumerate(rows, 1))


# ---------- reading pages -------------------------------------------------------------------------------------------

_META_TAG = re.compile(r"<meta\b[^>]{0,2000}>", re.I)
_ATTR = re.compile(r"""([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
META_SCAN = 500_000                                  # meta tags live in <head>: never scan a huge page for them


def _metas(page: str) -> dict:
    """{name: content} for the page's <meta> tags (property, name or itemprop; first one wins). Each tag is matched
    once with a bounded pattern, so a hostile page cannot make this slow."""
    out: dict[str, str] = {}
    for m in _META_TAG.finditer(page, 0, min(len(page), META_SCAN)):
        attrs = {}
        for a in _ATTR.finditer(m.group(0), 5):
            attrs.setdefault(a.group(1).lower(), next((g for g in a.groups()[1:] if g is not None), ""))
        content = attrs.get("content")
        if not content:
            continue
        for k in ("property", "name", "itemprop"):
            if attrs.get(k):
                out.setdefault(attrs[k].strip().lower(), html.unescape(content).strip())
    return out


def _meta(page: str | dict, *names: str) -> str:
    tags = page if isinstance(page, dict) else _metas(page)
    for nm in names:
        if tags.get(nm.lower()):
            return tags[nm.lower()]
    return ""


JUNK = re.compile(r"(?:^|[\s_-])(nav|navbar|navbox|menu|sidebar|side-bar|footer|header|masthead|breadcrumbs?|cookie|consent|"
                  r"share|sharing|social|related|recommend|promo|advert|ads?|banner|popup|modal|newsletter|subscribe|"
                  r"comments?|toc|langlinks|interlanguage|catlinks|mw-jump|vector-menu|vector-header|skip|sr-only|"
                  r"visually-hidden|print-only|ambox|hatnote|mbox-small|metadata|noprint|mw-editsection|navigation|login|signup)(?:$|[\s_-])", re.I)
BLOCK = {"p", "div", "section", "article", "main", "ul", "ol", "table", "blockquote", "pre", "figure", "dl", "tr",
         "header", "footer", "aside", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dd", "dt", "br", "hr"}


def _lxml_text(page: str, base: str, links: bool) -> tuple[str, list] | None:
    try:
        import lxml.html
    except ImportError:
        return None
    try:
        doc = lxml.html.document_fromstring(page.encode("utf-8", "replace"))
    except Exception:                                          # noqa: BLE001 - fall back to the regex reader
        return None
    for el in doc.xpath("//script|//style|//noscript|//svg|//template|//iframe|//canvas|//form|//button|//select|"
                        "//nav|//footer|//aside|//input|//textarea|"
                        "//*[@role='navigation']|//*[@role='banner']|//*[@role='contentinfo']"):
        if el.getparent() is not None:
            el.drop_tree()
    for el in doc.xpath("//*[@class or @id]"):
        if el.tag in ("html", "body", "main", "article"):
            continue
        tag = (el.get("class") or "") + " " + (el.get("id") or "")
        if JUNK.search(tag) and el.getparent() is not None and len(el.text_content()) < 4000:
            el.drop_tree()
    root = None
    for xp in ("//article", "//main", "//*[@role='main']", "//*[@id='mw-content-text']", "//*[@id='content']",
               "//*[@itemprop='articleBody']"):
        hits = [h for h in doc.xpath(xp) if len(h.text_content().strip()) > 400]
        if hits:
            root = max(hits, key=lambda h: len(h.text_content()))
            break
    if root is None:                                          # densest block of paragraph text
        best, score = None, 0
        for el in doc.xpath("//div|//section|//td"):
            ps = el.xpath("./p|./div/p")
            sc = sum(len(p.text_content()) for p in ps)
            if sc > score:
                best, score = el, sc
        root = best if best is not None and score > 600 else (doc.find("body") if doc.find("body") is not None else doc)
    found = []
    if links:
        for a in root.xpath(".//a[@href]"):
            t = re.sub(r"\s+", " ", a.text_content()).strip()
            h = a.get("href")
            if t and len(t) > 2 and not h.startswith(("#", "javascript:")):
                found.append((t[:80], urllib.parse.urljoin(base, h)))
    out = []

    def walk(el, pre=False):
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            t = re.sub(r"\s+", " ", el.text_content()).strip()
            if t:
                out.append("\n\n" + "#" * int(tag[1]) + " " + t + "\n\n")
            if el.tail:
                out.append(el.tail)
            return
        if tag == "li":
            out.append("\n- ")
        elif tag in ("td", "th"):
            out.append(" | ")
        elif tag == "img" and el.get("alt"):
            out.append(f" [image: {el.get('alt')}] ")
        elif tag in BLOCK:
            out.append("\n")
        if el.text and tag:
            out.append(el.text)
        for ch in el:
            walk(ch, pre or tag == "pre")
        if tag in BLOCK:
            out.append("\n")
        if el.tail:
            out.append(el.tail)
    walk(root)
    t = "".join(out)
    t = re.sub(r"[ \t\r\f\v\u00a0]+", " ", t)
    t = "\n".join(x.strip() for x in t.split("\n"))
    t = re.sub(r"(?m)^(- )?\|?\s*$", "", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t).strip()
    # long runs of tiny list items are link farms (language lists, tag clouds)
    t = re.sub(r"(?:(?:^|\n\n)- [^\n]{1,28}){12,}", "\n", t)
    return re.sub(r"\n\s*\n+", "\n\n", t).strip(), found


def html_to_text(page: str, base: str = "", links: bool = False) -> dict:
    """Readable text of an HTML page: the main content as light Markdown, plus title, date and description."""
    tags = _metas(page)
    title = _meta(tags, "og:title") or _strip((re.search(r"(?is)<title[^>]*>(.*?)</title>", page) or [None, ""])[1])
    date = _meta(tags, "article:published_time", "datePublished", "pubdate", "date", "og:updated_time", "dc.date")
    if not date:
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', page)
        date = m.group(1) if m else ""
    desc = _meta(tags, "og:description", "description")
    lx = _lxml_text(page, base, links)
    if lx and len(lx[0]) > 200:
        return {"title": title, "date": date, "description": desc, "text": lx[0], "links": lx[1]}
    s = re.sub(r"(?is)<(script|style|noscript|svg|template|iframe|canvas|form|button|select)\b.*?</\1>", " ", page)
    s = re.sub(r"(?is)<!--.*?-->", " ", s)
    body = None
    for pat in (r"(?is)<article\b[^>]*>(.*)</article>", r"(?is)<main\b[^>]*>(.*?)</main>",
                r'(?is)<div[^>]+(?:role=["\']main["\']|id=["\'](?:content|main|article)["\'])[^>]*>(.*)'):
        m = re.search(pat, s)
        if m and len(_strip(m.group(1))) > 400:
            body = m.group(1)
            break
    if body is None:
        m = re.search(r"(?is)<body\b[^>]*>(.*)</body>", s)
        body = m.group(1) if m else s
        body = re.sub(r"(?is)<(nav|header|footer|aside)\b.*?</\1>", " ", body)
    found = []
    if links:
        for a in re.finditer(r'(?is)<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', body):
            t = _strip(a.group(2))
            if t and len(t) > 2:
                found.append((t[:80], urllib.parse.urljoin(base, html.unescape(a.group(1)))))
    b = re.sub(r"(?is)<h([1-6])[^>]*>(.*?)</h\1>", lambda m: "\n\n" + "#" * int(m.group(1)) + " " + _strip(m.group(2)) + "\n\n", body)
    b = re.sub(r"(?is)<li[^>]*>", "\n- ", b)
    b = re.sub(r"(?is)<(tr)[^>]*>", "\n", b)
    b = re.sub(r"(?is)</t[dh]>", " | ", b)
    b = re.sub(r"(?is)<br\s*/?>|</(p|div|li|tr|section|article|blockquote|pre|table|ul|ol|dd|dt)>", "\n", b)
    b = _untag(b)
    b = html.unescape(b)
    b = re.sub(r"[ \t\r\f\v ]+", " ", b)
    b = "\n".join(x.strip() for x in b.split("\n"))
    b = re.sub(r"\n\s*\n+", "\n\n", b).strip()
    b = re.sub(r"(?m)^- \s*$\n?", "", b)
    return {"title": title, "date": date, "description": desc, "text": b, "links": found}


# content types of the documents docread reads, most specific first
CTYPE_EXT = (("wordprocessingml", ".docx"), ("spreadsheetml", ".xlsx"), ("presentationml", ".pptx"),
             ("ms-excel.sheet.macroenabled", ".xlsm"), ("ms-excel", ".xls"), ("text/csv", ".csv"),
             ("text/tab-separated-values", ".tsv"), ("msword", ".doc"), ("ms-powerpoint", ".ppt"))


def _doc_ext(ctype: str, body: bytes, path: str) -> str:
    """The file extension docread needs: PDF by type or magic bytes, then a document extension in the link, then the
    content type (download links often have no extension), else .bin."""
    if "pdf" in ctype or body[:5] == b"%PDF-":
        return ".pdf"
    ext = os.path.splitext(path)[1]
    if ext in DOC_EXT:
        return ext
    return next((e for key, e in CTYPE_EXT if key in ctype), ext or ".bin")


def fetch(url: str, refresh: bool = False) -> dict:
    """Fetch a URL and return {url, status, type, title, date, description, text, links}. Cached for 15 minutes."""
    url = (url or "").strip()
    if not re.match(r"https?://", url, re.I):
        url = "https://" + url if re.match(r"[\w.-]+\.[a-z]{2,}(/|$)", url, re.I) else url
    if not re.match(r"https?://", url, re.I):
        raise WebError("url must start with http:// or https://")
    with _LOCK:
        hit = _CACHE.get(url)
    if hit and not refresh and time.time() - hit[0] < 900:
        return hit[1]
    st, hdr, body, final = _get(url, timeout=30, public_only=True)
    ctype = hdr.get("content-type", "").lower()
    if st >= 400:
        raise WebError(f"HTTP {st} from {url}" + (" (the site blocks automated reading; try another source)" if st in (401, 403, 429) else ""))
    path = urllib.parse.urlparse(final).path.lower()
    res = {"url": final, "status": st, "type": ctype.split(";")[0], "title": "", "date": "", "description": "", "links": []}
    if "pdf" in ctype or body[:5] == b"%PDF-" or path.endswith(DOC_EXT) or "officedocument" in ctype or "ms-excel" in ctype:
        ext = _doc_ext(ctype, body, path)
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "doc" + ext)
            with open(fp, "wb") as fh:
                fh.write(body)
            try:
                from meshpool_harness import docread
                res["text"] = docread.extract(fp, max_chars=400_000)
            except Exception as e:                     # noqa: BLE001 - report any reader failure to the agent
                raise WebError(f"could not read the {ext} file at {url}: {e}")
        res["title"] = os.path.basename(path)
    elif "json" in ctype:
        txt = _decode(body, ctype)
        try:
            txt = json.dumps(json.loads(txt), indent=1, ensure_ascii=False)
        except ValueError:
            pass
        res["text"] = txt
    elif "html" in ctype or "xml" in ctype or body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        res.update(html_to_text(_decode(body, ctype), final, links=True))
        if len(res["text"]) < 250:
            res["note"] = ("This page shows little text without JavaScript. If you need what it renders, open it with "
                           "the browser tools, or find the same facts on another source.")
    elif ctype.startswith("image/"):
        res["text"] = f"[image, {len(body)} bytes; download it with bash if you need to look at it]"
    else:
        res["text"] = _decode(body, ctype)
    with _LOCK:
        if len(_CACHE) > 200:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[url] = (time.time(), res)
    return res


def format_page(p: dict, start: int = 0, max_chars: int = 15000, links: bool = False) -> str:
    text = p.get("text") or ""
    start = max(0, int(start or 0))
    chunk = text[start:start + max_chars]
    head = [f"URL: {p['url']}"] + ([f"Title: {p['title']}"] if p.get("title") else []) \
        + ([f"Published: {p['date']}"] if p.get("date") else []) + ([f"Note: {p['note']}"] if p.get("note") else [])
    if start:
        head.append(f"(characters {start} to {start + len(chunk)} of {len(text)})")
    out = "\n".join(head) + "\n\n" + chunk
    if start + max_chars < len(text):
        out += f"\n\n[{len(text) - start - max_chars} more characters. Call again with start={start + max_chars} to read on.]"
    if links and p.get("links"):
        out += "\n\nLinks on the page:\n" + "\n".join(f"- {t}: {u}" for t, u in p["links"][:60])
    return out


# ---------- research ------------------------------------------------------------------------------------------------

_STOP = set("the a an and or of to in on for with by from at is are was were be been this that these those what which "
            "who whom how why when where does do did can could should would will it its as about into than then their "
            "there them they his her our your you we i me my not no yes vs versus between".split())


def _terms(s: str) -> list[str]:
    return [w for w in re.findall(r"[^\W_]{2,}", s.lower()) if w not in _STOP]


def _passages(text: str, terms: list[str], budget: int) -> str:
    """The paragraphs of a page that best match the question, in page order, within a character budget."""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=#)", text) if len(p.strip()) > 40]
    if not paras:
        return text[:budget]
    tset = set(terms)
    scored = []
    for i, p in enumerate(paras):
        words = _terms(p)
        if not words:
            continue
        hits = sum(1 for w in words if w in tset)
        uniq = len(tset & set(words))
        num = 0.3 if re.search(r"\d", p) else 0
        scored.append(((uniq * 2 + hits / max(len(words), 1) * 10 + num) / (1 + len(p) / 2500), i))
    keep, used = set(), 0
    for sc, i in sorted(scored, reverse=True):
        if sc <= 0 or used >= budget:
            break
        keep.add(i)
        used += len(paras[i])
    if not keep:
        keep = {0}
    out, last = [], -2
    for i in sorted(keep):
        p = paras[i]
        out.append(("[...]\n" if i != last + 1 and out else "") + (p if len(p) < budget else p[:budget] + " [...]"))
        last = i
    return "\n".join(out)[: budget + 400]


def research(question: str, queries: list | None = None, max_sources: int = 6, recency: str = "",
             site: str = "") -> str:
    """Search several queries in parallel, read the best sources in parallel, and return the relevant passages."""
    question = (question or "").strip()
    qs = [q.strip() for q in (queries or []) if isinstance(q, str) and q.strip()][:6] or [question]
    max_sources = max(2, min(int(max_sources or 6), 12))
    results, errors = [], []
    with ThreadPoolExecutor(min(len(qs), 4)) as ex:
        futs = [ex.submit(search, q, 8, recency, "", site) for q in qs]
        for f in futs:
            try:
                results.append(f.result())
            except WebError as e:
                errors.append(str(e))
    seen, ranked = set(), []
    for i in range(8):
        for rows in results:
            if i < len(rows):
                r = rows[i]
                k = _norm(r["url"])
                host = urllib.parse.urlparse(r["url"]).netloc.lower()
                if k in seen or any(h in host for h in SKIP_HOSTS):
                    continue
                seen.add(k)
                ranked.append(r)
    if not ranked:
        return "No results. " + ("; ".join(errors) if errors else "Try other words.")
    terms = _terms(question + " " + " ".join(qs))
    pages = {}
    # Read pages in parallel, but never wait on stragglers: stop once enough good pages are in or the time budget is
    # spent (MH_RESEARCH_S, default 15 s). A page still loading is listed under "Other results" instead.
    ex = ThreadPoolExecutor(6)
    futs = {ex.submit(fetch, r["url"]): r for r in ranked[: max_sources + 4]}
    good = 0
    try:
        for f in as_completed(futs, timeout=float(os.environ.get("MH_RESEARCH_S", "15"))):
            try:
                pages[futs[f]["url"]] = f.result()
                good += len(pages[futs[f]["url"]].get("text") or "") >= 200
            except Exception:                              # noqa: BLE001 - a dead source is skipped, not fatal
                pass
            if good >= max_sources:
                break
    except (TimeoutError, _FutTimeout):            # distinct classes before Python 3.11
        pass
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    out, n, budget = [], 0, max(1200, 16000 // max_sources)
    for r in ranked:
        p = pages.get(r["url"])
        if not p or len(p.get("text") or "") < 200:
            continue
        n += 1
        meta = " | ".join(x for x in (p.get("date") and f"published {p['date'][:10]}",) if x)
        out.append(f"[{n}] {p.get('title') or r['title']}\n{p['url']}" + (f"\n{meta}" if meta else "")
                   + "\n" + _passages(p["text"], terms, budget))
        if n >= max_sources:
            break
    rest = [r for r in ranked if r["url"] not in pages or not pages[r["url"]].get("text")][:6]
    tail = ("\n\nOther results (not opened):\n" + format_results(rest)) if rest else ""
    head = (f"Research on: {question}\nQueries: " + "; ".join(qs) + f"\nRead {n} sources. Cite them by number and "
            "link. Read a source in full with your page-reading tool (fetch_url, or web_fetch where that is the "
            "tool's name).\n\n")
    return head + ("\n\n".join(out) if out else "None of the top pages could be read.") + tail
