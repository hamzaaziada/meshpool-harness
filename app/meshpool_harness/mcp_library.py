"""MCP library: well-known MCP servers the user adds in one click (Settings, MCP servers, Library).

Each entry says what the server does, how it runs (a local command through npx or uvx, or a remote URL), which
settings it needs (environment variable NAMES only; values never live here), its docs and its licence. Every package
name and URL below was checked on 2026-11-13, and testkit/connectors_live.py starts each one against npm, PyPI, the official MCP registry or the vendor's repository
(see VERIFIED); deprecated or archived packages were left out.

Adding an entry writes it into Meshpool's mcp.json switched OFF, with ${NAME} placeholders for its settings. The user
fills the settings (saved in meshpool.config on this computer, like other keys) and turns it on. Entries that sign in
with OAuth go through Connectors instead (mcp_apps): either a built-in connector or a custom one made from the URL.
"""
from __future__ import annotations

import os

from meshpool_harness import config as C

CATEGORIES = ["files", "dev", "data", "finance", "productivity", "research", "design"]
MARK = "From the MCP library."           # starts the description of every server the library adds
MCP_SPEC = "MIT or Apache-2.0"           # modelcontextprotocol/servers is moving from MIT to Apache-2.0


def _npx(pkg: str, *args: str) -> dict:
    return {"transport": "stdio", "command": "npx", "args": ["-y", pkg, *args]}


def _uvx(pkg: str, *args: str) -> dict:
    return {"transport": "stdio", "command": "uvx", "args": [pkg, *args]}


def _http(url: str, **headers: str) -> dict:
    return {"transport": "http", "url": url, **({"headers": headers} if headers else {})}


def _e(id, name, category, description, run, docs, license, settings=None, env=True, connector=None, oauth=None):
    """One library entry. `settings` maps each required setting name to a hint; with env=True they are passed to a
    local server as environment variables. `connector` names a built-in connector (mcp_apps.CATALOG); `oauth` is a
    remote URL that signs in through the browser (added as a custom connector)."""
    row = {"id": id, "name": name, "category": category, "description": description, "docs": docs,
           "license": license, "settings": [{"name": k, "hint": v} for k, v in (settings or {}).items()]}
    row.update(run or {})
    if run and run.get("transport") == "stdio" and settings and env:
        row["env"] = {k: "${" + k + "}" for k in settings}
    if connector:
        row.update(transport="connector", connector=connector)
    if oauth:
        row.update(transport="oauth", url=oauth)
    return row


