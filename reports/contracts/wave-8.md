# Wave 8 contract: final system, LangGraph build, sealed test set

Three tasks. 8a and 8b are LIGHT builds (Sonnet, in parallel). 8c is the one-time test-set run (GPU, through `scripts/run.sh 8` after GO). Plan: Plan tab section 12. Inputs: the winners from waves 5, 6, and 7 in `runs/wave-5/winners.json`, `runs/wave-6/winners.json`, `runs/wave-7/winners.json` (each written by that wave's report step: index, search, reranker, thinking, table option, prefix option).

## 8a. Final optimized RAG (Sonnet). Owns `final/`, `tests/test_final.py`.
1. `final/rag.py`: one file, 300 lines or fewer including imports and docstrings, no framework. It contains: the winning retrieval (BM25 or vector or hybrid with RRF), the winning reranker call, small-to-big context with the 20k budget, the answer prompt, the Qwen call with the winning thinking setting, the citation check, and a `main()` that answers one question from the command line. It may import `psycopg`, `numpy`, `sentence_transformers`, `httpx`, and the tokenizer. It may NOT import from `citation_rag/` (the point is a standalone, readable system), except `citation_rag.settings` for the connection string and cache path.
2. `final/README.md`: how to run it, what each block of the file does, and the measured numbers it was chosen on (copied from the wave reports).
3. `tests/test_final.py`: line count check (`wc -l` style, at most 300), and an end-to-end test with a fake LLM and the fixture corpus in a throwaway schema.

## 8b. LangGraph build (Sonnet). Owns `langgraph_build/`, `tests/test_langgraph_build.py`. Uses a separate uv group `langgraph` so the main environment stays framework-free.
1. `langgraph_build/pipeline.py`: the same task with LangGraph and LangChain components at their defaults: `RecursiveCharacterTextSplitter` (default 400 tokens is not a setting there; use `chunk_size=1600` characters, about 400 tokens, `chunk_overlap=200`), the LangChain `PGVector` store with the same bge-small model through `HuggingFaceEmbeddings`, `as_retriever(k=8)`, a two-node graph (retrieve, generate) with the Ollama chat model, and a prompt that asks for citations to the retrieved documents' metadata (page from our parsed JSON stored as document metadata). BM25 is not added unless it is a one-line default; record whether it was.
2. `langgraph_build/ingest.py`: loads the same passing filings' section texts (prose only, tables as text) into the framework's own table, so the corpus is identical.
3. `langgraph_build/NOTES.md`: lines of code (`cloc`-style count of the files you wrote), the effort log for two changes tried after the build: adding BM25 hybrid, and adding page-number citations with a quote check. For each: what had to change, how many lines, what broke. This is the "how hard is it to change one step" measure.
4. A retriever adapter so the harness (`citation_rag.evals.runner`) can score it with the same metrics, and an answer adapter for the judge.
5. Tests with fakes: the graph runs end to end on the fixture corpus.

## 8c. Sealed test set (GPU, after GO).
1. `citation_rag/evals/final.py --unseal`: runs, on `evals/golden/test.jsonl` one time: the final RAG (8a), the LangGraph build (8b), and run A (Qwen, no documents) as the baseline. Retrieval metrics, answer metrics through the calibrated judge, latency, and cost. Refuses to run twice: it writes `evals/results/FINAL_DONE` and stops if the marker exists.
2. `reports/FINAL.md`: every experiment table from waves 5, 6, 7 (dev), the test-set table, the winners and why, the limits from the System tab, the open questions, and the answer to each requirement in Plan section 0, with the number that answers it.

## Definition of done
- `wc -l final/rag.py` <= 300. `uv run pytest tests/test_final.py tests/test_langgraph_build.py` passes.
- `FINAL.md` exists and every requirement in Plan section 0 has a measured answer or an explanation.
