"""Wave 8b, contract point 4: adapters between the LangGraph build and the
shared eval harness.

Neither adapter changes the pipeline; each only translates its inputs or
outputs, so `citation_rag.evals.runner.run_eval` (the same harness that
scored every wave-5/6/7 winner and will score `final/rag.py`, wave 8a) can
score this framework build with the exact same recall/MRR/latency code, and
so the wave-8c judge run can call it the same way it calls the other two
systems under test.
"""

from __future__ import annotations

from typing import Any

from langchain_core.documents import Document

from citation_rag.chunk.tokens import count_tokens
from citation_rag.evals.runner import Result


def doc_to_result(doc: Document) -> Result:
    """A LangChain `Document` (as produced by `langgraph_build.ingest`) as a
    `citation_rag.evals.runner.Result`, the shape every retriever the
    harness scores must return."""
    meta = doc.metadata
    table_ids = meta.get("table_ids") or []
    return Result(
        chunk_id=meta.get("chunk_id", meta.get("section_id")),
        text=doc.page_content,
        token_count=count_tokens(doc.page_content),
        section_id=meta.get("section_id"),
        table_id=table_ids[0] if table_ids else None,
        page_start=meta.get("page_start"),
        page_end=meta.get("page_end"),
        accession_no=meta.get("accession_no"),
    )


def _company_filter(companies: list[str] | str | None) -> dict | None:
    if not companies or companies == "general":
        return None
    ciks = companies if isinstance(companies, list) else [companies]
    return {"cik": {"$in": ciks}}


def make_retriever(vectorstore, k: int = 8):
    """A `citation_rag.evals.runner.Retriever` (`(question, companies) ->
    list[Result]`) over this build's `PGVector` store, so it can be handed
    straight to `citation_rag.evals.runner.run_eval` in place of any of the
    project's own retrievers (`citation_rag.search.retriever`, or the
    wave-5/6/7 winners)."""

    def retriever(question: str, companies: list[str] | str) -> list[Result]:
        filt = _company_filter(companies)
        docs = vectorstore.similarity_search(question, k=k, filter=filt)
        return [doc_to_result(d) for d in docs]

    return retriever


def make_answer_fn(graph):
    """Answer adapter for the judge (wave 8c). `graph` is a compiled
    `langgraph_build.pipeline.build_graph(...)` graph. Returns a callable
    `(question, companies=None) -> {"answer": str, "context": list[Result]}`,
    the same two fields the judge needs from `final/rag.py`'s answer path
    (the answer text to judge, and the retrieved context to check
    faithfulness/citation-support against)."""

    def answer_fn(
        question: str, companies: list[str] | str | None = None
    ) -> dict[str, Any]:
        state = graph.invoke({"question": question, "companies": companies})
        return {
            "answer": state.get("answer", ""),
            "context": [doc_to_result(d) for d in state.get("context", [])],
        }

    return answer_fn
