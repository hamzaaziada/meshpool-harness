#!/bin/sh
# Meshpool command line installer for macOS and Linux.
#   curl -fsSL https://raw.githubusercontent.com/hamzaaziada/meshpool-harness/main/install.sh | sh
# Installs into ~/.meshpool-cli and puts a `meshpool` command in ~/.local/bin. Needs Python 3.10 or later.
set -eu
REPO="hamzaaziada/meshpool-harness"
REF="${MESHPOOL_REF:-main}"
DIR="${MESHPOOL_DIR:-$HOME/.meshpool-cli}"
BIN="${MESHPOOL_BIN:-$HOME/.local/bin}"

blue() { printf '\033[38;2;37;99;235m%s\033[0m\n' "$1"; }
blue "  ▗▄▖ ▗▄▖"
blue " ▟███▙███▙   meshpool"
blue "▟█▛ ▜█▛ ▜█▙  command line installer"
echo

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "Meshpool needs Python 3.10 or later. Install it from python.org, then run this again."; exit 1; }

echo "Downloading Meshpool ($REF)..."
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
curl -fsSL "https://codeload.github.com/$REPO/tar.gz/$REF" -o "$TMP/src.tgz"
tar xzf "$TMP/src.tgz" -C "$TMP"
SRC="$(find "$TMP" -maxdepth 1 -type d -name 'meshpool-harness-*' | head -1)"
mkdir -p "$DIR"
rm -rf "$DIR/app.new" && cp -R "$SRC/app" "$DIR/app.new"
cp "$SRC/LICENSE" "$SRC/NOTICE" "$SRC/THIRD_PARTY_NOTICES.md" "$DIR/app.new/"
rm -rf "$DIR/app" && mv "$DIR/app.new" "$DIR/app"

echo "Installing Python packages (first time takes a minute)..."
[ -x "$DIR/venv/bin/python" ] || "$PY" -m venv "$DIR/venv"
"$DIR/venv/bin/python" -m pip install -q --upgrade pip >/dev/null
"$DIR/venv/bin/python" -m pip install -q -r "$SRC/packaging/requirements-cli.txt"

mkdir -p "$BIN"
cat > "$BIN/meshpool" <<SHIM
#!/bin/sh
PYTHONPATH="$DIR/app" exec "$DIR/venv/bin/python" -m meshpool_harness "\$@"
SHIM
chmod +x "$BIN/meshpool"

echo
blue "Meshpool is installed."
case ":$PATH:" in *":$BIN:"*) ;; *) echo "Add $BIN to your PATH, for example: echo 'export PATH=\"$BIN:\$PATH\"' >> ~/.zshrc";; esac
echo "  meshpool login     save your Meshpool API key"
echo "  meshpool           start the agent in the current folder"
echo "  meshpool ui        open the full app in your browser on localhost"
