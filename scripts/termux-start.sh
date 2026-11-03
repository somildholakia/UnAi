#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE_DIR="$ROOT/apps/engine"
WEB_DIR="$ROOT/apps/web"
LLAMA_CPP_DIR="\${UNAI_LLAMA_CPP_DIR:-$HOME/src/llama.cpp}"
MODEL_FILE="\${UNAI_MODEL_FILE:-\${UNAI_MODEL_DIR:-$HOME/models/unai}/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf}"
LLAMA_SERVER_BIN="\${UNAI_LLAMA_SERVER_BIN:-$LLAMA_CPP_DIR/build/bin/llama-server}"
CPU_THREADS="\${UNAI_CPU_THREADS:-4}"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is missing. Run bash scripts/termux-install.sh first."
  exit 1
fi
if [[ ! -x "$LLAMA_SERVER_BIN" || ! -s "$MODEL_FILE" ]]; then
  echo "Local runtime/model missing. Run bash scripts/termux-install.sh first."
  exit 1
fi
if [[ ! -x "$ENGINE_DIR/.venv/bin/uvicorn" || ! -d "$WEB_DIR/node_modules" ]]; then
  echo "Engine/Web dependencies are missing. Run bash scripts/termux-install.sh first."
  exit 1
fi

if tmux has-session -t unai 2>/dev/null; then
  echo "unAI is already running; attaching to its Termux session."
  exec tmux attach-session -t unai
fi

printf -v MODEL_COMMAND '%q ' "$LLAMA_SERVER_BIN" -m "$MODEL_FILE" \
  --host 127.0.0.1 --port 8080 --alias unai-local-coder \
  --ctx-size 2048 --threads "$CPU_THREADS" --batch-size 128
printf -v ENGINE_PATH '%q' "$ENGINE_DIR"
printf -v WEB_PATH '%q' "$WEB_DIR"
ENGINE_COMMAND="cd $ENGINE_PATH && .venv/bin/uvicorn unai_engine.main:app --host 127.0.0.1 --port 8000"
WEB_COMMAND="cd $WEB_PATH && npm run dev -- --host 127.0.0.1 --port 5173 --strictPort"

tmux new-session -d -s unai -n model
tmux send-keys -t unai:model "$MODEL_COMMAND" C-m
tmux new-window -t unai -n engine
tmux send-keys -t unai:engine "$ENGINE_COMMAND" C-m
tmux new-window -t unai -n web
tmux send-keys -t unai:web "$WEB_COMMAND" C-m

echo "Waiting for the local model server..."
MODEL_READY=false
for _ in $(seq 1 30); do
  if curl --fail --silent http://127.0.0.1:8080/health >/dev/null; then
    MODEL_READY=true
    break
  fi
  sleep 1
done
if [[ "$MODEL_READY" != true ]]; then
  echo "Model server is not ready yet; inspect the 'model' tmux window."
fi

echo "Web UI:  http://127.0.0.1:5173"
echo "Engine:  http://127.0.0.1:8000/api/health"
echo "Model:   http://127.0.0.1:8080/health"
echo "Detach: Ctrl+B, then D | Stop: tmux kill-session -t unai"
tmux select-window -t unai:web
exec tmux attach-session -t unai
