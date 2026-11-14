"""Connectors: remote MCP servers for the apps people work in (GitHub, Jira and Confluence, Linear, Notion, ...).

A connector is an MCP server Meshpool knows how to sign in to. Sign-in follows the MCP authorization spec:
protected-resource metadata, authorization-server metadata, dynamic client registration, then the authorization
code flow with PKCE and a loopback redirect to Meshpool's own local server (/oauth/callback). Tokens live in
<home>/mcp-auth.json (owner-only) and refresh on their own. Token connectors (GitHub) use a saved setting instead.

Connected connectors are added to the MCP servers every harness gets (Meshpool, Hermes, Pi), each with a fresh
Authorization header, so one sign-in serves all of them. Standard library only.

Everything happens between this computer and the app's own servers: Meshpool runs no server of its own and never sees
a token. Apps that do not let other apps register themselves (Slack, HubSpot, Google and a few others) take a client ID
the user creates in that app's developer settings, so that part also stays on the user's side.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from meshpool_harness import __version__ as _VERSION
from meshpool_harness import config as C

# Every entry was checked against the live server: it answers, publishes how to sign in, and (for "oauth") lets other
# apps register themselves, or (with "needs_client") takes the user's own client ID. testkit/connectors_live.py
# re-checks them all. "category" groups them in the app.
_G = ("Google needs your own sign-in client (free, two minutes): in console.cloud.google.com create a project, enable "
      "the {api}, set up the OAuth consent screen (External, add yourself as a test user), then Credentials, Create "
      "credentials, OAuth client ID, Desktop app. Paste its client ID and secret here.")
_OWN = ("{name} only signs in apps it knows, so it needs your own client: create an app in {where} with the redirect "
        "URL shown below, then paste its client ID and secret here.")
_GOOGLE = {"auth_params": {"access_type": "offline", "prompt": "consent"}, "needs_client": True}


def _c(cid, name, url, desc, category, auth="oauth", **kw):
    return {"id": cid, "name": name, "url": url, "auth": auth, "description": desc, "category": category, **kw}


CATALOG = [
    # Engineering
    _c("github", "GitHub", "https://api.githubcopilot.com/mcp/", "Repositories, issues, pull requests, Actions runs and "
       "code search.", "Engineering", auth="token", token_key="GITHUB_TOKEN",
       token_help="Or create a token (repo and workflow access) at github.com/settings/tokens/new and paste it."),
    _c("gitlab", "GitLab", "https://gitlab.com/api/v4/mcp", "Issues, merge requests, pipelines and code on GitLab.com.",
       "Engineering"),
    _c("linear", "Linear", "https://mcp.linear.app/mcp", "Find, create and update Linear issues, projects and comments.",
       "Engineering"),
    _c("atlassian", "Jira and Confluence", "https://mcp.atlassian.com/v1/mcp", "Search, create and update Jira issues "
       "and Confluence pages in your Atlassian site.", "Engineering"),
    _c("sentry", "Sentry", "https://mcp.sentry.dev/mcp", "Look up errors, issues, traces and releases in Sentry.",
       "Engineering"),
    _c("vercel", "Vercel", "https://mcp.vercel.com", "Projects, deployments, build logs and domains on Vercel.",
       "Engineering"),
    _c("netlify", "Netlify", "https://netlify-mcp.netlify.app/mcp", "Sites, deploys, forms and environment variables "
       "on Netlify.", "Engineering"),
    _c("cloudflare", "Cloudflare", "https://bindings.mcp.cloudflare.com/mcp", "Workers, KV, R2 and D1 in your "
       "Cloudflare account.", "Engineering"),
    _c("supabase", "Supabase", "https://mcp.supabase.com/mcp", "Databases, tables, SQL, edge functions and logs in "
       "your Supabase projects.", "Engineering"),
    _c("neon", "Neon", "https://mcp.neon.tech/mcp", "Postgres projects, branches and SQL on Neon.", "Engineering"),
    _c("prisma", "Prisma Postgres", "https://mcp.prisma.io/mcp", "Prisma Postgres databases, schemas and migrations.",
       "Engineering"),
    _c("posthog", "PostHog", "https://mcp.posthog.com/mcp", "Product analytics, insights, feature flags and errors in "
       "PostHog.", "Engineering"),
    _c("jam", "Jam", "https://mcp.jam.dev/mcp", "Bug reports with console logs, network requests and repro steps from "
       "Jam.", "Engineering"),
    _c("context7", "Context7", "https://mcp.context7.com/mcp", "Up-to-date docs and code examples for thousands of "
       "libraries. No sign-in.", "Engineering", auth="none"),
    _c("deepwiki", "DeepWiki", "https://mcp.deepwiki.com/mcp", "Ask questions about any public GitHub repository. No "
       "sign-in.", "Engineering", auth="none"),
    _c("cloudflare-docs", "Cloudflare Docs", "https://docs.mcp.cloudflare.com/mcp", "Search Cloudflare's "
       "documentation. No sign-in.", "Engineering", auth="none"),
    _c("huggingface", "Hugging Face", "https://huggingface.co/mcp", "Search models, datasets, Spaces and papers on "
       "Hugging Face. No sign-in.", "Engineering", auth="none"),
    # Work and documents
    _c("google-gmail", "Gmail", "https://gmailmcp.googleapis.com/mcp/v1", "Search, read, label and draft email in "
       "Gmail.", "Work and documents", scopes=["https://www.googleapis.com/auth/gmail.modify"],
       client_help=_G.format(api="Gmail API"), **_GOOGLE),
    _c("google-calendar", "Google Calendar", "https://calendarmcp.googleapis.com/mcp/v1", "Events, free time and "
       "invitations in Google Calendar.", "Work and documents", scopes=["https://www.googleapis.com/auth/calendar"],
       client_help=_G.format(api="Google Calendar API"), **_GOOGLE),
    _c("google-drive", "Google Drive", "https://drivemcp.googleapis.com/mcp/v1", "Find, read, create and copy files "
       "in Google Drive.", "Work and documents", scopes=["https://www.googleapis.com/auth/drive"],
       client_help=_G.format(api="Google Drive API"), **_GOOGLE),
    _c("slack", "Slack", "https://mcp.slack.com/mcp", "Search and read channels, threads and canvases, and post "
       "messages in Slack.", "Work and documents", needs_client=True,
       client_help=_OWN.format(name="Slack", where="api.slack.com/apps")),
    _c("notion", "Notion", "https://mcp.notion.com/mcp", "Search, read and write Notion pages and databases.",
       "Work and documents"),
    _c("asana", "Asana", "https://mcp.asana.com/sse", "Tasks, projects, goals and comments in Asana.",
       "Work and documents", type="sse"),
    _c("clickup", "ClickUp", "https://mcp.clickup.com/mcp", "Tasks, lists, docs and comments in ClickUp.",
       "Work and documents"),
    _c("monday", "monday.com", "https://mcp.monday.com/mcp", "Boards, items, updates and docs in monday.com.",
       "Work and documents"),
    _c("airtable", "Airtable", "https://mcp.airtable.com/mcp", "Read and update Airtable bases, tables and records.",
       "Work and documents"),
    _c("dropbox", "Dropbox", "https://mcp.dropbox.com/mcp", "Find, read and share files in Dropbox.",
       "Work and documents"),
    _c("box", "Box", "https://mcp.box.com", "Search, read and organise files in Box.", "Work and documents",
       needs_client=True, client_help=_OWN.format(name="Box", where="the Box developer console (Custom App, "
                                                  "user authentication with OAuth 2.0)")),
    _c("miro", "Miro", "https://mcp.miro.com/", "Read and create boards, sticky notes and diagrams in Miro.",
       "Work and documents"),
    _c("granola", "Granola", "https://mcp.granola.ai/mcp", "Your meeting notes and transcripts from Granola.",
       "Work and documents"),
    _c("fireflies", "Fireflies", "https://api.fireflies.ai/mcp", "Meeting transcripts, summaries and action items "
       "from Fireflies.", "Work and documents"),
    _c("otter", "Otter", "https://mcp.otter.ai/mcp", "Meeting transcripts and summaries from Otter.",
       "Work and documents"),
    _c("tldv", "tl;dv", "https://mcp.tldv.io/mcp", "Meeting recordings, transcripts and notes from tl;dv.",
       "Work and documents"),
    _c("docusign", "Docusign", "https://mcp.docusign.com/mcp", "Agreements, envelopes and their status in Docusign.",
       "Work and documents", needs_client=True,
       client_help=_OWN.format(name="Docusign", where="Docusign Apps and Keys (an integration key)")),
    _c("zapier", "Zapier", "https://mcp.zapier.com/api/mcp/mcp", "Run the actions you set up in Zapier across "
       "thousands of apps.", "Work and documents"),
    _c("make", "Make", "https://mcp.make.com/mcp", "Run your Make scenarios as tools.", "Work and documents"),
    _c("pipedream", "Pipedream", "https://mcp.pipedream.net/v2", "Thousands of app actions through Pipedream.",
       "Work and documents"),
    # Sales and marketing
    _c("hubspot", "HubSpot", "https://mcp.hubspot.com/", "Contacts, companies, deals, tickets and notes in your "
       "HubSpot CRM.", "Sales and marketing", needs_client=True,
       client_help=_OWN.format(name="HubSpot", where="HubSpot's developer portal (an MCP auth app)")),
    _c("attio", "Attio", "https://mcp.attio.com/mcp", "People, companies, deals and notes in your Attio CRM.",
       "Sales and marketing"),
    _c("close", "Close", "https://mcp.close.com/mcp", "Leads, contacts, opportunities and activities in Close.",
       "Sales and marketing"),
    _c("apollo", "Apollo", "https://mcp.apollo.io/mcp", "Find and enrich people and companies, and manage contacts "
       "and sequences in Apollo.io.", "Sales and marketing"),
    _c("lusha", "Lusha", "https://mcp.lusha.com/mcp", "Find and enrich contacts and companies with Lusha.",
       "Sales and marketing"),
    _c("instantly", "Instantly", "https://mcp.instantly.ai/mcp", "Campaigns, leads, replies and analytics in "
       "Instantly.", "Sales and marketing", auth="token", token_key="INSTANTLY_API_KEY",
       token_help="Create an API key in Instantly under Settings, Integrations, API keys, and paste it."),
    _c("intercom", "Intercom", "https://mcp.intercom.com/mcp", "Conversations, contacts and help articles in "
       "Intercom.", "Sales and marketing"),
    _c("mailchimp", "Mailchimp", "https://mcp.mailchimp.com/mcp", "Audiences, campaigns and reports in Mailchimp.",
       "Sales and marketing", needs_client=True,
       client_help=_OWN.format(name="Mailchimp", where="Mailchimp, Account, Extras, Registered apps")),
    _c("semrush", "Semrush", "https://mcp.semrush.com/v1/mcp", "Keyword, traffic and competitor research from "
       "Semrush.", "Sales and marketing"),
    _c("similarweb", "Similarweb", "https://mcp.similarweb.com/mcp", "Website traffic, engagement and market data "
       "from Similarweb.", "Sales and marketing"),
    _c("wix", "Wix", "https://mcp.wix.com/mcp", "Sites, stores, bookings and content in Wix.", "Sales and marketing"),
    _c("webflow", "Webflow", "https://mcp.webflow.com/mcp", "Sites, pages and CMS collections in Webflow.",
       "Sales and marketing"),
    _c("canva", "Canva", "https://mcp.canva.com/mcp", "Search, create and export Canva designs.",
       "Sales and marketing"),
    _c("gamma", "Gamma", "https://mcp.gamma.app/mcp", "Create presentations, documents and web pages with Gamma.",
       "Sales and marketing"),
    # Finance and data
    _c("stripe", "Stripe", "https://mcp.stripe.com", "Customers, payments, subscriptions and the Stripe docs.",
       "Finance and data"),
    _c("paypal", "PayPal", "https://mcp.paypal.com/mcp", "Invoices, orders, payments and disputes in PayPal.",
       "Finance and data"),
    _c("square", "Square", "https://mcp.squareup.com/sse", "Payments, orders, customers and catalog in Square.",
       "Finance and data", type="sse"),
    _c("crunchbase", "Crunchbase", "https://mcp.crunchbase.com/mcp", "Companies, funding rounds and investors from "
       "Crunchbase.", "Finance and data", needs_client=True,
       client_help=_OWN.format(name="Crunchbase", where="your Crunchbase API settings")),
    _c("morningstar", "Morningstar", "https://mcp.morningstar.com/mcp", "Fund, stock and market data from "
       "Morningstar.", "Finance and data"),
    _c("exa", "Exa", "https://mcp.exa.ai/mcp", "Web search and page contents built for agents. No sign-in.",
       "Finance and data", auth="none"),
    _c("firecrawl", "Firecrawl", "https://mcp.firecrawl.dev/mcp", "Scrape and search the web into clean text. No "
       "sign-in.", "Finance and data", auth="none"),
]
_LOCK = threading.Lock()
_FLOWS: dict[str, dict] = {}            # state -> pending sign-in
LAST_FINISHED = [""]                    # the connector whose sign-in finished last
UA = {"User-Agent": f"Meshpool/{_VERSION}", "Accept": "application/json"}


def _auth_file() -> Path:
    return C.home() / "mcp-auth.json"


def _state_file() -> Path:
    return C.home() / "connectors.json"


def _read(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(p: Path, d: dict):
    """Owner-only (0600) from the first byte, and atomic: a unique temporary file next to it, then a rename. These
    files hold access tokens, refresh tokens and client secrets."""
    import tempfile
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")   # mkstemp creates it 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(d, indent=1))
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        C.replace_file(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _custom() -> list[dict]:
    rows = _read(_state_file()).get("custom") or []
    return [dict(r, custom=True) for r in rows if isinstance(r, dict) and r.get("id") and r.get("url")]


def _all() -> list[dict]:
    return CATALOG + _custom()


def get(cid: str) -> dict:
    for c in _all():
        if c["id"] == cid:
            return c
    raise KeyError(cid)


_LOCAL = re.compile(r"http://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(/|$)", re.I)


def _check_url(url: str) -> None:
    if not (re.match(r"https://[^\s/]+", url) or _LOCAL.match(url)):
        raise ValueError("Enter the server's https:// address (http:// works only for a server on this computer).")


def _header(token: str) -> tuple[str, str]:
    """A pasted credential as (header, value): 'X-Api-Key: abc' keeps its header; anything else is a bearer token."""
    m = re.match(r"^([A-Za-z0-9-]{2,60}):\s*(\S.*)$", token)
    if m and m.group(1).lower() not in ("bearer", "http", "https"):
        return m.group(1), m.group(2).strip()
    return "Authorization", "Bearer " + re.sub(r"^bearer\s+", "", token, flags=re.I)


def test_server(spec: dict, timeout: float = 30) -> dict:
    """Connect to an MCP server once and list its tools: {"ok", "tools", "error", "status"}."""
    from meshpool_harness.agent.mcp import Server
    srv = Server("test", spec, C.runs_dir() / "mcp-logs", [os.getcwd()])
    srv.start(timeout)
    try:
        if srv.status == "connected":
            return {"ok": True, "tools": [t["name"] for t in srv.tools], "error": ""}
        m = re.search(r"HTTP (\d{3})", srv.error)
        code = int(m.group(1)) if m else 0
        if code == 400 and re.search(r"authori[sz]", srv.error, re.I):
            code = 401                         # some servers (GitHub) answer 400 to a malformed token
        return {"ok": False, "tools": [], "error": srv.error, "status": code}
    finally:
        srv.close()


def add_custom(name: str, url: str, token: str = "", client_id: str = "", client_secret: str = "") -> dict:
    """Add any remote MCP server by URL. Meshpool works out what it needs: nothing, the pasted token or API key, or a
    browser sign-in (with the user's own client ID when the app does not let other apps register themselves)."""
    name, url, token = (name or "").strip(), (url or "").strip(), (token or "").strip()
    _check_url(url)
    host = urllib.parse.urlparse(url).netloc
    base = re.sub(r"[^a-z0-9]+", "-", (name or host).lower()).strip("-")[:30] or "server"
    ids = {c["id"] for c in _all()}
    cid, n = "x-" + base, 2
    while cid in ids:
        cid, n = f"x-{base}-{n}", n + 1
    row = {"id": cid, "name": name or host, "url": url, "description": f"Your server at {host}."}
    if token:
        hdr, val = _header(token)
        r = test_server({"url": url, "headers": {hdr: val}})
        if not r["ok"]:
            if r.get("status") in (401, 403):
                raise ValueError(f"{row['name']} refused that token or key. Check it, or leave it empty to sign in "
                                 "through your browser.")
            raise ValueError(f"Could not connect to {host}: {r['error']}")
        row.update(auth="token", header=hdr)
    else:
        r = test_server({"url": url})
        if r["ok"]:
            row["auth"] = "none"                    # an open server: nothing to sign in to
        elif r.get("status") in (401, 403):
            try:
                _discover(url)
            except ValueError:
                raise ValueError(f"{row['name']} needs a token or API key. Paste it in the token field (for a "
                                 "header other than Authorization, paste it as Header-Name: value).")
            row["auth"] = "oauth"
        else:
            raise ValueError(f"Could not connect to {host}: {r['error']}")
    with _LOCK:
        st = _read(_state_file())
        st.setdefault("custom", []).append(row)
        if row["auth"] == "none":
            st.setdefault("enabled", {})[cid] = True
        _write(_state_file(), st)
        store = _read(_auth_file())
        if token:
            store[cid] = {"access_token": _header(token)[1], "static": True}
        elif client_id.strip():
            store[cid] = {"client": _own_client(client_id, client_secret)}
        _write(_auth_file(), store)
    return dict(get(cid), tools=r["tools"])


def _own_client(client_id: str, client_secret: str = "") -> dict:
    c = {"client_id": client_id.strip(), "own": True}
    if client_secret.strip():
        c["client_secret"] = client_secret.strip()
    return c


def set_client(cid: str, client_id: str, client_secret: str = "") -> None:
    """Use the user's own OAuth client for an app that does not let other apps register themselves."""
    get(cid)
    if not client_id.strip():
        raise ValueError("Paste the client ID from the app's developer settings.")
    with _LOCK:
        store = _read(_auth_file())
        e = store.get(cid) or {}
        e["client"] = _own_client(client_id, client_secret)
        for k in ("access_token", "refresh_token", "expired"):
            e.pop(k, None)
        store[cid] = e
        _write(_auth_file(), store)


def remove_custom(cid: str):
    with _LOCK:
        st = _read(_state_file())
        st["custom"] = [r for r in st.get("custom") or [] if r.get("id") != cid]
        st.get("enabled", {}).pop(cid, None)
        _write(_state_file(), st)
        store = _read(_auth_file())
        store.pop(cid, None)
        _write(_auth_file(), store)


# ---------- HTTP helpers ---------------------------------------------------------------------------------------------

def _http(url: str, data: dict | None = None, form: dict | None = None, headers: dict | None = None,
          timeout: float = 20) -> tuple[int, dict, dict]:
    h = dict(UA, **(headers or {}))
    body = None
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    elif data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, dict(r.headers), (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            j = json.loads(raw)
        except ValueError:
            j = {"error": raw[:300].decode("utf-8", "replace")}
        return e.code, dict(e.headers), j if isinstance(j, dict) else {"error": str(j)}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        return 0, {}, {"error": str(e)}


def _well_known(base: str, name: str) -> list[str]:
    u = urllib.parse.urlparse(base)
    path = u.path.rstrip("/")
    origin = f"{u.scheme}://{u.netloc}"
    out = [f"{origin}/.well-known/{name}{path}"] if path else []
    return out + [f"{origin}/.well-known/{name}"]


def _discover(url: str) -> dict:
    """Authorization-server metadata for an MCP server URL, following the MCP authorization spec."""
    st, h, _ = _http(url, data={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "meshpool", "version": _VERSION}}},
        headers={"Accept": "application/json, text/event-stream"})
    www = next((v for k, v in h.items() if k.lower() == "www-authenticate"), "")
    cands = []
    if 'resource_metadata="' in www:
        cands.append(www.split('resource_metadata="', 1)[1].split('"', 1)[0])
    cands += _well_known(url, "oauth-protected-resource")
    prm = {}
    for c in cands:
        s, _, j = _http(c)
        if s == 200 and j.get("authorization_servers"):
            prm = j
            break
    issuer = (prm.get("authorization_servers") or [None])[0]
    if not issuer:
        u = urllib.parse.urlparse(url)
        issuer = f"{u.scheme}://{u.netloc}"
    meta = {}
    oidc = issuer.rstrip("/") + "/.well-known/openid-configuration"
    for c in _well_known(issuer, "oauth-authorization-server") + _well_known(issuer, "openid-configuration") + [oidc]:
        s, _, j = _http(c)
        if s == 200 and j.get("authorization_endpoint") and j.get("token_endpoint"):
            meta = j
            break
    if not meta:
        if st == 0:
            raise ValueError(f"Could not reach {url}. Check the internet connection.")
        raise ValueError("This server does not publish how to sign in (no OAuth metadata).")
    return {"as": meta, "resource": prm.get("resource") or url, "scopes": prm.get("scopes_supported") or []}


