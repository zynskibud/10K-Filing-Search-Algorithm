"""Effort-log change 1 (NOTES.md): add BM25 hybrid retrieval to the default
build.

The default `retrieve` node (`langgraph_build.pipeline.build_graph`) is
`vectorstore.as_retriever(k=8)` alone -- no BM25 -- because BM25 is *not* a
one-line default for a `PGVector`-backed graph:

1. `langchain_community.retrievers.BM25Retriever` needs the `rank_bm25`
   package, which is not pulled in by `langchain`, `langchain-community`,
   or `langchain-postgres` (confirmed: `BM25Retriever.from_documents(...)`
   raises `ModuleNotFoundError: No module named 'rank_bm25'` until it is
   installed separately -- one more line in the `langgraph` uv group).
2. `BM25Retriever` is in-memory only: it has no Postgres-backed form in
   this stack, so it needs its own copy of every document held in Python
   the whole time the graph runs (the vector store's documents live in
   Postgres and are never all resident at once).
3. Combining it with the vector retriever needs a *second* framework
   import, `EnsembleRetriever` -- and in the installed version
   (`langchain` 1.4.2 with `langchain-classic` 1.0.8), it is not even
   where the older LangChain docs say: `from langchain.retrievers import
   EnsembleRetriever` raises `ModuleNotFoundError`; it now lives in
   `langchain_classic.retrievers`. That relocation cost real time to find
   (see NOTES.md).

What changed, concretely: one new file (this one, ~45 lines of code) plus
one new dependency (`rank_bm25`, one line in `pyproject.toml`'s `langgraph`
group). Nothing in `pipeline.py` or `ingest.py` was touched -- the hybrid
retriever is built alongside the existing vector store, not instead of it.
"""

from __future__ import annotations

from langchain_classic.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document


def build_hybrid_retriever(
    docs: list[Document], vectorstore, k: int = 8, weights: tuple[float, float] = (0.5, 0.5)
):
    """BM25 (in-memory, over `docs`) + the vector store's own retriever,
    combined by `EnsembleRetriever`'s reciprocal-rank fusion (the same RRF
    the project's own hybrid search uses, schemas.md glossary: `c=60` by
    default in both).

    `docs` must be the exact chunk list `langgraph_build.ingest.load_and_split`
    produced for the vector store, so both retrievers rank the same chunks.
    """
    bm25 = BM25Retriever.from_documents(docs, k=k)
    vector_retriever = vectorstore.as_retriever(search_kwargs={"k": k})
    return EnsembleRetriever(
        retrievers=[bm25, vector_retriever],
        weights=list(weights),
        id_key="chunk_id",
    )
