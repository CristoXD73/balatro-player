#!/bin/zsh
# Set up Balatro from the MAC APP STORE for use with balatro-player.
#
# The App Store build is sandboxed and code-signed, so mod loaders can't inject into it.
# This script leaves /Applications/Balatro.app untouched and instead:
#   1. copies it to ~/Games/Balatro-Bot/Balatro.app
#   2. re-signs the COPY ad hoc (no sandbox, no hardened runtime) so Lovely Injector can load
#   3. lays the copy out like a Steam install (what BalatroBot expects)
#   4. installs Lovely, Steamodded and BalatroBot
#   5. writes .appstore.env, which scripts/start.sh picks up automatically
#
# Usage: ./scripts/setup_appstore.sh [--skip-python]
#   --skip-python   only do the game side (skip uv / venv / LLM download); run setup_mac.sh pieces yourself
set -euo pipefail

SRC_APP="/Applications/Balatro.app"
BASE="$HOME/Games/Balatro-Bot"
APP="$BASE/Balatro.app"
MODS_DIR="$HOME/Library/Application Support/Balatro/Mods"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
SKIP_PY=0
[[ "${1:-}" == "--skip-python" ]] && SKIP_PY=1

say() { print -P "%F{cyan}==>%f $*"; }

[[ "$(uname -m)" == "arm64" ]] || { echo "This setup is for Apple silicon Macs."; exit 1; }
[[ -d "$SRC_APP" ]] || { echo "Balatro not found at $SRC_APP"; exit 1; }
command -v codesign >/dev/null || { echo "codesign missing (install Xcode command line tools: xcode-select --install)"; exit 1; }

say "Copying Balatro.app (original stays untouched)"
mkdir -p "$BASE"
rm -rf "$APP"
ditto "$SRC_APP" "$APP"
rm -rf "$APP/Contents/_MASReceipt"
xattr -cr "$APP" 2>/dev/null || true

# BalatroBot looks for a binary called "love" inside Balatro.app/Contents/MacOS.
BIN="$APP/Contents/MacOS"
[[ -f "$BIN/love" ]] || cp "$BIN/Balatro" "$BIN/love"

# The App Store build keeps the game as loose files in Resources/game; a LÖVE runtime also
# loads a Balatro.love archive from Resources, like the Steam build does. Make one if missing.
if ! ls "$APP/Contents/Resources/"*.love >/dev/null 2>&1; then
  say "Packing Resources/game into Balatro.love"
  (cd "$APP/Contents/Resources/game" && zip -qr "$APP/Contents/Resources/Balatro.love" . -x '.DS_Store' '*/.DS_Store')
fi

say "Re-signing the copy ad hoc (drops the sandbox and hardened runtime)"
codesign --remove-signature "$BIN/Balatro" 2>/dev/null || true
codesign --remove-signature "$BIN/love" 2>/dev/null || true
codesign --force --deep --sign - "$APP"
codesign -dv "$APP" 2>&1 | grep -E 'Identifier|Signature|flags' || true

latest_tag() {
  curl -fsSL "https://api.github.com/repos/$1/releases/latest" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])'
}

say "Installing Lovely Injector"
LOVELY_TAG=$(latest_tag ethangreen-dev/lovely-injector)
curl -fsSL -o "$TMP/lovely.tar.gz" \
  "https://github.com/ethangreen-dev/lovely-injector/releases/download/$LOVELY_TAG/lovely-aarch64-apple-darwin.tar.gz"
tar -xzf "$TMP/lovely.tar.gz" -C "$TMP"
cp "$(find "$TMP" -name liblovely.dylib | head -1)" "$BASE/liblovely.dylib"
xattr -cr "$BASE/liblovely.dylib" 2>/dev/null || true
codesign --force --sign - "$BASE/liblovely.dylib"
echo "   Lovely $LOVELY_TAG -> $BASE/liblovely.dylib"

mkdir -p "$MODS_DIR"

say "Installing Steamodded"
SMODS_TAG=$(latest_tag Steamodded/smods)
curl -fsSL -o "$TMP/smods.zip" "https://github.com/Steamodded/smods/archive/refs/tags/$SMODS_TAG.zip"
unzip -q "$TMP/smods.zip" -d "$TMP/smods"
rm -rf "$MODS_DIR/smods"
mv "$TMP/smods/"* "$MODS_DIR/smods"
echo "   Steamodded $SMODS_TAG -> $MODS_DIR/smods"

say "Installing BalatroBot mod"
BB_TAG=$(latest_tag coder/balatrobot)
curl -fsSL -o "$TMP/bb.zip" "https://github.com/coder/balatrobot/archive/refs/tags/$BB_TAG.zip"
unzip -q "$TMP/bb.zip" -d "$TMP/bb"
BB_SRC="$(find "$TMP/bb" -maxdepth 1 -mindepth 1 -type d | head -1)"
rm -rf "$MODS_DIR/balatrobot"
mkdir -p "$MODS_DIR/balatrobot/src"
cp "$BB_SRC/balatrobot.json" "$BB_SRC/balatrobot.lua" "$MODS_DIR/balatrobot/"
cp -R "$BB_SRC/src/lua" "$MODS_DIR/balatrobot/src/lua"
echo "   BalatroBot $BB_TAG -> $MODS_DIR/balatrobot"

say "Writing $REPO_DIR/.appstore.env"
cat > "$REPO_DIR/.appstore.env" <<EOF
export BALATROBOT_PLATFORM=darwin
export BALATROBOT_BALATRO_PATH="$BASE"
export BALATROBOT_LOVE_PATH="$BIN/love"
export BALATROBOT_LOVELY_PATH="$BASE/liblovely.dylib"
EOF

if [[ $SKIP_PY -eq 0 ]]; then
  if ! command -v uv >/dev/null; then
    say "Installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
  say "Creating Python environment"
  cd "$REPO_DIR"
  uv venv --python 3.12 .venv
  uv pip install --python .venv/bin/python -e ".[mlx,dev]"
  say "Building the screen recorder"
  mkdir -p tools/bin
  swiftc -parse-as-library -O tools/recorder.swift -o tools/bin/balatro-recorder || echo "   (recorder build failed; only needed for --record)"
  say "Running tests"
  .venv/bin/python -m pytest -q tests/test_engine.py tests/test_solver.py
fi

cat <<EOF

Game side is ready. Test it BEFORE the full bot:
  source .appstore.env
  uvx balatrobot serve --fast          # Balatro should open; leave it running
  # in a second terminal:
  .venv/bin/balatro-player doctor
If Balatro doesn't open or doctor can't connect, send me the last lines of the terminal output.
EOF