class NeedsClient(ValueError):
    """The app does not let other apps register themselves: the user creates a client in its developer settings."""

    def __init__(self, redirect: str):
        self.redirect = redirect
        super().__init__("This app does not let other apps register themselves. Create an OAuth app (client) in its "
                         f"developer settings with the redirect URL {redirect}, then paste its client ID (and secret, "
                         "if it gives you one) under More options.")


def _client_auth(client: dict, form: dict) -> tuple[dict, dict]:
    """How to authenticate to the token endpoint: public clients send their ID; clients that were given a secret send
    it the way the server asked (client_secret_basic is the default in RFC 7591)."""
    form = dict(form, client_id=client["client_id"])
    secret = client.get("client_secret")
    if not secret:
        return form, {}
    method = client.get("token_endpoint_auth_method") or ("client_secret_post" if client.get("own") else
                                                         "client_secret_basic")
    if method == "none":
        return form, {}
    if method == "client_secret_post":
        return dict(form, client_secret=secret), {}
    q = urllib.parse.quote
    basic = base64.b64encode(f"{q(client['client_id'], safe='')}:{q(secret, safe='')}".encode()).decode()
    form.pop("client_id", None)
    return form, {"Authorization": "Basic " + basic}


def _register(meta: dict, redirect: str, scopes: list | None = None) -> dict:
    """Dynamic client registration (RFC 7591). Some servers accept only http://localhost:<port> as the loopback
    address, so on a redirect error it tries that spelling; the address used is returned as client["redirect"]."""
    reg = meta.get("registration_endpoint")
    if not reg:
        raise NeedsClient(redirect)
    tries = [(redirect, False)]
    if "://127.0.0.1:" in redirect:
        tries.append((redirect.replace("://127.0.0.1:", "://localhost:"), False))
    last = {}
    while tries:
        r, with_scope = tries.pop(0)
        body = {"client_name": "Meshpool", "redirect_uris": [r], "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"], "token_endpoint_auth_method": "none", "client_uri": "https://meshpool.ai"}
        if with_scope:
            body["scope"] = " ".join(scopes or [])
        s, _, j = _http(reg, data=body)
        if s in (200, 201) and j.get("client_id"):
            return dict(j, redirect=r)
        last = j or {"error": str(s)}
        why = json.dumps(last).lower()
        if "scope" in why and scopes and not with_scope:     # a few servers want the scopes named at registration
            tries.insert(0, (r, True))
        elif "redirect" not in why:
            break
    raise ValueError(f"The server would not register Meshpool: {last.get('error_description') or last.get('error')}")


