#!/bin/sh
# Meshpool for the terminal, on macOS and Linux. One command installs it and starts setup:
#
#   curl -fsSL https://raw.githubusercontent.com/hamzaaziada/meshpool-harness/main/install.sh | sh
#
# It installs into ~/.meshpool-cli and puts the `meshpool` command on your PATH. It uses your Python 3.10 or later
# if you have one; otherwise it downloads a private copy of Python into ~/.meshpool-cli (nothing system-wide changes).
# Settings: MESHPOOL_REF (branch or tag), MESHPOOL_DIR, MESHPOOL_BIN, MESHPOOL_NO_SETUP=1 (do not start setup),
# MESHPOOL_SRC (install from a local checkout instead of downloading).
set -eu

REPO="hamzaaziada/meshpool-harness"
PBS="https://github.com/astral-sh/python-build-standalone/releases/download/20241016"
PBS_PY="cpython-3.12.7+20241016"

# ---------- look -------------------------------------------------------------------------------------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-}" != "dumb" ]; then
  case "${COLORTERM:-}" in truecolor|24bit) DEPTH=24 ;; *) DEPTH=256 ;; esac
  case "${TERM_PROGRAM:-}" in iTerm.app|WezTerm|vscode|ghostty|WarpTerminal) DEPTH=24 ;; esac
else
  DEPTH=0
fi
if [ "$DEPTH" = 24 ]; then BLUE='\033[1;38;2;22;112;232m'; GREEN='\033[38;2;46;160;67m'; RED='\033[38;2;214;69;65m'
elif [ "$DEPTH" = 256 ]; then BLUE='\033[1;38;5;33m'; GREEN='\033[38;5;71m'; RED='\033[38;5;167m'
else BLUE=''; GREEN=''; RED=''; fi
if [ "$DEPTH" = 0 ]; then DIM=''; OFF=''; else DIM='\033[2m'; OFF='\033[0m'; fi

say()  { printf '%b\n' "$*"; }
step() { printf '%b\n' "  ${BLUE}›${OFF} $*"; }
ok()   { printf '%b\n' "  ${GREEN}✓${OFF} $*"; }
die()  { printf '%b\n' "  ${RED}✗${OFF} $*" >&2; exit 1; }