GH = "https://github.com/"
LIBRARY = [
    # ---- files
    _e("local-files", "Filesystem", "files", "Read, write, move and search files inside one folder you choose.",
       _npx("@modelcontextprotocol/server-filesystem", "${MCP_FILES_DIR}"),
       GH + "modelcontextprotocol/servers/tree/main/src/filesystem", MCP_SPEC,
       {"MCP_FILES_DIR": "The folder the server may use, e.g. /Users/me/Documents/Deals"}, env=False),
    _e("google-workspace", "Google Workspace", "files", "Google Drive, Docs, Sheets, Gmail and Calendar.",
       _uvx("workspace-mcp", "--tool-tier", "core"), GH + "taylorwilsdon/google_workspace_mcp", "MIT",
       {"GOOGLE_OAUTH_CLIENT_ID": "OAuth client ID (Google Cloud console, Desktop app)",
        "GOOGLE_OAUTH_CLIENT_SECRET": "OAuth client secret"}),
    # ---- dev
    _e("git", "Git", "dev", "Read history, diffs and branches, and make commits in local git repositories.",
       _uvx("mcp-server-git"), GH + "modelcontextprotocol/servers/tree/main/src/git", "MIT"),
    _e("github", "GitHub", "dev", "Repositories, issues, pull requests and Actions (sign in under Connectors).",
       None, "https://github.com/github/github-mcp-server", "MIT", connector="github"),
    _e("playwright", "Playwright browser", "dev", "Drive a real browser through the page's accessibility tree.",
       _npx("@playwright/mcp@latest"), GH + "microsoft/playwright-mcp", "Apache-2.0"),
    _e("chrome-devtools", "Chrome DevTools", "dev", "Control Chrome and read its console, network and performance.",
       _npx("chrome-devtools-mcp@latest", "--no-usage-statistics"), GH + "ChromeDevTools/chrome-devtools-mcp",
       "Apache-2.0"),
    _e("context7", "Context7", "dev", "Current documentation and code examples for libraries. No key needed.",
       _http("https://mcp.context7.com/mcp"), GH + "upstash/context7", "MIT"),
    _e("sentry", "Sentry", "dev", "Errors, issues, traces and releases (sign in under Connectors).",
       None, "https://docs.sentry.io/product/sentry-mcp/", "FSL-1.1-ALv2 (hosted service)", connector="sentry"),
    _e("supabase", "Supabase", "dev", "Databases, SQL, edge functions and logs (sign in under Connectors).",
       None, "https://supabase.com/docs/guides/getting-started/mcp", "Apache-2.0", connector="supabase"),
    # ---- data
    _e("postgres", "SQL databases", "data", "Explore schemas and run SQL on PostgreSQL, MySQL, MariaDB, SQL Server "
       "or SQLite.", _npx("@bytebase/dbhub@1.6.1", "--transport", "stdio", "--dsn", "${DATABASE_URL}"),
       GH + "bytebase/dbhub", "MIT",
       {"DATABASE_URL": "postgres://user:password@host:5432/dbname (or mysql://, sqlserver://, sqlite:///path)"},
       env=False),
    _e("duckdb", "DuckDB", "data", "Fast SQL over local CSV, Parquet and Excel files in an in-memory DuckDB.",
       _uvx("mcp-server-motherduck", "--db-path", ":memory:", "--read-write"),
       GH + "motherduckdb/mcp-server-motherduck", "MIT"),
    _e("bigquery", "BigQuery", "data", "List tables and run SQL in Google BigQuery (uses your gcloud login).",
       _uvx("mcp-server-bigquery"), GH + "LucasHild/mcp-server-bigquery", "MIT",
       {"BIGQUERY_PROJECT": "GCP project ID", "BIGQUERY_LOCATION": "Dataset location, e.g. US or europe-west2"}),
    _e("mongodb", "MongoDB", "data", "Query MongoDB and Atlas collections (read-only).",
       _npx("mongodb-mcp-server@latest", "--readOnly"), GH + "mongodb-js/mongodb-mcp-server", "Apache-2.0",
       {"MDB_MCP_CONNECTION_STRING": "mongodb+srv://user:password@cluster/db"}),
    # ---- finance
    _e("sec-edgar", "SEC EDGAR", "finance", "Company filings (10-K, 10-Q, 8-K) and sections, XBRL financial "
       "statements and insider transactions from the SEC, with filing links.", _uvx("sec-edgar-mcp"),
       GH + "stefanoamorelli/sec-edgar-mcp", "AGPL-3.0",
       {"SEC_EDGAR_USER_AGENT": "Your name and email, e.g. Jane Doe (jane@fund.com); the SEC requires it"}),
    _e("alpha-vantage", "Alpha Vantage", "finance", "Stock, ETF, FX and crypto prices, fundamentals, earnings and "
       "economic indicators.", _http("https://mcp.alphavantage.co/mcp?apikey=${ALPHAVANTAGE_API_KEY}"),
       GH + "alphavantage/alpha_vantage_mcp", "MIT",
       {"ALPHAVANTAGE_API_KEY": "Free key from alphavantage.co/support/#api-key"}),
    _e("massive", "Massive (Polygon.io)", "finance", "Market data: trades, quotes, aggregates, reference data, "
       "financials and news.", {"transport": "stdio", "command": "uvx", "args": [   # mcp<2: it uses FastMCP, renamed in mcp 2
           "--with", "mcp<2", "--from", "git+https://github.com/massive-com/mcp_massive@v0.10.0", "mcp_massive"]},
       GH + "massive-com/mcp_massive", "MIT", {"MASSIVE_API_KEY": "API key from massive.com (formerly Polygon.io)"}),
    _e("financial-datasets", "Financial Datasets", "finance", "Income statements, balance sheets, cash flows, "
       "prices, filings and news for US equities (sign in through the browser).", None,
       "https://docs.financialdatasets.ai/mcp-server", "MIT (client); hosted service",
       oauth="https://mcp.financialdatasets.ai/"),
    _e("yahoo-finance", "Yahoo Finance", "finance", "Quotes, history, financials and news through yfinance. "
       "Unofficial; check Yahoo's terms before relying on it.", _uvx("yfmcp"), GH + "narumiruna/yfinance-mcp",
       "MIT"),
    _e("stripe", "Stripe", "finance", "Customers, payments and subscriptions (sign in under Connectors).",
       None, "https://docs.stripe.com/mcp", "Hosted service", connector="stripe"),
    # ---- productivity
    _e("slack", "Slack", "productivity", "Read and search channels, threads and DMs; post messages if you allow.",
       _npx("slack-mcp-server@latest", "--transport", "stdio"), GH + "korotovsky/slack-mcp-server", "MIT",
       {"SLACK_MCP_XOXP_TOKEN": "User OAuth token (xoxp-...) from a Slack app you create"}),
    _e("notion", "Notion", "productivity", "Search, read and write pages and databases (sign in under Connectors).",
       None, "https://developers.notion.com/docs/mcp", "Hosted service", connector="notion"),
    _e("linear", "Linear", "productivity", "Issues, projects and comments (sign in under Connectors).",
       None, "https://linear.app/docs/mcp", "Hosted service", connector="linear"),
    _e("atlassian", "Jira and Confluence", "productivity", "Issues and pages (sign in under Connectors).",
       None, "https://www.atlassian.com/platform/remote-mcp-server", "Hosted service", connector="atlassian"),
    _e("asana", "Asana", "productivity", "Tasks, projects and goals in Asana (sign in through the browser).",
       None, "https://developers.asana.com/docs/using-asanas-mcp-server", "Hosted service",
       oauth="https://mcp.asana.com/mcp"),
    _e("hubspot", "HubSpot", "productivity", "Contacts, companies, deals and notes in your HubSpot CRM.",
       _npx("@hubspot/mcp-server"), "https://developers.hubspot.com/mcp", "MIT",
       {"PRIVATE_APP_ACCESS_TOKEN": "Private app access token (HubSpot settings, Integrations, Private apps)"}),
    _e("airtable", "Airtable", "productivity", "Bases, tables and records (sign in under Connectors).",
       None, "https://support.airtable.com/docs/using-the-airtable-mcp-server", "Hosted service",
       connector="airtable"),
    _e("time", "Time and time zones", "productivity", "Current time and conversions between time zones.",
       _uvx("mcp-server-time"), GH + "modelcontextprotocol/servers/tree/main/src/time", "MIT"),
    # ---- research
    _e("web-fetch", "Fetch", "research", "Fetch a web page and convert it to Markdown.", _uvx("mcp-server-fetch"),
       GH + "modelcontextprotocol/servers/tree/main/src/fetch", "MIT"),
    _e("brave-search", "Brave Search", "research", "Web, news, image and local search from Brave's index.",
       _npx("@brave/brave-search-mcp-server"), GH + "brave/brave-search-mcp-server", "MIT",
       {"BRAVE_API_KEY": "Key from api-dashboard.search.brave.com"}),
    _e("exa", "Exa", "research", "Neural web search, company research and code search. Works without a key "
       "(rate-limited).", _http("https://mcp.exa.ai/mcp"), GH + "exa-labs/exa-mcp-server", "MIT"),
    _e("tavily", "Tavily", "research", "Search, extract, crawl and map websites for research.",
       _npx("tavily-mcp@latest"), GH + "tavily-ai/tavily-mcp", "MIT",
       {"TAVILY_API_KEY": "Key from app.tavily.com"}),
    _e("firecrawl", "Firecrawl", "research", "Scrape, crawl and extract structured data from websites.",
       _npx("firecrawl-mcp"), GH + "firecrawl/firecrawl-mcp-server", "MIT",
       {"FIRECRAWL_API_KEY": "Key from firecrawl.dev (fc-...)"}),
    _e("perplexity", "Perplexity", "research", "Answers with citations, deep research and reasoning from "
       "Perplexity's Sonar models.", _npx("@perplexity-ai/mcp-server"), GH + "perplexityai/modelcontextprotocol",
       "MIT", {"PERPLEXITY_API_KEY": "Key from perplexity.ai/settings/api"}),
    _e("arxiv", "arXiv", "research", "Search, download and read arXiv papers.", _uvx("arxiv-mcp-server"),
       GH + "blazickjp/arxiv-mcp-server", "Apache-2.0"),
    # ---- design
    _e("figma", "Figma", "design", "Layout, styles and components of Figma files, for building from designs.",
       _npx("figma-developer-mcp", "--stdio"), GH + "GLips/Figma-Context-MCP", "MIT",
       {"FIGMA_API_KEY": "Personal access token (Figma settings, Security)"}),
    _e("canva", "Canva", "design", "Search, create and export Canva designs (sign in under Connectors).",
       None, "https://www.canva.dev/docs/connect/mcp-server/", "Hosted service", connector="canva"),
]

