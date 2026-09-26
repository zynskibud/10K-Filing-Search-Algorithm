"""Shared reranker interface and result type (wave 6).

`Reranker.rerank(question, candidates, top) -> list[RerankedResult]`: every
reranker in this package takes the fused candidate list the retriever
produced and returns exactly `top` of them, reordered, each carrying a
`rerank_score`. Every reranker records its own CPU wall time for the call in
`self.last_wall_ms`.

`Result` re-exports the harness type from `citation_rag.evals.runner` (the
shared contract point named in the wave-6 brief). `citation_rag.search.
retriever` (wave 5a, written in parallel by another agent) returns its own
`ScoredResult`, which is the same shape plus a `scores` field.
`RerankedResult.from_result` reads a candidate structurally, by attribute,
not by exact type, so either `Result` or `ScoredResult` works as input.

Orchestrator note: as of this wave, `citation_rag.search.retriever.
Retriever` does not yet accept the `reranker=` argument the wave-5a contract
describes -- that file is owned by the parallel search agent and does not
have the hook wired in yet. Nothing in this package depends on that hook:
every `Reranker` here is a standalone `rerank(question, candidates, top)`
call, so it plugs in unchanged once the hook lands.
`citation_rag/rerank/experiments.py` works around the gap in the meantime
with its own wrapper (see the note there). No file outside `citation_rag/
rerank/` was edited.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, NamedTuple, Protocol, Sequence

from citation_rag.evals.runner import Result

__all__ = ["Result", "RerankedResult", "Reranker", "BaseReranker", "ensure_offline_env"]

_BASE_FIELDS = (
    "chunk_id",
    "text",
    "token_count",
    "section_id",
    "table_id",
    "page_start",
    "page_end",
    "accession_no",
)


class RerankedResult(NamedTuple):
    """`Result`'s fields plus `rerank_score`. Built from any candidate object
    that has the `Result` fields as attributes (`Result` itself, or the
    retriever's `ScoredResult`, which adds an extra `scores` field that this
    type does not carry forward)."""

    chunk_id: Any
    text: str
    token_count: int
    section_id: str | None
    table_id: str | None
    page_start: int | None
    page_end: int | None
    accession_no: str
    rerank_score: float

    @classmethod
    def from_result(cls, result: Any, rerank_score: float) -> "RerankedResult":
        values = {name: getattr(result, name) for name in _BASE_FIELDS}
        return cls(rerank_score=float(rerank_score), **values)


class Reranker(Protocol):
    name: str
    last_wall_ms: float

    def rerank(
        self, question: str, candidates: Sequence[Any], top: int
    ) -> list[RerankedResult]: ...  # pragma: no cover - protocol


def ensure_offline_env() -> str | None:
    """Point HF cache lookups at the project cache and force offline mode.

    Returns the cache dir, so callers can also pass it directly as
    `cache_folder=`/`cache_dir=`: `HF_HUB_CACHE` is read into a frozen
    constant the first time `huggingface_hub` is imported anywhere in the
    process (see `citation_rag.search.query_embed._ensure_offline_env`), so
    if some other module imports it first, setting the env var here has no
    effect. Passing the cache dir straight to `from_pretrained`/
    `hf_hub_download` sidesteps that import-order hazard.

    Mirrors `citation_rag.chunk.tokens` / `citation_rag.search.query_embed`:
    `HF_HOME` (from `.env` via `citation_rag.settings.Settings`) and
    `HF_HUB_OFFLINE=1` must both be set before `transformers` or
    `sentence_transformers` touches the hub, and `HF_HUB_CACHE` must point
    at the same path since this project's cache has no `hub/` subfolder.
    """
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    try:
        from citation_rag.settings import Settings

        hf_home = Settings().hf_home
    except Exception:
        hf_home = None
    if hf_home:
        os.environ.setdefault("HF_HOME", hf_home)
        os.environ.setdefault("HF_HUB_CACHE", hf_home)
    return hf_home or os.environ.get("HF_HUB_CACHE") or os.environ.get("HF_HOME")


@dataclass
class BaseReranker:
    """Scaffolding for a reranker that scores each candidate independently
    of the others (every reranker here except `mmr` and `llm_listwise`,
    which pick candidates relative to each other or all at once, and so
    implement `rerank` themselves).

    Subclasses implement `_score(question, candidates) -> list[float]`
    (same length and order as `candidates`); `rerank` sorts by score
    descending, keeps the top N, times the whole call, and wraps each kept
    candidate in a `RerankedResult`.
    """

    name: str = "base"
    last_wall_ms: float = field(default=0.0, init=False, repr=False)

    def _score(self, question: str, candidates: Sequence[Any]) -> list[float]:
        raise NotImplementedError

    def rerank(self, question: str, candidates: Sequence[Any], top: int) -> list[RerankedResult]:
        candidates = list(candidates)
        t0 = time.perf_counter()
        scores = self._score(question, candidates) if candidates else []
        self.last_wall_ms = (time.perf_counter() - t0) * 1000.0
        order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)
        return [RerankedResult.from_result(candidates[i], scores[i]) for i in order[:top]]
