#!/bin/zsh
# Start the local LLM server and Balatro (with BalatroBot), then play.
# Everything is stopped again when you press Ctrl-C.
#
# Usage: ./scripts/start.sh [balatro-player play options], e.g.
#   ./scripts/start.sh -n 50 --deck RED --stake WHITE
#   ./scripts/start.sh -n 100 --no-llm          # heuristics only, faster
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="${BALATRO_MODEL:-mlx-community/Qwen3-30B-A3B-4bit}"
mkdir -p data/logs
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

if [[ " $* " != *" --no-llm "* ]]; then
  echo "Starting LLM server ($MODEL)..."
  .venv/bin/mlx_lm.server --model "$MODEL" --port 8080 > data/logs/llm.log 2>&1 &
  pids+=($!)
fi

echo "Starting Balatro with BalatroBot (fast mode)..."
uvx balatrobot serve --fast --logs-path data/logs > data/logs/balatro.log 2>&1 &
pids+=($!)

echo "Waiting for services..."
for i in {1..120}; do
  .venv/bin/balatro-player --model "$MODEL" doctor >/dev/null 2>&1 && break
  sleep 2
done
.venv/bin/balatro-player --model "$MODEL" doctor || true

[[ $# -eq 0 ]] && set -- -n 10
.venv/bin/balatro-player --model "$MODEL" play "$@"
.venv/bin/balatro-player report
