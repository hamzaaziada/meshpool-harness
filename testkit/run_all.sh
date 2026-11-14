#!/bin/bash
# Run every consumer-journey suite against local apps and the mock model (no paid calls). See testkit/JOURNEYS.md.
# Usage: testkit/run_all.sh QA_DIR PYTHON
# QA_DIR holds fakehome/ (importer fixtures), proj-src/ (file fixtures) and is where results go.
set -u
Q="$1"; PY="$2"; R="$(cd "$(dirname "$0")/.." && pwd)"; OUT="$Q/run-$(date +%H%M%S)"; mkdir -p "$OUT"
pkill -f "mock_llm.py 8911"; pkill -f "meshpool_harness ui --port 879"; sleep 2
(cd "$R" && nohup python3 testkit/mock_llm.py 8911 "$OUT/mock.jsonl" > "$OUT/mock.log" 2>&1 &)
start() {  # port home extra-env...
  local port="$1" home="$2"; shift 2
  rm -rf "$home"; mkdir -p "$home"
  printf 'MESHPOOL_BASE_URL=http://127.0.0.1:8911/v1\nMESHPOOL_API_KEY=test-key-1234\n' > "$home/meshpool.config"
  [ "$port" = 8798 ] && rm -f "$home/meshpool.config"            # fresh install: no key yet (onboarding)
  # Strip every credential from the environment (any provider or cloud account the tester has): a subscription
  # harness (Codex) would otherwise sign in with the tester's own account and make real, paid calls.
  local unset=(); for v in $(env | cut -d= -f1 | grep -E '^(CLAUDE_|ANTHROPIC_|OPENAI_|CODEX_|AWS_|GH_|GITHUB_|GOOGLE_|AZURE_|GEMINI_)'); do unset+=(-u "$v"); done
  (cd "$R/app" && env "${unset[@]}" HOME="$Q/fakehome" MESHPOOL_HOME="$home" MH_BROWSER_HEADLESS=1 "$@" \
     nohup "$PY" -m meshpool_harness ui --port "$port" --no-browser > "$OUT/app-$port.log" 2>&1 &)
}
start 8799 "$OUT/home-main"; start 8798 "$OUT/home-fresh"; start 8797 "$OUT/home-code" MH_OBSERVE=1
sleep 12
rm -rf "$OUT/proj"; cp -r "$Q/proj-src" "$OUT/proj"
for d in /tmp/proj1 /tmp/proj2; do rm -rf "$d"; mkdir -p "$d"; printf 'def add(a, b):\n    return a + b\n' > "$d/calc.py"
  (cd "$d" && git init -q && git add -A && git -c user.email=t@t -c user.name=t commit -qm init); done
cd "$R/testkit"
"$PY" e2e_full.py "$OUT/proj" "$Q/fakehome" > "$OUT/e2e_full.txt" 2>&1
"$PY" journeys.py "$OUT/proj" 8797 > "$OUT/journeys.txt" 2>&1
"$PY" - <<PYEOF
import json, re, urllib.request
B = "http://127.0.0.1:8799"; tok = re.search(r'token: "([^"]+)"', urllib.request.urlopen(B + "/").read().decode()).group(1)
for p in ("/tmp/proj1", "/tmp/proj2"):
    urllib.request.urlopen(urllib.request.Request(B + "/api/projects", data=json.dumps({"action": "add", "path": p}).encode(),
                           headers={"X-Meshpool-Token": tok, "Content-Type": "application/json"}))
PYEOF
mkdir -p "$OUT/ui"
node ui_crawl.js "$OUT/ui" 8798 8799 > "$OUT/ui_crawl.txt" 2>&1
node about.js "$OUT/ui/about.png" > "$OUT/about.txt" 2>&1
for f in intr modes v24; do mkdir -p "$OUT/ui/$f"; node $f.js "$OUT/ui/$f" > "$OUT/$f.txt" 2>&1; done
node midrun.js 8799 /tmp/proj1 > "$OUT/midrun.txt" 2>&1
mkdir -p "$OUT/ui/crawl"; node crawl_all.js "$OUT/ui/crawl" 8799 /tmp/proj2 > "$OUT/crawl_all.txt" 2>&1
for i in 1 2 3 4 5 6; do mkdir -p "$OUT/jp$i"; done
"$PY" stop_check.py /tmp/proj1 > "$OUT/stop_check.txt" 2>&1
node jitter.js 8799 /tmp/proj2 "$OUT"/jp{1..6} > "$OUT/jitter.txt" 2>&1
bash update_flow.sh "$OUT/update" "$PY" > "$OUT/update_flow.txt" 2>&1
echo "== summary ($OUT)"
for f in e2e_full journeys ui_crawl about intr modes v24 midrun crawl_all stop_check jitter update_flow; do
  printf '%-10s ' "$f"; (grep -E "^[0-9]+/[0-9]+ passed|no page errors|^FAIL" "$OUT/$f.txt" | tail -1) || echo "?"; done
