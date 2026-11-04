#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE_DIR="$ROOT/apps/engine"
WEB_DIR="$ROOT/apps/web"
LLAMA_CPP_DIR="${UNAI_LLAMA_CPP_DIR:-$HOME/src/llama.cpp}"
MODEL_DIR="${UNAI_MODEL_DIR:-$HOME/models/unai}"
MODEL_FILE="${UNAI_MODEL_FILE:-$MODEL_DIR/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf}"
LLAMA_SERVER_BIN="${UNAI_LLAMA_SERVER_BIN:-$LLAMA_CPP_DIR/build/bin/llama-server}"
MODEL_URL="${UNAI_MODEL_URL:-http://127.0.0.1:8080}"
ENGINE_URL="${UNAI_ENGINE_URL:-http://127.0.0.1:8000}"
WEB_URL="${UNAI_WEB_URL:-http://127.0.0.1:5173}"
MODEL_NAME="${UNAI_MODEL_NAME:-unai-local-coder}"
MIN_MODEL_BYTES="${UNAI_MIN_MODEL_BYTES:-1000000000}"
PROBE_TIMEOUT="${UNAI_PROBE_TIMEOUT_SECONDS:-3}"
SMOKE_CHAT=false
FAILURES=0

usage() {
  echo "Usage: bash scripts/termux-doctor.sh [--smoke-chat]"
  echo "Checks the local Termux installation and the model, Engine and Web UI endpoints."
  echo "--smoke-chat sends one tiny local completion request to verify model inference."
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
if [[ "${1:-}" == "--smoke-chat" ]]; then
  SMOKE_CHAT=true
elif [[ -n "${1:-}" ]]; then
  usage >&2
  exit 2
fi
if ! [[ "$MIN_MODEL_BYTES" =~ ^[0-9]+$ ]] || ! [[ "$PROBE_TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
  echo "UNAI_MIN_MODEL_BYTES and UNAI_PROBE_TIMEOUT_SECONDS must be positive integers." >&2
  exit 2
fi

pass() { printf '  [ok] %s\n' "$1"; }
fail() {
  printf '  [FAIL] %s\n' "$1"
  FAILURES=$((FAILURES + 1))
}
warn() { printf '  [warn] %s\n' "$1"; }

echo "unAI local runtime doctor"
echo "Repository: $ROOT"

if [[ -n "${PREFIX:-}" && -d "$PREFIX" ]]; then
  pass "Termux environment detected"
else
  fail "Termux PREFIX is missing; run this command inside Termux"
fi

for command_name in curl python node npm tmux; do
  if command -v "$command_name" >/dev/null 2>&1; then
    pass "command: $command_name"
  else
    fail "missing command: $command_name"
  fi
done

if [[ -x "$LLAMA_SERVER_BIN" ]]; then
  pass "llama-server executable found"
else
  fail "llama-server missing or not executable: $LLAMA_SERVER_BIN"
fi

if [[ -f "$MODEL_FILE" ]]; then
  MODEL_BYTES="$(wc -c < "$MODEL_FILE" | tr -d '[:space:]')"
  if [[ "$MODEL_BYTES" =~ ^[0-9]+$ ]] && (( MODEL_BYTES >= MIN_MODEL_BYTES )); then
    pass "model file size: $MODEL_BYTES bytes"
  else
    fail "model file is incomplete: $MODEL_BYTES bytes (minimum $MIN_MODEL_BYTES)"
  fi
else
  fail "model file not found: $MODEL_FILE"
fi

if [[ -x "$ENGINE_DIR/.venv/bin/uvicorn" ]]; then
  pass "Engine virtual environment found"
else
  fail "Engine virtual environment is missing; run scripts/termux-install.sh"
fi

if [[ -x "$WEB_DIR/node_modules/.bin/vite" ]]; then
  pass "Web dependencies found"
else
  fail "Web dependencies are missing; run scripts/termux-install.sh"
fi

probe() {
  local label="$1"
  local url="$2"
  if curl --fail --silent --show-error --max-time "$PROBE_TIMEOUT" "$url" >/dev/null 2>&1; then
    pass "$label reachable: $url"
    return 0
  fi
  fail "$label unreachable: $url"
  return 1
}

echo
echo "Service checks"
probe "Model server" "$MODEL_URL/health" || true
probe "Engine API" "$ENGINE_URL/api/health" || true
probe "Web UI" "$WEB_URL/" || true

if [[ "$SMOKE_CHAT" == true ]]; then
  echo
  echo "Local inference smoke test"
  if ! command -v python >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
    fail "python and curl are required for the inference smoke test"
  else
    RESPONSE=""
    if RESPONSE="$(curl --fail --silent --show-error --max-time "${UNAI_SMOKE_TIMEOUT_SECONDS:-180}" \
      -H 'Content-Type: application/json' \
      -d '{"model":"'"$MODEL_NAME"'","messages":[{"role":"user","content":"Reply with the single word READY."}],"max_tokens":8,"temperature":0}' \
      "$MODEL_URL/v1/chat/completions")"; then
      if printf '%s' "$RESPONSE" | python -c '
import json
import sys
try:
    payload = json.load(sys.stdin)
    choices = payload.get("choices")
    content = choices[0]["message"]["content"] if isinstance(choices, list) and choices else ""
    valid = isinstance(content, str) and bool(content.strip())
except (json.JSONDecodeError, KeyError, TypeError, IndexError):
    valid = False
raise SystemExit(0 if valid else 1)
'; then
        pass "local model returned a non-empty completion"
      else
        fail "model endpoint responded, but its completion payload was invalid or empty"
      fi
    else
      fail "local model completion request failed"
    fi
  fi
fi

echo
if (( FAILURES == 0 )); then
  echo "All requested checks passed."
  exit 0
fi
echo "$FAILURES check(s) failed. Inspect the model, engine and web tmux windows, then rerun this doctor."
exit 1
