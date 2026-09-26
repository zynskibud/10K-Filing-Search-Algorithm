"""MMR (Maximal Marginal Relevance) reranker: pure numpy over embeddings.

Greedy selection: pick the candidate most similar to the query first; after
that, at each step pick the candidate that maximizes
`lambda * sim(query, candidate) - (1 - lambda) * max(sim(candidate, picked))`
(cosine similarity throughout), so a near-duplicate of something already
picked loses out to a more diverse, still-relevant candidate. `lambda = 0.7`
per the contract.

This module does not embed anything itself. `MMRReranker` takes a
`vector_lookup(chunk_ids) -> {chunk_id: vector}` callable -- in production,
a lookup against the chunk table by id (see `citation_rag.search.vector`);
in tests, a small fixture dict -- and an `embed_query_fn(text) -> vector`
(defaults to `citation_rag.search.query_embed.embed_query`, injectable so
unit tests never have to load a real embedding model).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import numpy as np

from citation_rag.rerank.base import RerankedResult

DEFAULT_LAMBDA = 0.7


def _normalize(vec: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def mmr_select(
    query_vec: Sequence[float],
    cand_vecs: Sequence[Sequence[float]],
    lambda_: float = DEFAULT_LAMBDA,
    top: int | None = None,
) -> list[int]:
    """Greedy MMR order over candidate indices, best first.

    Every vector is L2-normalized inside this function (cosine similarity),
    so callers may pass raw embeddings.
    """
    q = _normalize(np.asarray(query_vec, dtype=float))
    cands = np.array([_normalize(np.asarray(v, dtype=float)) for v in cand_vecs])
    n = cands.shape[0]
    if top is None or top > n:
        top = n
    sim_to_query = cands @ q

    remaining = list(range(n))
    selected: list[int] = []
    while remaining and len(selected) < top:
        if not selected:
            best = max(remaining, key=lambda i: sim_to_query[i])
        else:
            selected_vecs = cands[selected]

            def mmr_score(i: int) -> float:
                redundancy = float(np.max(selected_vecs @ cands[i]))
                return lambda_ * sim_to_query[i] - (1.0 - lambda_) * redundancy

            best = max(remaining, key=mmr_score)
        selected.append(best)
        remaining.remove(best)
    return selected


@dataclass
class MMRReranker:
    vector_lookup: Callable[[Sequence[Any]], dict[Any, Sequence[float]]]
    embed_query_fn: Callable[[str], Sequence[float]] | None = None
    lambda_: float = DEFAULT_LAMBDA
    name: str = "mmr"
    last_wall_ms: float = field(default=0.0, init=False, repr=False)

    def _embed_query(self, question: str) -> Sequence[float]:
        if self.embed_query_fn is not None:
            return self.embed_query_fn(question)
        from citation_rag.search.query_embed import embed_query  # deferred: real model load

        return embed_query(question)

    def rerank(self, question: str, candidates: Sequence[Any], top: int) -> list[RerankedResult]:
        candidates = list(candidates)
        t0 = time.perf_counter()
        if not candidates:
            self.last_wall_ms = (time.perf_counter() - t0) * 1000.0
            return []

        qvec = self._embed_query(question)
        vecs_by_id = self.vector_lookup([c.chunk_id for c in candidates])
        cand_vecs = [vecs_by_id[c.chunk_id] for c in candidates]
        order = mmr_select(qvec, cand_vecs, self.lambda_, top=top)

        q = _normalize(np.asarray(qvec, dtype=float))
        sims = [float(_normalize(np.asarray(v, dtype=float)) @ q) for v in cand_vecs]
        self.last_wall_ms = (time.perf_counter() - t0) * 1000.0
        return [RerankedResult.from_result(candidates[i], sims[i]) for i in order]
