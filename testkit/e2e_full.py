"""Meshpool full API test against a running app (port 8799) and the mock model (port 8911).

Usage: python e2e_full.py PROJECT_DIR FIXTURES_DIR
Prints one line per check and a summary; exits 1 if anything failed.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("MP_BASE", "http://127.0.0.1:8799")
TOK = re.search(r'token: "([^"]+)"', urllib.request.urlopen(BASE + "/").read().decode()).group(1)
P, FX = sys.argv[1], sys.argv[2]
RESULTS = []


def call(path, body=None, timeout=120):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Meshpool-Token": TOK, "Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return {"_http": e.code, **json.loads(e.read() or b"{}")}
        except ValueError:
            return {"_http": e.code}


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {str(detail)[:300]}"), flush=True)
    return cond


def wait_idle(sid, approve=True, limit=150):
    v = {}
    for _ in range(limit * 2):
        v = call(f"/api/agent/{sid}?since=0")
        if approve:
            done = {e["id"] for e in v.get("events", []) if e["type"] == "approval_done"}
            for e in v.get("events", []):
                if e["type"] == "approval" and e["id"] not in done:
                    call(f"/api/agent/{sid}/answer", {"id": e["id"], "decision": "yes"})
        if v.get("state") == "idle" and any(e["type"] == "turn_end" for e in v.get("events", [])):
            return v
        time.sleep(0.5)
    return v


def texts(v, kind="assistant"):
    return [e.get("text") or "" for e in v.get("events", []) if e["type"] == kind]


# ---------------------------------------------------------------- settings, models, about
s = call("/api/settings")
check("settings load", "fields" in s and s.get("has_key"), s)
check("settings has search key fields", any(f["key"] == "TAVILY_API_KEY" for f in s["fields"]))
r = call("/api/settings", {"values": {"MH_BUDGET_USD": "0"}})
check("settings save", "fields" in r, r)
r = call("/api/test", {})
check("test connection", r.get("ok"), r)
m = call("/api/models")
check("models list", len(m.get("models", [])) > 0, m)
mid = m["models"][0]["id"]
check("model favorite", mid in call("/api/models/favorite", {"id": mid, "on": True}).get("favorites", []))
check("model default", call("/api/models/default", {"id": mid}).get("default") == mid)
a = call("/api/about")
check("about page has version, credit and license", a.get("version") and "Meshpool" in a.get("notice", "") and "Apache" in a.get("license", "") and "Hamza" not in json.dumps(a), a)
check("usage", "total" in call("/api/usage"))
check("permissions", "rows" in call("/api/permissions", {}))

# ---------------------------------------------------------------- projects and files
r = call("/api/projects", {"action": "add", "path": P})
check("project add", any(p["path"] == os.path.realpath(P) or p["path"] == P for p in r.get("projects", [])), r)
r = call("/api/projects", {"action": "rename", "path": P, "name": "E2E project"})
check("project rename", any(p.get("name") == "E2E project" for p in r.get("projects", [])), r)
q = urllib.parse.quote(P)
r = call(f"/api/files?root={q}")
check("file tree", isinstance(r.get("entries"), list), r)
raw = urllib.request.urlopen(f"{BASE}/raw?t={TOK}&root={q}&path=ru.png")
check("image preview is served", raw.headers.get("Content-Type") == "image/png" and len(raw.read()) > 1000)
for f in ("sample.docx", "sample.xlsx", "sample.pptx", "report.pdf", "scan.pdf", "data.csv", "model.pbix"):
    r = call(f"/api/file?root={q}&path={urllib.parse.quote(f)}", timeout=180)
    body = json.dumps(r)
    check(f"preview {f}", "_http" not in r and len(body) > 200, body[:200])
check("bad root is refused", "_http" in call("/api/files?root=/etc"))
r = call("/api/file", {"root": P, "path": "notes/new.md", "content": "# hi\n"})
check("file save", "_http" not in r and os.path.exists(os.path.join(P, "notes/new.md")), r)
import base64
r = call("/api/attach", {"root": P, "name": "attached.txt", "data": base64.b64encode(b"attached text").decode()})
check("attach", "_http" not in r, r)

# ---------------------------------------------------------------- git, terminal, CI
subprocess.run("git init -q && git add -A && git -c user.email=t@t -c user.name=t commit -qm init", shell=True, cwd=P)
open(os.path.join(P, "data.csv"), "a").write("x,1\n")
g = call(f"/api/git?root={q}")
check("git status shows the change", "data.csv" in json.dumps(g), g)
t = call("/api/term", {"root": P, "command": "echo terminal-ok && sleep 30"})
time.sleep(1.5)
check("terminal output", "terminal-ok" in json.dumps(call(f"/api/term/{t.get('id')}?since=0")), t)
check("terminal stop", "_http" not in call(f"/api/term/{t.get('id')}/stop", {}))
c = call(f"/api/git/ci?root={q}")
check("CI panel without a remote answers cleanly", "_http" not in c, c)

# ---------------------------------------------------------------- harnesses
h = call("/api/harnesses")
ids = [x["id"] for x in h.get("harnesses", [])]
for want in ("meshpool-auto", "meshpool-code", "meshpool-work", "pi", "hermes", "codex"):
    check(f"harness listed: {want}", want in ids, ids)
check("no agents Meshpool does not support", not {"claude-code", "gemini-cli"} & set(ids), ids)
r = call("/api/harnesses", {"example": "goose"})
check("add example harness", any(x["id"] == "goose" for x in r.get("harnesses", [])), r)
r = call("/api/harnesses", {"remove": "goose"})
check("remove example harness", not any(x["id"] == "goose" for x in r.get("harnesses", [])), r)
r = call("/api/harnesses", {"default_mode": "cowork", "id": "meshpool-work"})
check("set cowork default", r.get("defaults", {}).get("cowork") == "meshpool-work", r)
call("/api/harnesses", {"default_mode": "cowork", "id": "hermes"})
for task, cat in (("Fix the failing unit test in auth.py", "code"), ("Make a 10 slide deck on Q3 results", "documents"),
                  ("Build a revenue forecast in Excel", "data"), ("Research AI gateway pricing", "research"),
                  ("Find 30 leads and write cold emails", "gtm")):
    r = call("/api/route-harness", {"text": task, "kind": "cowork"})
    check(f"auto routes '{task[:28]}' as {cat}", r.get("category") == cat and r.get("harness"), r)

# ---------------------------------------------------------------- threads in every harness
for hid, kind in (("meshpool-code", "code"), ("meshpool-work", "cowork"), ("pi", "code"), ("hermes", "cowork")):
    for f in ("hello.py",):
        try:
            os.remove(os.path.join(P, f))
        except OSError:
            pass
    v = call("/api/agent", {"workdir": P, "harness": hid, "kind": kind, "mode": "ask"})
    sid = v.get("id")
    if not check(f"[{hid}] create thread", sid, v):
        continue
    time.sleep(2)
    call(f"/api/agent/{sid}/send", {"text": "say hello and create hello.py"})
    v = wait_idle(sid)
    check(f"[{hid}] approval then write file", os.path.exists(os.path.join(P, "hello.py")), texts(v)[-1:])
    check(f"[{hid}] reply", any(texts(v)), v.get("events", [])[-3:])
    # add-on while busy, then stop
    call(f"/api/agent/{sid}/mode", {"mode": "auto"})
    call(f"/api/agent/{sid}/send", {"text": "long task please"})
    time.sleep(6)
    r = call(f"/api/agent/{sid}/send", {"text": "also mention the color blue"})
    check(f"[{hid}] add-on accepted while working", r.get("sent", {}).get("added") or r.get("sent", {}).get("queued"), r.get("sent"))
    t0 = time.time()
    call(f"/api/agent/{sid}/stop", {})
    for _ in range(100):
        if call(f"/api/agent/{sid}?since=100000").get("state") == "idle":
            break
        time.sleep(0.2)
    check(f"[{hid}] stop within 5 s", time.time() - t0 < 5, round(time.time() - t0, 1))
    # interrupt while busy
    call(f"/api/agent/{sid}/send", {"text": "long task please"})
    time.sleep(6)
    t0 = time.time()
    call(f"/api/agent/{sid}/send", {"text": "what is the time", "interrupt": True})
    v = wait_idle(sid, limit=90)
    check(f"[{hid}] interrupt answers the new message", any("what is the time" in x for x in texts(v)), texts(v)[-2:])
    check(f"[{hid}] interrupt time under 20 s", time.time() - t0 < 20, round(time.time() - t0, 1))
    # model switch and plan
    if hid in ("meshpool-code", "pi", "hermes"):
        r = call(f"/api/agent/{sid}/model", {"model": mid})
        check(f"[{hid}] switch model", "_http" not in r, r)
    r = call(f"/api/agent/{sid}/plan", {"on": True})
    check(f"[{hid}] plan mode on", r.get("plan") is True, r)
    call(f"/api/agent/{sid}/plan", {"on": False})
    call(f"/api/threads/{sid}", {"action": "rename", "title": f"E2E {hid}"})
    call(f"/api/threads/{sid}", {"action": "pin"})
    lst = call("/api/agent").get("sessions", [])
    check(f"[{hid}] rename and pin", any(x["id"] == sid and x["title"] == f"E2E {hid}" and x.get("pinned") for x in lst))
    check(f"[{hid}] no errors in thread", not texts(v, "error"), texts(v, "error"))

# documents through agents
for hid in ("meshpool-code", "pi"):
    v = call("/api/agent", {"workdir": P, "harness": hid, "kind": "code", "mode": "auto"})
    if not v.get("id"):                    # e.g. Pi not installed: one failed check, not a crash of the whole run
        check(f"[{hid}] reads a PDF through the document reader", False, v)
        continue
    time.sleep(2)
    call(f"/api/agent/{v['id']}/send", {"text": "read the pdf"})
    v = wait_idle(v["id"])
    outs = [str(e.get("output")) for e in v.get("events", []) if e["type"] == "tool_end"]
    check(f"[{hid}] reads a PDF through the document reader", any("Page" in o or "page" in o for o in outs), outs[-2:])

# Meshpool Auto creates the routed thread
v = call("/api/agent", {"workdir": P, "harness": "meshpool-auto", "kind": "cowork", "task": "Build a revenue forecast in Excel"})
st = [e["text"] for e in v.get("events", []) if e["type"] == "status"] if "events" in v else []
vv = call(f"/api/agent/{v.get('id')}?since=0")
check("auto thread created with routed harness", vv.get("harness", {}).get("id") not in (None, "meshpool-auto")
      and any("Meshpool Auto picked" in (e.get("text") or "") for e in vv.get("events", [])), vv.get("harness"))

# A thread naming an agent an older version had (saved chats, scheduled tasks) opens in Meshpool Code
v = call("/api/agent", {"workdir": P, "harness": "claude-code", "kind": "code", "mode": "auto"})
vv = call(f"/api/agent/{v.get('id')}?since=0") if v.get("id") else v
check("an old agent id opens in Meshpool Code", vv.get("harness", {}).get("id") == "meshpool-code", vv.get("harness") or v)

# thread search, archive, delete
check("thread search", call("/api/search?q=hello").get("hits"), "")
r = call(f"/api/threads/{sid}", {"action": "archive"})
check("archive thread", r.get("ok"), r)
r = call(f"/api/threads/{sid}", {"action": "delete"})
check("delete thread", r.get("ok") and not any(x["id"] == sid for x in call("/api/agent").get("sessions", [])), r)

# ---------------------------------------------------------------- skills
r = call("/api/skills", {"action": "create", "name": "e2e-skill", "description": "Test skill", "body": "Do the test."})
check("skill create", any(x["id"] == "e2e-skill" for x in r.get("skills", [])), r)
r = call("/api/skills", {"action": "toggle", "id": "e2e-skill", "on": False})
check("skill toggle off", any(x["id"] == "e2e-skill" and not x["enabled"] for x in r.get("skills", [])), r)
r = call("/api/skills", {"action": "remove", "id": "e2e-skill"})
check("skill remove", not any(x["id"] == "e2e-skill" for x in r.get("skills", [])), r)

# ---------------------------------------------------------------- import from other apps (fake HOME)
f = call("/api/import")
names = [x["name"] for x in f.get("skills", [])] + [x["name"] for x in f.get("mcp", [])]
check("import finds Claude Code, Codex and Cursor skills", {"claude-skill", "codex-skill"} <= set(names), names)
check("import finds MCP servers from Claude Desktop, Cursor and Codex", {"desk-server", "cursor-server", "codex-server"} <= set(names), names)
keys = [x["key"] for x in f.get("skills", []) + f.get("mcp", [])]
r = call("/api/import", {"keys": keys})
check("import runs", len(r.get("skills", [])) >= 2 and len(r.get("mcp", [])) >= 3 and not r.get("errors"), r)
check("imported skills are listed", {"claude-skill", "codex-skill"} <= {x["id"] for x in call("/api/skills").get("skills", [])})
mc = call("/api/mcp")
check("imported MCP servers are listed", sum(1 for s in mc.get("servers", []) if s["name"] in ("desk-server", "cursor-server", "codex-server")) == 3, [s["name"] for s in mc.get("servers", [])])

# ---------------------------------------------------------------- MCP and connectors
r = call("/api/mcp", {"name": "context7", "enabled": False})
check("MCP toggle", "_http" not in r, r)
r = call("/api/mcp/test", {"workdir": P}, timeout=240)
st = {x["name"]: x["status"] for x in r.get("status", [])}
for n in ("meshpool-web", "meshpool-assistant", "meshpool-tools", "browser"):
    check(f"MCP server starts: {n}", st.get(n) == "connected", st)
cn = call("/api/connectors").get("connectors", [])
check("connector catalog", len(cn) >= 20, len(cn))
r = call("/api/connectors", {"action": "custom", "name": "Test token server", "url": "https://example.com/mcp", "token": "abc123"})
cid = r.get("id")
check("custom connector with token", cid and any(x["id"] == cid and x["connected"] for x in r.get("connectors", [])), r)
r = call("/api/connectors", {"action": "enable", "id": cid, "on": False})
check("connector disable", any(x["id"] == cid and not x["enabled"] for x in r.get("connectors", [])), r)
r = call("/api/connectors", {"action": "disconnect", "id": cid})
check("custom connector removed", not any(x["id"] == cid for x in r.get("connectors", [])), r)
for c in ("notion", "apollo", "clickup"):
    r = call("/api/connectors", {"action": "connect", "id": c}, timeout=60)
    check(f"OAuth sign-in starts: {c}", str(r.get("url", "")).startswith("https://"), r)
r = call("/api/connectors", {"action": "token", "id": "instantly", "token": "test-not-real"})
check("token connector saves", any(x["id"] == "instantly" and x["connected"] for x in r.get("connectors", [])), r)
call("/api/connectors", {"action": "disconnect", "id": "instantly"})

# ---------------------------------------------------------------- assistant: instructions, memory, schedules
r = call("/api/assistant", {"action": "instructions", "text": "Be brief. No em dashes."})
check("instructions save", r.get("instructions", "").startswith("Be brief"), r)
r = call("/api/assistant", {"action": "memory", "text": "- The user prefers tables"})
check("memory save", "tables" in r.get("memory", ""), r)
r = call("/api/assistant", {"action": "task_add", "name": "Daily", "prompt": "Reply with the single word ok", "when": "daily", "time": "07:15"})
tid = next((t["id"] for t in r.get("tasks", []) if t["name"] == "Daily"), None)
check("scheduled task add", tid and "07:15" in json.dumps(r), r)
r = call("/api/assistant", {"action": "task_update", "id": tid, "enabled": False})
check("scheduled task pause", any(t["id"] == tid and not t["enabled"] for t in r.get("tasks", [])), r)
r = call("/api/assistant", {"action": "task_run", "id": tid})
check("scheduled task run now", r.get("thread"), r)
vv = wait_idle(r.get("thread"), limit=90)
check("scheduled run replies", any(texts(vv)), vv.get("events", [])[-2:])
r = call("/api/assistant", {"action": "task_delete", "id": tid})
check("scheduled task delete", not any(t["id"] == tid for t in r.get("tasks", [])), r)
bad = call("/api/assistant", {"action": "task_add", "prompt": "x", "when": "yearly"})
check("bad schedule is refused with a message", bad.get("_http") and bad.get("error"), bad)

# ---------------------------------------------------------------- web
r = call("/api/web-test", {}, timeout=60)
check("web search answers (ok or a clear message)", r.get("ok") or r.get("error"), r)

n_ok = sum(1 for _, ok in RESULTS if ok)
print(f"\n{n_ok}/{len(RESULTS)} passed")
for name, ok in RESULTS:
    if not ok:
        print("  failed:", name)
sys.exit(0 if n_ok == len(RESULTS) else 1)
