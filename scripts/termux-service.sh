#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE_DIR="$ROOT/apps/engine"
WEB_DIR="$ROOT/apps/web"
LLAMA_CPP_DIR="${UNAI_LLAMA_CPP_DIR:-$HOME/src/llama.cpp}"
MODEL_DIR="${UNAI_MODEL_DIR:-$HOME/models/unai}"
MODEL_FILE="${UNAI_MODEL_FILE:-$MODEL_DIR/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf}"
LLAMA_SERVER_BIN="${UNAI_LLAMA_SERVER_BIN:-$LLAMA_CPP_DIR/build/bin/llama-server}"
SERVICE="${1:-}"

usage() {
  echo "Usage: bash scripts/termux-service.sh {model|engine|web}"
}

case "$SERVICE" in
  model)
    if [[ ! -x "$LLAMA_SERVER_BIN" || ! -s "$MODEL_FILE" ]]; then
      echo "Model runtime or weights are missing; run scripts/termux-install.sh." >&2
      exit 1
    fi
    set -- "$LLAMA_SERVER_BIN" -m "$MODEL_FILE" \
      --host 127.0.0.1 --port 8080 --alias unai-local-coder \
      --ctx-size 2048 --threads "${UNAI_CPU_THREADS:-4}" --batch-size 128
    ;;
  engine)
    if [[ ! -x "$ENGINE_DIR/.venv/bin/uvicorn" ]]; then
      echo "Engine virtual environment is missing; run scripts/termux-install.sh." >&2
      exit 1
    fi
    set -- "$ENGINE_DIR/.venv/bin/uvicorn" unai_engine.main:app \
      --host 127.0.0.1 --port 8000
    cd "$ENGINE_DIR"
    ;;
  web)
    if [[ ! -x "$WEB_DIR/node_modules/.bin/vite" ]]; then
      echo "Web dependencies are missing; run scripts/termux-install.sh." >&2
      exit 1
    fi
    set -- npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
    cd "$WEB_DIR"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

exec bash "$ROOT/scripts/termux-supervise.sh" "$SERVICE" -- "$@"
