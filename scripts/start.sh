#!/bin/zsh
# Start the local LLM server and Balatro (with BalatroBot), then play.
# Everything is stopped again when you press Ctrl-C.
#
# Usage: ./scripts/start.sh [balatro-player play options], e.g.
#   ./scripts/start.sh -n 50                          # play 50 runs
#   ./scripts/start.sh -n 50 --record --speedup 8     # film every run + an 8x timelapse
#   ./scripts/start.sh -n 100 --no-llm                # heuristics only, fastest
#
# Environment:
#   BALATRO_SPEED  game speed multiplier (default 16; the game's own setting stops at 4,
#                  BalatroBot's --fast is 10). Lower it if animations glitch.
#   BALATRO_MODEL  local model (default mlx-community/Qwen3-30B-A3B-4bit)
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="${BALATRO_MODEL:-mlx-community/Qwen3-30B-A3B-4bit}"
SPEED="${BALATRO_SPEED:-16}"
mkdir -p data/logs
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

if [[ " $* " != *" --no-llm "* ]]; then
  echo "Starting LLM server ($MODEL)..."
  .venv/bin/mlx_lm.server --model "$MODEL" --port 8080 > data/logs/llm.log 2>&1 &
  pids+=($!)
fi

# BalatroBot's --fast pins the game at 10x; for any other speed set it directly,
# with unlimited frames and fast animations so it still looks smooth on video.
if [[ "$SPEED" == "10" ]]; then
  SPEED_ARGS=(--fast)
else
  SPEED_ARGS=(--gamespeed "$SPEED" --fps-cap 240 --animation-fps 60)
fi
echo "Starting Balatro with BalatroBot at ${SPEED}x speed..."
uvx balatrobot serve "${SPEED_ARGS[@]}" --logs-path data/logs > data/logs/balatro.log 2>&1 &
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
echo "Journal: $(pwd)/journal.txt"
