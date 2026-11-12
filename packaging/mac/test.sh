#!/bin/bash
# Test the built Meshpool.dmg on this Mac: mount, copy the app out, run its server against a local mock model,
# and drive Meshpool Code, Pi and Hermes threads through the API.
set -u
V4="${MESHPOOL_WORK:-$HOME/Meshpool-Build/v4}"; T="$V4/test"; rm -rf "$T"; mkdir -p "$T/proj" "$T/home"
MNT=$(hdiutil attach -nobrowse -readonly "${DMG:-$V4/work/out/Meshpool.dmg}" | awk '/Volumes/{print substr($0, index($0,"/Volumes"))}')
ditto "$MNT/Meshpool.app" "$T/Meshpool.app"; hdiutil detach -quiet "$MNT"
codesign --verify --deep --strict "$T/Meshpool.app" && echo "signature ok"
APP="$T/Meshpool.app/Contents"; PY="$APP/Resources/python/bin/python3"
"$PY" "${MOCK:-$(dirname "$0")/../../testkit/mock_llm.py}" 8912 "$T/mock.jsonl" > "$T/mock.log" 2>&1 &
MOCK=$!
printf 'MESHPOOL_API_KEY=test-key-1234\nMESHPOOL_BASE_URL=http://127.0.0.1:8912/v1\n' > "$T/home/meshpool.config"
MESHPOOL_HOME="$T/home" PYTHONPATH="$APP/Resources/app" "$APP/MacOS/Meshpool" -m meshpool_harness ui --no-browser --port 8791 > "$T/ui.log" 2>&1 &
UI=$!
for i in $(seq 1 60); do curl -s -o /dev/null http://127.0.0.1:8791/healthz && break; sleep 1; done
TOK=$(curl -s http://127.0.0.1:8791/ | sed -n 's/.*token: "\([^"]*\)".*/\1/p')
H="X-Meshpool-Token: $TOK"; J="Content-Type: application/json"
echo "harnesses: $(curl -s -H "$H" http://127.0.0.1:8791/api/harnesses | "$PY" -c 'import json,sys;print([(h["id"],h["available"]) for h in json.load(sys.stdin)["harnesses"]])')"
echo "models: $(curl -s -H "$H" http://127.0.0.1:8791/api/models | "$PY" -c 'import json,sys;d=json.load(sys.stdin);print(len(d["models"]), d.get("error"))')"
echo "connectors: $(curl -s -H "$H" http://127.0.0.1:8791/api/connectors | "$PY" -c 'import json,sys;print([c["id"] for c in json.load(sys.stdin)["connectors"]])')"
run() {  # harness model
  rm -f "$T/proj/hello.py"
  ID=$(curl -s -H "$H" -H "$J" -d "{\"workdir\":\"$T/proj\",\"harness\":\"$1\",\"model\":\"$2\",\"mode\":\"auto\"}" http://127.0.0.1:8791/api/agent | "$PY" -c 'import json,sys;d=json.load(sys.stdin);print(d.get("id") or d)')
  sleep 2
  curl -s -H "$H" -H "$J" -d '{"text":"Write hello and run it"}' http://127.0.0.1:8791/api/agent/$ID/send > /dev/null
  for i in $(seq 1 120); do
    st=$(curl -s -H "$H" "http://127.0.0.1:8791/api/agent/$ID?since=100000" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["state"])')
    [ "$st" = idle ] && [ $i -gt 2 ] && break; sleep 1
  done
  curl -s -H "$H" "http://127.0.0.1:8791/api/agent/$ID?since=0" | "$PY" -c '
import json,sys
v=json.load(sys.stdin); ev=v["events"]
tools=[(e["name"],e.get("ok")) for e in ev if e["type"]=="tool_end"]
txt=[e["text"][:70] for e in ev if e["type"]=="assistant" and e.get("text")]
err=[e["text"][:200] for e in ev if e["type"]=="error"]
print("  model:",v["model"],"| tools:",tools,"| reply:",txt[-1:] ,"| errors:",err)'
  echo "  hello.py: $(cat "$T/proj/hello.py" 2>/dev/null || echo missing)"
}
cp "$V4/report.pdf" "$T/proj/report.pdf"; cp "$V4/model.pbix" "$T/proj/model.pbix"
docs() {  # harness: read a PDF and a Power BI file
  ID=$(curl -s -H "$H" -H "$J" -d "{\"workdir\":\"$T/proj\",\"harness\":\"$1\",\"mode\":\"auto\"}" http://127.0.0.1:8791/api/agent | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["id"])')
  sleep 3; curl -s -H "$H" -H "$J" -d '{"text":"read the pdf"}' http://127.0.0.1:8791/api/agent/$ID/send > /dev/null
  for i in $(seq 1 90); do st=$(curl -s -H "$H" "http://127.0.0.1:8791/api/agent/$ID?since=100000" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["state"])'); [ "$st" = idle ] && [ $i -gt 2 ] && break; sleep 1; done
  curl -s -H "$H" "http://127.0.0.1:8791/api/agent/$ID?since=0" | "$PY" -c '
import json,sys
for e in json.load(sys.stdin)["events"]:
    if e["type"]=="tool_end": print("  ", e["name"], "->", str(e.get("output"))[:110].replace(chr(10)," "))'
}
cp "$V4/scan.pdf" "$T/proj/scan.pdf"; cp "$V4/ru.png" "$T/proj/ru.png"
echo "OCR (scanned PDF, Russian image) in the app's Python:"
PYTHONPATH="$APP/Resources/app" MESHPOOL_HOME="$T/home" "$PY" -c "
import time; from meshpool_harness import docread as D
for f in ('scan.pdf', 'ru.png'):
    t = time.time(); x = D.extract('$T/proj/' + f); print('  ', f, round(time.time() - t, 1), 's |', x.replace(chr(10), ' ')[-120:])" 2>&1 | grep -v INFO
echo "Git and CI (public GitHub repo, no login):"
rm -rf "$T/ci" && git clone -q --depth 2 https://github.com/psf/requests.git "$T/ci" 2>/dev/null
PYTHONPATH="$APP/Resources/app" MESHPOOL_HOME="$T/home" GITHUB_TOKEN= "$PY" -c "
from meshpool_harness.ui import workbench as W
W.add_project('$T/ci'); c = W.git_ci('$T/ci')
print('   ', c.get('slug'), c.get('branch'), 'summary:', c.get('summary'), '| checks:', len(c.get('checks', [])), '| runs:', len(c.get('runs', [])), '| deployments:', len(c.get('deployments', [])), '|', c.get('error', '')[:120])"
echo "Cowork thread kind:"
CID=$(curl -s -H "$H" -H "$J" -d "{\"workdir\":\"$T/proj\",\"harness\":\"pi\",\"kind\":\"cowork\"}" http://127.0.0.1:8791/api/agent | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["id"])')
curl -s -H "$H" http://127.0.0.1:8791/api/agent | "$PY" -c "import json,sys;print('   ', [s['kind'] for s in json.load(sys.stdin)['sessions'] if s['id']=='$CID'])"
echo "Documents, Meshpool Code:"; docs meshpool-code
echo "Documents, Pi:"; docs pi
echo "Meshpool Code:"; run meshpool-code anthropic/claude-sonnet-4.6
echo "Pi:"; run pi openai/gpt-5.1
echo "Hermes:"; run hermes google/gemini-3-pro

echo "Shared tools each harness sees (from the mock's request log):"
"$PY" -c "
import json
seen = {}
for l in open('$T/mock.jsonl'):
    d = json.loads(l); t = d.get('tool_names') or []
    if t: seen.setdefault(len(t), [x for x in t if 'web' in x or 'research' in x or 'memory' in x or 'subtask' in x or 'schedule' in x])
for k, v in seen.items(): print('   ', k, 'tools:', v[:8])
print('    context and memory reached the model:', any(json.loads(l).get('has_ctx') for l in open('$T/mock.jsonl')))"
echo "Web search, fetch and research on this Mac (no browser):"
PYTHONPATH="$APP/Resources/app" MESHPOOL_HOME="$T/home" "$PY" -c "
import time; from meshpool_harness import web as W
t = time.time(); r = W.search('Power BI pbix file format python', 6); print('   search', len(r), 'results in', round(time.time() - t, 1), 's:', [x['url'][:45] for x in r[:3]])
t = time.time(); p = W.fetch('https://en.wikipedia.org/wiki/Large_language_model'); print('   fetch', len(p['text']), 'chars in', round(time.time() - t, 1), 's:', p['title'])
t = time.time(); x = W.research('What does OpenRouter charge on top of model prices?', ['OpenRouter fees pricing', 'OpenRouter platform fee credits'], 4); print('   research', len(x), 'chars in', round(time.time() - t, 1), 's |', x.split(chr(10))[4][:100])"
echo "Stop and add-on while working:"
stopt() {  # harness
  ID=$(curl -s -H "$H" -H "$J" -d "{\"workdir\":\"$T/proj\",\"harness\":\"$1\",\"mode\":\"auto\"}" http://127.0.0.1:8791/api/agent | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["id"])')
  sleep 3; curl -s -H "$H" -H "$J" -d '{"text":"long task please"}' http://127.0.0.1:8791/api/agent/$ID/send > /dev/null; sleep 5
  ADD=$(curl -s -H "$H" -H "$J" -d '{"text":"also mention the color blue"}' http://127.0.0.1:8791/api/agent/$ID/send)
  sleep 1; t0=$("$PY" -c 'import time;print(time.time())'); curl -s -H "$H" -X POST http://127.0.0.1:8791/api/agent/$ID/stop > /dev/null
  for i in $(seq 1 100); do st=$(curl -s -H "$H" "http://127.0.0.1:8791/api/agent/$ID?since=100000" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["state"])'); [ "$st" = idle ] && break; sleep 0.2; done
  echo "   $1: add-on $ADD | stop -> $st in $("$PY" -c "import time;print(round(time.time()-$t0,1))") s | sleeps left: $(pgrep -f 'sleep 25' | wc -l | tr -d ' ')"
}
stopt meshpool-code; stopt pi; stopt hermes
echo "Scheduled task (runs a minute from now):"
AT=$("$PY" -c 'import datetime;print((datetime.datetime.now()+datetime.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M"))')
curl -s -H "$H" -H "$J" -d "{\"action\":\"task_add\",\"name\":\"Mac test\",\"prompt\":\"Reply with the single word ok\",\"when\":\"once\",\"at\":\"$AT\",\"folder\":\"$T/proj\"}" http://127.0.0.1:8791/api/assistant > /dev/null
sleep 95; curl -s -H "$H" http://127.0.0.1:8791/api/agent | "$PY" -c "import json,sys;print('   ', [ (s['title'], s['state']) for s in json.load(sys.stdin)['sessions'] if s['title'].startswith('Mac test')])"
echo "models the mock saw: $(cut -d'"' -f4 "$T/mock.jsonl" | sort | uniq -c | tr '\n' ' ')"
kill $UI $MOCK 2>/dev/null; sleep 1; pkill -f "$T/Meshpool.app" 2>/dev/null
tail -3 "$T/home/runs/harness-logs/pi.log" 2>/dev/null
