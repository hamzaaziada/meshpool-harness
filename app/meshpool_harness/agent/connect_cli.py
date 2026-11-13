"""Connectors from the terminal: the same catalog, sign-in and custom servers as Settings, Connectors in the app.

  meshpool connect                       list them, connected first
  meshpool connect notion                sign in (browser), paste a token, or turn on an open server
  meshpool connect https://x.dev/mcp     add any remote MCP server; Meshpool works out what it needs
  meshpool disconnect notion

The browser sign-in comes back to a small local page on 127.0.0.1 (the same address the app uses), so nothing passes
through any Meshpool server.
"""
from __future__ import annotations

import getpass
import os
import re
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from meshpool_harness import mcp_apps as CN

PAGE = ("<!doctype html><meta charset=utf-8><title>Meshpool</title><body style=\"font:15px -apple-system,Segoe UI,"
        "sans-serif;display:grid;place-items:center;height:90vh\"><div style=\"max-width:460px\"><h2>{title}</h2>"
        "<p>{msg}</p></div>")


class _Callback:
    """A one-shot local page for the OAuth redirect: /oauth/callback on 127.0.0.1, port 8765 if it is free."""

    def __init__(self):
        self.done = threading.Event()
        self.result: dict = {}
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                if u.path != "/oauth/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                q = dict(urllib.parse.parse_qsl(u.query))
                try:
                    name = CN.finish(q.get("state", ""), q.get("code"), q.get("error"))
                    outer.result = {"ok": True, "name": name}
                    title, msg = "Connected", f"{name} is connected. You can close this tab and go back to the terminal."
                except Exception as e:  # noqa: BLE001
                    outer.result = {"ok": False, "error": str(e)}
                    title, msg = "Sign-in did not finish", str(e)
                body = PAGE.format(title=title, msg=msg.replace("<", "&lt;")).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body)
                outer.done.set()

        self.httpd = None
        for port in range(8765, 8785):
            try:
                self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
                break
            except OSError:
                continue
        if not self.httpd:
            raise RuntimeError("No free local port between 8765 and 8784 for the sign-in to come back to.")
        self.redirect = f"http://127.0.0.1:{self.httpd.server_address[1]}/oauth/callback"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _p(s: str = "") -> None:
    print(s, flush=True)


def _style():
    from meshpool_harness.agent.tui import Style, _enable_ansi
    return Style(_enable_ansi())


def listing(st=None) -> int:
    st = st or _style()
    rows = CN.listing()
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault("Connected" if r["connected"] else ("Yours" if r["custom"] else r["category"] or "Other"),
                          []).append(r)
    for g in ["Connected", "Engineering", "Work and documents", "Sales and marketing", "Finance and data", "Yours"]:
        if g not in groups:
            continue
        _p(f"\n  {st.bold(g)}")
        for r in groups[g]:
            how = {"none": "no sign-in", "token": "token", "oauth": "browser sign-in"}[r["auth"]]
            if r["needs_client"] and not r["own_client"]:
                how = "your own client ID"
            mark = st.green("on ") if r["connected"] and r["enabled"] else st.dim("off") if r["connected"] else "   "
            _p(f"    {mark} {r['id']:<18} {r['name']:<22} {st.dim(how)}")
    _p(st.dim("\n  meshpool connect <name>   connect one   ·   meshpool connect <https://...>   add any MCP server"))
    return 0


def _ask_client(st, cid: str, redirect: str) -> bool:
    c = CN.get(cid)
    _p()
    _p("  " + (c.get("client_help") or "This app only signs in apps it knows. Create an OAuth app in its developer "
                                       "settings with the redirect URL below, then paste its client ID and secret."))
    _p(f"  Redirect URL: {st.blue(redirect)}")
    _p()
    try:
        cid_v = input(f"  {st.blue('›')} Client ID: ").strip()
        if not cid_v:
            return False
        sec = getpass.getpass(f"  {st.blue('›')} Client secret {st.dim('(Enter if none; hidden)')}: ").strip()
    except (EOFError, KeyboardInterrupt):
        _p()
        return False
    CN.set_client(cid, cid_v, sec)
    return True