# ---------- sign-in -----------------------------------------------------------------------------------------------

def begin(cid: str, redirect: str) -> str:
    """Start signing in to a connector; returns the URL to open in the browser."""
    c = get(cid)
    if c["auth"] != "oauth":
        raise ValueError(f"{c['name']} uses a token. Paste it in the connector's settings.")
    store = _read(_auth_file())
    entry = store.get(cid) or {}
    client = entry.get("client")
    if c.get("needs_client") and not (client and client.get("own")):
        raise NeedsClient(redirect)
    d = _discover(c["url"])
    meta = d["as"]
    scopes = d["scopes"] or meta.get("scopes_supported") or []
    if not (client and (client.get("own") or (entry.get("redirect") == redirect and
                                              entry.get("issuer") == meta.get("issuer")))):
        client = _register(meta, redirect, scopes)
    redirect_used = client.get("redirect") or redirect     # the spelling the server accepted
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    q = {"response_type": "code", "client_id": client["client_id"], "redirect_uri": redirect_used, "state": state,
         "code_challenge": challenge, "code_challenge_method": "S256", "resource": d["resource"]}
    if get(cid).get("scopes"):
        scopes = get(cid)["scopes"]
    q.update(get(cid).get("auth_params") or {})
    if scopes and cid not in ("notion", "linear"):
        q["scope"] = " ".join(scopes)
    with _LOCK:
        _FLOWS[state] = {"cid": cid, "verifier": verifier, "client": client, "meta": meta, "redirect": redirect_used,
                         "app_redirect": redirect,
                         "resource": d["resource"], "t": time.time()}
        for k in [k for k, v in _FLOWS.items() if time.time() - v["t"] > 900]:
            _FLOWS.pop(k, None)
    sep = "&" if "?" in meta["authorization_endpoint"] else "?"
    return meta["authorization_endpoint"] + sep + urllib.parse.urlencode(q)


