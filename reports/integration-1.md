# Integration pass 1

LIGHT (Sonnet). Closes the seams between the wave 4-7 builds per
`reports/contracts/integration-1.md`. No Ollama calls, no embedding runs, no
MPS use, no corpus-scale commands were run; the full test run excludes
`tests/test_parse_prose.py` and `tests/test_parse_tables.py` (wave 2c owns
`citation_rag/parse/` and was running the corpus parse concurrently).

## Changes per item

**1. Reranker hook** (`citation_rag/search/retriever.py`)
- Added `reranker: Any | None = None` to `Retriever`.
- New `_rerank_order`: when `reranker` is set, it reorders the *whole* fused
  candidate pool (`reranker.rerank(question, candidates, top=len(candidates))`,
  so nothing is dropped at this step) before the existing per-company cut
  (`_search_company`) or general cap (`_search_general`), which then run on
  the reordered id list unchanged.
- `ScoredResult` gained a trailing `rerank_score: float | None = None` field
  (default `None`, so every existing construction of `ScoredResult` still
  works); `scores` (bm25/vector/rrf) is untouched by reranking.
- `citation_rag/rerank/experiments.py`: `run_rerank_experiment` now passes
  `reranker=` straight into `Retriever(...)` and calls `run_eval` with the
  retriever directly; deleted the `_wrap_with_reranker` workaround. Updated
  the stale "hook doesn't exist yet" notes in `rerank/experiments.py` and
  `rerank/base.py`.
- Tests: two new tests in `tests/test_search.py` — `none` reranker keeps
  fusion order and sets `rerank_score` (while `scores` stays identical);
  `mmr` reranker respects `per_company_top` after reordering, over the
  Postgres-backed `test_wave5` fixture already used by wave 5a's tests.

**2. Canonical Ollama client** (new `citation_rag/llm.py`)
- One `OllamaClient` over `POST /api/generate`, with `think`, `temperature`,
  `num_ctx`, `format` (default `"json"`), `timeout` (default 900s, per the
  contract), tracking `last_input_tokens`/`last_output_tokens`/
  `last_wall_time_s` from `prompt_eval_count`/`eval_count` and measured wall
  time. `LLMClient` protocol and `FakeLLMClient` also moved here.
- `citation_rag/search/router.py`: dropped its own copies, imports and
  re-exports `LLMClient`, `FakeLLMClient`, `OllamaClient` from
  `citation_rag.llm` (`from citation_rag.search.router import OllamaClient`
  etc. still works everywhere it is imported today).
- `citation_rag/rerank/llm_listwise.py`: imports `LLMClient` from
  `citation_rag.llm` directly (was importing it via `search.router`).
- `citation_rag/answer/llm.py`: `OllamaChat` keeps its existing constructor
  shape (`thinking`, `qwen3:8b`/`32768`/`900` defaults — `run_all.py`
  instantiates it with `thinking=`) but is now a thin wrapper around
  `citation_rag.llm.OllamaClient` instead of its own `POST /api/chat` call.
  `last_input_tokens`/`last_output_tokens`/`last_wall_time_s` are now
  properties that read through to the shared client.

**2b. Router prompt file** (`citation_rag/search/router.py`)
- `EXTRACT_PROMPT_TEMPLATE` is now `evals/prompts/router.v1.md`'s contents,
  read once at import time, instead of an inline string. Verified the two
  were already byte-identical before this change, so `LLMRouter`'s prompts
  are unchanged.

**3. Judge timeout** (`citation_rag/evals/judge.py`)
- `OllamaJudge` now wraps `citation_rag.llm.OllamaClient` (its own `model`/
  `temperature`/`base_url` fields are unchanged; added `timeout: float =
  citation_rag.llm.DEFAULT_TIMEOUT` = 900s) instead of its own inline
  `httpx` call to `/api/generate`.

**4. Loader speed** (`citation_rag/index/load.py`)
- `load_chunks` now writes each batch of up to 10,000 rows with psycopg 3's
  `cursor.copy("COPY chunks_{index} (...) FROM STDIN")` instead of one
  `INSERT` per row. `COPY` cannot express the old
  `COALESCE(%s, nextval(...))` id expression, so ids missing from the JSONL
  are assigned in Python before each batch's `COPY`, fetched in one round
  trip per batch (`nextval(...) FROM generate_series(1, n)`), not one
  round trip per row. The sequence bump at the end is unchanged.
- `test_index_load.py::test_load_chunks_and_vectors` (ids 1-10, none given
  explicitly) and `test_vector_search_ordering` (ids 1-5) still pass
  unchanged, confirming sequential id assignment matches the old behavior.

**5. Section token counts** (`citation_rag/index/load.py`)
- `load_filings` now fills `sections.token_count` with
  `citation_rag.chunk.tokens.count_tokens(section["text"])` instead of a
  literal `0`.

**6. Cache folder** (`citation_rag/settings.py`, `citation_rag/chunk/tokens.py`)
- `Settings.hf_home` now has a `field_validator` that normalizes it to an
  absolute path (`os.path.abspath(os.path.expanduser(value))`); the current
  `.env` value was already absolute, so behavior is unchanged today, but
  every caller can now rely on it.
