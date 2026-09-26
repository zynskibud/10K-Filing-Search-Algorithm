# Runbook: how heavy work runs on this machine

The machine is shared. One HEAVY job runs at a time, under the coordinator's GPU lock at `../.coord/gpu.lock` (formerly `heavy.lock`) (see `../.coord/PROTOCOL.md`). HEAVY means any Ollama inference, any embedding run, any benchmark timing run, any container over 4 GB, or any model pull over 5 GB.

## Rules

1. **Waves 4 to 8 run only through `scripts/run.sh <wave>`.** Never start an embedding run, a Qwen run, or the judge with a bare `uv run` command. `run.sh` runs the preflight, takes the lock, runs the wave detached under `caffeinate -i`, and releases the lock when the wave ends or is killed.
2. **Each HEAVY wave waits for the coordinator's GO** before `run.sh` is called.
3. **Resource classes** (from the protocol): GPU (Ollama, embedding, MPS) one at a time under `gpu.lock` after GO; TIMING (Vector latency benchmarks) alone on the machine under `timing.lock`, during which only API work is allowed; CPU-BULK (parsing, index builds, test suites over one core) at most 4 workers, never during TIMING; LIGHT (code edits, one-core unit tests, subagents writing code, git) always, except during TIMING.
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
mkdir ../.coord/gpu.lock && echo "citation-rag <wave> <ISO time>" > ../.coord/gpu.lock/owner
```

`run.sh` releases only a lock whose owner line starts with `citation-rag <wave>`. It never removes another project's lock.

## Isolation

- Python runs in the project `.venv`. Models load from the project `.cache/hf` with `HF_HUB_OFFLINE=1`.
- Postgres runs in the container `citation-rag-db` on port 5433 with a 2 GB memory limit.
- No writes outside the project folder, except the Docker named volume.

## Environment

- Default environment: `uv sync --extra dev` (pytest and ruff live in the `dev` extra). A plain `uv sync` removes them.
- Framework comparison only: `uv run --group langgraph ...` installs LangGraph and LangChain into the same venv for that command. Run `uv sync --extra dev` afterwards to return to the framework-free state.
