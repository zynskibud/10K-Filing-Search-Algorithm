#!/usr/bin/env bash
# Corpus parse launcher. Fixed caps from the protocol: at most 4 workers,
# low priority. Usage: scripts/parse.sh [extra args for citation_rag.parse.run]
set -u
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" && exec nice -n 10 uv run python -m citation_rag.parse.run \
  --manifest data/raw/corpus.jsonl --out data/parsed --workers 4 "$@"
