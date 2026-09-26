# final/rag.py

The final, optimized RAG system: one file, 300 lines or fewer (`wc -l final/rag.py`),
no framework. It contains only the winning choices from waves 5 to 7 (retrieval
method, reranker, chunking/index, thinking setting): BM25/vector search with RRF
fusion, a cross-encoder reranker, small-to-big context assembly under a 20,000-token
budget, the answer prompt, the Qwen3 call, and the citation check.

It does not import from `citation_rag/` (except `citation_rag.settings`, for the
database URL and the HF cache path). Every piece of logic — BM25 scoring, RRF, the
prompt, the citation check — is reimplemented directly in this file so it stands on
its own and can be read start to finish. See the module docstring in `rag.py` for the
one-paragraph version of this.

## Status: winners not yet known

Waves 5, 6, and 7 (which pick the winning index/search method, reranker, and
thinking setting on the dev set) have not run yet. `CONFIG` at the top of `rag.py`
therefore ships with the plan's *expected* winners as defaults:

```python
CONFIG = {
    "index": "bge_small__s3",       # bge-small, chunking strategy 3 (paragraph-based, in-section)
    "search": "hybrid",             # BM25 + vector, merged with RRF
    "reranker": "cross_encoder",    # bge-reranker-v2-m3
    "thinking": False,
    "table_option": "labels_only",  # informational only; baked into the chunk table at index time
}
```

When wave 5, 6, or 7 finishes, it writes `runs/wave-{5,6,7}/winners.json` (a flat
JSON object with any of the keys above). `rag.py` reads whichever of those three
files exist, in order, and overrides the matching `CONFIG` keys — so this file needs
no code change once the real winners are in. **The "measured numbers this was chosen
on" section below is a placeholder until then**; once waves 5-7 land, copy their
dev-set winning-configuration numbers (recall@8, latency, and the A/B/C margin over
the next-best configuration) in here from `reports/wave-5a.md`, `reports/wave-6.md`,
and `reports/wave-7a.md`.

| Choice | Winner (expected default) | Measured on dev (TBD) |
|---|---|---|
| Chunking + embedding index | `bge_small__s3` | -- |
| Search method | hybrid (BM25 + vector, RRF) | -- |
| Reranker | cross-encoder (`bge-reranker-v2-m3`) | -- |
| Table handling | labels only | -- |
| Thinking | off | -- |

## How to run it

Answer one question from the command line:

```bash
uv run python final/rag.py "What did Acme say about tariffs on Vietnam?"
uv run python final/rag.py "What did Acme say about tariffs on Vietnam?" --company 0000320193
```

- `--company` (repeatable) restricts search to those CIKs. Omit it to search every
  filing (this file does not include the LLM company router from `citation_rag/` --
  see "What is left out" below).
- Requires `.env` at the project root (`DATABASE_URL`, `HF_HOME`, `OLLAMA_URL`), a
  reachable Postgres with the `chunks_{CONFIG['index']}`, `sections`, `filings`, and
  `tables_parsed` tables populated, the BM25 pickle at
  `data/bm25/{CONFIG['index']}.pkl` (written by wave 5), and Ollama running
  `qwen3:8b` for the answer call.

## What each block does

Reading top to bottom, in the order the file is laid out:

1. **CONFIG and constants.** The winning choices (see above), `K`/`TOP`/`RRF_K`,
   the 20k context budget, the embedding/reranker model names, and the exact answer
   prompt (`ANSWER_PROMPT`, matching `evals/prompts/answer.v1.md`).
2. **Lazy singletons** (`_get_conn`, `_get_embedder`, `_get_reranker`, `count_tokens`).
   One Postgres connection, one `SentenceTransformer` (bge-small), one `CrossEncoder`
   (bge-reranker-v2-m3), created on first use. `count_tokens` uses the bge-small
   tokenizer -- the project's token ruler for the 20k budget.
3. **BM25 loading and scoring.** `data/bm25/{index}.pkl` holds a
   `citation_rag.search.bm25.BM25Index` instance (wave 5's format). `_BM25Unpickler`
   overrides `find_class` so that unpickling it never actually imports
   `citation_rag`: the pickled object's `__dict__` (`postings`, `doc_freq`,
   `doc_len`, `doc_cik`, `n_docs`, `avgdl` -- all plain `dict`/`int`/`float`) is
   loaded into a bare local placeholder class (`_PlainBM25`) instead. `_bm25_search`
   then reimplements the Okapi BM25 formula (`k1=1.5`, `b=0.75`) directly over those
   plain dicts, matching `citation_rag/search/bm25.py` exactly. This was the
   simplest way to reuse wave 5's precomputed index without a `citation_rag` import
   or a code change to `bm25.py` -- see "The BM25 pickle question" below for the
   alternative that was considered and not needed.
