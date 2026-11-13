#!/bin/bash
# The in-app update, end to end through the real UI: an older install sees the update bar, updates from a release,
# restarts, and comes back on the new version. A local server stands in for GitHub. Usage: update_flow.sh WORKDIR PYTHON
set -u
W="$1"; PY="$2"; R="$(cd "$(dirname "$0")/.." && pwd)"
rm -rf "$W"; mkdir -p "$W/inst" "$W/rel/meshpool-harness-0.2.0/app" "$W/srv" "$W/home"
for d in meshpool_harness engines profiles defaults; do cp -r "$R/app/$d" "$W/inst/"; cp -r "$R/app/$d" "$W/rel/meshpool-harness-0.2.0/app/"; done
cp "$R/app/requirements.txt" "$W/inst/"; cp "$R/app/requirements.txt" "$W/rel/meshpool-harness-0.2.0/app/"
sed -i 's/__version__ = "[^"]*"/__version__ = "0.1.0"/' "$W/inst/meshpool_harness/__init__.py"
sed -i 's/__version__ = "[^"]*"/__version__ = "0.2.0"/' "$W/rel/meshpool-harness-0.2.0/app/meshpool_harness/__init__.py"
tar czf "$W/srv/src.tgz" -C "$W/rel" meshpool-harness-0.2.0
PORT=8781
printf '{"tag_name":"v0.2.0","body":"Test release","html_url":"http://127.0.0.1:%s/","assets":[]}' $PORT > "$W/srv/latest.json"
(cd "$W/srv" && nohup "$PY" -m http.server $PORT --bind 127.0.0.1 > "$W/srv.log" 2>&1 &)
printf 'MESHPOOL_BASE_URL=http://127.0.0.1:8911/v1\nMESHPOOL_API_KEY=test-key-1234\n' > "$W/home/meshpool.config"
pkill -f "ui --port 8782" ; sleep 1
(cd "$W" && MESHPOOL_HOME="$W/home" MESHPOOL_UPDATE_API="http://127.0.0.1:$PORT/latest.json" \
  MESHPOOL_UPDATE_SRC="http://127.0.0.1:$PORT/src.tgz" PYTHONPATH="$W/inst" nohup "$PY" -m meshpool_harness ui --port 8782 --no-browser > "$W/app.log" 2>&1 &)
sleep 8
node "$R/testkit/update_flow.js" 8782 "$W"
rc=$?
pkill -f "ui --port 8782"; pkill -f "http.server $PORT"
exit $rc