def finish(state: str, code: str | None, error: str | None = None) -> str:
    """Handle the browser's redirect back to Meshpool; returns the connector's name."""
    with _LOCK:
        f = _FLOWS.pop(state, None)
    if not f:
        raise ValueError("This sign-in link expired. Start again from Meshpool.")
    LAST_FINISHED[0] = f["cid"]
    name = get(f["cid"])["name"]
    if error or not code:
        raise ValueError(f"{name} did not finish signing in ({error or 'no code'}).")
    form, hdr = _client_auth(f["client"], {"grant_type": "authorization_code", "code": code,
                                           "redirect_uri": f["redirect"], "code_verifier": f["verifier"],
                                           "resource": f["resource"]})
    s, _, tok = _http(f["meta"]["token_endpoint"], form=form, headers=hdr)
    if s != 200 or not tok.get("access_token"):
        raise ValueError(f"{name} refused the sign-in: {tok.get('error_description') or tok.get('error') or s}")
    with _LOCK:
        store = _read(_auth_file())
        store[f["cid"]] = {"client": f["client"], "redirect": f.get("app_redirect") or f["redirect"],
                           "issuer": f["meta"].get("issuer"),
                           "token_endpoint": f["meta"]["token_endpoint"], "resource": f["resource"],
                           "access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
                           "expires_at": time.time() + float(tok.get("expires_in") or 3600) - 60,
                           "connected": time.time()}
        _write(_auth_file(), store)
        st = _read(_state_file())
        st.setdefault("enabled", {})[f["cid"]] = True
        _write(_state_file(), st)
    return name