# How each entry was checked on 2026-11-13 (npm view / pip index versions / registry.modelcontextprotocol.io /
# git ls-remote of the repository / an MCP initialize call to the URL).
VERIFIED = {
    "local-files": "npm @modelcontextprotocol/server-filesystem 2.3.0", "google-workspace": "PyPI workspace-mcp 2.4.2",
    "git": "PyPI mcp-server-git 2026.11.11", "playwright": "npm @playwright/mcp 0.0.85",
    "chrome-devtools": "npm chrome-devtools-mcp 1.13.0", "context7": "initialize 200 at mcp.context7.com",
    "postgres": "npm @bytebase/dbhub 1.6.1 (postgres-mcp left out: every release pins a pglast that is gone)",
    "duckdb": "PyPI mcp-server-motherduck 1.1.1",
    "bigquery": "PyPI mcp-server-bigquery 0.3.3", "mongodb": "npm mongodb-mcp-server 3.3.0",
    "sec-edgar": "PyPI sec-edgar-mcp 1.1.0", "alpha-vantage": "registry io.github.alphavantage/alpha_vantage_mcp; 401 without key",
    "massive": "git tag v0.10.0 of massive-com/mcp_massive, with mcp<2", "financial-datasets": "registry ai.financialdatasets/mcp; "
    "OAuth with dynamic registration", "yahoo-finance": "PyPI yfmcp 0.14.0", "slack": "npm slack-mcp-server 1.3.0",
    "asana": "OAuth with dynamic registration at mcp.asana.com", "hubspot": "npm @hubspot/mcp-server 0.4.0",
    "time": "PyPI mcp-server-time 2026.11.11", "web-fetch": "PyPI mcp-server-fetch 2026.11.11",
    "brave-search": "npm @brave/brave-search-mcp-server 2.1.4", "exa": "initialize 200 at mcp.exa.ai",
    "tavily": "npm tavily-mcp 0.2.22", "firecrawl": "npm firecrawl-mcp 3.40.0",
    "perplexity": "npm @perplexity-ai/mcp-server 1.3.0", "arxiv": "PyPI arxiv-mcp-server 0.8.2",
    "figma": "npm figma-developer-mcp 0.13.2",
}


