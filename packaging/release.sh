#!/bin/bash
# Publish a Meshpool release to GitHub from a computer where `gh` is signed in.
# Usage: packaging/release.sh 0.1.0 NOTES.md Meshpool.dmg Meshpool-Intel.dmg Meshpool-Setup.exe
# Creates the release as a draft, uploads each file with retries (slow or shaky connections are fine; a file that
# fails is retried, and a rerun skips files already uploaded), checks the sizes on GitHub, then publishes it.
set -u
VER="$1"; NOTES="$2"; shift 2
REPO="${MESHPOOL_REPO:-hamzaaziada/meshpool-harness}"
TAG="v$VER"
DIR="$(mktemp -d)"
shasum -a 256 "$@" | sed 's#  .*/#  #' > "$DIR/SHA256SUMS.txt"
cat "$DIR/SHA256SUMS.txt"
gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1 || \
  gh release create "$TAG" --repo "$REPO" --target main --draft --title "Meshpool $VER$(case "$VER" in 0.*) echo " beta";; esac)" --notes-file "$NOTES" || exit 1
have() { gh release view "$TAG" --repo "$REPO" --json assets --jq ".assets[] | select(.name==\"$1\" and .state==\"uploaded\") | .size"; }
for f in "$@" "$DIR/SHA256SUMS.txt"; do
  name="$(basename "$f")"; size="$(wc -c < "$f" | tr -d ' ')"
  for try in 1 2 3 4 5 6; do
    [ "$(have "$name")" = "$size" ] && { echo "uploaded $name"; break; }
    echo "uploading $name (try $try)..."
    gh release upload "$TAG" "$f" --repo "$REPO" --clobber && continue
    sleep $((try * 10))
  done
  [ "$(have "$name")" = "$size" ] || { echo "Could not upload $name. Run this again to resume."; exit 1; }
done
gh release edit "$TAG" --repo "$REPO" --draft=false --latest && echo "Published $TAG: https://github.com/$REPO/releases/tag/$TAG"
