"""Smoke test of a built Meshpool, run with the app's own bundled Python before a release is published.

    <bundled python> packaging/smoke.py --app <the app's code folder> --version 0.0.5

Checks what a user would hit first, with no network and no paid calls:
  1. the packages the app ships with import (PDF, Word, Excel, PowerPoint, browser),
  2. the app's version is the release's version,
  3. Word and PDF files can be made,
  4. the app's server starts, and a Meshpool Code chat gets an answer from a stand-in model (testkit/mock_llm.py).
Exits non-zero on the first failure, so the release is not published.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def step(msg):
    print(f"smoke: {msg}", flush=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, secs: int = 90):
    end = time.time() + secs
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            time.sleep(1)
    raise SystemExit(f"smoke: FAILED, nothing answered at {url} within {secs} s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True, help="the built app's code folder (holds meshpool_harness)")
    ap.add_argument("--version", required=True)
    ap.add_argument("--launch", nargs="*", default=None,
                    help="command that starts Python with the app on its path (default: this Python)")
    a = ap.parse_args()
    app = Path(a.app).resolve()
    sys.path.insert(0, str(app))
    env = dict(os.environ, PYTHONPATH=str(app), PYTHONUTF8="1")

    step("imports")
    import importlib
    for mod in ("meshpool_harness", "pptx", "docx", "openpyxl", "reportlab", "pypdf", "pypdfium2", "pdfminer",
                "PIL", "lxml", "yaml", "pandas", "playwright"):
        importlib.import_module(mod)
    import meshpool_harness as MH
    if MH.__version__ != a.version:
        raise SystemExit(f"smoke: FAILED, the app says version {MH.__version__}, the release is {a.version}")

    step("Word and PDF files")
    tmp = Path(tempfile.mkdtemp(prefix="meshpool-smoke-"))
    proj, home = tmp / "proj", tmp / "home"
    proj.mkdir()
    home.mkdir()
    os.environ.update(MESHPOOL_PROJECT=str(proj), MESHPOOL_HOME=str(home), MH_CLARITY="0")
    from meshpool_harness.agent import office_mcp_server as O
    for fn, name in ((O.make_document, "smoke.docx"), (O.make_pdf, "smoke.pdf")):
        out, bad = fn("# Smoke test\n\nRevenue grew 12% to $4.2m.\n\n| a | b |\n|---|---|\n| 1 | 2 |", name)
        if bad or not (proj / "Meshpool outputs" / name).stat().st_size:
            raise SystemExit(f"smoke: FAILED, {name}: {out}")

    step("server and a chat")
    mport, uport = free_port(), free_port()
    (home / "meshpool.config").write_text(f"MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:{mport}/v1\n")
    launch = a.launch or [sys.executable]
    procs = [subprocess.Popen([sys.executable, str(REPO / "testkit" / "mock_llm.py"), str(mport), str(tmp / "mock.jsonl")],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    log = open(tmp / "ui.log", "w")
    procs.append(subprocess.Popen(launch + ["-m", "meshpool_harness", "ui", "--no-browser", "--port", str(uport)],
                                  env=dict(env, MESHPOOL_HOME=str(home)), stdout=log, stderr=subprocess.STDOUT))
    try:
        base = f"http://127.0.0.1:{uport}"
        page = wait_http(base + "/")
        tok = re.search(r'token: "([^"]+)"', page).group(1)

        def api(path, body=None):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"X-Meshpool-Token": tok, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read() or b"{}")
        sid = api("/api/agent", {"workdir": str(proj), "harness": "meshpool-code", "mode": "auto"})["id"]
        time.sleep(1)
        api(f"/api/agent/{sid}/send", {"text": "Write hello and run it"})
        end, v = time.time() + 120, {}
        while time.time() < end:
            time.sleep(1)
            v = api(f"/api/agent/{sid}?since=0")
            if v.get("state") == "idle" and any(e.get("type") == "assistant" for e in v.get("events", [])):
                break
        replies = [e.get("text", "") for e in v.get("events", []) if e.get("type") == "assistant" and e.get("text")]
        errors = [e.get("text", "") for e in v.get("events", []) if e.get("type") == "error"]
        if not replies or errors:
            raise SystemExit(f"smoke: FAILED, the chat did not answer (state {v.get('state')}, errors {errors[:2]})")
        step(f"chat answered: {replies[-1][:80]!r}")
    except SystemExit:
        log.flush()
        print((tmp / "ui.log").read_text(errors="replace")[-3000:])
        raise
    finally:
        for p in procs:
            p.kill()
    step(f"PASSED (Meshpool {MH.__version__})")


if __name__ == "__main__":
    main()
