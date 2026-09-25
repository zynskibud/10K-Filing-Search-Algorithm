#!/usr/bin/env bash
# Preflight checks before a HEAVY wave on this shared machine.
#
# Prints one line per check. Exits 1 on a hard failure: disk under 15 GB free,
# or the heavy lock held by someone else. Every other check prints WARN and
# lets the run start; a human decides what to do about a warning.
#
# This script never starts or stops Ollama, Docker, or a container. It only looks.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AI_ENGINEERING_ROOT="$(cd "$REPO_ROOT/.." && pwd)"
LOCK_DIR="$AI_ENGINEERING_ROOT/.coord/heavy.lock"
DISK_FAIL_GB=15
LOAD_WARN=2
HARD_FAIL=0

ok()   { printf 'OK    %s\n' "$1"; }
warn() { printf 'WARN  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; HARD_FAIL=1; }

echo "Preflight for Citation RAG, $(date)"
echo "Repo: $REPO_ROOT"
echo

# Load average over the last minute.
load="$(sysctl -n vm.loadavg 2>/dev/null | awk '{print $2}')"
if [ -n "$load" ] && awk -v l="$load" -v w="$LOAD_WARN" 'BEGIN{exit !(l < w)}'; then
  ok "load average $load (under $LOAD_WARN)"
else
  warn "load average ${load:-unknown} (at or over $LOAD_WARN): another job is busy"
fi

# Free disk on the root volume, in GB.
free_gb="$(df -g / | awk 'NR==2 {print $4}')"
if [ -n "$free_gb" ] && [ "$free_gb" -ge "$DISK_FAIL_GB" ]; then
  ok "disk free ${free_gb} GB"
else
  fail "disk free ${free_gb:-unknown} GB, under the ${DISK_FAIL_GB} GB floor"
fi

# Heavy lock. Held by us is fine (a re-run of the same wave). Held by anyone
# else is a hard stop; the protocol allows one HEAVY job at a time.
if [ -d "$LOCK_DIR" ]; then
  owner="$(cat "$LOCK_DIR/owner" 2>/dev/null || echo "(no owner file)")"
  case "$owner" in
    citation-rag*) warn "heavy lock held by this project: $owner" ;;
    *)             fail "heavy lock held by another owner: $owner" ;;
  esac
else
  ok "heavy lock free"
fi

# Ollama daemon and the models this project uses.
tags_json="$(curl -s --max-time 2 http://127.0.0.1:11434/api/tags 2>/dev/null)"
if [ -n "$tags_json" ]; then
  ok "Ollama daemon up"
  for m in "qwen3:8b" "gpt-oss:20b"; do
    if printf '%s' "$tags_json" | grep -q "\"$m\""; then ok "$m present"; else warn "$m not pulled"; fi
  done
else
  warn "Ollama daemon not reachable on 127.0.0.1:11434"
fi

# Postgres container.
health="$(docker inspect citation-rag-db --format '{{.State.Health.Status}}' 2>/dev/null)"
case "$health" in
  healthy) ok "citation-rag-db healthy" ;;
  "")      warn "citation-rag-db not found (docker compose up -d db)" ;;
  *)       warn "citation-rag-db status: $health" ;;
esac

# Power. Long runs on battery are a bad idea.
if pmset -g batt 2>/dev/null | head -1 | grep -q "AC Power"; then
  ok "on AC power"
else
  warn "not on AC power"
fi

echo
if [ "$HARD_FAIL" -eq 1 ]; then
  echo "PREFLIGHT FAILED"
  exit 1
fi
echo "PREFLIGHT OK"
exit 0