def _fresh_token(cid: str, force: bool = False) -> str | None:
    """The connector's access token, refreshed when it has expired (or, with force, because the server just refused
    it). None when there is none or the refresh failed: the user signs in again."""
    with _LOCK:
        store = _read(_auth_file())
        e = store.get(cid)
        if not e or not e.get("access_token"):
            return None
        if not force and time.time() < float(e.get("expires_at") or 0):
            return e["access_token"]
        if not e.get("refresh_token") or not e.get("token_endpoint") or not e.get("client"):
            return None if force else e["access_token"]   # let the server decide; it may still accept it
        form, hdr = _client_auth(e["client"], {"grant_type": "refresh_token", "refresh_token": e["refresh_token"],
                                               "resource": e.get("resource", "")})
        s, _, tok = _http(e["token_endpoint"], form=form, headers=hdr)
        if s != 200 or not tok.get("access_token"):
            e["expired"] = True
            _write(_auth_file(), store)
            return None
        e.update({"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token") or e["refresh_token"],
                  "expires_at": time.time() + float(tok.get("expires_in") or 3600) - 60, "expired": False})
        _write(_auth_file(), store)
        return e["access_token"]


def refresh_headers(cid: str) -> dict | None:
    """Fresh request headers for a connector whose server refused the ones it was started with (an OAuth access
    token lasts about an hour). None when there is nothing better to send: the user signs in again."""
    try:
        c = get(cid)
    except KeyError:
        return None
    if c["auth"] == "oauth" and not _fresh_token(cid, force=True):
        return None
    spec = spec_for(cid)
    return (spec or {}).get("headers") or None


