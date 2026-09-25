# Wave 0 Report: Environment and Skeleton

## Summary

Wave 0 was successfully completed on 2026-09-25. The Citation RAG environment and skeleton were set up as specified in the contract.

## Created Artifacts

1. **pyproject.toml**: Project configuration with uv, Python 3.12, and all required dependencies
   - Core dependencies: httpx, lxml, psycopg[binary,pool], pgvector, numpy, pydantic, pydantic-settings, python-dotenv, sentence-transformers, torch, transformers, tqdm, anthropic
   - Dev dependencies: pytest, ruff

2. **citation_rag package structure**:
   - Main package: `citation_rag/__init__.py`
   - Subpackages: corpus, parse, chunk, index, search, rerank, answer, evals (each with `__init__.py`)

3. **Project directories**:
   - scripts/
   - tests/
   - data/raw, data/parsed, data/survey
   - reports/
   - .cache/hf (for HuggingFace models)

4. **.gitignore**: Configured to ignore .env, .venv/, .cache/, data/, __pycache__/, *.pyc, .pytest_cache/, .ruff_cache/

5. **.env.example**: Template with all required keys (ANTHROPIC_API_KEY, DATABASE_URL, HF_HOME, SEC_USER_AGENT, OLLAMA_URL)

6. **.env**: Updated with absolute paths for HF_HOME and other configuration values

7. **docker-compose.yml**: Postgres service with:
   - Image: pgvector/pgvector:pg17
   - Container name: citation-rag-db
   - Port: 5433:5432
   - Memory limit: 2g
   - Health check: pg_isready

8. **citation_rag/settings.py**: Pydantic-settings class for configuration management

9. **scripts/smoke.py**: Comprehensive smoke test suite with 9 tests

## Environment Setup

- Python version: 3.12 (uv managed)
- Virtual environment created with `uv venv --python 3.12`
- Dependencies installed with `uv sync`

## Models Downloaded

The following models were downloaded to .cache/hf:

- BAAI/bge-small-en-v1.5: 765 MB
- BAAI/bge-m3: 8.5 GB
- BAAI/bge-reranker-v2-m3: 4.3 GB

Total models: 13.565 GB

## Smoke Test Results

All 9 tests passed:

```
PASS: Postgres reachable and vector extension works
PASS: Docker container memory limit is 2g
PASS: bge-small tokenizer loads from HF_HOME offline
PASS: bge-small model loads on CPU
PASS: bge-m3 model loads on CPU
PASS: bge-reranker-v2-m3 model loads on CPU
PASS: ollama list shows qwen3:8b
PASS: Free disk >= 15 GB
PASS: torch MPS available

Tests passed: 9
Tests failed: 0
```

## Disk Space

- Before: 43 GB available (on /dev/disk3s1s1)
- After: 21 GB available
- Used in Wave 0: ~22 GB (models + venv + docker container)
- Requirement met: > 15 GB remains

## Git Status

- Branch: dev
- Commit message: "Wave 0: environment, Postgres container, models, smoke checks"
- All project files staged and committed

## Notes

- All tests passed without errors
- torch.backends.mps.is_available() returns True (MPS available but not used for inference in this wave)
- Postgres container is running and accessible at localhost:5433
- Models are loaded on CPU for smoke tests as specified
- Docker memory limit verified at 2147483648 bytes (2 GB)
