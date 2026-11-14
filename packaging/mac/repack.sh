#!/bin/bash
# Refresh the Meshpool app code inside existing Mac app trees, then recompile, sign and build the dmgs.
# Usage: packaging/mac/repack.sh [WORK]
#   WORK holds app-mac-arm64/Meshpool.app and app-mac-x64/Meshpool.app (from build_all.py and build_mac.sh).
#   Output: WORK/out/Meshpool.dmg (Apple silicon) and WORK/out/Meshpool-Intel.dmg.
# Run it on a Mac, from anywhere.
#
# Signing: with the Apple Developer secrets set (see packaging/mac/sign.sh) the apps are signed with the Developer ID
# certificate and the dmgs are notarized and stapled, so they open with no Gatekeeper warning. Without them the apps
# are ad hoc signed, as before, and the build still works.
set -u
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
W="${1:-$HOME/Meshpool-Build/v4/work}"; OUT="$W/out"
rm -rf "${OUT:?}"; mkdir -p "$OUT"
PY="$W/app-mac-arm64/Meshpool.app/Contents/Resources/python/bin/python3"
# The version comes from one place, app/meshpool_harness/__init__.py; Info.plist holds placeholders for it.
VER="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$REPO/app/meshpool_harness/__init__.py")"
NUM="$(printf '%s' "$VER" | grep -oE '^[0-9]+\.[0-9]+\.[0-9]+')"
[ -n "$NUM" ] || { echo "Could not read the version from app/meshpool_harness/__init__.py"; exit 1; }
for a in arm64 x64; do
  APP="$W/app-mac-$a/Meshpool.app"; R="$APP/Contents/Resources"
  for d in meshpool_harness engines profiles defaults; do
    rm -rf "${R:?}/app/$d"; rsync -a --exclude __pycache__ --exclude '*.pyc' "$REPO/app/$d/" "$R/app/$d/"
  done
  cp "$REPO/THIRD_PARTY_NOTICES.md" "$REPO/app/requirements.txt" "$REPO/packaging/requirements-cli.txt" "$R/app/"
  cp "$REPO/packaging/hermes-NOTICE-MESHPOOL.txt" "$R/app/vendor/hermes/NOTICE-MESHPOOL.txt"
  (cd "$R/app/vendor/hermes" && patch -p1 -N -s < "$REPO/packaging/hermes-acp-server.patch" >/dev/null 2>&1 || true)
  sed -e "s/@VERSION_NUM@/$NUM/g" -e "s/@VERSION@/$VER/g" "$REPO/packaging/mac/Info.plist" > "$APP/Contents/Info.plist"
  for d in meshpool_harness engines profiles vendor/hermes/acp_adapter; do
    PYTHONPATH= "$PY" -m compileall -q -j0 --invalidation-mode unchecked-hash -x 'tests?/|test_' \
      -s "$R" -p /Applications/Meshpool.app/Contents/Resources "$R/app/$d" >/dev/null 2>&1
  done
  xattr -cr "$APP"
done
# Sign the app trees before the dmgs are built, so the copy inside each image is already signed. sign.sh does
# nothing (and exits 0) when the Apple Developer secrets are not set.
bash "$REPO/packaging/mac/sign.sh" sign "$W"
for a in arm64 x64; do
  APP="$W/app-mac-$a/Meshpool.app"
  # Without the Developer ID secrets sign.sh did nothing, so the app is still ad hoc signed here, as before.
  if [ -z "${MAC_SIGN_IDENTITY:-}" ] || [ -z "${MAC_CERT_P12_BASE64:-}" ]; then
    codesign --force --deep --sign - "$APP" 2>&1 | grep -v "replacing existing" | tail -2
  fi
  codesign --verify --deep --strict "$APP" && echo "signature ok ($a) $(/usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' "$APP/Contents/Info.plist")"
  ST="$W/stage-$a"; rm -rf "$ST"; mkdir -p "$ST"; ditto "$APP" "$ST/Meshpool.app"; ln -s /Applications "$ST/Applications"
  NAME=Meshpool; [ $a = x64 ] && NAME=Meshpool-Intel
  rm -f "$W/$NAME-udzo.dmg"
  # hdiutil sometimes fails with "Resource busy" on a loaded machine; try again before giving up
  ok=; for try in 1 2 3 4; do
    hdiutil create -quiet -volname Meshpool -srcfolder "$ST" -ov -format UDZO "$W/$NAME-udzo.dmg" && { ok=1; break; }
    echo "hdiutil create failed (attempt $try), retrying"; sleep $((try * 10))
  done
  [ -n "$ok" ] || exit 1
  hdiutil convert -quiet "$W/$NAME-udzo.dmg" -format ULMO -o "$OUT/$NAME.dmg" && rm -f "$W/$NAME-udzo.dmg" && echo "built $NAME.dmg"
done
# Notarize, staple and check both disk images. sign.sh does nothing (and exits 0) when the Apple Developer secrets
# are not set, so this is safe before the owner's accounts are ready.
bash "$REPO/packaging/mac/sign.sh" notarize "$W"
ls -la "$OUT"