# ---------- reading -------------------------------------------------------------------------------------------------

def get(eid: str) -> dict:
    for e in LIBRARY:
        if e["id"] == eid:
            return e
    raise ValueError(f"No library entry named {eid}.")


def spec(e: dict) -> dict:
    """The mcp.json entry for a local or remote library server: switched off, with ${NAME} placeholders."""
    if e["transport"] == "stdio":
        out = {"command": e["command"], "args": list(e["args"])}
        if e.get("env"):
            out["env"] = dict(e["env"])
    elif e["transport"] == "http":
        out = {"url": e["url"]}
        if e.get("headers"):
            out["headers"] = dict(e["headers"])
    else:
        raise ValueError(f"{e['name']} signs in through Connectors; it has no mcp.json entry.")
    need = ", ".join(s["name"] for s in e["settings"])
    out["description"] = f"{MARK} {e['description']}" + (f" Needs: {need}." if need else "")
    out["disabled"] = True
    return out


def target(e: dict) -> str:
    """What the server runs or connects to, as the user sees it before turning it on."""
    if e["transport"] == "stdio":
        return " ".join([e["command"], *e["args"]])
    return e.get("url", "")


def _servers() -> dict:
    from meshpool_harness.agent import mcp as M
    try:
        return M.load_config()
    except (OSError, ValueError):
        return {}


def _missing(e: dict) -> list[str]:
    """Required settings that have no value yet (in the environment or meshpool.config)."""
    cfg = C.read()
    return [s["name"] for s in e["settings"] if not (os.environ.get(s["name"]) or cfg.get(s["name"]))]


