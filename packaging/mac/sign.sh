#!/bin/bash
# Sign, notarize and staple the Meshpool Mac apps, so a fresh download opens with no Gatekeeper warning.
#
# Usage: packaging/mac/sign.sh sign     WORK   sign the app trees in WORK (before the dmgs are built)
#        packaging/mac/sign.sh notarize WORK   notarize, staple and check the dmgs in WORK/out (after they are built)
#        packaging/mac/sign.sh all      WORK   both, in order (repack.sh calls the two phases itself)
#
# Every step needs the owner's Apple Developer account, passed in as environment variables:
#   MAC_CERT_P12_BASE64  the Developer ID Application certificate, base64 of the .p12
#   MAC_CERT_PASSWORD    the .p12 password
#   MAC_SIGN_IDENTITY    e.g. "Developer ID Application: Your Name (TEAMID)"
#   APPLE_ID             the Apple ID email
#   APPLE_APP_PASSWORD   an app-specific password for notarization
#   APPLE_TEAM_ID        the Apple Developer team id
# With any of them missing the script does nothing and exits 0, so the release build keeps working before the
# accounts are ready (it then produces the ad hoc-signed apps it always did).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PHASE="${1:-all}"
W="${2:-$HOME/Meshpool-Build/v4/work}"
OUT="$W/out"
ENT="$REPO/packaging/mac/entitlements.plist"

need=(MAC_CERT_P12_BASE64 MAC_CERT_PASSWORD MAC_SIGN_IDENTITY APPLE_ID APPLE_APP_PASSWORD APPLE_TEAM_ID)
missing=()
for v in "${need[@]}"; do [ -n "${!v:-}" ] || missing+=("$v"); done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "sign.sh: signing is off, no Apple Developer secrets set (${missing[*]})."
  echo "sign.sh: the apps stay ad hoc-signed, as before. See docs/TRUSTED_INSTALL_PROMPT.md."
  exit 0
fi

[ -f "$ENT" ] || { echo "sign.sh: $ENT is missing"; exit 1; }
command -v xcrun >/dev/null || { echo "sign.sh: xcrun is missing (install the Xcode command line tools)"; exit 1; }

# ---------- the certificate, in a keychain of its own ------------------------------------------------------------
# A temporary keychain keeps the certificate out of the login keychain and off the runner's disk afterwards.
KC="$W/meshpool-signing.keychain-db"
KCPW="$(openssl rand -hex 24)"
P12="$W/meshpool-signing.p12"
cleanup() {
  security delete-keychain "$KC" >/dev/null 2>&1 || true
  rm -f "$P12"
}
trap cleanup EXIT

echo "sign.sh: importing the Developer ID certificate"
security create-keychain -p "$KCPW" "$KC"
security set-keychain-settings -lut 3600 "$KC"
security unlock-keychain -p "$KCPW" "$KC"
printf '%s' "$MAC_CERT_P12_BASE64" | base64 --decode > "$P12"
# -T /usr/bin/codesign lets codesign use the key without an interactive prompt.
security import "$P12" -k "$KC" -P "$MAC_CERT_PASSWORD" -T /usr/bin/codesign -T /usr/bin/security >/dev/null
# Without this, codesign still asks for permission to use the key and the build hangs.
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$KCPW" "$KC" >/dev/null
security list-keychains -d user -s "$KC" $(security list-keychains -d user | tr -d '"')
rm -f "$P12"

if ! security find-identity -v -p codesigning "$KC" | grep -qF "$MAC_SIGN_IDENTITY"; then
  echo "sign.sh: the certificate in MAC_CERT_P12_BASE64 is not \"$MAC_SIGN_IDENTITY\""
  security find-identity -v -p codesigning "$KC" || true
  exit 1
fi

# ---------- sign, from the inside out ---------------------------------------------------------------------------
# --deep is not used: it signs nested code with the wrong entitlements and misses files. Every Mach-O file is signed
# first, then the launcher, then the bundle. Mach-O files are found by their magic number, the way check_minos.py does.
is_macho() {
  python3 - "$1" <<'PY'
import struct, sys
try:
    with open(sys.argv[1], "rb") as f:
        head = f.read(4)
except OSError:
    sys.exit(1)
if len(head) < 4:
    sys.exit(1)
thin = (0xFEEDFACE, 0xFEEDFACF, 0xCEFAEDFE, 0xCFFAEDFE)
sys.exit(0 if struct.unpack(">I", head)[0] in (0xCAFEBABE, *thin) or struct.unpack("<I", head)[0] in thin else 1)
PY
}