logo() {
  say ""
  if [ "$DEPTH" = 24 ]; then
    say '     \033[38;2;30;132;250m▄\033[0m\033[38;2;43;149;251m\033[48;2;6;120;255m▀\033[0m\033[38;2;25;135;253m\033[48;2;0;99;254m▀\033[0m\033[38;2;17;128;252m\033[48;2;0;91;252m▀\033[0m\033[38;2;24;130;255m\033[48;2;2;91;255m▀\033[0m\033[38;2;10;94;255m▄\033[0m   \033[38;2;112;200;252m▄\033[0m\033[38;2;141;222;252m\033[48;2;119;216;255m▀\033[0m\033[38;2;170;231;253m\033[48;2;126;214;255m▀\033[0m\033[38;2;202;245;255m\033[48;2;165;236;255m▀\033[0m\033[38;2;205;244;255m▄\033[0m'
    say '    \033[38;2;14;112;251m▄\033[0m\033[38;2;6;116;255m\033[48;2;1;89;253m▀\033[0m\033[38;2;0;87;250m\033[48;2;3;76;246m▀\033[0m\033[38;2;4;84;249m\033[48;2;3;70;239m▀\033[0m\033[38;2;4;76;245m\033[48;2;3;64;227m▀\033[0m\033[38;2;3;67;235m\033[48;2;3;57;215m▀\033[0m\033[38;2;1;66;240m\033[48;2;4;53;201m▀\033[0m\033[38;2;13;74;228m\033[48;2;0;45;197m▀\033[0m\033[38;2;61;122;224m▄\033[0m\033[38;2;96;184;251m\033[48;2;77;172;255m▀\033[0m\033[38;2;95;197;255m\033[48;2;71;167;251m▀\033[0m\033[38;2;94;187;250m\033[48;2;86;180;251m▀\033[0m\033[38;2;109;198;251m\033[48;2;100;190;251m▀\033[0m\033[38;2;131;210;250m\033[48;2;119;203;252m▀\033[0m\033[38;2;179;238;255m\033[48;2;144;215;251m▀\033[0m\033[38;2;215;245;253m\033[48;2;201;247;255m▀\033[0m\033[38;2;216;240;252m▄\033[0m'
    say '   \033[38;2;16;105;250m▄\033[0m\033[38;2;6;98;255m\033[48;2;2;80;255m▀\033[0m\033[38;2;2;71;242m\033[48;2;3;63;227m▀\033[0m\033[38;2;2;65;230m\033[48;2;3;58;211m▀\033[0m\033[38;2;2;59;216m\033[48;2;3;51;194m▀\033[0m\033[38;2;4;54;204m\033[48;2;0;35;175m▀\033[0m\033[38;2;4;48;192m\033[48;2;0;34;165m▀\033[0m\033[38;2;3;43;180m\033[48;2;0;32;156m▀\033[0m\033[38;2;5;46;172m\033[48;2;19;84;203m▀\033[0m\033[38;2;31;121;248m\033[48;2;3;91;247m▀\033[0m\033[38;2;55;152;254m\033[48;2;26;114;246m▀\033[0m\033[38;2;64;158;250m\033[48;2;58;154;253m▀\033[0m\033[38;2;74;168;251m\033[48;2;61;157;251m▀\033[0m\033[38;2;87;179;251m\033[48;2;73;168;251m▀\033[0m\033[38;2;105;192;251m\033[48;2;88;179;251m▀\033[0m\033[38;2;126;205;251m\033[48;2;109;195;252m▀\033[0m\033[38;2;163;221;252m\033[48;2;139;210;251m▀\033[0m\033[38;2;214;250;255m\033[48;2;190;236;253m▀\033[0m\033[38;2;214;241;251m▄\033[0m     \033[1m\033[38;2;22;112;232mmeshpool\033[0m'
    say '   \033[38;2;8;91;251m\033[48;2;4;71;231m▀\033[0m\033[38;2;1;61;223m\033[48;2;1;40;171m▀\033[0m\033[38;2;3;49;186m\033[48;2;3;38;158m▀\033[0m\033[38;2;15;56;172m\033[48;2;2;35;148m▀\033[0m\033[38;2;35;85;189m\033[48;2;2;33;147m▀\033[0m\033[38;2;53;114;216m\033[48;2;26;70;174m▀\033[0m\033[38;2;23;75;194m\033[48;2;70;148;248m▀\033[0m\033[38;2;12;62;178m\033[48;2;55;141;255m▀\033[0m\033[38;2;13;91;231m\033[48;2;30;104;237m▀\033[0m\033[38;2;0;63;222m\033[48;2;8;68;212m▀\033[0m\033[38;2;6;75;224m\033[48;2;0;50;198m▀\033[0m\033[38;2;48;141;251m\033[48;2;30;106;228m▀\033[0m\033[38;2;57;153;252m\033[48;2;58;157;255m▀\033[0m\033[38;2;60;157;251m\033[48;2;74;168;251m▀\033[0m\033[38;2;79;173;251m\033[48;2;94;186;252m▀\033[0m\033[38;2;88;179;251m\033[48;2;80;173;251m▀\033[0m\033[38;2;85;176;251m\033[48;2;75;170;251m▀\033[0m\033[38;2;103;187;250m\033[48;2;72;169;250m▀\033[0m\033[38;2;187;239;255m\033[48;2;86;184;252m▀\033[0m\033[38;2;223;244;250m\033[48;2;155;218;252m▀\033[0m    \033[2mfor the terminal · meshpool.ai\033[0m'
    say '  \033[38;2;16;86;234m\033[48;2;11;81;242m▀\033[0m\033[38;2;2;56;212m\033[48;2;2;67;234m▀\033[0m\033[38;2;2;46;185m\033[48;2;3;57;208m▀\033[0m\033[38;2;3;43;172m\033[48;2;3;48;187m▀\033[0m\033[38;2;3;38;162m\033[48;2;2;44;176m▀\033[0m\033[38;2;4;39;160m\033[48;2;0;43;175m▀\033[0m\033[38;2;0;33;164m\033[48;2;10;70;205m▀\033[0m\033[38;2;30;87;196m▀\033[0m\033[38;2;91;165;255m▀\033[0m\033[38;2;58;130;246m\033[48;2;91;165;255m▀\033[0m\033[38;2;38;105;225m\033[48;2;57;125;242m▀\033[0m\033[38;2;13;71;202m\033[48;2;40;105;223m▀\033[0m\033[38;2;3;60;196m\033[48;2;8;76;214m▀\033[0m\033[38;2;73;163;247m\033[48;2;62;127;212m▀\033[0m\033[38;2;112;200;253m\033[48;2;129;213;255m▀\033[0m\033[38;2;94;185;251m\033[48;2;94;184;251m▀\033[0m\033[38;2;82;176;251m\033[48;2;85;179;252m▀\033[0m\033[38;2;76;171;252m\033[48;2;79;173;252m▀\033[0m\033[38;2;78;173;252m\033[48;2;80;175;251m▀\033[0m\033[38;2;83;179;251m\033[48;2;87;182;250m▀\033[0m\033[38;2;112;208;255m\033[48;2;109;202;255m▀\033[0m\033[38;2;156;225;253m\033[48;2;134;217;251m▀\033[0m'
    say '  \033[38;2;7;101;251m\033[48;2;25;129;250m▀\033[0m\033[38;2;0;82;250m\033[48;2;13;114;255m▀\033[0m\033[38;2;2;70;230m\033[48;2;11;86;245m▀\033[0m\033[38;2;2;60;208m\033[48;2;8;77;230m▀\033[0m\033[38;2;0;52;192m\033[48;2;17;91;244m▀\033[0m\033[38;2;7;70;216m\033[48;2;41;106;230m▀\033[0m\033[38;2;33;96;216m▀\033[0m   \033[38;2;88;158;253m▀\033[0m\033[38;2;66;132;242m▀\033[0m\033[38;2;51;116;227m▀\033[0m\033[38;2;7;66;188m\033[48;2;19;85;211m▀\033[0m\033[38;2;100;178;239m\033[48;2;38;112;225m▀\033[0m\033[38;2;104;195;253m\033[48;2;108;200;255m▀\033[0m\033[38;2;87;179;251m\033[48;2;92;185;251m▀\033[0m\033[38;2;83;177;252m\033[48;2;87;180;251m▀\033[0m\033[38;2;84;178;252m\033[48;2;88;182;251m▀\033[0m\033[38;2;91;186;252m\033[48;2;95;187;250m▀\033[0m\033[38;2;111;200;252m\033[48;2;119;213;255m▀\033[0m\033[38;2;137;220;251m\033[48;2;136;220;252m▀\033[0m'
    say '   \033[38;2;52;143;252m▀\033[0m\033[38;2;46;127;255m▀\033[0m\033[38;2;45;119;251m▀\033[0m\033[38;2;60;131;250m▀\033[0m          \033[38;2;58;140;238m▀\033[0m\033[38;2;105;201;255m\033[48;2;77;157;247m▀\033[0m\033[38;2;96;190;254m\033[48;2;104;193;251m▀\033[0m\033[38;2;93;187;252m\033[48;2;106;200;251m▀\033[0m\033[38;2;104;203;255m\033[48;2;112;209;253m▀\033[0m\033[38;2;118;211;253m▀\033[0m'
  elif [ "$DEPTH" = 256 ]; then
    say '     \033[38;5;33m▄\033[0m\033[38;5;33m\033[48;5;33m▀\033[0m\033[38;5;33m\033[48;5;27m▀\033[0m\033[38;5;33m\033[48;5;27m▀\033[0m\033[38;5;33m\033[48;5;27m▀\033[0m\033[38;5;27m▄\033[0m   \033[38;5;81m▄\033[0m\033[38;5;117m\033[48;5;117m▀\033[0m\033[38;5;153m\033[48;5;117m▀\033[0m\033[38;5;195m\033[48;5;159m▀\033[0m\033[38;5;195m▄\033[0m'
    say '    \033[38;5;27m▄\033[0m\033[38;5;33m\033[48;5;27m▀\033[0m\033[38;5;27m\033[48;5;27m▀\033[0m\033[38;5;27m\033[48;5;27m▀\033[0m\033[38;5;27m\033[48;5;26m▀\033[0m\033[38;5;26m\033[48;5;26m▀\033[0m\033[38;5;27m\033[48;5;26m▀\033[0m\033[38;5;26m\033[48;5;20m▀\033[0m\033[38;5;68m▄\033[0m\033[38;5;75m\033[48;5;75m▀\033[0m\033[38;5;81m\033[48;5;75m▀\033[0m\033[38;5;75m\033[48;5;75m▀\033[0m\033[38;5;81m\033[48;5;75m▀\033[0m\033[38;5;117m\033[48;5;117m▀\033[0m\033[38;5;159m\033[48;5;117m▀\033[0m\033[38;5;195m\033[48;5;195m▀\033[0m\033[38;5;195m▄\033[0m'
    say '   \033[38;5;27m▄\033[0m\033[38;5;27m\033[48;5;27m▀\033[0m\033[38;5;27m\033[48;5;26m▀\033[0m\033[38;5;26m\033[48;5;26m▀\033[0m\033[38;5;26m\033[48;5;25m▀\033[0m\033[38;5;26m\033[48;5;19m▀\033[0m\033[38;5;25m\033[48;5;19m▀\033[0m\033[38;5;19m\033[48;5;19m▀\033[0m\033[38;5;19m\033[48;5;26m▀\033[0m\033[38;5;33m\033[48;5;27m▀\033[0m\033[38;5;69m\033[48;5;27m▀\033[0m\033[38;5;75m\033[48;5;69m▀\033[0m\033[38;5;75m\033[48;5;75m▀\033[0m\033[38;5;75m\033[48;5;75m▀\033[0m\033[38;5;75m\033[48;5;75m▀\033[0m\033[38;5;117m\033[48;5;75m▀\033[0m\033[38;5;153m\033[48;5;117m▀\033[0m\033[38;5;195m\033[48;5;159m▀\033[0m\033[38;5;195m▄\033[0m     \033[1m\033[38;5;75mmeshpool\033[0m'
    say '   \033[38;5;27m\033[48;5;26m▀\033[0m\033[38;5;26m\033[48;5;19m▀\033[0m\033[38;5;25m\033[48;5;19m▀\033[0m\033[38;5;25m\033[48;5;18m▀\033[0m\033[38;5;25m\033[48;5;18m▀\033[0m\033[38;5;62m\033[48;5;25m▀\033[0m\033[38;5;25m\033[48;5;69m▀\033[0m\033[38;5;25m\033[48;5;69m▀\033[0m\033[38;5;26m\033[48;5;27m▀\033[0m\033[38;5;26m\033[48;5;26m▀\033[0m\033[38;5;26m\033[48;5;26m▀\033[0m\033[38;5;69m\033[48;5;26m▀\033[0m\033[38;5;69m\033[48;5;75m▀\033[0m\033[38;5;75m\033[48;5;75m▀\033[0m\033[38;5;75m\033[48;5;75m▀\033[0m\033[38;5;75m\033[48;5;75m▀\033[0m\033[38;5;75m\033[48;5;75m▀\033[0m\033[38;5;75m\033[48;5;75m▀\033[0m\033[38;5;159m\033[48;5;75m▀\033[0m\033[38;5;195m\033[48;5;117m▀\033[0m    \033[2mfor the terminal · meshpool.ai\033[0m'
    say '  \033[38;5;26m\033[48;5;27m▀\033[0m\033[38;5;26m\033[48;5;26m▀\033[0m\033[38;5;19m\033[48;5;26m▀\033[0m\033[38;5;19m\033[48;5;25m▀\033[0m\033[38;5;19m\033[48;5;19m▀\033[0m\033[38;5;19m\033[48;5;19m▀\033[0m\033[38;5;19m\033[48;5;26m▀\033[0m\033[38;5;26m▀\033[0m\033[38;5;75m▀\033[0m\033[38;5;69m\033[48;5;75m▀\033[0m\033[38;5;26m\033[48;5;69m▀\033[0m\033[38;5;26m\033[48;5;26m▀\033[0m\033[38;5;26m\033[48;5;26m▀\033[0m\033[38;5;75m\033[48;5;68m▀\033[0m\033[38;5;81m\033[48;5;117m▀\033[0m\033[38;5;75m\033[48;5;75m▀\033[0m\033[38;5;75m\033[48;5;75m▀\033[0m\033[38;5;75m\033[48;5;75m▀\033[0m\033[38;5;75m\033[48;5;75m▀\033[0m\033[38;5;75m\033[48;5;75m▀\033[0m\033[38;5;81m\033[48;5;81m▀\033[0m\033[38;5;153m\033[48;5;117m▀\033[0m'
    say '  \033[38;5;27m\033[48;5;33m▀\033[0m\033[38;5;27m\033[48;5;27m▀\033[0m\033[38;5;26m\033[48;5;27m▀\033[0m\033[38;5;26m\033[48;5;26m▀\033[0m\033[38;5;25m\033[48;5;27m▀\033[0m\033[38;5;26m\033[48;5;26m▀\033[0m\033[38;5;26m▀\033[0m   \033[38;5;75m▀\033[0m\033[38;5;69m▀\033[0m\033[38;5;68m▀\033[0m\033[38;5;25m\033[48;5;26m▀\033[0m\033[38;5;75m\033[48;5;26m▀\033[0m\033[38;5;75m\033[48;5;81m▀\033[0m\033[38;5;75m\033[48;5;75m▀\033[0m\033[38;5;75m\033[48;5;75m▀\033[0m\033[38;5;75m\033[48;5;75m▀\033[0m\033[38;5;75m\033[48;5;75m▀\033[0m\033[38;5;81m\033[48;5;117m▀\033[0m\033[38;5;117m\033[48;5;117m▀\033[0m'
    say '   \033[38;5;69m▀\033[0m\033[38;5;33m▀\033[0m\033[38;5;33m▀\033[0m\033[38;5;69m▀\033[0m          \033[38;5;69m▀\033[0m\033[38;5;81m\033[48;5;75m▀\033[0m\033[38;5;75m\033[48;5;75m▀\033[0m\033[38;5;75m\033[48;5;81m▀\033[0m\033[38;5;81m\033[48;5;81m▀\033[0m\033[38;5;117m▀\033[0m'
  else
    say "  meshpool"
  fi
  say ""
}

