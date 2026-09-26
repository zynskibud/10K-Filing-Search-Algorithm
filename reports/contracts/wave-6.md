# Wave 6 contract: rerankers

LIGHT build (Sonnet). Owns `citation_rag/rerank/`, `tests/test_rerank.py`. Depends on `citation_rag/search/retriever.py` (wave 5a) for the hook point. Plan: Plan tab section 9. Execution is GPU class for the model rerankers and runs later through `scripts/run.sh 6` after GO.

## Rules
- LIGHT: unit tests on CPU with at most 20 candidate chunks and short texts. Model rerankers load from the project cache offline (`HF_HOME` from `.env`, `HF_HUB_OFFLINE=1`). Models not in the cache yet (ColBERTv2, monoT5) are downloaded in this wave only if each is under 5 GB (both are; ColBERTv2 about 440 MB, monoT5-base about 900 MB) into the project cache, and the test skips with a reason if the download fails. No Ollama calls in tests.
- No commits.

## Interface
`Reranker.rerank(question: str, candidates: list[Result], top: int) -> list[Result]` where each returned Result carries `rerank_score`. The retriever gets an optional `reranker=` argument applied after fusion and before the per-company cut. Every reranker records its wall time per call.

## The six rerankers
1. `none.py`: identity, keeps fusion order.
2. `mmr.py`: Maximal Marginal Relevance over the candidates' embeddings (pass the query vector and candidate vectors; fetch candidate vectors from the chunk table by id), `lambda = 0.7`, cosine similarity. Pure numpy.
3. `colbert.py`: ColBERTv2 (`colbert-ir/colbertv2.0`) as a reranker on the candidates only: encode query and candidate tokens, MaxSim score, no index. Implement with `transformers` directly (the HF checkpoint is a BERT with a linear head; the projection weight is in the checkpoint), or with the `pylate` package if it installs cleanly with uv; state which. Truncate candidates to 512 tokens.
4. `cross_encoder.py`: `BAAI/bge-reranker-v2-m3` through `sentence_transformers.CrossEncoder`, batch 16, max length 1024 for chunks and 8,192 only for whole-section chunks (strategy s4). Device from a `--device` setting, CPU in tests.
5. `monot5.py`: `castorini/monot5-base-msmarco-10k`: prompt `Query: {q} Document: {d} Relevant:`, score = softmax over the logits of the tokens `true` and `false` at the first decoding step, P(true). Batch 8.
6. `llm_listwise.py`: Qwen3 8B through Ollama, thinking off: the prompt lists the question and the candidates as `[1] ... [50]` (each truncated to 300 tokens), asks for a JSON list of candidate numbers ordered from most to least relevant. Parse, fall back to fusion order for any missing numbers. `FakeLLMClient` in tests. The Ollama client is shared with the router (`citation_rag/search/router.py`); import it, do not duplicate.

## Experiment runner
`citation_rag/rerank/experiments.py --exp rerank --index <winner> --search <winner>`: runs the six rerankers on the dev split with oracle routing through `evals.runner.run_eval`, config recorded (`reranker` name), plus per-question wall time; prints the comparison table (recall@8, MRR, prose/table split, latency p50 and p95) with paired-difference intervals against `none`. `--dry-run` prints the matrix. Nothing runs in this wave beyond the dry run.

## Tests
- Interface: each reranker returns exactly `top` results, all from the candidates, with `rerank_score` set.
- MMR: on a fixture where two candidates are near-duplicates, the second duplicate is pushed below a less similar but relevant one.
- Cross-encoder and monoT5 on CPU with 6 short candidates: the candidate that answers a toy question ranks first.
- ColBERT: MaxSim of a query with itself is higher than with an unrelated text.
- Listwise: the fake client's ordering is applied; a malformed response falls back to fusion order.
- Timing is recorded for every call.

## Definition of done
- `uv run pytest tests/test_rerank.py` passes (skips allowed only for a failed model download, with the reason printed).
- `--dry-run` prints the 6-run matrix.
