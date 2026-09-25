# Wave 0 contract: environment and skeleton

Owner: orchestrator session citation-rag-41. Implementer: Haiku subagent.
Project root: /Users/matthewpisinski/work/work/side-projects/AI_Engineering/Citation_Rag

## Rules (from .coord/PROTOCOL.md and the user)
- Everything stays inside the project folder, except the Docker named volume.
- Never kill, restart, or prune anything on the machine. No `docker system prune`. No Ollama restarts. Do not run Ollama inference (no `ollama run`, no chat calls). `ollama list` is allowed.
- Do not pull gpt-oss:20b.
- Postgres container: name `citation-rag-db`, host port 5433, `mem_limit: 2g`, named volume `citation_rag_pgdata`.
- `.env` exists and holds ANTHROPIC_API_KEY. Append to it. Never print its contents, never commit it.
- Stop and report if free disk drops under 15 GB (`df -h /`).
- Work on git branch `dev`. One commit at the end. Do not touch `main`.

## Deliverables
1. `pyproject.toml` (uv, Python 3.12, package name `citation_rag`, src layout not required; package dir `citation_rag/` at root). Dependencies:
   httpx, lxml, psycopg[binary,pool], pgvector, numpy, pydantic, pydantic-settings, python-dotenv, sentence-transformers, torch, transformers, tqdm, anthropic.
   Dev group: pytest, ruff.
2. `citation_rag/__init__.py` and empty subpackages: `corpus`, `parse`, `chunk`, `index`, `search`, `rerank`, `answer`, `evals` (each with `__init__.py`).
3. Folders: `scripts/`, `tests/`, `data/raw`, `data/parsed`, `data/survey`, `reports/`, `.cache/hf` (data and .cache are gitignored; commit a `.gitkeep` in `data/` is NOT needed).
4. `.gitignore`: `.env`, `.venv/`, `.cache/`, `data/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.ruff_cache/`.
5. `.env.example` with keys: ANTHROPIC_API_KEY=, DATABASE_URL=postgresql://rag:rag@localhost:5433/rag, HF_HOME=.cache/hf, SEC_USER_AGENT="Matthew Pisinski mattpisinski@gmail.com", OLLAMA_URL=http://127.0.0.1:11434.
   Append DATABASE_URL, HF_HOME, SEC_USER_AGENT, OLLAMA_URL lines to the existing `.env` (do not overwrite it). Make HF_HOME absolute in `.env`.
6. `docker-compose.yml`: service `db`, image `pgvector/pgvector:pg17`, container_name `citation-rag-db`, POSTGRES_USER/PASSWORD/DB = rag/rag/rag, ports `5433:5432`, `mem_limit: 2g`, volume `citation_rag_pgdata:/var/lib/postgresql/data`, healthcheck with `pg_isready`.
7. `citation_rag/settings.py`: pydantic-settings class that reads `.env` (database_url, hf_home, sec_user_agent, ollama_url, anthropic_api_key optional).
8. `uv venv --python 3.12` then `uv sync`. Torch must load with MPS available (`torch.backends.mps.is_available()`), but do not run any model on MPS in this wave.
9. Download models into HF_HOME (each under 5 GB, LIGHT): `BAAI/bge-small-en-v1.5`, `BAAI/bge-m3`, `BAAI/bge-reranker-v2-m3`. Use `huggingface_hub.snapshot_download` with HF_HOME set. Report the size of each.
10. `scripts/smoke.py` that checks and prints PASS/FAIL per line:
    - Postgres reachable on 5433, `CREATE EXTENSION IF NOT EXISTS vector` works, `SELECT '[1,2,3]'::vector` works.
    - Container memory limit is 2g (`docker inspect citation-rag-db --format '{{.HostConfig.Memory}}'` = 2147483648).
    - bge-small tokenizer loads from HF_HOME offline and counts tokens of a sentence.
    - Each of the three models loads on CPU (SentenceTransformer / CrossEncoder, device="cpu") and processes ONE short sentence. CPU only. No MPS.
    - `ollama list` shows `qwen3:8b`. Do not run inference.
    - Free disk >= 15 GB.
    - torch MPS available (check only).
11. `reports/wave-0.md`: what was created, the smoke output verbatim, model sizes, free disk before and after, and anything that did not work.
12. `git add` everything that is not ignored, one commit on `dev`: "Wave 0: environment, Postgres container, models, smoke checks". Do not push.

## Checks the orchestrator runs afterwards
- `uv run python scripts/smoke.py` prints only PASS lines.
- `docker inspect citation-rag-db` shows Memory 2147483648 and port 5433.
- `git status` clean, `.env` not tracked.
- `df -h /` >= 15 GB free.
