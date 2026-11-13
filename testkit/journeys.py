"""Consumer journeys the full API test (e2e_full.py) does not cover, against a running app and the mock model.

  python journeys.py PROJECT_DIR [CODE_TOOLS_PORT]

Covers, for each chat harness where it applies: using an imported skill in chat, an MCP tool called in chat, writing
and running a file, a Word document from Meshpool Work, browser use on a real page (read-only date field, late menu),
computer use failing cleanly where it is not supported, the meshpool command line, and (when CODE_TOOLS_PORT points at
an app started with MH_OBSERVE=1 MH_NAV=1 MH_LINT_EDIT=1) probe, symbol and the edit check inside Meshpool Code.
Run e2e_full.py first: it imports claude-skill and turns the MCP servers on. Prints PASS/FAIL lines and exits 1 on
any failure.
"""
import functools
import http.server
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

P = sys.argv[1]
CODE_PORT = sys.argv[2] if len(sys.argv) > 2 else None
APP = Path(__file__).resolve().parents[1] / "app"
RESULTS = []


def client(base):
    tok = re.search(r'token: "([^"]+)"', urllib.request.urlopen(base + "/").read().decode()).group(1)

    def call(path, body=None, timeout=120):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-Meshpool-Token": tok, "Content-Type": "application/json"})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return {"_http": e.code, **json.loads(e.read() or b"{}")}
            except ValueError:
                return {"_http": e.code}
    return call


call = client(os.environ.get("MP_BASE", "http://127.0.0.1:8799"))


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {str(detail)[:400]}"), flush=True)
    return cond


def run_turn(c, harness, kind, text, workdir=P, limit=180):
    v = c("/api/agent", {"workdir": workdir, "harness": harness, "kind": kind, "mode": "auto"})
    if "id" not in v:
        return {"error": v}
    sid = v["id"]
    time.sleep(1.5)
    c(f"/api/agent/{sid}/send", {"text": text})
    for _ in range(limit * 2):
        v = c(f"/api/agent/{sid}?since=0")
        done = {e["id"] for e in v.get("events", []) if e["type"] == "approval_done"}
        for e in v.get("events", []):
            if e["type"] == "approval" and e["id"] not in done:
                c(f"/api/agent/{sid}/answer", {"id": e["id"], "decision": "yes"})
        if v.get("state") == "idle" and any(e["type"] == "turn_end" for e in v.get("events", [])):
            break
        time.sleep(0.5)
    return v


def last_text(v):
    t = [e.get("text") or "" for e in v.get("events", []) if e["type"] == "assistant"]
    return t[-1] if t else ""


def tool_outputs(v):
    return [str(e.get("output") or "") for e in v.get("events", []) if e["type"] == "tool_end"]


def errors(v):
    return [e.get("text") for e in v.get("events", []) if e["type"] == "error"]


def serve_page():
    d = Path(P) / "_site"
    d.mkdir(exist_ok=True)
    (d / "form.html").write_text(
        "<!doctype html><html><head><title>Booking</title></head><body><h1>Book a trip</h1>"
        "<label>Name <input id='n'></label><label>Date <input id='d' readonly></label>"
        "<button onclick=\"document.body.insertAdjacentHTML('beforeend','<p>Booked</p>')\">Book</button>"
        "</body></html>")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    h = functools.partial(type("Q", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None}),
                          directory=str(d))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/form.html"


# ---------------------------------------------------------------- skills used in chat
for hid, kind in (("meshpool-code", "code"), ("meshpool-work", "cowork"), ("pi", "code"), ("hermes", "cowork")):
    v = run_turn(call, hid, kind, "$claude-skill greet me")
    check(f"[{hid}] an imported skill is used in chat", "Bonjour" in last_text(v),
          (last_text(v), errors(v)))

# ---------------------------------------------------------------- MCP tool called in chat
for hid, kind in (("meshpool-code", "code"), ("pi", "code"), ("hermes", "cowork")):
    v = run_turn(call, hid, kind, "use the mcp calculator please")
    check(f"[{hid}] an MCP tool is called in chat", "calculator says" in last_text(v).lower()
          and "no tool" not in last_text(v), (last_text(v), errors(v)))

# ---------------------------------------------------------------- write and run a file
for hid, kind in (("meshpool-code", "code"), ("pi", "code"), ("hermes", "cowork")):
    v = run_turn(call, hid, kind, "hello, make a small script and run it")
    out = " ".join(tool_outputs(v)) + last_text(v)
    check(f"[{hid}] writes a file and runs it", "hello from meshpool" in out, (last_text(v), errors(v)))

