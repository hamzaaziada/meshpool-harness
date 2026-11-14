"""Memory and scheduled tasks work in every agent: each one saves a memory, every new chat (any agent) gets it, and
each one schedules a task through the shared meshpool-assistant tools. Mock model (testkit/mock_llm.py), no paid calls.
Usage: python memory_check.py PORT AGENT[,AGENT...]   e.g. 8799 meshpool-code,meshpool-work,pi,hermes,opencode
The app at PORT must have /tmp/memproj as a folder it can open (this script adds it as a project)."""
import json, re, sys, time, urllib.request
B = "http://127.0.0.1:" + sys.argv[1]
TOK = re.search(r'token: "([^"]+)"', urllib.request.urlopen(B + "/").read().decode()).group(1)
def call(p, body=None, t=300):
    r = urllib.request.Request(B + p, data=None if body is None else json.dumps(body).encode(), headers={"X-Meshpool-Token": TOK, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=t).read() or b"{}")
def chat(h, text, wait=240):
    v = call("/api/agent", {"workdir": "/tmp/memproj", "harness": h, "kind": "cowork", "mode": "auto"})
    sid = v["id"]
    for _ in range(60):
        if call(f"/api/agent/{sid}?since=0")["state"] != "starting": break
        time.sleep(1)
    call(f"/api/agent/{sid}/send", {"text": text})
    end = time.time() + wait
    while time.time() < end:
        time.sleep(1.5); v = call(f"/api/agent/{sid}?since=0")
        if v["state"] in ("idle", "error") and any(e["type"] in ("turn_end", "error") for e in v["events"]): break
    texts = [e.get("text", "") for e in v["events"] if e["type"] in ("assistant", "error")]
    return (texts[-1] if texts else f"(no reply, state {v['state']})").strip().replace("\n", " ")[:150]
call("/api/projects", {"action": "add", "path": "/tmp/memproj"})
hs = sys.argv[2].split(",")
for h in hs:
    print(f"[{h}] remember ->", chat(h, f"Please remember that {h} user prefers EUR reports"))
print("memory file:", call("/api/assistant")["memory"].replace("\n", " | ")[:400])
for h in hs:
    print(f"[{h}] new chat recall ->", chat(h, "What do you remember about me?"))
n0 = len(call("/api/assistant")["tasks"])
for h in hs:
    print(f"[{h}] schedule ->", chat(h, "Schedule a daily morning brief for me"))
tasks = call("/api/assistant")["tasks"]
print("tasks:", len(tasks) - n0, "new;", [(t["name"], t.get("describe")) for t in tasks][:3])
