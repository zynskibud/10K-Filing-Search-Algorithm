"""The retriever: routing, per-company search loop, general-question cap.

`Retriever.__call__(question, companies=None)` matches the harness's
`Retriever` callable type in `citation_rag.evals.runner` (it returns a list
of objects with `.chunk_id`, `.text`, `.token_count`, `.section_id`,
`.table_id`, `.page_start`, `.page_end`, `.accession_no`; the harness never
unpacks positionally, so the extra `.scores` field on `ScoredResult` below
does not break that contract).

Steps (plan section 8 and the System tab):
1. Route: use the given `companies`, or call `router.route(question)`.
2. For each named company: BM25 and/or vector search filtered to that cik,
   RRF fusion when hybrid, then (when `reranker` is set) the reranker
   reorders the whole fused candidate pool, then keep `per_company_top`.
3. For "general": unfiltered search, optional rerank, then cap
   `general_cap` per cik so one filing cannot fill the list.
4. Results across companies are concatenated (small-to-big parent lookup and
   prompt assembly happen downstream, in the answer stage, not here).

Integration-1 item 1: `reranker` (any `citation_rag.rerank.base.Reranker`,
or `None`) is applied after RRF fusion and before the per-company cut and
before the general cap. It reorders the fused candidate pool (asking for
`top=len(candidates)` so nothing is dropped at this step); the existing
`per_company_top`/`general_cap` slicing then runs on that reordered list,
unchanged. Each result keeps its `scores` dict (bm25/vector/rrf) and also
gets `rerank_score` set when a reranker ran (`None` otherwise).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, NamedTuple, Sequence

from citation_rag.search import fusion, vector
from citation_rag.search.bm25 import BM25Index
from citation_rag.search.query_embed import embed_query as _default_embed_query
from citation_rag.search.router import Router

VALID_METHODS = {"bm25", "vector", "hybrid"}


class ScoredResult(NamedTuple):
    """Same shape as `citation_rag.evals.runner.Result`, plus `scores` for the
    log and `rerank_score` (set only when a reranker ran; `None` otherwise)."""

    chunk_id: object
    text: str
    token_count: int
    section_id: str | None
    table_id: str | None
    page_start: int | None
    page_end: int | None
    accession_no: str
    scores: dict[str, float | None]
    rerank_score: float | None = None


@dataclass
class Retriever:
    index_name: str
    method: str
    k: int = 50
    top: int = 8
    ef_search: int = 100
    per_company_top: int = 4
    general_cap: int = 2
    router: Router | None = None
    reranker: Any | None = None  # citation_rag.rerank.base.Reranker, applied after fusion
    schema: str | None = None  # Postgres schema override, for tests (test_wave5)
    bm25_index: BM25Index | None = None  # lazy-loaded from disk if not given
    pool: Any = None
    embed_query_fn: Callable[[str], list[float]] | None = None

    def __post_init__(self) -> None:
        if self.method not in VALID_METHODS:
            raise ValueError(f"method must be one of {sorted(VALID_METHODS)}, got {self.method!r}")

    # -- lazy resources -----------------------------------------------------

    def _bm25(self) -> BM25Index:
        if self.bm25_index is None:
            self.bm25_index = BM25Index.load(self.index_name)
        return self.bm25_index

    def _embed_query(self, question: str) -> list[float]:
        fn = self.embed_query_fn or _default_embed_query
        return fn(question)

    # -- one filtered search (bm25 and/or vector, fused if hybrid) ----------

    def _ranked_with_scores(
        self, question: str, ciks: Sequence[str] | None
    ) -> tuple[list[object], dict[object, dict[str, float | None]]]:
        bm25_hits: list[tuple[object, float]] = []
        vector_hits: list[tuple[object, float]] = []

        if self.method in ("bm25", "hybrid"):
            bm25_hits = self._bm25().search(question, k=self.k, ciks=ciks)
        if self.method in ("vector", "hybrid"):
            qvec = self._embed_query(question)
            vector_hits = vector.search(
                self.index_name,
                qvec,
                k=self.k,
                ciks=ciks,
                ef_search=self.ef_search,
                schema=self.schema,
                pool=self.pool,
            )

        bm25_map = dict(bm25_hits)
        vector_map = dict(vector_hits)

        rrf_map: dict[object, float] = {}
        if self.method == "bm25":
            ranked_ids = [doc_id for doc_id, _ in bm25_hits]
        elif self.method == "vector":
            ranked_ids = [doc_id for doc_id, _ in vector_hits]
        else:
            fused = fusion.rrf([bm25_hits, vector_hits])
            ranked_ids = [doc_id for doc_id, _ in fused]
            rrf_map = dict(fused)

        scores_map = {
            doc_id: {
                "bm25": bm25_map.get(doc_id),
                "vector": vector_map.get(doc_id),
                "rrf": rrf_map.get(doc_id),
            }
            for doc_id in ranked_ids
        }
        return ranked_ids, scores_map

    def _to_results(
        self,
        ids: Sequence[object],
        rows: dict[object, dict],
        scores_map: dict[object, dict[str, float | None]],
        rerank_scores: dict[object, float | None] | None = None,
    ) -> list[ScoredResult]:
        out = []
        for doc_id in ids:
            row = rows.get(doc_id)
            if row is None:
                continue
            out.append(
                ScoredResult(
                    chunk_id=doc_id,
                    text=row["text"],
                    token_count=row["token_count"],
                    section_id=row.get("section_id"),
                    table_id=row.get("table_id"),
                    page_start=row.get("page_start"),
                    page_end=row.get("page_end"),
                    accession_no=row["accession_no"],
                    scores=scores_map.get(doc_id, {"bm25": None, "vector": None, "rrf": None}),
                    rerank_score=(rerank_scores.get(doc_id) if rerank_scores else None),
                )
            )
        return out

    def _rerank_order(
        self,
        question: str,
        ranked_ids: Sequence[object],
        rows: dict[object, dict],
        scores_map: dict[object, dict[str, float | None]],
    ) -> tuple[list[object], dict[object, float | None]]:
        """Reorder `ranked_ids` with `self.reranker`, over the whole fused
        pool (`top=len(candidates)`, so nothing is dropped here -- the
        per-company/general cut, downstream, does the dropping)."""
        candidates = self._to_results(ranked_ids, rows, scores_map)
        if not candidates:
            return list(ranked_ids), {}
        reranked = self.reranker.rerank(question, candidates, top=len(candidates))
        new_ids = [r.chunk_id for r in reranked]
        rerank_scores = {r.chunk_id: r.rerank_score for r in reranked}
        return new_ids, rerank_scores

    # -- per-company and general searches ------------------------------------

    def _search_company(self, question: str, cik: str) -> list[ScoredResult]:
        ranked_ids, scores_map = self._ranked_with_scores(question, ciks=[cik])
        rerank_scores: dict[object, float | None] = {}

        if self.reranker is not None and ranked_ids:
            rows_all = vector.fetch_rows(self.index_name, ranked_ids, schema=self.schema, pool=self.pool)
            ranked_ids, rerank_scores = self._rerank_order(question, ranked_ids, rows_all, scores_map)
            top_ids = ranked_ids[: self.per_company_top]
            rows = {doc_id: rows_all[doc_id] for doc_id in top_ids if doc_id in rows_all}
        else:
            top_ids = ranked_ids[: self.per_company_top]
            rows = vector.fetch_rows(self.index_name, top_ids, schema=self.schema, pool=self.pool)

        return self._to_results(top_ids, rows, scores_map, rerank_scores)

    def _search_general(self, question: str) -> list[ScoredResult]:
        ranked_ids, scores_map = self._ranked_with_scores(question, ciks=None)
        rows = vector.fetch_rows(self.index_name, ranked_ids, schema=self.schema, pool=self.pool)
        rerank_scores: dict[object, float | None] = {}

        if self.reranker is not None and ranked_ids:
            ranked_ids, rerank_scores = self._rerank_order(question, ranked_ids, rows, scores_map)

        per_cik_count: dict[str, int] = {}
        capped_ids: list[object] = []
        for doc_id in ranked_ids:
            row = rows.get(doc_id)
            if row is None:
                continue
            cik = row.get("cik")
            if per_cik_count.get(cik, 0) >= self.general_cap:
                continue
            per_cik_count[cik] = per_cik_count.get(cik, 0) + 1
            capped_ids.append(doc_id)

        return self._to_results(capped_ids, rows, scores_map, rerank_scores)

    # -- the harness-facing callable ------------------------------------------

    def __call__(
        self, question: str, companies: "list[str] | str | None" = None
    ) -> list[ScoredResult]:
        if companies is None:
            if self.router is None:
                raise ValueError("no companies given and no router configured")
            routed = self.router.route(question)
            companies = routed["companies"]

        if companies == "general":
            return self._search_general(question)

        results: list[ScoredResult] = []
        for cik in companies:
            results.extend(self._search_company(question, cik))
        return results
