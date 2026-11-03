#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE_DIR="$ROOT/apps/engine"
WEB_DIR="$ROOT/apps/web"
LLAMA_CPP_DIR="${UNAI_LLAMA_CPP_DIR:-$HOME/src/llama.cpp}"
MODEL_DIR="${UNAI_MODEL_DIR:-$HOME/models/unai}"
MODEL_FILE="${UNAI_MODEL_FILE:-$MODEL_DIR/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf}"
MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF/resolve/main/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"
BUILD_JOBS="${UNAI_BUILD_JOBS:-2}"

if [[ -z "${PREFIX:-}" || ! -d "$PREFIX" ]]; then
  echo "Run this installer inside Termux on Android."
  echo "For desktop setup, follow docs/LOCAL_INSTALL.md."
  exit 1
fi

echo "[1/5] Installing Termux packages..."
pkg update -y
pkg install -y python nodejs-lts git cmake clang make pkg-config libandroid-spawn \
  curl tmux rust openssl libffi libyaml

echo "[2/5] Building llama.cpp server (CPU path)..."
mkdir -p "$(dirname "$LLAMA_CPP_DIR")" "$MODEL_DIR"
if [[ ! -d "$LLAMA_CPP_DIR/.git" ]]; then
  git clone --depth 1 https://github.com/ggml-org/llama.cpp.git "$LLAMA_CPP_DIR"
fi
cmake -S "$LLAMA_CPP_DIR" -B "$LLAMA_CPP_DIR/build" \
  -DCMAKE_BUILD_TYPE=Release -DGGML_OPENMP=OFF -DGGML_LLAMAFILE=OFF
cmake --build "$LLAMA_CPP_DIR/build" --config Release -j "$BUILD_JOBS" --target llama-server
LLAMA_SERVER="$LLAMA_CPP_DIR/build/bin/llama-server"
if [[ ! -x "$LLAMA_SERVER" ]]; then
  echo "llama-server build did not produce $LLAMA_SERVER"
  exit 1
fi

echo "[3/5] Downloading the compact local coding model..."
MODEL_BYTES=0
if [[ -f "$MODEL_FILE" ]]; then
  MODEL_BYTES="$(wc -c < "$MODEL_FILE" | tr -d ' ')"
fi
if [[ "$MODEL_BYTES" -lt 1000000000 ]]; then
  curl --fail --location --retry 3 --retry-delay 2 --continue-at - \
    "$MODEL_URL" --output "$MODEL_FILE"
fi
MODEL_BYTES="$(wc -c < "$MODEL_FILE" | tr -d ' ')"
if [[ "$MODEL_BYTES" -lt 1000000000 ]]; then
  echo "Model file looks incomplete ($MODEL_BYTES bytes): $MODEL_FILE"
  echo "Re-run this installer to resume the download."
  exit 1
fi
echo "Model ready: $MODEL_FILE ($MODEL_BYTES bytes)"

echo "[4/5] Installing the local Engine and Web UI..."
if [[ ! -f "$ENGINE_DIR/.env" ]]; then
  cp "$ENGINE_DIR/.env.example" "$ENGINE_DIR/.env"
else
  sed -i 's|^UNAI_MODEL_BASE_URL=http://127.0.0.1:11434/v1$|UNAI_MODEL_BASE_URL=http://127.0.0.1:8080/v1|' "$ENGINE_DIR/.env"
  sed -i 's|^UNAI_MODEL_NAME=qwen3-coder:latest$|UNAI_MODEL_NAME=unai-local-coder|' "$ENGINE_DIR/.env"
fi
python -m venv "$ENGINE_DIR/.venv"
"$ENGINE_DIR/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"$ENGINE_DIR/.venv/bin/pip" install -e "$ENGINE_DIR"
npm --prefix "$WEB_DIR" install --no-audit --no-fund

echo "[5/5] Installation complete."
echo "Next: bash scripts/termux-start.sh"
echo "Then open http://127.0.0.1:5173 in the Android browser."
echo "Model weights are stored locally; first setup needs about 1.2 GB of free storage."
