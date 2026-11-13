"""Threads survive a restart: the message, the steps, stop notes and approvals look the same, and a turn cut off by
the restart says so. Starts and kills its own app on the mock model.

  python restart_check.py PYTHON MOCK_PORT WORKDIR
"""
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PY, MOCK, W = sys.argv[1], sys.argv[2], Path(sys.argv[3])
APP = Path(__file__).resolve().parents[1] / "app"
PORT = 8789
HOME = W / "home"
PROJ = W / "proj"
R = []


def check(n, ok, d=""):
    R.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"  -> {str(d)[:300]}"), flush=True)


def start():
    env = {k: v for k, v in os.environ.items() if not re.match(r"(CLAUDE|ANTHROPIC|OPENAI|CODEX|AWS|GH|GITHUB|GOOGLE|AZURE|GEMINI)_", k)}
    env.update(MESHPOOL_HOME=str(HOME), MH_BROWSER_HEADLESS="1")
    p = subprocess.Popen([PY, "-m", "meshpool_harness", "ui", "--port", str(PORT), "--no-browser"], cwd=APP, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=1)
            break
        except OSError:
            time.sleep(0.5)
    tok = re.search(r'token: "([^"]+)"', urllib.request.urlopen(f"http://127.0.0.1:{PORT}/").read().decode()).group(1)

    def c(path, body=None):
        r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=None if body is None else json.dumps(body).encode(),
                                   headers={"X-Meshpool-Token": tok, "Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(r, timeout=60).read() or b"{}")
    return p, c


def kill(p):
    os.killpg(p.pid, signal.SIGKILL)
    p.wait()
    subprocess.run("pkill -f 'sleep 25'", shell=True)
    time.sleep(1)


def wait(c, sid, want, t=30):
    t0 = time.time()
    while time.time() - t0 < t:
        if c(f"/api/agent/{sid}?since=100000")["state"] in want:
            return True
        time.sleep(0.3)
    return False


HOME.mkdir(parents=True, exist_ok=True)
PROJ.mkdir(parents=True, exist_ok=True)
(HOME / "meshpool.config").write_text(f"MESHPOOL_BASE_URL=http://127.0.0.1:{MOCK}/v1\nMESHPOOL_API_KEY=test-key-1234\n")
subprocess.run("git init -q && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m i", shell=True, cwd=PROJ)

p, c = start()
# thread A: a finished turn, then a stopped turn
a = c("/api/agent", {"workdir": str(PROJ), "harness": "meshpool-code", "kind": "code", "mode": "auto"})["id"]
time.sleep(1)
c(f"/api/agent/{a}/send", {"text": "say hello"}); wait(c, a, ["idle"])
c(f"/api/agent/{a}/send", {"text": "long task please"}); wait(c, a, ["working"]); time.sleep(3)
c(f"/api/agent/{a}/stop", {}); wait(c, a, ["idle"])
before = [(e["type"], e.get("text") or e.get("output") or "") for e in c(f"/api/agent/{a}?since=0")["events"]
          if e["type"] in ("user", "assistant", "interrupted", "tool_end")]
# thread B: brand new, killed while its first turn runs
b = c("/api/agent", {"workdir": str(PROJ), "harness": "meshpool-code", "kind": "code", "mode": "auto"})["id"]
time.sleep(1)
c(f"/api/agent/{b}/send", {"text": "long task please"}); wait(c, b, ["working"]); time.sleep(3)
kill(p)

p, c = start()
lst = {t["id"]: t for t in c("/api/agent")["sessions"]}
check("a thread started just before a restart keeps its title", lst.get(b, {}).get("title", "").lower().startswith("long task"),
      lst.get(b))
eb = c(f"/api/agent/{b}?since=0")["events"]
check("its message is still there", any(e["type"] == "user" and "long task" in (e.get("text") or "") for e in eb),
      [e["type"] for e in eb])
note = [e for e in eb if e["type"] == "interrupted"]
check("it says the restart stopped it and offers the message back", note and note[-1].get("resend", "").startswith("long task"),
      note)
after = [(e["type"], e.get("text") or e.get("output") or "") for e in c(f"/api/agent/{a}?since=0")["events"]
         if e["type"] in ("user", "assistant", "interrupted", "tool_end")]
check("a stopped turn looks the same after a restart (stop note and step result kept)", after == before,
      {"before": before[-4:], "after": after[-4:]})
check("a finished thread gets no false 'closed while running' note",
      not any(e.get("restarted") for e in c(f"/api/agent/{a}?since=0")["events"]))
kill(p)
print(f"\n{sum(R)}/{len(R)} passed")
sys.exit(0 if all(R) else 1)