def _token_for(c: dict) -> str | None:
    if c["auth"] == "none":
        return None
    if c["auth"] == "token" and c.get("custom"):
        return (_read(_auth_file()).get(c["id"]) or {}).get("access_token") or None
    if c["auth"] == "token":
        return os.environ.get(c["token_key"]) or None
    return _fresh_token(c["id"])


# ---------- what the app and the harnesses see -------------------------------------------------------------------

def listing() -> list[dict]:
    store, st = _read(_auth_file()), _read(_state_file()).get("enabled", {})
    rows = []
    for c in _all():
        if c["auth"] == "none":
            connected = bool(st.get(c["id"]))          # open servers: "connected" once turned on
        elif c["auth"] == "token":
            connected = bool(_token_for(c))
        else:
            e = store.get(c["id"]) or {}
            connected = bool(e.get("access_token")) and not e.get("expired")
        if c["id"] == "github":
            c = dict(c, can_detect=not connected, can_sign_in=bool(github_client_id()))   # detection runs on click only
        rows.append({**{k: c[k] for k in ("id", "name", "description", "auth", "url")}, "type": c.get("type", ""),
                     **{k: c[k] for k in ("can_detect", "can_sign_in") if k in c},
                     "token_help": c.get("token_help", ""), "connected": connected, "custom": bool(c.get("custom")),
                     "own_client": bool(((store.get(c["id"]) or {}).get("client") or {}).get("own")),
                     "needs_client": bool(c.get("needs_client")), "client_help": c.get("client_help", ""),
                     "category": c.get("category", ""),
                     "enabled": connected and st.get(c["id"], True),
                     "expired": bool((store.get(c["id"]) or {}).get("expired"))})
    return rows


