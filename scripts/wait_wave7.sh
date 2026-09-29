#!/usr/bin/env bash
# Wave 7 waiter (coordinator conditions): all locks free, free disk >= 30 GB,
# no Vector container running, and wave 4w finished successfully (4w rebuilds
# the chunks table that wave 7 reads). Then scripts/run.sh 7.
set -u
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; C="$R/../.coord"
ok() {
  [ ! -d "$C/gpu.lock" ] && [ ! -d "$C/heavy.lock" ] && [ ! -d "$C/timing.lock" ] || return 1
  [ "$(df -g / | awk 'NR==2{print $4}')" -ge 30 ] || return 1
  ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qi vector || return 1
  grep -q "end wave 4w exit=0" "$R/runs/wave-4w/console.log" 2>/dev/null || return 1
  ! pgrep -f "scripts/run.sh --_runner" >/dev/null || return 1
}
until ok; do sleep 120; done
echo "$(date) conditions met; starting wave 7"
"$R/scripts/run.sh" 7
