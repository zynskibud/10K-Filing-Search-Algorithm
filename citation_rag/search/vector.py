"""Vector search over pgvector chunk tables (`chunks_{index_name}`).

One psycopg connection pool, built lazily from `citation_rag.settings`.
`search` uses the HNSW index with a tunable `ef_search`. `exact_search`
turns the index scans off (`enable_indexscan`, `enable_bitmapscan`) for the
exact-vs-HNSW recall check in `experiments.py --exp hnsw`.

`schema` lets tests point at a throwaway Postgres schema (this wave's tests
use `test_wave5`) instead of the production `public` schema, without
changing any query logic.
"""

from __future__ import annotations

import re
from typing import Sequence

from psycopg_pool import ConnectionPool

_INDEX_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """The module-level connection pool, opened on first use."""
    global _pool
    if _pool is None:
        from citation_rag.settings import Settings

        settings = Settings()
        _pool = ConnectionPool(settings.database_url, min_size=1, max_size=4, open=True)
    return _pool


def reset_pool() -> None:
    """Close and drop the module-level pool. Mainly for tests."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def table_name(index_name: str, schema: str | None = None) -> str:
    if not _INDEX_NAME_RE.fullmatch(index_name):
        raise ValueError(f"unsafe index_name: {index_name!r}")
    if schema is not None and not _INDEX_NAME_RE.fullmatch(schema):
        raise ValueError(f"unsafe schema: {schema!r}")
    name = f"chunks_{index_name}"
    return f"{schema}.{name}" if schema else name


def to_pgvector_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def _run_search(
    table: str,
    vec_lit: str,
    k: int,
    ciks: Sequence[str] | None,
    pool: ConnectionPool,
    pragmas: list[tuple[str, tuple | None]],
) -> list[tuple[object, float]]:
    with pool.connection() as conn:
        with conn.cursor() as cur:
            for stmt, params in pragmas:
                cur.execute(stmt, params)
            if ciks:
                cur.execute(
                    f"SELECT id, embedding <=> %s::vector AS distance FROM {table} "
                    f"WHERE cik = ANY(%s) ORDER BY distance LIMIT %s",
                    (vec_lit, list(ciks), k),
                )
            else:
                cur.execute(
                    f"SELECT id, embedding <=> %s::vector AS distance FROM {table} "
                    f"ORDER BY distance LIMIT %s",
                    (vec_lit, k),
                )
            rows = cur.fetchall()
        conn.commit()
    return [(row[0], float(row[1])) for row in rows]


def search(
    index_name: str,
    qvec: Sequence[float],
    k: int = 50,
    ciks: Sequence[str] | None = None,
    ef_search: int = 100,
    schema: str | None = None,
    pool: ConnectionPool | None = None,
) -> list[tuple[object, float]]:
    """Approximate nearest-neighbor search via the HNSW index.

    Returns [(id, cosine_distance)], best (smallest distance) first.
    """
    pool = pool or get_pool()
    table = table_name(index_name, schema=schema)
    vec_lit = to_pgvector_literal(qvec)
    # SET does not accept bind parameters over the wire protocol; ef_search
    # is our own int, never user text, so a validated literal is safe here.
    ef_search = int(ef_search)
    pragmas = [(f"SET LOCAL hnsw.ef_search = {ef_search}", None)]
    return _run_search(table, vec_lit, k, ciks, pool, pragmas)


def exact_search(
    index_name: str,
    qvec: Sequence[float],
    k: int = 50,
    ciks: Sequence[str] | None = None,
    schema: str | None = None,
    pool: ConnectionPool | None = None,
) -> list[tuple[object, float]]:
    """Exact nearest-neighbor search (index scans disabled) for the recall check."""
    pool = pool or get_pool()
    table = table_name(index_name, schema=schema)
    vec_lit = to_pgvector_literal(qvec)
    pragmas = [
        ("SET LOCAL enable_indexscan = off", None),
        ("SET LOCAL enable_bitmapscan = off", None),
    ]
    return _run_search(table, vec_lit, k, ciks, pool, pragmas)


def fetch_rows(
    index_name: str,
    ids: Sequence[object],
    schema: str | None = None,
    pool: ConnectionPool | None = None,
) -> dict[object, dict]:
    """Fetch the citation-relevant columns for a set of chunk ids, keyed by id."""
    if not ids:
        return {}
    pool = pool or get_pool()
    table = table_name(index_name, schema=schema)
    columns = [
        "id",
        "text",
        "token_count",
        "section_id",
        "table_id",
        "page_start",
        "page_end",
        "accession_no",
        "cik",
    ]
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(columns)} FROM {table} WHERE id = ANY(%s)",
                (list(ids),),
            )
            rows = cur.fetchall()
        conn.commit()
    return {row[0]: dict(zip(columns, row)) for row in rows}