sign_one() {   # path
  codesign --force --timestamp --options runtime --entitlements "$ENT" --sign "$MAC_SIGN_IDENTITY" "$1"
}

sign_apps() {
  for a in arm64 x64; do
    APP="$W/app-mac-$a/Meshpool.app"
    [ -d "$APP" ] || { echo "sign.sh: $APP is missing"; exit 1; }
    echo "sign.sh: signing $a"
    # Every Mach-O file inside the bundle: .so, .dylib, the executables under Resources/python, Resources/node,
    # Resources/app/vendor and Playwright's driver. Symlinks are skipped (they point at a file signed in its own right).
    n=0
    while IFS= read -r -d '' f; do
      if is_macho "$f"; then sign_one "$f"; n=$((n + 1)); fi
    done < <(find "$APP" -type f -print0)
    echo "sign.sh: signed $n file(s) inside the bundle"
    sign_one "$APP/Contents/MacOS/Meshpool"
    sign_one "$APP"
    codesign --verify --deep --strict --verbose=2 "$APP"
  done
}

# ---------- notarize and staple ---------------------------------------------------------------------------------
# The dmg is what the user downloads, so the dmg is what gets notarized and stapled; the app inside it is stapled too,
# so it still passes after it is dragged out of the image.
notarize_dmgs() {
  for a in arm64 x64; do
    NAME=Meshpool; [ $a = x64 ] && NAME=Meshpool-Intel
    DMG="$OUT/$NAME.dmg"
    [ -f "$DMG" ] || { echo "sign.sh: $DMG is missing (repack.sh builds it)"; exit 1; }
    echo "sign.sh: notarizing $NAME.dmg (this takes a few minutes)"
    if ! xcrun notarytool submit "$DMG" --apple-id "$APPLE_ID" --password "$APPLE_APP_PASSWORD" \
         --team-id "$APPLE_TEAM_ID" --wait --output-format json > "$W/notary-$NAME.json"; then
      echo "sign.sh: notarytool failed for $NAME.dmg"
      cat "$W/notary-$NAME.json" || true
      exit 1
    fi
    cat "$W/notary-$NAME.json"
    if ! grep -q '"status"[[:space:]]*:[[:space:]]*"Accepted"' "$W/notary-$NAME.json"; then
      # Every file the notary rejected is in the log; print it so the next run can fix them.
      ID="$(sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$W/notary-$NAME.json" | head -1)"
      echo "sign.sh: notarization was not accepted for $NAME.dmg"
      [ -n "$ID" ] && xcrun notarytool log "$ID" --apple-id "$APPLE_ID" --password "$APPLE_APP_PASSWORD" \
        --team-id "$APPLE_TEAM_ID" || true
      exit 1
    fi
    xcrun stapler staple "$DMG"
    xcrun stapler validate "$DMG"
  done
}

# ---------- prove it --------------------------------------------------------------------------------------------
# Gatekeeper's own verdict, on the dmg and on the app inside it. A build that fails here is not published.
check_dmgs() {
  for a in arm64 x64; do
    NAME=Meshpool; [ $a = x64 ] && NAME=Meshpool-Intel
    DMG="$OUT/$NAME.dmg"
    echo "sign.sh: checking $NAME.dmg with spctl"
    spctl -a -vvv -t install "$DMG" 2>&1 | tee "$W/spctl-$NAME.txt"
    grep -q "source=Notarized Developer ID" "$W/spctl-$NAME.txt" || {
      echo "sign.sh: $NAME.dmg is not accepted as a Notarized Developer ID"; exit 1; }
    MNT="$W/mnt-$a"; rm -rf "$MNT"; mkdir -p "$MNT"
    hdiutil attach "$DMG" -nobrowse -quiet -mountpoint "$MNT"
    spctl -a -vvv "$MNT/Meshpool.app" 2>&1 | tee "$W/spctl-$NAME-app.txt"
    ok=0; grep -q "source=Notarized Developer ID" "$W/spctl-$NAME-app.txt" && ok=1
    xcrun stapler validate "$MNT/Meshpool.app" || ok=0
    hdiutil detach "$MNT" -quiet || true
    [ "$ok" = 1 ] || { echo "sign.sh: Meshpool.app in $NAME.dmg is not accepted as a Notarized Developer ID"; exit 1; }
  done
}

case "$PHASE" in
  sign)     sign_apps ;;
  notarize) notarize_dmgs; check_dmgs ;;
  all)      sign_apps; notarize_dmgs; check_dmgs ;;
  *)        echo "sign.sh: unknown phase \"$PHASE\" (sign, notarize or all)"; exit 2 ;;
esac

echo "sign.sh: $PHASE done"