# ---------- python -----------------------------------------------------------------------------------------------
find_python() {
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 &&
       "$c" -c 'import sys, venv, ensurepip; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
      command -v "$c"; return 0
    fi
  done
  return 1
}

sha256() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1; else sha256sum "$1" | cut -d' ' -f1; fi
}

private_python() {
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64)               T=aarch64-apple-darwin;      SUM=4c18852bf9c1a11b56f21bcf0df1946f7e98ee43e9e4c0c5374b2b3765cf9508 ;;
    Darwin-x86_64)              T=x86_64-apple-darwin;       SUM=60c5271e7edc3c2ab47440b7abf4ed50fbc693880b474f74f05768f5b657045a ;;
    Linux-x86_64)               T=x86_64-unknown-linux-gnu;  SUM=43576f7db1033dd57b900307f09c2e86f371152ac8a2607133afa51cbfc36064 ;;
    Linux-aarch64|Linux-arm64)  T=aarch64-unknown-linux-gnu; SUM=bba3c6be6153f715f2941da34f3a6a69c2d0035c9c5396bc5bb68c6d2bd1065a ;;
    *) die "Meshpool needs Python 3.10 or later on this computer. Install it from python.org, then run this again." ;;
  esac
  F="$TMP/python.tgz"
  curl -fsSL "$PBS/$PBS_PY-$T-install_only.tar.gz" -o "$F" || die "Could not download Python. Check your internet connection."
  [ "$(sha256 "$F")" = "$SUM" ] || die "The Python download did not match its checksum. Run this again."
  mkdir -p "$TMP/py" && tar xzf "$F" -C "$TMP/py"
  rm -rf "$DIR/python" && mv "$TMP/py/python" "$DIR/python"
  echo "$DIR/python/bin/python3"
}

