"""Stop must stop, for every harness, whatever the agent is waiting on. Against a running app and the mock model.

  python stop_check.py PROJECT_DIR [BASE_URL]

Cases: a model that takes 30 s to answer; a tool call that blocks for 30 s (built-in agents); a 25 s command. Each
is stopped after 2 s of work. Pass: the thread is idle within 5 s of Stop, nothing from the stopped request appears
afterwards, and the next message is answered normally.
"""
import http.server
import json
import re
import socket
import sys
import threading
import time
import urllib.request

P = sys.argv[1]
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8799"
TOK = re.search(r'token: "([^"]+)"', urllib.request.urlopen(BASE + "/").read().decode()).group(1)
RESULTS = []


def call(path, body=None, timeout=60):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Meshpool-Token": TOK, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read() or b"{}")


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {str(detail)[:300]}"), flush=True)


class Slow(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(30)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<html><body>slow page</body></html>")

    def log_message(self, *a):
        pass


s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
threading.Thread(target=http.server.ThreadingHTTPServer(("127.0.0.1", port), Slow).serve_forever, daemon=True).start()
SLOW_URL = f"http://127.0.0.1:{port}/page"


def state(sid):
    return call(f"/api/agent/{sid}?since=1000000").get("state")


def events(sid):
    return call(f"/api/agent/{sid}?since=0").get("events", [])


def stop_case(hid, kind, text, label):
    v = call("/api/agent", {"workdir": P, "harness": hid, "kind": kind, "mode": "auto"})
    sid = v["id"]
    time.sleep(1.5)
    call(f"/api/agent/{sid}/send", {"text": text})
    t0 = time.time()
    while state(sid) != "working" and time.time() - t0 < 30:
        time.sleep(0.2)
    time.sleep(2)
    n_before = len(events(sid))
    call(f"/api/agent/{sid}/stop", {})
    t1 = time.time()
    while state(sid) not in ("idle", "saved") and time.time() - t1 < 15:
        time.sleep(0.1)
    took = time.time() - t1
    check(f"[{hid}] {label}: stops within 5 s", took <= 5, f"{took:.1f} s, state {state(sid)}")
    time.sleep(1)
    evs = events(sid)
    said = [e for e in evs if e["type"] == "interrupted" or "Stopped" in (e.get("text") or "")]
    check(f"[{hid}] {label}: says it stopped", said, [e["type"] for e in evs[-6:]])
    return sid, n_before


def leak_and_continue(hid, sid, marker):
    time.sleep(32)                                     # past the point the stopped request would have answered
    evs = events(sid)
    leaked = [e for e in evs if marker in (e.get("text") or "") or marker in str(e.get("output") or "")]
    check(f"[{hid}] nothing from the stopped request appears later", not leaked, leaked[:1])
    call(f"/api/agent/{sid}/send", {"text": "say hello"})
    t0 = time.time()
    while time.time() - t0 < 60:
        evs = events(sid)
        if state(sid) == "idle" and evs and evs[-1]["type"] == "turn_end":
            break
        time.sleep(0.5)
    last = [e.get("text") or "" for e in evs if e["type"] == "assistant"]
    check(f"[{hid}] the next message is answered", last and "done thinking" not in last[-1].lower(), last[-1:])


LISTED = {h["id"] for h in call("/api/harnesses").get("harnesses", [])}
for hid, kind in (("meshpool-code", "code"), ("meshpool-work", "cowork"), ("pi", "code"), ("hermes", "cowork")):
    if hid not in LISTED:                      # e.g. no Pi or Hermes in a cloud container: say so, do not crash
        print(f"SKIP [{hid}] not installed here", flush=True)
        continue
    sid, _ = stop_case(hid, kind, "think slowly please", "slow model")
    leak_and_continue(hid, sid, "done thinking")
    stop_case(hid, kind, "long task please", "long command")
for hid, kind in (("meshpool-code", "code"), ("meshpool-work", "cowork")):
    stop_case(hid, kind, f"fetch the slow page {SLOW_URL}", "slow tool")

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
