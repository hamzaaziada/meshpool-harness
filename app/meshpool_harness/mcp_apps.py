"""Connectors: remote MCP servers for the apps people work in (GitHub, Jira and Confluence, Linear, Notion, ...).

A connector is an MCP server Meshpool knows how to sign in to. Sign-in follows the MCP authorization spec:
protected-resource metadata, authorization-server metadata, dynamic client registration, then the authorization
code flow with PKCE and a loopback redirect to Meshpool's own local server (/oauth/callback). Tokens live in
<home>/mcp-auth.json (owner-only) and refresh on their own. Token connectors (GitHub) use a saved setting instead.

Connected connectors are added to the MCP servers every harness gets (Meshpool, Hermes, Pi), each with a fresh
Authorization header, so one sign-in serves all of them. Standard library only.
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

CATALOG = [
    {"id": "github", "name": "GitHub", "url": "https://api.githubcopilot.com/mcp/", "auth": "token",
     "token_key": "GITHUB_TOKEN", "description": "Repositories, issues, pull requests, Actions runs and code search.",
     "token_help": "Or create a token (repo and workflow access) at github.com/settings/tokens/new and paste it."},
    {"id": "atlassian", "name": "Jira and Confluence", "url": "https://mcp.atlassian.com/v1/mcp", "auth": "oauth",
     "description": "Search, create and update Jira issues and Confluence pages in your Atlassian site."},
    {"id": "linear", "name": "Linear", "url": "https://mcp.linear.app/mcp", "auth": "oauth",
     "description": "Find, create and update Linear issues, projects and comments."},
    {"id": "notion", "name": "Notion", "url": "https://mcp.notion.com/mcp", "auth": "oauth",
     "description": "Search, read and write Notion pages and databases."},
    {"id": "sentry", "name": "Sentry", "url": "https://mcp.sentry.dev/mcp", "auth": "oauth",
     "description": "Look up errors, issues, traces and releases in Sentry."},
    {"id": "gitlab", "name": "GitLab", "url": "https://gitlab.com/api/v4/mcp", "auth": "oauth",
     "description": "Issues, merge requests, pipelines and code on GitLab.com."},
    {"id": "vercel", "name": "Vercel", "url": "https://mcp.vercel.com", "auth": "oauth",
     "description": "Projects, deployments, build logs and domains on Vercel."},
    {"id": "supabase", "name": "Supabase", "url": "https://mcp.supabase.com/mcp", "auth": "oauth",
     "description": "Databases, tables, SQL, edge functions and logs in Supabase."},
    {"id": "stripe", "name": "Stripe", "url": "https://mcp.stripe.com", "auth": "oauth",
     "description": "Customers, payments, subscriptions and the Stripe docs."},
    {"id": "apollo", "name": "Apollo", "url": "https://mcp.apollo.io/mcp", "auth": "oauth",
     "description": "Find and enrich people and companies, and manage contacts and sequences in Apollo.io."},
    {"id": "instantly", "name": "Instantly", "url": "https://mcp.instantly.ai/mcp", "auth": "token",
     "token_key": "INSTANTLY_API_KEY", "description": "Campaigns, leads, replies and analytics in Instantly.",
     "token_help": "Create an API key in Instantly under Settings, Integrations, API keys, and paste it."},
    {"id": "clickup", "name": "ClickUp", "url": "https://mcp.clickup.com/mcp", "auth": "oauth",
     "description": "Tasks, lists, docs and comments in ClickUp."},
    {"id": "airtable", "name": "Airtable", "url": "https://mcp.airtable.com/mcp", "auth": "oauth",
     "description": "Read and update Airtable bases, tables and records."},
    {"id": "attio", "name": "Attio", "url": "https://mcp.attio.com/mcp", "auth": "oauth",
     "description": "People, companies, deals and notes in your Attio CRM."},
    {"id": "intercom", "name": "Intercom", "url": "https://mcp.intercom.com/mcp", "auth": "oauth",
     "description": "Conversations, contacts and help articles in Intercom."},
    {"id": "granola", "name": "Granola", "url": "https://mcp.granola.ai/mcp", "auth": "oauth",
     "description": "Your meeting notes and transcripts from Granola."},
    {"id": "canva", "name": "Canva", "url": "https://mcp.canva.com/mcp", "auth": "oauth",
     "description": "Search, create and export Canva designs."},
    {"id": "zapier", "name": "Zapier", "url": "https://mcp.zapier.com/api/mcp/mcp", "auth": "oauth",
     "description": "Run the actions you set up in Zapier across thousands of apps (Gmail, Slack, HubSpot and more)."},
    {"id": "netlify", "name": "Netlify", "url": "https://netlify-mcp.netlify.app/mcp", "auth": "oauth",
     "description": "Sites, deploys, forms and environment variables on Netlify."},
    {"id": "webflow", "name": "Webflow", "url": "https://mcp.webflow.com/mcp", "auth": "oauth",
     "description": "Sites, pages and CMS collections in Webflow."},
]
_LOCK = threading.Lock()
_FLOWS: dict[str, dict] = {}            # state -> pending sign-in
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
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(p)


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


def add_custom(name: str, url: str, token: str = "") -> dict:
    """Add any remote MCP server by URL. With a token it uses that; otherwise it signs in through the browser."""
    name, url, token = (name or "").strip(), (url or "").strip(), (token or "").strip()
    if not re.match(r"https://[^\s/]+", url):
        raise ValueError("Enter the server's https:// address.")
    base = re.sub(r"[^a-z0-9]+", "-", (name or urllib.parse.urlparse(url).netloc).lower()).strip("-")[:30] or "server"
    ids = {c["id"] for c in _all()}
    cid, n = "x-" + base, 2
    while cid in ids:
        cid, n = f"x-{base}-{n}", n + 1
    row = {"id": cid, "name": name or urllib.parse.urlparse(url).netloc, "url": url,
           "auth": "token" if token else "oauth", "description": f"Your server at {urllib.parse.urlparse(url).netloc}."}
    if not token:
        _discover(url)                      # fail now, with a clear message, if it cannot sign in
    with _LOCK:
        st = _read(_state_file())
        st.setdefault("custom", []).append(row)
        _write(_state_file(), st)
        if token:
            store = _read(_auth_file())
            store[cid] = {"access_token": token, "static": True}
            _write(_auth_file(), store)
    return get(cid)


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
    for c in _well_known(issuer, "oauth-authorization-server") + _well_known(issuer, "openid-configuration"):
        s, _, j = _http(c)
        if s == 200 and j.get("authorization_endpoint") and j.get("token_endpoint"):
            meta = j
            break
    if not meta:
        if st == 0:
            raise ValueError(f"Could not reach {url}. Check the internet connection.")
        raise ValueError("This server does not publish how to sign in (no OAuth metadata).")
    return {"as": meta, "resource": prm.get("resource") or url, "scopes": prm.get("scopes_supported") or []}


def _register(meta: dict, redirect: str) -> dict:
    reg = meta.get("registration_endpoint")
    if not reg:
        raise ValueError("This server only accepts apps it already knows, so Meshpool cannot sign in to it directly.")
    s, _, j = _http(reg, data={"client_name": "Meshpool", "redirect_uris": [redirect],
                               "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
                               "token_endpoint_auth_method": "none", "client_uri": "https://meshpool.ai"})
    if s not in (200, 201) or not j.get("client_id"):
        raise ValueError(f"The server would not register Meshpool: {j.get('error_description') or j.get('error') or s}")
    return j


# ---------- sign-in -----------------------------------------------------------------------------------------------

def begin(cid: str, redirect: str) -> str:
    """Start signing in to a connector; returns the URL to open in the browser."""
    c = get(cid)
    if c["auth"] != "oauth":
        raise ValueError(f"{c['name']} uses a token. Paste it in the connector's settings.")
    d = _discover(c["url"])
    meta = d["as"]
    store = _read(_auth_file())
    entry = store.get(cid) or {}
    client = entry.get("client") if entry.get("redirect") == redirect and entry.get("issuer") == meta.get("issuer") \
        else None
    if not client:
        client = _register(meta, redirect)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    q = {"response_type": "code", "client_id": client["client_id"], "redirect_uri": redirect, "state": state,
         "code_challenge": challenge, "code_challenge_method": "S256", "resource": d["resource"]}
    scopes = d["scopes"] or meta.get("scopes_supported") or []
    if scopes and cid not in ("notion", "linear"):
        q["scope"] = " ".join(scopes)
    with _LOCK:
        _FLOWS[state] = {"cid": cid, "verifier": verifier, "client": client, "meta": meta, "redirect": redirect,
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
    name = get(f["cid"])["name"]
    if error or not code:
        raise ValueError(f"{name} did not finish signing in ({error or 'no code'}).")
    s, _, tok = _http(f["meta"]["token_endpoint"], form={
        "grant_type": "authorization_code", "code": code, "redirect_uri": f["redirect"],
        "client_id": f["client"]["client_id"], "code_verifier": f["verifier"], "resource": f["resource"]})
    if s != 200 or not tok.get("access_token"):
        raise ValueError(f"{name} refused the sign-in: {tok.get('error_description') or tok.get('error') or s}")
    with _LOCK:
        store = _read(_auth_file())
        store[f["cid"]] = {"client": f["client"], "redirect": f["redirect"], "issuer": f["meta"].get("issuer"),
                           "token_endpoint": f["meta"]["token_endpoint"], "resource": f["resource"],
                           "access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
                           "expires_at": time.time() + float(tok.get("expires_in") or 3600) - 60,
                           "connected": time.time()}
        _write(_auth_file(), store)
        st = _read(_state_file())
        st.setdefault("enabled", {})[f["cid"]] = True
        _write(_state_file(), st)
    return name


def _fresh_token(cid: str) -> str | None:
    with _LOCK:
        store = _read(_auth_file())
        e = store.get(cid)
        if not e or not e.get("access_token"):
            return None
        if time.time() < float(e.get("expires_at") or 0):
            return e["access_token"]
        if not e.get("refresh_token"):
            return e["access_token"]                  # let the server decide; it may still accept it
        s, _, tok = _http(e["token_endpoint"], form={"grant_type": "refresh_token", "refresh_token": e["refresh_token"],
                                                     "client_id": e["client"]["client_id"], "resource": e.get("resource", "")})
        if s != 200 or not tok.get("access_token"):
            e["expired"] = True
            _write(_auth_file(), store)
            return None
        e.update({"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token") or e["refresh_token"],
                  "expires_at": time.time() + float(tok.get("expires_in") or 3600) - 60, "expired": False})
        _write(_auth_file(), store)
        return e["access_token"]


def _token_for(c: dict) -> str | None:
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
        if c["auth"] == "token":
            connected = bool(_token_for(c))
        else:
            e = store.get(c["id"]) or {}
            connected = bool(e.get("access_token")) and not e.get("expired")
        if c["id"] == "github":
            c = dict(c, can_detect=not connected, can_sign_in=bool(github_client_id()))   # detection runs on click only
        rows.append({**{k: c[k] for k in ("id", "name", "description", "auth", "url")},
                     **{k: c[k] for k in ("can_detect", "can_sign_in") if k in c},
                     "token_help": c.get("token_help", ""), "connected": connected, "custom": bool(c.get("custom")),
                     "enabled": connected and st.get(c["id"], True),
                     "expired": bool((store.get(c["id"]) or {}).get("expired"))})
    return rows


def set_enabled(cid: str, on: bool):
    get(cid)
    st = _read(_state_file())
    st.setdefault("enabled", {})[cid] = bool(on)
    _write(_state_file(), st)


def disconnect(cid: str):
    c = get(cid)
    if c.get("custom"):
        remove_custom(cid)
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
        if not st.get(c["id"], True):
            continue
        tok = _token_for(c)
        if tok:
            out[c["id"]] = {"url": c["url"], "headers": {"Authorization": f"Bearer {tok}"},
                            "description": f"{c['name']}: {c['description']}", "timeout": 120, "connector": True}
    return out


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
