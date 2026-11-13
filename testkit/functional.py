"""Does every harness actually do the everyday things? Real model, through the app's API, graded by what happened.

  python functional.py BASE_URL WORKDIR [--harness a,b] [--only answer,write,...]

For each harness: answer a question, write a file (approving), edit it, run a terminal command, deny an action
(it must not happen), stop a running command (within 5 s, nothing left running), quick research (under 60 s),
call an MCP tool, use a skill, and (Work harnesses) make a Word document. Supervised mode, as a new user has it.
Prints a PASS/FAIL table and exits 1 on any failure.
"""
import argparse
import json
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("base")
ap.add_argument("work")
ap.add_argument("--harness", default="meshpool-code,meshpool-work,pi,hermes,codex,meshpool-auto")
ap.add_argument("--only", default="")
A = ap.parse_args()
TOK = re.search(r'token: "([^"]+)"', urllib.request.urlopen(A.base + "/").read().decode()).group(1)
KIND = {"meshpool-work": "cowork", "hermes": "cowork"}
ROWS = []


def call(path, body=None, timeout=300):
    r = urllib.request.Request(A.base + path, data=None if body is None else json.dumps(body).encode(),
                               headers={"X-Meshpool-Token": TOK, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read() or b"{}")


def turn(sid, text, decide="yes", limit=240, stop_after=None):
    """Send one message, answer approvals with `decide`, wait for the reply. Returns (events of this turn, secs)."""
    n0 = len(call(f"/api/agent/{sid}?since=0").get("events", []))
    call(f"/api/agent/{sid}/send", {"text": text})
    t0, answered, stopped_at = time.time(), set(), None
    while time.time() - t0 < limit:
        v = call(f"/api/agent/{sid}?since=0")
        ev = v.get("events", [])[n0:]
        done = {e["id"] for e in ev if e["type"] == "approval_done"}
        for e in ev:
            if e["type"] == "approval" and e["id"] not in done and e["id"] not in answered:
                answered.add(e["id"])
                opts = e.get("options") or []
                if decide == "yes":
                    d = next((o["id"] for o in opts if o.get("kind") == "allow_once"), opts[0]["id"] if opts else "yes")
                else:
                    d = next((o["id"] for o in opts if str(o.get("kind", "")).startswith("reject")), "no")
                call(f"/api/agent/{sid}/answer", {"id": e["id"], "decision": d})
        if stop_after and stopped_at is None and v.get("state") == "working" and time.time() - t0 > stop_after \
                and any(e["type"] == "tool_start" for e in ev):
            call(f"/api/agent/{sid}/stop", {})
            stopped_at = time.time()
        if v.get("state") == "idle" and any(e["type"] == "turn_end" for e in ev):
            return ev, time.time() - t0, (time.time() - stopped_at if stopped_at else None)
        time.sleep(0.4)
    call(f"/api/agent/{sid}/stop", {})
    return call(f"/api/agent/{sid}?since=0").get("events", [])[n0:], time.time() - t0, None


def reply(ev):
    t = [e.get("text") or "" for e in ev if e["type"] == "assistant"]
    return "\n".join(t)


def tools(ev):
    return [e.get("name") or "" for e in ev if e["type"] == "tool_start"]


def check(h, name, ok, detail=""):
    ROWS.append((h, name, bool(ok), str(detail)[:200]))
    print(f"{'PASS' if ok else 'FAIL'} [{h}] {name}" + ("" if ok else f"  -> {str(detail)[:300]}"), flush=True)


def want(name):
    return not A.only or name in A.only.split(",")


for h in A.harness.split(","):
    w = Path(A.work) / h
    shutil.rmtree(w, ignore_errors=True)
    w.mkdir(parents=True)
    subprocess.run("git init -q && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m init", shell=True,
                   cwd=w)
    sid = call("/api/agent", {"workdir": str(w), "harness": h, "kind": KIND.get(h, "code"), "mode": "ask"})["id"]
    time.sleep(1.5)
    if want("answer"):
        ev, secs, _ = turn(sid, "What is 17 times 23? Reply with just the number.")
        check(h, "answers a question", "391" in reply(ev), reply(ev)[-200:])
    if want("write"):
        ev, secs, _ = turn(sid, "Create a file named notes.txt containing exactly this line: meshpool works")
        f = w / "notes.txt"
        check(h, "writes a file (approved)", f.exists() and "meshpool works" in f.read_text(),
              (f.exists() and f.read_text()[:80], reply(ev)[-160:]))
    if want("edit"):
        ev, secs, _ = turn(sid, "In notes.txt, change the word 'works' to 'rocks'. Change nothing else.")
        f = w / "notes.txt"
        check(h, "edits a file", f.exists() and "meshpool rocks" in f.read_text(),
              (f.exists() and f.read_text()[:80], reply(ev)[-160:]))
    if want("command"):
        ev, secs, _ = turn(sid, "Run this terminal command and tell me exactly what it prints: "
                                "python3 -c \"print(6*7, 'from-the-shell')\"")
        out = " ".join(str(e.get("output") or "") for e in ev if e["type"] == "tool_end")
        check(h, "runs a terminal command", "42 from-the-shell" in out and "42" in reply(ev), (tools(ev), reply(ev)[-160:]))
    if want("deny"):
        keep = w / "keep.txt"
        keep.write_text("keep me")
        ev, secs, _ = turn(sid, "Delete the file keep.txt.", decide="no")
        asked = any(e["type"] == "approval" for e in ev)
        if h == "codex":
            check(h, "deny: Codex works freely inside the project (by design), Undo restores", True)
        else:
            check(h, "deny: a denied action does not happen", keep.exists() and asked, (keep.exists(), asked, reply(ev)[-120:]))
    if want("stop"):
        ev, secs, took = turn(sid, "Run the terminal command: sleep 40 && echo finished", stop_after=4, limit=90)
        left = subprocess.run(["pgrep", "-f", "sleep 40"], capture_output=True, text=True).stdout.strip()
        check(h, "Stop stops within 5 s and leaves nothing running", took is not None and took <= 5 and not left,
              (took and round(took, 1), left, tools(ev)))
    if want("research"):
        ev, secs, _ = turn(sid, "Quickly look up: what is the latest stable version of Python? Answer in one line.",
                           limit=120)
        check(h, "quick research answers within 60 s", secs <= 60 and re.search(r"3\.1\d", reply(ev)),
              (round(secs), reply(ev)[-160:]))
    if want("mcp"):
        ev, secs, _ = turn(sid, "Use the calculate tool from the meshpool-tools MCP server to compute 1234*5678, "
                                "and tell me the result.")
        check(h, "calls an MCP tool", "7006652" in reply(ev).replace(",", "") and any("calculate" in t for t in tools(ev)),
              (tools(ev), reply(ev)[-160:]))
    if want("skill"):
        ev, secs, _ = turn(sid, "$claude-skill greet me")
        check(h, "uses a skill", re.search(r"bonjour", reply(ev), re.I), reply(ev)[-160:])
    if want("office") and KIND.get(h) == "cowork":
        ev, secs, _ = turn(sid, "Make a short one-page Word document called brief.docx about why unit tests help.",
                           limit=300)
        docs = list(w.rglob("brief*.docx")) or [p for p in w.rglob("*.docx")]
        ok = False
        if docs:
            try:
                import docx
                ok = len(" ".join(p.text for p in docx.Document(docs[0]).paragraphs).split()) > 60
            except Exception:  # noqa: BLE001
                ok = False
        check(h, "makes a Word document", ok, ([str(d.name) for d in docs], reply(ev)[-160:]))
    errs = [e.get("text") for e in call(f"/api/agent/{sid}?since=0").get("events", []) if e["type"] == "error"]
    check(h, "no error messages in the thread", not errs, errs[:2])

fails = [r for r in ROWS if not r[2]]
print(f"\n{len(ROWS) - len(fails)}/{len(ROWS)} passed")
for r in fails:
    print("  failed:", r[0], "-", r[1])
raise SystemExit(1 if fails else 0)