4. **Vector search and RRF** (`_vector_search`, `rrf`). A single `<=>` (cosine
   distance) query against the `chunks_{index}` table's HNSW index, and the
   standard `1 / (60 + rank)` reciprocal-rank-fusion formula, summed across the
   BM25 and vector rank lists.
5. **`retrieve()`.** Runs BM25 and/or vector search per `CONFIG["search"]`, fuses
   with RRF when hybrid, fetches the candidate rows, reranks with the cross-encoder
   when `CONFIG["reranker"] == "cross_encoder"`, and returns the top `TOP` (8).
   Company routing (which filing(s) a question is about) is intentionally not here
   -- see "What is left out."
6. **`build_context()` (small-to-big).** Groups the top chunks by `section_id`,
   fetches each parent section's full text (joined with `filings` for
   company/fiscal year), fills any `[Table: id]` placeholder in with the matching
   `tables_parsed` row, and keeps sections in rank order under the 20,000-token
   budget. A single section that alone would exceed the whole budget is cut to what
   remains, by characters -- see "What is left out" for how this differs from the
   experiment code's paragraph-windowing.
7. **Prompt and LLM call** (`format_block`, `build_prompt`, `parse_response`,
   `call_llm`). Builds the exact `answer.v1.md` prompt with the numbered blocks,
   calls Qwen3 8B over Ollama's `/api/generate` with `CONFIG["thinking"]`,
   `temperature=0`, `num_ctx=32768`, `format="json"`, and parses the JSON reply
   (degrading to an unanswerable, uncited response on malformed JSON rather than
   raising).
8. **`check_citations()`.** Every `[n]` marker in the answer must have a matching
   citation; every citation's `ref` must exist among the sent blocks; every quote
   (whitespace-normalized) must be an exact substring of that block's text; an
   `answerable=false` answer must carry no citations.
9. **`answer_question()` and `main()`.** Wires the above into one call and a
   command-line entry point.

## What is left out, and why

To stay standalone and under 300 lines, a few things in the experiment code
(`citation_rag/`) are deliberately not reproduced here. All are explainable
trade-offs, not oversights:

- **Company routing and the multi-company/general-cap search loop**
  (`citation_rag/search/router.py`, and the per-company/general-cap logic in
  `citation_rag/search/retriever.py`) are not part of this file -- they are not in
  the wave-8a content list (BM25/vector/hybrid retrieval, the reranker, context,
  the prompt, the LLM call, and the citation check). `--company` on the command
  line does the filtering the router would otherwise resolve to.
- **Small-to-big's paragraph windowing** (`citation_rag/answer/context.py`'s
  `_window_section`, which grows a window of paragraphs breadth-first around the
  matched chunks when a section is oversized) is replaced here with a plain
  character cut to whatever budget remains. This only matters for the rare
  question whose single best-matching section alone exceeds the 20k budget; the
  chunk texts closest to the question still open the section, so the cut rarely
  removes the evidence the question needs, but it is a strictly simpler (and
  slightly less precise) fallback than the experiment code's.
- **The BM25 pickle question.** The contract asked whether `data/bm25/{index}.pkl`
  (a `citation_rag.search.bm25.BM25Index` instance) forces a `citation_rag` import,
  and, if so, to propose the smallest change. It does not force one here: see block
  3 above (`_BM25Unpickler`) for the loader that avoids it without touching
  `citation_rag/search/bm25.py`. No change to that file was needed.
- **Only one reranker family.** `CONFIG["reranker"]` supports `"cross_encoder"` or
  `"none"` (not MMR, ColBERT, monoT5, or the LLM listwise reranker from
  `citation_rag/rerank/`), since only one reranker wins per the wave-6 contract.

## Tests

`tests/test_final.py` (owned alongside this file) checks: the 300-line cap; the
`CONFIG`/`winners.json` override mechanism; BM25 tokenizing, the unpickle-without-
`citation_rag` loader, and scoring (checked against a real `BM25Index` built in the
test file); RRF against a hand-computed example; retrieval, small-to-big context
assembly, and a full `answer_question()` run against a throwaway Postgres schema
(`test_wave8a`) loaded from `tests/fixtures/search_chunks.jsonl` (200 chunks, real
384-d vectors); prompt building; response parsing; the citation check's five cases;
and `call_llm`'s request shape and `main()`'s argument wiring, both without any real
network or Ollama call. A `FakeLLMClient` (from `citation_rag.llm`, reused only in
the test file) stands in for Qwen throughout.
