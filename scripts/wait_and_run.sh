#!/usr/bin/env bash
# Wait until gpu.lock, heavy.lock, and timing.lock are free, then start scripts/run.sh <wave>.
# Detached use: nohup scripts/wait_and_run.sh 4w > runs/waiter-4w.log 2>&1 &
set -u
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
C="$REPO_ROOT/../.coord"
W="${1:?wave}"
while [ -d "$C/gpu.lock" ] || [ -d "$C/heavy.lock" ] || [ -d "$C/timing.lock" ] || { [ -s "$C/gpu.next" ] && ! grep -qi "^citation" "$C/gpu.next"; }; do sleep 120; done
echo "$(date) locks free; starting wave $W"
"$REPO_ROOT/scripts/run.sh" "$W"
