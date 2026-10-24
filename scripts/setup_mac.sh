#!/bin/zsh
# One-time setup on an Apple silicon Mac (tested design target: Mac mini, 32 GB).
#   - installs uv (Python tooling) if missing
#   - installs Lovely Injector into the Steam copy of Balatro
#   - installs Steamodded + BalatroBot into Balatro's Mods folder
#   - creates the Python environment and downloads the local LLM
#
# Usage: ./scripts/setup_mac.sh [--model mlx-community/Qwen3-30B-A3B-4bit]
set -euo pipefail

MODEL="mlx-community/Qwen3-30B-A3B-4bit"
[[ "${1:-}" == "--model" && -n "${2:-}" ]] && MODEL="$2"

GAME_DIR="$HOME/Library/Application Support/Steam/steamapps/common/Balatro"
MODS_DIR="$HOME/Library/Application Support/Balatro/Mods"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

say() { print -P "%F{cyan}==>%f $*"; }

[[ "$(uname -m)" == "arm64" ]] || { echo "This setup is for Apple silicon Macs."; exit 1; }
[[ -d "$GAME_DIR/Balatro.app" ]] || { echo "Balatro not found at: $GAME_DIR (install it from Steam first)"; exit 1; }

if ! command -v uv >/dev/null; then
  say "Installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

latest_tag() {
  curl -fsSL "https://api.github.com/repos/$1/releases/latest" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])'
}

say "Installing Lovely Injector"
LOVELY_TAG=$(latest_tag ethangreen-dev/lovely-injector)
curl -fsSL -o "$TMP/lovely.tar.gz" \
  "https://github.com/ethangreen-dev/lovely-injector/releases/download/$LOVELY_TAG/lovely-aarch64-apple-darwin.tar.gz"
tar -xzf "$TMP/lovely.tar.gz" -C "$TMP"
cp "$(find "$TMP" -name liblovely.dylib | head -1)" "$GAME_DIR/liblovely.dylib"
echo "   Lovely $LOVELY_TAG -> $GAME_DIR/liblovely.dylib"

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

say "Creating Python environment"
cd "$REPO_DIR"
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[mlx,dev]"

say "Downloading $MODEL (one time, ~17 GB for the default model)"
.venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('$MODEL')"

say "Building the screen recorder"
mkdir -p tools/bin
swiftc -parse-as-library -O tools/recorder.swift -o tools/bin/balatro-recorder
echo "   tools/bin/balatro-recorder (first recording asks for Screen Recording permission)"

say "Running tests"
.venv/bin/python -m pytest -q tests/test_engine.py tests/test_solver.py

cat <<EOF

Setup complete. To play:
  ./scripts/start.sh -n 20                        # starts the LLM server and Balatro, then plays
  ./scripts/start.sh -n 20 --record --speedup 8   # same, filming every run (+ 8x timelapse)
or step by step, in three terminals:
  .venv/bin/mlx_lm.server --model $MODEL --port 8080
  uvx balatrobot serve --fast
  .venv/bin/balatro-player play -n 10
EOF
