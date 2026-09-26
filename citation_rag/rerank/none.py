"""The identity reranker: keeps fusion order.

Used as the baseline every other reranker is compared against in the wave-6
experiment (does reranking help, and by how much).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from citation_rag.rerank.base import BaseReranker


@dataclass
class NoneReranker(BaseReranker):
    name: str = "none"

    def _score(self, question: str, candidates: Sequence[Any]) -> list[float]:
        # A descending placeholder score that preserves input order: the
        # first candidate (best per fusion) gets the highest score, and so
        # on. `BaseReranker.rerank` sorts by this score, so the fusion order
        # comes back unchanged.
        n = len(candidates)
        return [float(n - i) for i in range(n)]
