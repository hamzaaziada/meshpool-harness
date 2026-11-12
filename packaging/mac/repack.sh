#!/bin/bash
# Refresh the Meshpool app code inside existing Mac app trees, then recompile, ad hoc sign and build the dmgs.
# Usage: packaging/mac/repack.sh [WORK]
#   WORK holds app-mac-arm64/Meshpool.app and app-mac-x64/Meshpool.app (from build_all.py and build_mac.sh).
#   Output: WORK/out/Meshpool.dmg (Apple silicon) and WORK/out/Meshpool-Intel.dmg.
# Run it on a Mac, from anywhere.
set -u
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
W="${1:-$HOME/Meshpool-Build/v4/work}"; OUT="$W/out"
rm -rf "${OUT:?}"; mkdir -p "$OUT"
PY="$W/app-mac-arm64/Meshpool.app/Contents/Resources/python/bin/python3"
for a in arm64 x64; do
  APP="$W/app-mac-$a/Meshpool.app"; R="$APP/Contents/Resources"
  for d in meshpool_harness engines profiles memory defaults; do
    rm -rf "${R:?}/app/$d"; rsync -a --exclude __pycache__ --exclude '*.pyc' "$REPO/app/$d/" "$R/app/$d/"
  done
  cp "$REPO/LICENSE" "$REPO/NOTICE" "$REPO/THIRD_PARTY_NOTICES.md" "$REPO/app/requirements.txt" "$R/app/"
  cp "$REPO/packaging/hermes-NOTICE-MESHPOOL.txt" "$R/app/vendor/hermes/NOTICE-MESHPOOL.txt"
  (cd "$R/app/vendor/hermes" && patch -p1 -N -s < "$REPO/packaging/hermes-acp-server.patch" >/dev/null 2>&1 || true)
  cp "$REPO/packaging/mac/Info.plist" "$APP/Contents/Info.plist"
  for d in meshpool_harness engines profiles memory vendor/hermes/acp_adapter; do
    PYTHONPATH= "$PY" -m compileall -q -j0 --invalidation-mode unchecked-hash -x 'tests?/|test_' \
      -s "$R" -p /Applications/Meshpool.app/Contents/Resources "$R/app/$d" >/dev/null 2>&1
  done
  xattr -cr "$APP"
  codesign --force --deep --sign - "$APP" 2>&1 | grep -v "replacing existing" | tail -2
  codesign --verify --deep --strict "$APP" && echo "signature ok ($a) $(/usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' "$APP/Contents/Info.plist")"
  ST="$W/stage-$a"; rm -rf "$ST"; mkdir -p "$ST"; ditto "$APP" "$ST/Meshpool.app"; ln -s /Applications "$ST/Applications"
  NAME=Meshpool; [ $a = x64 ] && NAME=Meshpool-Intel
  rm -f "$W/$NAME-udzo.dmg"
  hdiutil create -quiet -volname Meshpool -srcfolder "$ST" -ov -format UDZO "$W/$NAME-udzo.dmg" || exit 1
  hdiutil convert -quiet "$W/$NAME-udzo.dmg" -format ULMO -o "$OUT/$NAME.dmg" && rm -f "$W/$NAME-udzo.dmg" && echo "built $NAME.dmg"
done
ls -la "$OUT"
