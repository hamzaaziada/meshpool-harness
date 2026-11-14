#!/bin/bash
# Publish a Meshpool release to GitHub from a computer where `gh` is signed in.
# Usage: packaging/release.sh 0.1.0 NOTES.md Meshpool.dmg Meshpool-Intel.dmg Meshpool-Setup.exe
# Creates the release as a draft, uploads each file with retries (slow or shaky connections are fine; a file that
# fails is retried, and a rerun skips files already uploaded unless they were rebuilt), checks the sizes on GitHub,
# then publishes it. SHA256SUMS.txt is uploaded on every run.
set -u
VER="$1"; NOTES="$2"; shift 2
REPO="${MESHPOOL_REPO:-hamzaaziada/meshpool-harness}"
TAG="v$VER"
DIR="$(mktemp -d)"
shasum -a 256 "$@" | sed 's#  .*/#  #' > "$DIR/SHA256SUMS.txt"
cat "$DIR/SHA256SUMS.txt"
SUFFIX=""
case "$VER" in 0.*) SUFFIX=" beta" ;; esac       # set apart: a case inside $(...) does not parse in macOS bash 3.2
gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1 || \
  gh release create "$TAG" --repo "$REPO" --target main --draft --title "Meshpool $VER$SUFFIX" --notes-file "$NOTES" || exit 1
have() { gh release view "$TAG" --repo "$REPO" --json assets --jq ".assets[] | select(.name==\"$1\" and .state==\"uploaded\") | .size"; }
# On a rerun, the sums published last time tell a file that is already up from one that was rebuilt since (a rebuilt
# file can keep its size). SHA256SUMS.txt itself always goes up again, so it never describes older files.
mkdir -p "$DIR/old"
gh release download "$TAG" --repo "$REPO" --pattern SHA256SUMS.txt --dir "$DIR/old" >/dev/null 2>&1
sum_of() { [ -f "$2" ] && awk -v n="$1" '$2 == n { print $1 }' "$2"; }
for f in "$@" "$DIR/SHA256SUMS.txt"; do
  name="$(basename "$f")"; size="$(wc -c < "$f" | tr -d ' ')"
  fresh=""
  if [ "$name" = SHA256SUMS.txt ]; then
    fresh=1
  elif [ -f "$DIR/old/SHA256SUMS.txt" ] && [ "$(sum_of "$name" "$DIR/old/SHA256SUMS.txt")" != "$(sum_of "$name" "$DIR/SHA256SUMS.txt")" ]; then
    fresh=1
  fi
  for try in 1 2 3 4 5 6; do
    [ -z "$fresh" ] && [ "$(have "$name")" = "$size" ] && { echo "uploaded $name"; break; }
    echo "uploading $name (try $try)..."
    gh release upload "$TAG" "$f" --repo "$REPO" --clobber && { fresh=""; continue; }
    sleep $((try * 10))
  done
  [ "$(have "$name")" = "$size" ] || { echo "Could not upload $name. Run this again to resume."; exit 1; }
done
gh release edit "$TAG" --repo "$REPO" --draft=false --latest && echo "Published $TAG: https://github.com/$REPO/releases/tag/$TAG"
