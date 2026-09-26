# Integration pass 1 contract

LIGHT (Sonnet). Runs after wave 7a lands. Purpose: close the seams between the parallel builds so that wave 4 to 7 execution needs no code changes. Every item is small. Record each change in `reports/integration-1.md`.

## Items
1. **Reranker hook.** `citation_rag/search/retriever.py`: add `reranker=None` to `Retriever`; when set, apply `reranker.rerank(question, fused_candidates, top=k)` after RRF and before the per-company cut (and before the general cap). Keep `scores` on results and add `rerank_score` when present. Update `citation_rag/rerank/experiments.py` to use the hook instead of its wrapper. Test in `tests/test_search.py` with the `none` and `mmr` rerankers.
2. **Ollama client.** One client for router, listwise reranker, answer, and judge: pick `citation_rag/search/router.py`'s `OllamaClient` as the canonical one, move it to `citation_rag/llm.py`, and import it from there in `search/router.py`, `rerank/llm_listwise.py`, `answer/llm.py`, and `evals/judge.py` (keep their public names working). Options: `think: bool` (Ollama `"think"` field), `temperature`, `num_ctx`, `format`, `timeout` (default 900 s), and it returns token counts and wall time from the response. `FakeLLMClient` stays where tests import it, or re-export.
2b. **Router prompt file.** `citation_rag/search/router.py` loads its extraction prompt from `evals/prompts/router.v1.md` (the mirror file wave 7a wrote) instead of an inline string, so the prompt is versioned like the others. Keep the file and the code identical in wording.
3. **Judge timeout.** `citation_rag/evals/judge.py`: use the shared client with timeout 900 s.
4. **Loader speed.** `citation_rag/index/load.py`: `load_chunks` uses `COPY ... FROM STDIN` (psycopg 3 `cursor.copy`) in batches of 10,000 rows, with the explicit ids. Keep the sequence bump. Test on the fixture: same rows as before.
5. **Section token counts.** `load_filings` fills `sections.token_count` with the bge-small tokenizer (import `citation_rag.chunk.tokens.count_tokens`).
6. **Cache folder.** All model loads pass `cache_folder`/`cache_dir` explicitly from settings (bge in `index/models.py`, `search/query_embed.py`, rerankers, `chunk/tokens.py`), and `citation_rag/settings.py` exposes `hf_home` as an absolute path. Remove any reliance on `HF_HUB_CACHE` env ordering. Check `.cache/hf` for duplicate blob folders created by the wave 6 downloads; if a `models--*/blobs` folder holds real copies (not symlinks) of files that also exist in `snapshots/`, delete the `blobs/` copies and confirm the model still loads offline.
7. **waves.conf.** Fill `scripts/waves.conf` for waves 4, 5, 6, 7 with the real commands:
   - `4`: `uv run python -m citation_rag.index.build_all --eta-only` then `--full` (write `build_all.py`: chunk all 7 configs to `data/chunks/`, load filings and chunks, embed each index with `--sample 0.01` first writing `runs/wave-4/eta.txt`, stop if any ETA exceeds `--max-hours` (default 8) unless `--subset N` is given, then `--full` per index, then build HNSW, then BM25 pickles). `--subset N` restricts to a random N filings with seed 20260925 and writes the subset list to `data/subset.txt`.
   - `5`: `uv run python -m citation_rag.search.experiments --exp A` then `--exp hnsw`.
   - `6`: `uv run python -m citation_rag.rerank.experiments --exp rerank --index $WINNER_INDEX --search $WINNER_SEARCH` (read winners from `runs/wave-5/winners.json`, which the wave 5 report step writes).
   - `7`: `uv run python -m citation_rag.answer.run_all --split dev --runs A_think A_nothink B_think B_nothink`.
8. **Full test run.** `uv run pytest tests/ -q` passes except the two known `test_parse_tables.py` range tests; fix those two tests to the measured range (5 to 900) so the suite is green.

## Definition of done
- `uv run pytest tests/ -q`: all pass.
- `scripts/run.sh --dry-run 4` prints the wave 4 command (preflight may fail on the lock; that is fine, the dry run still shows the command).
- `reports/integration-1.md` lists every change.
