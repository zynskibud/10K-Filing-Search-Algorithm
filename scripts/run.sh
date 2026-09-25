#!/usr/bin/env bash
# Run one HEAVY wave under the coordinator's heavy lock, detached, with the
# machine kept awake.
#
# Usage: scripts/run.sh [--dry-run] <wave> [command ...]
#
#   <wave>     a name such as 4, 5a, 7. With no command, the command comes from
#              scripts/waves.conf (one line per wave: "<wave>=<command>").
#   --dry-run  take and release the lock, print what would run, run nothing.
#
# Steps: preflight (stop on failure), then start a detached runner under
# `caffeinate -i` and `nohup`. The runner takes the lock with owner
# "citation-rag <wave> <ISO time>", runs the command, and always releases the
# lock on exit, INT, or TERM. The console log is runs/wave-<wave>/console.log.
# Watch it with scripts/status.sh.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AI_ENGINEERING_ROOT="$(cd "$REPO_ROOT/.." && pwd)"
LOCK_DIR="$AI_ENGINEERING_ROOT/.coord/heavy.lock"
LOCK_OWNER_FILE="$LOCK_DIR/owner"
WAVES_CONF="$REPO_ROOT/scripts/waves.conf"

usage() { echo "usage: scripts/run.sh [--dry-run] <wave> [command ...]" >&2; exit 2; }

DRY_RUN=0
RUNNER=0
WAVE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --_runner) RUNNER=1; shift ;;
    -h|--help) usage ;;
    *) WAVE="$1"; shift; break ;;
  esac
done
[ -n "$WAVE" ] || usage
COMMAND="$*"

if [ -z "$COMMAND" ]; then
  if [ -f "$WAVES_CONF" ]; then
    COMMAND="$(grep -E "^${WAVE}=" "$WAVES_CONF" | head -1 | cut -d= -f2-)"
  fi
  if [ -z "$COMMAND" ]; then
    echo "refusing: no command for wave '$WAVE' in $WAVES_CONF and none given" >&2
    exit 2
  fi
fi

RUN_DIR="$REPO_ROOT/runs/wave-$WAVE"
LOG="$RUN_DIR/console.log"
mkdir -p "$RUN_DIR"

take_lock() {
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "heavy lock is held. Owner:" >&2
    cat "$LOCK_OWNER_FILE" >&2 2>/dev/null
    return 3
  fi
  echo "citation-rag $WAVE $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$LOCK_OWNER_FILE"
  return 0
}

release_lock() {
  # Release only a lock this project holds. Never remove someone else's.
  if [ -f "$LOCK_OWNER_FILE" ] && grep -q "^citation-rag $WAVE " "$LOCK_OWNER_FILE" 2>/dev/null; then
    rm -rf "$LOCK_DIR"
  fi
}

# ---------------------------------------------------------------- runner ----
if [ "$RUNNER" -eq 1 ]; then
  take_lock || exit 3
  trap release_lock EXIT INT TERM HUP
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) start wave $WAVE pid $$: $COMMAND"
  ( cd "$REPO_ROOT" && eval "$COMMAND" )
  code=$?
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) end wave $WAVE exit=$code"
  exit "$code"
fi

# --------------------------------------------------------------- wrapper ----
echo "wave: $WAVE"
echo "command: $COMMAND"
echo

if ! "$REPO_ROOT/scripts/preflight.sh"; then
  echo "refusing: preflight failed" >&2
  exit 1
fi
echo

if [ "$DRY_RUN" -eq 1 ]; then
  take_lock || exit 3
  echo "[dry run] lock acquired: $(cat "$LOCK_OWNER_FILE")"
  echo "[dry run] would run detached under caffeinate -i, log: $LOG"
  release_lock
  echo "[dry run] lock released"
  exit 0
fi

if [ -d "$LOCK_DIR" ]; then
  echo "refusing: heavy lock is held. Owner: $(cat "$LOCK_OWNER_FILE" 2>/dev/null)" >&2
  exit 3
fi

nohup caffeinate -i "$REPO_ROOT/scripts/run.sh" --_runner "$WAVE" "$COMMAND" >> "$LOG" 2>&1 &
pid=$!
echo "$pid" > "$RUN_DIR/runner.pid"
sleep 1
if kill -0 "$pid" 2>/dev/null; then
  echo "started wave $WAVE detached, pid $pid"
  echo "log: $LOG"
  echo "status: scripts/status.sh"
else
  echo "runner exited at once; last log lines:" >&2
  tail -5 "$LOG" >&2
  exit 1
fi