def listing() -> list[dict]:
    """Every entry with its state: added to mcp.json, switched on, settings still missing, connector connected."""
    servers = _servers()
    conns = {}
    try:
        from meshpool_harness import mcp_apps as CN
        conns = {c["id"]: c for c in CN.listing()}
    except Exception:  # noqa: BLE001 - the library still lists without connector state
        pass
    out = []
    for e in LIBRARY:
        row = dict(e, target=target(e) if e["transport"] in ("stdio", "http") else e.get("url", ""),
                   missing=_missing(e), verified=VERIFIED.get(e["id"], ""))
        if e["transport"] == "connector":
            c = conns.get(e["connector"], {})
            row.update(added=bool(c.get("connected")), enabled=bool(c.get("connected") and c.get("enabled", True)))
        elif e["transport"] == "oauth":
            c = next((x for x in conns.values() if x.get("url") == e["url"]), {})
            row.update(added=bool(c), enabled=bool(c.get("connected")), connector=c.get("id", ""))
        else:
            s = servers.get(e["id"])
            row.update(added=isinstance(s, dict), enabled=isinstance(s, dict) and not s.get("disabled"))
        out.append(row)
    return out


# ---------- changing ------------------------------------------------------------------------------------------------

def add(eid: str) -> dict:
    """Add an entry. Local and keyed remote servers go into mcp.json switched off; OAuth ones become connectors.
    Returns {"server": name} or {"connector": id} (the UI then starts the browser sign-in)."""
    e = get(eid)
    if e["transport"] == "connector":
        return {"connector": e["connector"]}
    if e["transport"] == "oauth":
        from meshpool_harness import mcp_apps as CN
        have = next((c for c in CN.listing() if c.get("url") == e["url"]), None)
        return {"connector": (have or CN.add_custom(e["name"], e["url"]))["id"]}
    if isinstance(_servers().get(e["id"]), dict):
        return {"server": e["id"], "existing": True}
    from meshpool_harness import importer
    s = spec(e)
    return {"server": importer.import_mcp(e["id"], s, description=s["description"])}


def _set_disabled(name: str, off: bool):
    from meshpool_harness.agent import mcp as M

    def change(servers):
        srv = servers.get(name)
        if not isinstance(srv, dict):
            raise ValueError(f"{name} is not in your MCP servers. Add it first.")
        if off:
            srv["disabled"] = True
        else:
            srv.pop("disabled", None)
    if not M.config_file().exists():
        raise ValueError(f"{name} is not in your MCP servers. Add it first.")
    M.edit_config(change, create=False)


def configure(eid: str, values: dict, enable: bool = True) -> dict:
    """Save an entry's settings to meshpool.config (only the names it declares), then switch it on when nothing is
    missing. The values never go into mcp.json, which keeps the ${NAME} placeholders."""
    e = get(eid)
    names = {s["name"] for s in e["settings"]}
    vals = {k: str(v).strip() for k, v in (values or {}).items() if k in names and str(v).strip()}
    if any("\n" in v for v in vals.values()):
        raise ValueError("A setting cannot span several lines.")
    if vals:
        C.write(vals)
        C.load_into_env(override=True)
    if e["transport"] in ("stdio", "http") and not isinstance(_servers().get(e["id"]), dict):
        add(eid)
    missing = _missing(e)
    if enable and e["transport"] in ("stdio", "http"):
        if missing:
            raise ValueError(f"{e['name']} still needs: {', '.join(missing)}.")
        _set_disabled(e["id"], False)
    return {"missing": missing}


def remove(eid: str):
    """Take a library server out of mcp.json (only one the library added). Saved settings stay."""
    e = get(eid)
    from meshpool_harness.agent import mcp as M

    def drop(servers):
        s = servers.get(e["id"])
        if isinstance(s, dict) and str(s.get("description", "")).startswith(MARK):
            del servers[e["id"]]
    M.edit_config(drop, create=False)


# ---------- app API (Settings, MCP servers, Library) ------------------------------------------------------------------

def api_view() -> dict:
    return {"library": listing(), "categories": CATEGORIES}


def api_action(body: dict) -> dict:
    """POST /api/mcp-library: {action: add, id} | {action: configure, id, values, enable?} | {action: remove, id}."""
    act, eid = body.get("action"), body.get("id", "")
    res: dict = {}
    if act == "add":
        res = add(eid)
    elif act == "configure":
        res = configure(eid, body.get("values") or {}, bool(body.get("enable", True)))
    elif act == "remove":
        remove(eid)
    else:
        raise ValueError(f"Unknown library action {act!r}.")
    return dict(api_view(), result=res)
