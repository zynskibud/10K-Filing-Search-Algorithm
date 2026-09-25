# Runbook: how heavy work runs on this machine

The machine is shared. One HEAVY job runs at a time, under the coordinator's lock at `../.coord/heavy.lock` (see `../.coord/PROTOCOL.md`). HEAVY means any Ollama inference, any embedding run, any benchmark timing run, any container over 4 GB, or any model pull over 5 GB.

## Rules

1. **Waves 4 to 8 run only through `scripts/run.sh <wave>`.** Never start an embedding run, a Qwen run, or the judge with a bare `uv run` command. `run.sh` runs the preflight, takes the lock, runs the wave detached under `caffeinate -i`, and releases the lock when the wave ends or is killed.
2. **Each HEAVY wave waits for the coordinator's GO** before `run.sh` is called.
3. **LIGHT work** (editing code, unit tests, downloads under 5 GB, parsing) does not take the lock. While a Vector Retrieval timing run holds the lock, LIGHT work also stops.
4. **Stop for the human** only for: disk under 15 GB free, spend over $5, or a failed isolation check.

## Commands

| Command | What it does |
|---|---|
| `scripts/preflight.sh` | Checks load, disk, lock, Ollama, the Postgres container, and power. Exits 1 on disk under 15 GB or a lock held by another owner. |
| `scripts/run.sh --dry-run <wave>` | Preflight, take and release the lock, print what would run. |
| `scripts/run.sh <wave> [command]` | Preflight, then start the wave detached. The command comes from `scripts/waves.conf` when not given. Log: `runs/wave-<wave>/console.log`. |
| `scripts/status.sh` | Lock owner, running wave runners, last log line per wave, Ollama models loaded, container health, disk. |

## Lock format

```
mkdir ../.coord/heavy.lock && echo "citation-rag <wave> <ISO time>" > ../.coord/heavy.lock/owner
```

`run.sh` releases only a lock whose owner line starts with `citation-rag <wave>`. It never removes another project's lock.

## Isolation

- Python runs in the project `.venv`. Models load from the project `.cache/hf` with `HF_HUB_OFFLINE=1`.
- Postgres runs in the container `citation-rag-db` on port 5433 with a 2 GB memory limit.
- No writes outside the project folder, except the Docker named volume.
