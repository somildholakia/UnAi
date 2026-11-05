#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
START_SCRIPT="$ROOT/scripts/termux-start.sh"
SERVICE_LAUNCHER="$ROOT/scripts/termux-service.sh"
MODEL_URL="${UNAI_MODEL_URL:-http://127.0.0.1:8080}"
ENGINE_URL="${UNAI_ENGINE_URL:-http://127.0.0.1:8000}"
WEB_URL="${UNAI_WEB_URL:-http://127.0.0.1:5173}"
STARTUP_TIMEOUT="${UNAI_RECOVERY_TIMEOUT_SECONDS:-30}"
SESSION="unai"
SERVICES=(model engine web)
PENDING=0

usage() {
  echo "Usage:"
  echo "  bash scripts/termux-control.sh status"
  echo "  bash scripts/termux-control.sh recover [model|engine|web|all]"
  echo "Recovery only respawns dead/missing tmux panes; it never replaces a healthy service or a live supervisor."
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
ACTION="${1:-}"
TARGET="${2:-all}"
if [[ "$ACTION" != "status" && "$ACTION" != "recover" ]]; then
  usage >&2
  exit 2
fi
if [[ "$ACTION" == "status" && $# -ne 1 ]] || [[ "$ACTION" == "recover" && $# -gt 2 ]]; then
  usage >&2
  exit 2
fi
if [[ "$ACTION" == "recover" && "$TARGET" != "all" && "$TARGET" != "model" && "$TARGET" != "engine" && "$TARGET" != "web" ]]; then
  usage >&2
  exit 2
fi
if ! [[ "$STARTUP_TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
  echo "UNAI_RECOVERY_TIMEOUT_SECONDS must be a positive integer." >&2
  exit 2
fi
if ! command -v tmux >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
  echo "tmux and curl are required; run inside the installed Termux environment." >&2
  exit 1
fi
if [[ ! -f "$SERVICE_LAUNCHER" || ! -f "$ROOT/scripts/termux-supervise.sh" ]]; then
  echo "Service launcher/supervisor missing; update the repository before recovery." >&2
  exit 1
fi

service_url() {
  case "$1" in
    model) echo "$MODEL_URL/health" ;;
    engine) echo "$ENGINE_URL/api/health" ;;
    web) echo "$WEB_URL/" ;;
  esac
}

is_healthy() {
  local service="$1"
  curl --fail --silent --show-error --max-time 2 "$(service_url "$service")" >/dev/null 2>&1
}

has_window() {
  local service="$1"
  local windows
  windows="$(tmux list-windows -t "$SESSION" -F '#{window_name}' 2>/dev/null)" || return 1
  grep -Fxq "$service" <<< "$windows"
}

pane_is_dead() {
  local service="$1"
  local dead
  dead="$(tmux display-message -p -t "$SESSION:$service" '#{pane_dead}' 2>/dev/null || echo 1)"
  [[ "$dead" == "1" ]]
}

service_command() {
  local service="$1"
  printf '%q ' bash "$SERVICE_LAUNCHER" "$service"
}

show_status() {
  local session_alive=false
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    session_alive=true
  fi
  echo "unAI local service status"
  echo "tmux session: $session_alive"
  local failures=0
  local service
  for service in "${SERVICES[@]}"; do
    local pane_state="missing"
    if [[ "$session_alive" == true ]] && has_window "$service"; then
      if pane_is_dead "$service"; then
        pane_state="dead"
      else
        pane_state="alive"
      fi
    fi
    if is_healthy "$service"; then
      echo "  [ok] $service endpoint healthy; tmux pane: $pane_state"
    else
      failures=$((failures + 1))
      if [[ "$pane_state" == "alive" ]]; then
        echo "  [wait] $service endpoint unavailable; supervisor pane is alive and may be retrying"
      else
        echo "  [down] $service endpoint unavailable; tmux pane: $pane_state"
      fi
    fi
  done
  (( failures == 0 ))
}

recover_one() {
  local service="$1"
  local command
  command="$(service_command "$service")"

  # A responding endpoint wins over tmux state: a dead pane must never cause
  # a second listener to be started on a port that is already serving traffic.
  if is_healthy "$service"; then
    echo "[$service] already healthy; leaving its process untouched."
    return 0
  fi

  if has_window "$service"; then
    if ! pane_is_dead "$service"; then
      echo "[$service] tmux pane is alive but endpoint is not ready; leaving the existing supervisor to retry."
      PENDING=1
      return 0
    fi
    echo "[$service] respawning its dead tmux pane."
    tmux respawn-pane -k -t "$SESSION:$service" "$command"
    tmux set-window-option -t "$SESSION:$service" remain-on-exit on
  else
    echo "[$service] creating a missing tmux service window."
    tmux new-window -d -t "$SESSION" -n "$service" "$command"
    tmux set-window-option -t "$SESSION:$service" remain-on-exit on
  fi

  local attempt
  for ((attempt = 1; attempt <= STARTUP_TIMEOUT; attempt++)); do
    if is_healthy "$service"; then
      echo "[$service] recovered and responding."
      return 0
    fi
    sleep 1
  done

  echo "[$service] still unavailable after ${STARTUP_TIMEOUT}s; inspect its tmux pane and supervisor log."
  PENDING=1
  return 0
}

if [[ "$ACTION" == "status" ]]; then
  show_status
  exit $?
fi

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  any_healthy=false
  for service in "${SERVICES[@]}"; do
    if is_healthy "$service"; then
      any_healthy=true
    fi
  done
  if [[ "$any_healthy" == true ]]; then
    echo "Some local services are still reachable but the tmux session is missing."
    echo "Refusing to launch the entire stack because that could create duplicate listeners."
    exit 1
  fi
  echo "No tmux session or healthy service found; starting the complete local stack."
  exec bash "$START_SCRIPT"
fi

if [[ "$TARGET" == "all" ]]; then
  for service in "${SERVICES[@]}"; do
    recover_one "$service"
  done
else
  recover_one "$TARGET"
fi

if (( PENDING > 0 )); then
  echo "Recovery is pending; check status and inspect logs before retrying."
  exit 1
fi
echo "Requested service recovery checks completed."