# ---------- PATH -------------------------------------------------------------------------------------------------
on_path() { case ":$PATH:" in *":$1:"*) return 0 ;; esac; return 1; }

pick_bin() {
  if [ -n "${MESHPOOL_BIN:-}" ]; then echo "$MESHPOOL_BIN"; return; fi
  # A folder that is already on PATH, so `meshpool` works at once, in this window too.
  for d in "$HOME/.local/bin" "$HOME/bin" /opt/homebrew/bin /usr/local/bin; do
    if on_path "$d" && [ -d "$d" ] && [ -w "$d" ]; then echo "$d"; return; fi
  done
  echo "$HOME/.local/bin"
}

add_line() {   # file, line: append once
  [ -f "$1" ] && grep -qF "$BIN" "$1" 2>/dev/null && return 0
  mkdir -p "$(dirname "$1")"
  printf '\n# Added by the Meshpool installer\n%s\n' "$2" >> "$1"
  ADDED="${ADDED:+$ADDED, }$(echo "$1" | sed "s|^$HOME|~|")"
}

add_to_path() {
  line="export PATH=\"$BIN:\$PATH\""
  sh_name="$(basename "${SHELL:-sh}")"
  # zsh is the default shell on macOS: Terminal reads ~/.zshrc. bash reads ~/.bashrc, or ~/.bash_profile at login.
  if [ "$sh_name" = zsh ] || [ -f "$HOME/.zshrc" ] || [ "$(uname -s)" = Darwin ]; then add_line "$HOME/.zshrc" "$line"; fi
  if [ "$sh_name" = bash ] || [ -f "$HOME/.bashrc" ]; then add_line "$HOME/.bashrc" "$line"; fi
  if [ -f "$HOME/.bash_profile" ] || { [ "$sh_name" = bash ] && [ "$(uname -s)" = Darwin ]; }; then
    add_line "$HOME/.bash_profile" "$line"
  fi
  if [ -f "$HOME/.profile" ] && [ ! -f "$HOME/.bash_profile" ]; then add_line "$HOME/.profile" "$line"; fi
  if [ "$sh_name" = fish ] || [ -d "$HOME/.config/fish" ]; then
    add_line "$HOME/.config/fish/conf.d/meshpool.fish" "fish_add_path $BIN"
  fi
}