def _browser_sign_in(st, cid: str) -> bool:
    cb = _Callback()
    try:
        for _ in range(2):
            try:
                url = CN.begin(cid, cb.redirect)
                break
            except CN.NeedsClient:
                if not _ask_client(st, cid, cb.redirect):
                    return False
        else:
            return False
        opened = False
        try:
            opened = webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
        _p(f"  {'Your browser opened' if opened else 'Open this link'} to sign in to {CN.get(cid)['name']}:")
        _p(f"  {st.dim(url)}")
        _p(st.dim("  Waiting for the sign-in (Ctrl+C to stop)..."))
        try:
            if not cb.done.wait(600):
                _p(st.red("  The sign-in did not finish within 10 minutes."))
                return False
        except KeyboardInterrupt:
            _p()
            return False
        if not cb.result.get("ok"):
            _p(st.red("  " + cb.result.get("error", "The sign-in did not finish.")))
            return False
        return True
    finally:
        cb.close()


def _report(st, cid: str) -> bool:
    r = CN.check(cid)
    name = CN.get(cid)["name"]
    if r["ok"]:
        _p(f"  {st.green('✓')} {name} is connected: {len(r['tools'])} tools. New sessions use it.")
    else:
        _p(st.red(f"  {name}: {r['error']}"))
    return r["ok"]


def connect(target: str, name: str = "", token: str = "", st=None) -> tuple[bool, str]:
    """Connect a catalog app by id or name, or add a server by URL. Returns (ok, connector id)."""
    st = st or _style()
    target = target.strip()
    if re.match(r"https?://", target):
        _p(st.dim("  Checking the server..."))
        try:
            c = CN.add_custom(name, target, token)
        except ValueError as e:
            _p(st.red(f"  {e}"))
            return False, ""
        cid = c["id"]
        if c["auth"] == "oauth" and not _browser_sign_in(st, cid):
            return False, cid
        return _report(st, cid), cid
    want = target.lower()
    rows = CN.listing()
    hit = next((r for r in rows if r["id"] == want), None) or \
        next((r for r in rows if r["name"].lower() == want), None) or \
        next((r for r in rows if want in r["id"] or want in r["name"].lower()), None)
    if not hit:
        _p(st.red(f"  No connector called {target!r}. `meshpool connect` lists them; or pass a server's URL."))
        return False, ""
    cid = hit["id"]
    if hit["auth"] == "none":
        CN.set_enabled(cid, True)
    elif hit["auth"] == "token":
        c = CN.get(cid)
        _p("  " + (c.get("token_help") or f"{c['name']} uses a token."))
        try:
            tok = token or getpass.getpass(f"  {st.blue('›')} Paste the token {st.dim('(hidden)')}: ").strip()
        except (EOFError, KeyboardInterrupt):
            _p()
            return False, cid
        if not tok:
            return False, cid
        CN.set_token(cid, tok)
    elif not _browser_sign_in(st, cid):
        return False, cid
    return _report(st, cid), cid


def disconnect(target: str, st=None) -> int:
    st = st or _style()
    rows = CN.listing()
    hit = next((r for r in rows if r["id"] == target or r["name"].lower() == target.lower()), None)
    if not hit:
        _p(st.red(f"  No connector called {target!r}."))
        return 1
    CN.disconnect(hit["id"])
    _p(f"  {hit['name']} is disconnected.")
    return 0


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="meshpool connect", description="Connect apps (MCP servers) to every agent.")
    ap.add_argument("target", nargs="?", help="a connector name (see the list) or a server's https:// address")
    ap.add_argument("--name", default="", help="a name for a server added by address")
    ap.add_argument("--token", default="", help="a token or API key (Header-Name: value for another header)")
    a = ap.parse_args(argv)
    if os.environ.get("MESHPOOL_HOME"):
        from meshpool_harness import config as C
        C.ensure_home()
    if not a.target:
        return listing()
    ok, _ = connect(a.target, a.name, a.token)
    return 0 if ok else 1
