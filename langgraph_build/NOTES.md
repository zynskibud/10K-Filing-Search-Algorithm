# Wave 8b notes: the LangGraph comparison build

Plan tab section 12 ("Framework comparison"). Contract: `reports/contracts/wave-8.md`, section 8b.

## What this is

The same task as `final/rag.py` (wave 8a) -- retrieve from the same filings, answer with
citations -- built instead with LangGraph and LangChain, at the framework's own defaults:
`RecursiveCharacterTextSplitter` (chunk_size=1600 characters / chunk_overlap=200, about 400
tokens), `PGVector` (`langchain_postgres`) with `bge-small-en-v1.5` through
`HuggingFaceEmbeddings`, `as_retriever(k=8)`, a two-node graph (retrieve, generate), and
`ChatOllama` for the LLM call. No BM25 in the default build (see "Effort log" below for why).

Files: `pipeline.py` (the graph), `ingest.py` (loads filings into the framework's own
`PGVector` table), `adapters.py` (retriever + answer adapters for the shared eval harness and
judge), `variants/` (the two effort-log changes, on branches of the code that the default build
never imports).

## Packages added (uv group `langgraph`)

`uv add --group langgraph langgraph langchain langchain-community langchain-postgres
langchain-huggingface langchain-ollama`, then `uv add --group langgraph rank_bm25` for the BM25
effort-log variant. Resolved:

| package | version |
|---|---|
| langgraph | 1.2.12 |
| langchain | 1.4.2 |
| langchain-classic | 1.0.8 |
| langchain-community | 0.4.2 |
| langchain-core | 1.6.5 |
| langchain-huggingface | 1.2.2 |
| langchain-ollama | 1.1.0 |
| langchain-postgres | 0.0.18 |
| langchain-text-splitters | 1.1.2 |
| rank-bm25 | 0.2.2 |

(Plus their own transitive dependencies -- sqlalchemy, asyncpg, langsmith, ollama, and others --
all confined to the `langgraph` group.)

The default environment (`uv sync`, `uv run` with no `--group`) does not install this group:
confirmed by running `uv sync` after `uv add`, which uninstalled all of the above from `.venv`,
and by running `uv run --group langgraph ...`, which reinstalled them for that one invocation
only. Run this build's code and tests with `uv run --group langgraph ...`.

**Side effect on a main dependency:** resolving the `langgraph` group's constraints against the
project's existing `dependencies` (unpinned `pgvector` in `pyproject.toml`) moved the locked
`pgvector` (the Python package, not the Postgres extension) from `0.5.0` down to `0.3.6` --
`langchain-postgres` resolves against an older `pgvector` range. Checked: nothing in
`citation_rag/` imports the `pgvector` Python package (`grep -rn "import pgvector" citation_rag/`
returns nothing; the codebase only uses the Postgres `vector` extension type through raw SQL and
psycopg), so this has no functional effect. Left as-is rather than pinning `pgvector` in the main
`dependencies` list, which is outside this task's owned files.

## Lines of code (cloc-style: blank and comment/docstring lines excluded)

Default build (the four files above):

| file | total | code | comment/docstring | blank |
|---|---|---|---|---|
| `pipeline.py` | 169 | 91 | 42 | 36 |
| `ingest.py` | 218 | 128 | 57 | 33 |
| `adapters.py` | 79 | 39 | 23 | 17 |
| `__init__.py` | 4 | 0 | 4 | 0 |
| **total** | **470** | **258** | **126** | **86** |

`variants/` (both effort-log changes together): 221 total lines, 78 code lines.
`tests/test_langgraph_build.py`: 397 lines, 16 tests.

## Effort log

### Change 1: add BM25 hybrid retrieval

**Not a one-line default**, confirmed directly: the default build's `retrieve` node is
`vectorstore.as_retriever(k=8)` alone.

What had to change (`langgraph_build/variants/bm25_hybrid.py`, 55 lines / 14 code lines):
- A new dependency, `rank_bm25` -- `langchain_community.retrievers.BM25Retriever` raises
  `ModuleNotFoundError: No module named 'rank_bm25'` until it is installed separately; it is not
  pulled in by `langchain`, `langchain-community`, or `langchain-postgres`.