ready() {   # a key, or a signed-in subscription chosen at setup
  grep -Eq '^(MESHPOOL_API_KEY=.|OPENAI_API_KEY=.|MH_PROVIDER=chatgpt)' "$DIR/meshpool.config" 2>/dev/null
}

# ---------- install ----------------------------------------------------------------------------------------------
main() {
  REF="${MESHPOOL_REF:-main}"
  DIR="${MESHPOOL_DIR:-$HOME/.meshpool-cli}"
  BIN="$(pick_bin)"
  ADDED=""
  command -v curl >/dev/null 2>&1 || die "This installer needs curl."
  command -v tar >/dev/null 2>&1 || die "This installer needs tar."
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  mkdir -p "$DIR"

  logo
  say "  ${DIM}Installing Meshpool for the terminal. This takes a minute the first time.${OFF}"
  say ""

  if PY="$(find_python)"; then
    ok "Python $("$PY" -c 'import platform; print(platform.python_version())')"
  elif [ -x "$DIR/python/bin/python3" ]; then
    PY="$DIR/python/bin/python3"; ok "Python (Meshpool's own copy)"
  else
    step "Downloading Python (your computer does not have 3.10 or later)..."
    PY="$(private_python)"; ok "Python (Meshpool's own copy, in $(echo "$DIR" | sed "s|^$HOME|~|"))"
  fi

  if [ -n "${MESHPOOL_SRC:-}" ]; then          # a local checkout, for testing this installer
    SRC="$MESHPOOL_SRC"
  else
    step "Downloading Meshpool ($REF)..."
    curl -fsSL "https://codeload.github.com/$REPO/tar.gz/$REF" -o "$TMP/src.tgz" ||
      die "Could not download Meshpool. Check your internet connection."
    tar xzf "$TMP/src.tgz" -C "$TMP"
    SRC="$(find "$TMP" -maxdepth 1 -type d -name 'meshpool-harness-*' | head -1)"
  fi
  [ -n "$SRC" ] || die "The download was incomplete. Run this again."
  rm -rf "$DIR/app.new" && cp -R "$SRC/app" "$DIR/app.new"
  cp "$SRC/THIRD_PARTY_NOTICES.md" "$DIR/app.new/"
  # The package list this copy is installed from: the in-app update compares it with a new release's list.
  cp "$SRC/packaging/requirements-cli.txt" "$DIR/app.new/requirements-cli.txt"
  # The command runs this file rather than `python -m`: Python then puts the app folder first on sys.path, never the
  # folder the command is typed in, so a project's own json.py or argparse.py is not imported in place of Python's.
  # (`python -P` does the same, but only from Python 3.11, and this installer accepts 3.10.)
  cat > "$DIR/app.new/meshpool_launch.py" <<'PY'
import runpy

runpy.run_module("meshpool_harness", run_name="__main__", alter_sys=True)
PY
  rm -rf "$DIR/app" && mv "$DIR/app.new" "$DIR/app"
  ok "Meshpool"

  step "Installing its Python packages..."
  if [ ! -x "$DIR/venv/bin/python" ] || ! "$DIR/venv/bin/python" -c 'import sys' >/dev/null 2>&1; then
    rm -rf "$DIR/venv" && "$PY" -m venv "$DIR/venv" || die "Could not create a Python environment with $PY."
  fi
  "$DIR/venv/bin/python" -m pip install -q --disable-pip-version-check --upgrade pip >/dev/null 2>&1 || true
  "$DIR/venv/bin/python" -m pip install -q --disable-pip-version-check -r "$SRC/packaging/requirements-cli.txt" ||
    die "Installing the Python packages failed (see above). Run this again."
  ok "Packages"

  mkdir -p "$BIN"
  SHIM="#!/bin/sh
PYTHONPATH=\"$DIR/app\" exec \"$DIR/venv/bin/python\" \"$DIR/app/meshpool_launch.py\" \"\$@\""
  printf '%s\n' "$SHIM" > "$BIN/meshpool" && chmod +x "$BIN/meshpool"
  # An earlier install may have put the command in ~/.local/bin: keep that copy current too.
  if [ "$BIN" != "$HOME/.local/bin" ] && [ -f "$HOME/.local/bin/meshpool" ]; then
    printf '%s\n' "$SHIM" > "$HOME/.local/bin/meshpool"
  fi
  if on_path "$BIN"; then NEW_WINDOW=""; else add_to_path; NEW_WINDOW=1; fi
  ok "The meshpool command ($(echo "$BIN" | sed "s|^$HOME|~|")/meshpool)${ADDED:+, added to PATH in $ADDED}"
  say ""

  # Straight into setup: pick how Meshpool runs, where it works, and what it may do. Then the session starts.
  if [ -z "${MESHPOOL_NO_SETUP:-}" ] && ! ready && (exec </dev/tty) 2>/dev/null; then
    MESHPOOL_FROM_INSTALLER=1 "$BIN/meshpool" setup </dev/tty || true
    say ""
  fi

  if ready; then
    say "  ${BLUE}Meshpool is ready.${OFF} Go to a project folder and type ${BLUE}meshpool${OFF}"
  else
    say "  ${BLUE}Meshpool is installed.${OFF} Type ${BLUE}meshpool${OFF} to set it up and start."
  fi
  if [ -n "$NEW_WINDOW" ]; then
    say "  ${DIM}Open a new terminal window first, or run this once here:${OFF} export PATH=\"$BIN:\$PATH\""
  fi
  say "  ${DIM}meshpool ui opens the full app in your browser. meshpool setup changes your choices.${OFF}"
  say ""
}

main "$@"
