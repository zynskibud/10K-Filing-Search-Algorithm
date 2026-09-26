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
   RRF fusion when hybrid, keep `per_company_top` after fusion. (Reranking
   is an optional step added between fusion and this cut in wave 6; there is
   none here.)
3. For "general": unfiltered search, then cap `general_cap` per cik so one
   filing cannot fill the list.
4. Results across companies are concatenated (small-to-big parent lookup and
   prompt assembly happen downstream, in the answer stage, not here).
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
    """Same shape as `citation_rag.evals.runner.Result`, plus `scores` for the log."""

    chunk_id: object
    text: str
    token_count: int
    section_id: str | None
    table_id: str | None
    page_start: int | None
    page_end: int | None
    accession_no: str
    scores: dict[str, float | None]


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
                )
            )
        return out

    # -- per-company and general searches ------------------------------------

    def _search_company(self, question: str, cik: str) -> list[ScoredResult]:
        ranked_ids, scores_map = self._ranked_with_scores(question, ciks=[cik])
        top_ids = ranked_ids[: self.per_company_top]
        rows = vector.fetch_rows(self.index_name, top_ids, schema=self.schema, pool=self.pool)
        return self._to_results(top_ids, rows, scores_map)

    def _search_general(self, question: str) -> list[ScoredResult]:
        ranked_ids, scores_map = self._ranked_with_scores(question, ciks=None)
        rows = vector.fetch_rows(self.index_name, ranked_ids, schema=self.schema, pool=self.pool)

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

        return self._to_results(capped_ids, rows, scores_map)

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