- `citation_rag/chunk/tokens.py::get_tokenizer` was the one loader in the
  contract's list that did not pass `cache_dir` explicitly to
  `AutoTokenizer.from_pretrained` (it only set `HF_HOME`/`HF_HUB_CACHE` env
  vars and hoped they'd be read in time). Fixed: `_configure_offline_env`
  now returns the resolved `hf_home`, passed straight through as
  `cache_dir=`. `index/models.py`, `search/query_embed.py`, and every
  reranker in `citation_rag/rerank/` already passed `cache_folder=`/
  `cache_dir=` explicitly — no change needed there.
- Checked `.cache/hf` for the duplicate-blob problem the contract describes
  (a `models--*/blobs/` folder holding real copies, not symlinks, of files
  also in `snapshots/`): **no duplicates found.** `bge-small`, `bge-m3`, and
  `bge-reranker-v2-m3` were pulled with `huggingface_hub`'s newer "xet"
  backend, which stores content-addressed blobs in a single shared
  `.cache/hf/blobs/` (marked by a `.huggingface-shared-blobs` file) rather
  than one `blobs/` per model; those three have no per-model `blobs/`
  folder at all. `colbert-ir/colbertv2.0` and `castorini/monot5-base-msmarco-10k`
  (the wave 6 downloads) do each have their own `models--*/blobs/`, but
  every file under their `snapshots/<rev>/` is a symlink into that same
  folder (`ls -la` confirms `lrwxr-xr-x`, e.g.
  `snapshots/.../pytorch_model.bin -> ../../blobs/<hash>`) — the normal,
  non-duplicated layout. Nothing was deleted.

**7. `scripts/waves.conf`** and new `citation_rag/index/build_all.py`
- Wrote `citation_rag/index/build_all.py` (`--eta-only` / `--full`,
  `--subset N`, `--max-hours`, `--device`): chunks all 7 (model, strategy)
  configs, loads filings/sections/tables and each index's chunks, sample-
  embeds at 1% and writes `runs/wave-4/eta.txt`, gates on `--max-hours`
  (default 8) unless `--subset` was given, then (on `--full`) embeds every
  chunk bge_small-first, attaches vectors (which also builds each index's
  HNSW index), and builds every BM25 pickle. See its module docstring for
  the full sequence and the two judgment calls below.
- Filled in `scripts/waves.conf` for waves 4-7; exact contents and the
  `scripts/run.sh --dry-run 4` output are in the final report.

**8. Full test run**
- `uv run pytest tests/ -q --ignore=tests/test_parse_prose.py --ignore=tests/test_parse_tables.py`
  passes: **154 passed, 1 skipped** (the skip is a pre-existing,
  environment-dependent one in `test_embed.py` — bge-m3 needs >= 4 GB free
  memory and the machine had 3.94 GB free at run time — unrelated to this
  pass's changes).
- Deferred: fixing the two range tests in `tests/test_parse_tables.py` (5 to
  900) per the hard rule — that file is off limits while wave 2c is editing
  `citation_rag/parse/*.py` and running the corpus parse concurrently.

## Judgment calls

1. **Reranking reorders the pool, then the existing cut slices it** (item
   1). The contract's wording ("apply `reranker.rerank(question,
   fused_candidates, top=k)`... before the per-company cut") is read as: the
   reranker reorders the whole fused pool (asking for `top=len(candidates)`,
   i.e. keep everything, just reordered) rather than performing the final
   cut itself; `per_company_top`/`general_cap` then slice that reordered
   list exactly as they already sliced the plain fused list. This keeps the
   retriever's existing cut logic as the single place company budgets are
   enforced, for both the reranked and non-reranked paths.

2. **`OllamaChat` now calls `/api/generate`, not `/api/chat`** (item 2). The
   contract asks for *one* client shared by all four callers; the router's
   canonical `OllamaClient` (the one named as canonical) already used
   `/api/generate`. Since every one of the four callers sends a single,
   fully-built prompt string (no multi-turn conversation), `/api/generate`
   covers the answer stage too. This is a behavior change from wave 7a's
   original `OllamaChat` (which called `/api/chat` with a `messages` list) —
   never exercised in any wave's tests (`FakeLLMClient` stands in), so
   nothing observable changed for tests, but a live Qwen3 run will now hit
   the generate endpoint instead of the chat endpoint.

3. **`waves.conf`'s wave 6 line reads `winners.json` inline** (item 7).
   `waves.conf` holds one static command per wave, but the wave-6 command
   needs two values from a file the wave-5 report step writes at run time.
   Simplest deterministic option: a `bash -c '...'` one-liner that reads
   `runs/wave-5/winners.json`'s `index` and `search` fields with two small
   `python -c` calls (no `jq` dependency assumed) and passes them as
   `--index`/`--search`.

4. **`build_all.py --eta-only` never touches Postgres** (item 7).
   `citation_rag.index.embed`'s `--sample` path never writes to disk or the
   database by design, so `--eta-only` only chunks and sample-embeds; the DB
   load (filings/sections/tables/chunks) happens only in `--full`, right
   before the real embedding run that needs the rows to already exist
   (`attach_vectors` does an `UPDATE ... WHERE id = %s`). The contract's
   prose lists the DB load before the sample-embed step; here it is
   deferred to `--full` since nothing in `--eta-only` depends on it.

5. **`build_all.py`'s `load_all` truncates each chunk table before `COPY`**
   (item 7). So that `--full` (or a retried `--full`) never double-inserts
   chunks if it is run more than once against the same index, `load_all`
   runs `TRUNCATE TABLE chunks_{index} RESTART IDENTITY` immediately before
   that index's `load_chunks` call. Chunking is deterministic, so
   re-truncating and reloading is always safe.

6. **`.cache/hf` duplicate-blob check: nothing to delete** (item 6). See
   item 6 above for the finding — no `models--*/blobs/` folder holds a real
   (non-symlink) copy of a file that also exists under `snapshots/`.
