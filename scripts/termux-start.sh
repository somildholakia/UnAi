#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE_DIR="$ROOT/apps/engine"
WEB_DIR="$ROOT/apps/web"
LLAMA_CPP_DIR="${UNAI_LLAMA_CPP_DIR:-$HOME/src/llama.cpp}"
MODEL_DIR="${UNAI_MODEL_DIR:-$HOME/models/unai}"
MODEL_FILE="${UNAI_MODEL_FILE:-$MODEL_DIR/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf}"
LLAMA_SERVER_BIN="${UNAI_LLAMA_SERVER_BIN:-$LLAMA_CPP_DIR/build/bin/llama-server}"
CPU_THREADS="${UNAI_CPU_THREADS:-4}"

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
  echo "unAI is already running; checking its services before attaching."
  bash "$ROOT/scripts/termux-doctor.sh" || echo "One or more checks failed; inspect the existing tmux windows."
  exec tmux attach-session -t unai
fi

SERVICE_LAUNCHER="$ROOT/scripts/termux-service.sh"
if [[ ! -f "$SERVICE_LAUNCHER" || ! -f "$ROOT/scripts/termux-supervise.sh" ]]; then
  echo "Termux service launcher or supervisor is missing; update the repository and reinstall."
  exit 1
fi

# The same service command is used by startup and explicit recovery, avoiding
# mismatched flags or duplicate process definitions.
printf -v MODEL_COMMAND '%q ' bash "$SERVICE_LAUNCHER" model
printf -v ENGINE_COMMAND '%q ' bash "$SERVICE_LAUNCHER" engine
printf -v WEB_COMMAND '%q ' bash "$SERVICE_LAUNCHER" web

tmux new-session -d -s unai -n model
tmux set-window-option -t unai:model remain-on-exit on
tmux send-keys -t unai:model "$MODEL_COMMAND" C-m
tmux new-window -t unai -n engine
tmux set-window-option -t unai:engine remain-on-exit on
tmux send-keys -t unai:engine "$ENGINE_COMMAND" C-m
tmux new-window -t unai -n web
tmux set-window-option -t unai:web remain-on-exit on
tmux send-keys -t unai:web "$WEB_COMMAND" C-m

STARTUP_TIMEOUT="${UNAI_STARTUP_TIMEOUT_SECONDS:-90}"
if ! [[ "$STARTUP_TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
  echo "UNAI_STARTUP_TIMEOUT_SECONDS must be a positive integer."
  tmux kill-session -t unai 2>/dev/null || true
  exit 1
fi

wait_for_service() {
  local label="$1"
  local window="$2"
  local url="$3"
  local attempt

  echo "Waiting for $label ($url)..."
  for ((attempt = 1; attempt <= STARTUP_TIMEOUT; attempt++)); do
    if curl --fail --silent --show-error --max-time 2 "$url" >/dev/null 2>&1; then
      echo "$label is ready."
      return 0
    fi
    sleep 1
  done

  echo "$label did not become ready within ${STARTUP_TIMEOUT}s."
  echo "Recent output from tmux window '$window':"
  tmux capture-pane -pt "unai:$window" -S -12 2>/dev/null | tail -n 8 || true
  return 1
}

ALL_READY=true
wait_for_service "model server" "model" "http://127.0.0.1:8080/health" || ALL_READY=false
wait_for_service "Engine API" "engine" "http://127.0.0.1:8000/api/health" || ALL_READY=false
wait_for_service "Web UI" "web" "http://127.0.0.1:5173/" || ALL_READY=false

echo
echo "Web UI:  http://127.0.0.1:5173"
echo "Engine:  http://127.0.0.1:8000/api/health"
echo "Model:   http://127.0.0.1:8080/health"
if [[ "$ALL_READY" == true ]]; then
  echo "All three local services responded. Run: bash scripts/termux-doctor.sh --smoke-chat"
else
  echo "At least one service is not ready. Inspect the tmux windows and run: bash scripts/termux-doctor.sh"
fi
echo "Detach: Ctrl+B, then D | Stop: tmux kill-session -t unai"
tmux select-window -t unai:web
exec tmux attach-session -t unai