def set_enabled(cid: str, on: bool):
    get(cid)
    with _LOCK:
        st = _read(_state_file())
        st.setdefault("enabled", {})[cid] = bool(on)
        _write(_state_file(), st)


def disconnect(cid: str):
    c = get(cid)
    if c.get("custom"):
        remove_custom(cid)
        return
    if c["auth"] == "none":
        set_enabled(cid, False)
        return
    if c["auth"] == "token":
        C.write({c["token_key"]: ""})
        os.environ.pop(c["token_key"], None)
        return
    with _LOCK:
        store = _read(_auth_file())
        store.pop(cid, None)
        _write(_auth_file(), store)


def set_token(cid: str, token: str):
    c = get(cid)
    if c["auth"] != "token":
        raise ValueError(f"{c['name']} signs in through the browser.")
    C.write({c["token_key"]: token.strip()})
    C.load_into_env(override=True)
    set_enabled(cid, True)


def servers() -> dict:
    """Connected, enabled connectors as MCP server specs with a fresh Authorization header."""
    out = {}
    st = _read(_state_file()).get("enabled", {})
    for c in _all():
        on = st.get(c["id"]) if c["auth"] == "none" else st.get(c["id"], True)   # open servers: only once turned on
        if not on:
            continue
        spec = spec_for(c["id"])
        if spec:
            out[c["id"]] = dict(spec, description=f"{c['name']}: {c['description']}", timeout=120, connector=True)
    return out


