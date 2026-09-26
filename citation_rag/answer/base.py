"""Minimal collaborator protocols for wave 7a (answer generation).

Wave 7a's `pipeline.answer()` calls into two pieces owned by parallel waves:

- The retriever (wave 5a, `citation_rag.search.retriever.Retriever`): already
  built, imported directly. Not stubbed here.
- The reranker (wave 6, `citation_rag/rerank/`): landed mid-wave with the
  interface the contract promised -- `Reranker.rerank(question, candidates,
  top) -> list[RerankedResult]` (each carrying `rerank_score`), see
  `citation_rag.rerank.base.Reranker`. `Reranker` below is the same
  protocol, kept here (rather than imported) only so this package does not
  take a hard dependency on `citation_rag/rerank/` for callers that pass no
  reranker at all; any real wave 6 reranker satisfies it unchanged.
  `NoRerank` is the default used when no reranker is given (keeps the
  incoming order, cuts to `top`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence, runtime_checkable


@runtime_checkable
class Reranker(Protocol):
    def rerank(self, question: str, candidates: Sequence[Any], top: int) -> list[Any]: ...  # pragma: no cover - protocol


@dataclass
class NoRerank:
    """Identity reranker: keeps the incoming order, cuts to `top`. Used when
    no wave 6 reranker is configured."""

    def rerank(self, question: str, candidates: Sequence[Any], top: int) -> list[Any]:
        return list(candidates)[:top]


@runtime_checkable
class ResultLike(Protocol):
    """Documents the attribute shape this package expects from a search or
    rerank result (matches `citation_rag.evals.runner.Result` and
    `citation_rag.search.retriever.ScoredResult`). Duck-typed, not enforced:
    any object with these attributes works."""

    chunk_id: Any
    text: str
    token_count: int
    section_id: "str | None"
    table_id: "str | None"
    page_start: "int | None"
    page_end: "int | None"
    accession_no: str
