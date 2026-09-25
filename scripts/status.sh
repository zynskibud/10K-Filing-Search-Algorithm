#!/usr/bin/env bash
# Print the machine and project state: heavy lock owner, running wave runners,
# the last progress line of each console log, the Postgres container, and disk.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AI_ENGINEERING_ROOT="$(cd "$REPO_ROOT/.." && pwd)"
LOCK_DIR="$AI_ENGINEERING_ROOT/.coord/heavy.lock"

echo "Citation RAG status, $(date)"
echo

echo "Heavy lock:"
if [ -d "$LOCK_DIR" ]; then
  echo "  HELD by: $(cat "$LOCK_DIR/owner" 2>/dev/null || echo "(no owner file)")"
else
  echo "  free"
fi

echo
echo "Wave runners (scripts/run.sh --_runner):"
runners="$(pgrep -fl 'scripts/run.sh --_runner' 2>/dev/null)"
if [ -n "$runners" ]; then printf '%s\n' "$runners" | sed 's/^/  /'; else echo "  none"; fi

echo
echo "Project python processes (citation_rag.*):"
procs="$(pgrep -fl 'citation_rag' 2>/dev/null | grep -v pgrep)"
if [ -n "$procs" ]; then printf '%s\n' "$procs" | cut -c1-140 | sed 's/^/  /'; else echo "  none"; fi

echo
echo "Console logs (last line each):"
found=0
for log in "$REPO_ROOT"/runs/wave-*/console.log; do
  [ -f "$log" ] || continue
  found=1
  printf '  %s\n    %s\n' "${log#$REPO_ROOT/}" "$(tail -1 "$log" | cut -c1-160)"
done
[ "$found" -eq 1 ] || echo "  none"

echo
echo "Ollama loaded models:"
ollama ps 2>/dev/null | sed 's/^/  /' || echo "  (ollama not reachable)"

echo
echo "Postgres container:"
docker inspect citation-rag-db --format '  {{.Name}} {{.State.Status}} health={{.State.Health.Status}} mem={{.HostConfig.Memory}}' 2>/dev/null || echo "  citation-rag-db not found"

echo
echo "Disk:"
df -h / | awk 'NR==2 {print "  free " $4 " of " $2}'