- `BM25Retriever` is in-memory only: it needs every document held in Python for the run (the
  vector store's documents live in Postgres and are never all resident at once), so it takes the
  same `list[Document]` `load_and_split` produced for ingestion, not the vector store itself.
- Combining it with the vector retriever needs `EnsembleRetriever` (weighted reciprocal-rank
  fusion -- the same RRF the project's own hybrid search uses, `c=60` by default in both). In the
  installed version (`langchain` 1.4.2 / `langchain-classic` 1.0.8), `from langchain.retrievers
  import EnsembleRetriever` -- the import path most LangChain examples still show -- raises
  `ModuleNotFoundError`. It now lives in `langchain_classic.retrievers`.

**What broke:** the `EnsembleRetriever` import path (found by trial; not obvious from the class's
own error message). Once past that, the ensemble's default `id_key=None` would have double-counted
identical chunks returned by both retrievers as separate results; using `id_key="chunk_id"` (the
id `ingest.py` already attaches to every chunk) fixed it -- confirmed with
`test_bm25_hybrid_variant_dedupes_across_both_retrievers`, which checks a two-chunk corpus stays
at two results, not four.

**Not measured here (LIGHT wave, no Ollama, no real retrieval-quality run):** whether BM25 hybrid
actually improves recall over vector-only on this corpus. That is a wave-8c question.

### Change 2: add page-number citations with a quote check

The default build's `generate` node asks the model to write `(p. N)` inline in free text and
never checks it -- a citation could name any page, or none, and the graph would not notice.

What had to change (`langgraph_build/variants/page_citations.py`, 160 lines / 64 code lines):
- A new JSON-mode prompt (`CITE_JSON_PROMPT_TEMPLATE`) asking for `{"answer": ..., "citations":
  [{"ref": n, "quote": "..."}]}` instead of free text with inline page markers.
- A new `generate` node (`generate_with_citation_check`) that parses that JSON and checks each
  citation: `ref` must index a retrieved document, and `quote` must be an exact substring of that
  document's `page_content` -- the same two checks
  `citation_rag.answer.cite.check_citations` runs for the custom RAG.
- A new, wider graph state (`CitationCheckState`) and a new `build_graph_with_citation_check`,
  because **LangGraph silently drops any key a node returns that is not declared on the compiled
  graph's state schema** -- confirmed directly: a node returning `{"a": ..., "b": "extra"}` against
  a schema with only `a` came back from `.invoke()` with `b` missing, no error or warning. The
  default `pipeline.GraphState` has no `citation_check` field, so this variant could not just
  return one from a swapped-in node; it needed its own state type and its own compiled graph.
- `check_citations` here does **not** reuse `citation_rag.answer.cite.check_citations`: that
  function is typed against the custom RAG's `ContextBlock` (company, fiscal_year, section_title,
  page_start/end, a chunk_ids list), not a LangChain `Document`. Adapting `Document`s into
  `ContextBlock`s just to reuse ~20 lines of substring-matching logic would have needed more glue
  code than writing a small `Document`-native version, so this duplicates that logic rather than
  sharing it -- a real, measurable cost of the two systems having incompatible internal types.

**What broke:** nothing in the fake-LLM tests (three cases: a valid citation, a quote not found
in the text, and unparsable output degrading cleanly to the raw string with no citations to
check -- all pass, `test_page_citations_variant_*`). **What is not tested here:** whether
`qwen3:8b` reliably returns valid JSON under this stricter prompt. `citation_rag`'s own answer
stage hit exactly this failure mode and needed `AnswerParseError` plus a raw-text fallback
(`citation_rag/answer/pipeline.py`) -- this variant copies that same degrade-on-parse-failure
choice, but its real compliance rate against Ollama is unmeasured in this LIGHT wave (no Ollama
calls). Wave 8c, which does call the real model, should measure it.

## Framework limitations hit (beyond the two effort-log changes)

- **`PGVector` has no `schema=` parameter.** Getting the throwaway test schema (`test_wave8b`)
  required an undocumented workaround: `engine_args={"connect_args": {"options":
  "-csearch_path=<schema>,public"}}` on the SQLAlchemy engine PGVector builds internally. `public`
  has to stay in the search path too, or the `vector` extension's own type does not resolve.
- **`PGVector`'s connection string needs a driver name SQLAlchemy understands**
  (`postgresql+psycopg://`, not the project's plain `postgresql://` from `.env`); `ingest.py`
  converts it.
- **Filter syntax is Mongo-style** (`{"cik": {"$in": [...]}}`), not SQL -- a different vocabulary
  from the project's own SQL-based filtering (`citation_rag/search/vector.py`), which is a real
  porting cost anywhere the two systems need to share filter logic.
- **`langchain_community` (BM25Retriever's home) is being sunset** -- installing it prints
  `DeprecationWarning: langchain-community is being sunset and is no longer actively maintained`,
  which is itself a data point about "typical framework build" durability, not a bug in this code.
- **No small-to-big context assembly.** The default `generate` node sends only the individual
  retrieved chunk (about 400 tokens each), never the full parent section the custom RAG assembles
  under its 20k-token budget (`citation_rag/answer/context.py`). This is a real, functional
  difference between the two systems for wave 8c's comparison, not something this wave's contract
  asked the framework build to match.
