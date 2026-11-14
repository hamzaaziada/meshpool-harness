"""Live check of every built-in connector and MCP library entry (needs the internet; not part of pytest).

  python testkit/connectors_live.py            # all
  python testkit/connectors_live.py --library  # only the MCP library (starts each local server and lists its tools)
  python testkit/connectors_live.py --connectors

Connectors: the server answers; OAuth ones publish how to sign in and accept Meshpool's registration (or are marked
needs_client); open ones list their tools. Library: every local server starts through npx or uvx and lists tools, with
placeholder values for required settings (a server that checks its key at start-up is reported, not failed).
Exit code 1 if anything is broken.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from meshpool_harness import mcp_apps as CN  # noqa: E402
from meshpool_harness import mcp_library as L  # noqa: E402
from meshpool_harness.agent.mcp import Server  # noqa: E402

REDIRECT = "http://127.0.0.1:8765/oauth/callback"
KEY_WORDS = ("key", "token", "api", "credential", "unauthor", "auth", "401", "403", "password", "secret", "env")


def connector(c: dict) -> tuple[str, str, str]:
    try:
        if c["auth"] == "none":
            r = CN.test_server({"url": c["url"], **({"type": c["type"]} if c.get("type") else {})}, 40)
            return c["id"], "ok" if r["ok"] else "BROKEN", f"{len(r['tools'])} tools" if r["ok"] else r["error"][:150]
        if c["auth"] == "token":
            r = CN.test_server({"url": c["url"]}, 40)
            return c["id"], "ok" if r.get("status") in (401, 403) else "BROKEN", "asks for its token" \
                if r.get("status") in (401, 403) else r["error"][:150]
        d = CN._discover(c["url"])
        if c.get("needs_client"):
            return c["id"], "ok", "signs in with the user's own client"
        cl = CN._register(d["as"], REDIRECT, d["scopes"] or d["as"].get("scopes_supported"))
        return c["id"], "ok", "registers" + (f" (redirect {cl['redirect']})" if cl.get("redirect") != REDIRECT else "")
    except Exception as e:  # noqa: BLE001
        return c["id"], "BROKEN", str(e)[:150]


def library(e: dict, tmp: str) -> tuple[str, str, str]:
    if e.get("transport") in ("connector",):
        return e["id"], "ok", "uses connector " + e["connector"]
    env = {}
    for s in e["settings"]:
        n = s["name"]
        env[n] = tmp if n.endswith("_DIR") else \
            f"sqlite://{tmp}/check.db" if n == "DATABASE_URL" else \
            "mongodb://127.0.0.1:1/check" if n == "MDB_MCP_CONNECTION_STRING" else "placeholder-" + n.lower()
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        if e.get("transport") == "oauth":
            CN._discover(e["url"])
            return e["id"], "ok", "signs in through the browser"
        spec = {k: v for k, v in e.items() if k in ("command", "args", "env", "url", "headers")}
        s = Server(e["id"], spec, Path(tmp) / "logs", [tmp])
        s.start(240)
        s.close()
        if s.status == "connected":
            return e["id"], "ok", f"{len(s.tools)} tools"
        if e["settings"] and any(w in s.error.lower() for w in KEY_WORDS):
            return e["id"], "ok", "starts, then needs its real settings: " + s.error[:80]
        return e["id"], "BROKEN", s.error[:200]
    except Exception as x:  # noqa: BLE001
        return e["id"], "BROKEN", str(x)[:200]
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", action="store_true")
    ap.add_argument("--connectors", action="store_true")
    a = ap.parse_args()
    both = not (a.library or a.connectors)
    bad = 0
    if a.connectors or both:
        print("Connectors")
        with cf.ThreadPoolExecutor(12) as ex:
            for cid, st, note in ex.map(connector, CN.CATALOG):
                bad += st != "ok"
                print(f"  {cid:<18} {st:<7} {note}")
    if a.library or both:
        print("MCP library")
        tmp = tempfile.mkdtemp(prefix="mp-lib-")
        with cf.ThreadPoolExecutor(6) as ex:
            for eid, st, note in ex.map(lambda e: library(e, tmp), L.LIBRARY):
                bad += st != "ok"
                print(f"  {eid:<18} {st:<7} {note}")
    print(f"\n{'All working.' if not bad else f'{bad} broken.'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