# ---------------------------------------------------------------- an office file from Meshpool Work
before = set(p.name for p in Path(P).rglob("*.docx"))
v = run_turn(call, "meshpool-work", "cowork", "make a word doc of the spend report")
new = [p for p in Path(P).rglob("*.docx") if p.name not in before]
check("[meshpool-work] makes a Word document", new or "spend.docx" in last_text(v) or any(
    ".docx" in o for o in tool_outputs(v)), (last_text(v), tool_outputs(v)[-1:], errors(v)))

# ---------------------------------------------------------------- browser use on a real page
url = serve_page()
v = run_turn(call, "meshpool-code", "code", f"open the test page {url}")
outs = " ".join(tool_outputs(v))
check("[meshpool-code] browser opens a page and lists its elements", "Book a trip" in outs and "Date" in outs,
      (outs[:400], errors(v)))
check("[meshpool-code] browser fills a read-only date field at once (MH_BROWSER_READONLY)",
      "read-only" in outs and "10/23/2016" in outs, outs[-600:])

# ---------------------------------------------------------------- computer use where unsupported (Linux)
if sys.platform not in ("darwin", "win32"):
    r = subprocess.run([sys.executable, "-c", "from meshpool_harness.agent import computer_mcp_server as m; "
                        "print(m.screenshot())"], cwd=APP, capture_output=True, text=True, timeout=60)
    msg = (r.stdout + r.stderr)[-400:]
    check("computer use on an unsupported OS fails with a clear message, not a crash",
          "Traceback" not in msg or "not supported" in msg.lower() or "macos" in msg.lower(), msg)

# ---------------------------------------------------------------- the meshpool command line
env = dict(os.environ, MESHPOOL_BASE_URL="http://127.0.0.1:8911/v1", MESHPOOL_API_KEY="test-key-1234",
           PYTHONPATH=str(APP))
r = subprocess.run([sys.executable, "-m", "meshpool_harness", "check"], cwd=APP, env=env, capture_output=True,
                   text=True, timeout=120)
check("CLI: meshpool check runs", r.returncode == 0, (r.stdout + r.stderr)[-400:])
work = Path(P) / "_cli"
work.mkdir(exist_ok=True)
r = subprocess.run([sys.executable, "-m", "meshpool_harness", "run", "--engine", "code", "--workdir", str(work),
                    "please finish this harness task"], cwd=APP, env=env, capture_output=True, text=True,
                   timeout=300)
check("CLI: meshpool run finishes a one-shot task", "42" in (r.stdout + r.stderr), (r.stdout + r.stderr)[-500:])

# ---------------------------------------------------------------- probe, symbol and the edit check in Meshpool Code
if CODE_PORT:
    c2 = client(f"http://127.0.0.1:{CODE_PORT}")
    proj = Path(P) / "_codetools"
    proj.mkdir(exist_ok=True)
    (proj / "calc.py").write_text("def add(a, b):\n    return a + b\n\n\ndef total(xs):\n    return sum(xs)\n")
    (proj / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    x = add(2, 2)\n"
                                       "    assert x == 5\n")
    subprocess.run("git init -q && git add -A && git -c user.email=t@t -c user.name=t commit -qm i", shell=True,
                   cwd=proj)
    v = run_turn(c2, "meshpool-code", "code", "check the code", workdir=str(proj))
    outs = tool_outputs(v)
    joined = " ".join(outs)
    check("[meshpool-code + MH_OBSERVE] probe shows the failing test and its locals",
          "test_add" in joined and "x" in joined and "= 4" in joined, outs[:1])
    check("[meshpool-code + MH_NAV] symbol shows the definition and its uses",
          "definition: calc.py:1" in joined and "test_calc.py" in joined, outs[1:2])
    check("[meshpool-code + MH_LINT_EDIT] an edit that breaks a name is flagged at once",
          "F821" in joined and "bb" in joined, outs[-1:])
    check("[meshpool-code + code tools] no errors", not errors(v), errors(v))

passed = sum(ok for _, ok in RESULTS)
print(f"\n{passed}/{len(RESULTS)} passed")
for n, ok in RESULTS:
    if not ok:
        print("  failed:", n)
sys.exit(0 if passed == len(RESULTS) else 1)
