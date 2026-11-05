#!/usr/bin/env bash
set -uo pipefail

usage() {
  echo "Usage: bash scripts/termux-supervise.sh SERVICE -- COMMAND [ARG ...]"
  echo "Restarts a long-running local service after unexpected non-zero exits."
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
if (( $# < 3 )) || [[ "$2" != "--" ]]; then
  usage >&2
  exit 2
fi

SERVICE="$1"
shift 2
if [[ ! "$SERVICE" =~ ^[A-Za-z0-9_-]{1,32}$ ]]; then
  echo "Service name must contain only letters, numbers, '_' or '-'."
  exit 2
fi
if (( $# == 0 )); then
  usage >&2
  exit 2
fi

MAX_RESTARTS="${UNAI_MAX_RESTARTS:-5}"
BASE_DELAY="${UNAI_RESTART_DELAY_SECONDS:-2}"
MAX_DELAY="${UNAI_MAX_RESTART_DELAY_SECONDS:-30}"
LOG_MAX_BYTES="${UNAI_LOG_MAX_BYTES:-1048576}"
LOG_DIR="${UNAI_LOG_DIR:-$HOME/.local/state/unai}"

if ! [[ "$MAX_RESTARTS" =~ ^[0-9]+$ ]] ||
   ! [[ "$BASE_DELAY" =~ ^[1-9][0-9]*$ ]] ||
   ! [[ "$MAX_DELAY" =~ ^[1-9][0-9]*$ ]] ||
   ! [[ "$LOG_MAX_BYTES" =~ ^[1-9][0-9]*$ ]] ||
   (( MAX_DELAY < BASE_DELAY )); then
  echo "Invalid restart configuration: use non-negative MAX_RESTARTS and positive delay/log limits; max delay must be >= base delay."
  exit 2
fi

if ! mkdir -p "$LOG_DIR"; then
  echo "Unable to create service log directory: $LOG_DIR" >&2
  exit 1
fi

LOG_FILE="$LOG_DIR/$SERVICE.log"
if [[ -f "$LOG_FILE" ]]; then
  CURRENT_LOG_BYTES="$(wc -c < "$LOG_FILE" | tr -d '[:space:]')"
  if [[ "$CURRENT_LOG_BYTES" =~ ^[0-9]+$ ]] && (( CURRENT_LOG_BYTES > LOG_MAX_BYTES )); then
    mv -f "$LOG_FILE" "$LOG_FILE.1"
  fi
fi

if ! command -v tee >/dev/null 2>&1; then
  echo "tee is required to mirror service output to tmux and its log file." >&2
  exit 1
fi
exec > >(tee -a "$LOG_FILE") 2>&1

CHILD_PID=""
stop_child() {
  local signal="$1"
  local exit_code="$2"
  trap - TERM INT HUP
  echo "[$SERVICE] received $signal; forwarding shutdown to child process."
  if [[ -n "$CHILD_PID" ]]; then
    kill -s "$signal" "$CHILD_PID" 2>/dev/null || true
    wait "$CHILD_PID" 2>/dev/null || true
    CHILD_PID=""
  fi
  exit "$exit_code"
}
trap 'stop_child TERM 143' TERM
trap 'stop_child INT 130' INT
trap 'stop_child HUP 129' HUP

echo "[$SERVICE] supervisor started; maximum automatic restarts: $MAX_RESTARTS"
echo "[$SERVICE] persistent log: $LOG_FILE"

RESTARTS=0
DELAY="$BASE_DELAY"
while true; do
  echo "[$SERVICE] starting process (restart count: $RESTARTS)"
  "$@" &
  CHILD_PID=$!
  wait "$CHILD_PID"
  EXIT_CODE=$?
  CHILD_PID=""

  if (( EXIT_CODE == 0 )); then
    echo "[$SERVICE] process exited cleanly; supervisor stopping."
    exit 0
  fi

  if (( RESTARTS >= MAX_RESTARTS )); then
    echo "[$SERVICE] restart limit reached after $RESTARTS retries; last exit code: $EXIT_CODE"
    exit "$EXIT_CODE"
  fi

  RESTARTS=$((RESTARTS + 1))
  echo "[$SERVICE] process exited with code $EXIT_CODE; restarting in ${DELAY}s (retry $RESTARTS/$MAX_RESTARTS)."
  sleep "$DELAY"
  DELAY=$((DELAY * 2))
  if (( DELAY > MAX_DELAY )); then
    DELAY="$MAX_DELAY"
  fi
done
