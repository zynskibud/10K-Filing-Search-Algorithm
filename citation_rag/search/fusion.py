"""Reciprocal Rank Fusion (RRF): merge ranked candidate lists by rank only.

Per the plan (section 8), only RRF is implemented. `weighted_sum` is not:
raw BM25 scores and cosine distances live on different scales, and RRF
avoids having to reconcile them.
"""

from __future__ import annotations

from typing import Sequence

DEFAULT_K = 60


def rrf(lists: Sequence[Sequence[tuple]], k: int = DEFAULT_K) -> list[tuple[object, float]]:
    """Fuse ranked lists of (id, score) pairs (best first) into one ranking.

    Only the 1-indexed rank of each id within each list is used; the score
    values themselves are ignored (they may be on incomparable scales, e.g.
    BM25 score vs. cosine distance). An id missing from a list contributes 0
    for that list. Returns [(id, rrf_score)] sorted by score, descending.
    """
    totals: dict[object, float] = {}
    for lst in lists:
        for rank, item in enumerate(lst, start=1):
            doc_id = item[0]
            totals[doc_id] = totals.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