def spec_for(cid: str) -> dict | None:
    """The MCP server spec a connected connector runs with (None if it is not connected)."""
    c = get(cid)
    if c["auth"] == "none":
        return {"url": c["url"], **({"type": c["type"]} if c.get("type") else {})}
    tok = _token_for(c)
    if not tok:
        return None
    hdr = c.get("header") or "Authorization"
    val = tok if hdr != "Authorization" or tok.lower().startswith("bearer ") else f"Bearer {tok}"
    return {"url": c["url"], "headers": {hdr: val}, **({"type": c["type"]} if c.get("type") else {})}


def check(cid: str) -> dict:
    """Connect to a connector once and list its tools, so the user sees it works (or exactly why not)."""
    c = get(cid)
    spec = spec_for(cid)
    if not spec:
        return {"ok": False, "tools": [], "error": f"{c['name']} is not connected yet."}
    r = test_server(spec)
    if not r["ok"] and r.get("status") in (401, 403):
        r["error"] = f"{c['name']} refused the saved sign-in. Connect it again."
    return r


# ---------- GitHub: use an existing login, a device sign-in, or a token ------------------------------------------

GITHUB_SCOPES = "repo workflow read:org"
_DEVICE: dict = {}


def _github_check(token: str) -> str | None:
    """The GitHub login a token belongs to, or None if GitHub refuses it."""
    s, _, j = _http("https://api.github.com/user", headers={"Authorization": f"Bearer {token}",
                                                             "Accept": "application/vnd.github+json"})
    return j.get("login") if s == 200 else None


def github_found() -> dict:
    """A GitHub login already on this computer: the GitHub CLI, or Git's saved credentials for github.com."""
    import shutil
    import subprocess
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
    gh = shutil.which("gh")
    if gh:
        try:
            r = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=15, **kw)
            tok = r.stdout.strip()
            if r.returncode == 0 and tok:
                return {"token": tok, "source": "GitHub CLI"}
        except (OSError, subprocess.TimeoutExpired):
            pass
    git = shutil.which("git") or os.environ.get("MH_GIT", "")
    if git:
        try:
            r = subprocess.run([git, "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                               capture_output=True, text=True, timeout=15,
                               env=dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never"), **kw)
            pw = next((ln.split("=", 1)[1] for ln in r.stdout.splitlines() if ln.startswith("password=")), "")
            if pw and len(pw) > 20:
                return {"token": pw, "source": "Git on this computer"}
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {}


def github_use_found() -> dict:
    f = github_found()
    if not f:
        raise ValueError("No GitHub login found on this computer. Sign in or paste a token instead.")
    login = _github_check(f["token"])
    if not login:
        raise ValueError(f"The GitHub login from {f['source']} no longer works. Sign in or paste a token instead.")
    set_token("github", f["token"])
    return {"login": login, "source": f["source"]}


def github_client_id() -> str:
    """Client ID of Meshpool's GitHub OAuth app (device flow). Set in defaults/build.json for a release build."""
    v = (os.environ.get("MESHPOOL_GITHUB_CLIENT_ID") or C.read().get("MESHPOOL_GITHUB_CLIENT_ID") or "").strip()
    if not v:
        try:
            v = str(json.loads((C.APP / "defaults" / "build.json").read_text(encoding="utf-8")).get("github_client_id") or "")
        except (OSError, ValueError):
            v = ""
    return v.strip()


def github_device_start() -> dict:
    cid = github_client_id()
    if not cid:
        raise ValueError("GitHub sign-in is not set up in this build. Use the login on this computer or paste a token.")
    s, _, j = _http("https://github.com/login/device/code", form={"client_id": cid, "scope": GITHUB_SCOPES})
    if s != 200 or not j.get("device_code"):
        raise ValueError(f"GitHub did not start the sign-in: {j.get('error_description') or j.get('error') or s}")
    _DEVICE.clear()
    _DEVICE.update(j, started=time.time())
    return {"user_code": j["user_code"], "url": j.get("verification_uri") or "https://github.com/login/device",
            "interval": int(j.get("interval") or 5)}


def github_device_poll() -> dict:
    if not _DEVICE:
        return {"status": "none"}
    s, _, j = _http("https://github.com/login/oauth/access_token", form={
        "client_id": github_client_id(), "device_code": _DEVICE["device_code"],
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
    if j.get("access_token"):
        set_token("github", j["access_token"])
        _DEVICE.clear()
        return {"status": "done", "login": _github_check(j["access_token"]) or ""}
    err = j.get("error") or ""
    if err in ("authorization_pending", "slow_down"):
        return {"status": "waiting"}
    _DEVICE.clear()
    return {"status": "failed", "error": j.get("error_description") or err or str(s)}


def github_token() -> str:
    """The token Meshpool uses for GitHub: the saved one, else a login found on this computer."""
    t = os.environ.get("GITHUB_TOKEN") or C.read().get("GITHUB_TOKEN", "")
    return t or github_found().get("token", "")
