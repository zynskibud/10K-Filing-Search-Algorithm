"""Unit tests for wave 5a: BM25, vector search, RRF fusion, the company
router, and the retriever end to end.

Postgres tests use a throwaway schema, `test_wave5`, that this file creates
and drops itself (independent of whatever `citation_rag/index/schema.py`
looks like at any given time; see the wave 5a contract). They are skipped if
Postgres is not reachable.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from citation_rag.search import experiments, fusion
from citation_rag.search.bm25 import STOPWORDS, BM25Index, tokenize
from citation_rag.search.query_embed import embed_query
from citation_rag.search.retriever import Retriever, ScoredResult
from citation_rag.search.router import (
    DEFAULT_ALIASES,
    FakeLLMClient,
    LLMRouter,
    OllamaClient,
    OracleRouter,
    normalize_name,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SEARCH_CHUNKS_PATH = FIXTURES_DIR / "search_chunks.jsonl"

K1 = 1.5
B = 0.75


def _load_search_chunks() -> list[dict]:
    with SEARCH_CHUNKS_PATH.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# --------------------------------------------------------------------------
# BM25: tokenizer
# --------------------------------------------------------------------------


def test_tokenize_lowercases_splits_and_drops_stopwords():
    tokens = tokenize("The Company depends on 1 supplier in Vietnam, for Tariffs.")
    assert tokens == ["company", "depends", "1", "supplier", "vietnam", "tariffs"]


def test_tokenize_keeps_single_digit_numbers_drops_single_letters():
    tokens = tokenize("a 9 x item")
    # "a" is a stopword; "x" is a single non-numeric char and is dropped;
    # "9" is a number and survives; "item" is length >= 2 and survives.
    assert tokens == ["9", "item"]


def test_stopword_list_is_about_50_words():
    assert 40 <= len(STOPWORDS) <= 60


# --------------------------------------------------------------------------
# BM25: hand-computed scoring (IDF and length normalization)
# --------------------------------------------------------------------------


def test_bm25_hand_computed_idf_and_length_normalization():
    """A tiny 3-document corpus, scored against query "sat mat" by hand.

    doc1: "the cat sat on the mat"                  -> tokens: cat, sat, mat   (len 3)
    doc2: "the dog sat on the log"                   -> tokens: dog, sat, log  (len 3)
    doc3: "cats and dogs sat together on the mat again" -> tokens: cats, dogs, sat, together, mat (len 5)

    Expected ranking: doc1 first (matches both query terms, short), doc3
    second (matches both query terms, but longer -> length penalty), doc2
    last (matches only "sat"). This is derived independently below with
    math.log, not by calling into BM25Index's internals.
    """
    docs = {
        "doc1": "the cat sat on the mat",
        "doc2": "the dog sat on the log",
        "doc3": "cats and dogs sat together on the mat again",
    }
    rows = list(docs.items())
    idx = BM25Index.build("tiny", rows)

    doc_lens = {doc_id: len(tokenize(text)) for doc_id, text in docs.items()}
    n = len(docs)
    avgdl = sum(doc_lens.values()) / n

    df = {"sat": 3, "mat": 2}  # sat: all 3 docs; mat: doc1 and doc3 only
    idf = {
        term: math.log(1.0 + (n - d + 0.5) / (d + 0.5)) for term, d in df.items()
    }

    def hand_score(doc_id: str, tf: dict[str, int]) -> float:
        dl = doc_lens[doc_id]
        total = 0.0
        for term, freq in tf.items():
            denom = freq + K1 * (1 - B + B * dl / avgdl)
            total += idf[term] * (freq * (K1 + 1)) / denom
        return total

    expected = {
        "doc1": hand_score("doc1", {"sat": 1, "mat": 1}),
        "doc2": hand_score("doc2", {"sat": 1}),  # "mat" absent from doc2
        "doc3": hand_score("doc3", {"sat": 1, "mat": 1}),
    }

    results = dict(idx.search("sat mat", k=10))

    assert set(results) == set(expected)
    for doc_id, exp_score in expected.items():
        assert results[doc_id] == pytest.approx(exp_score, rel=1e-9)

    ranked = [doc_id for doc_id, _ in idx.search("sat mat", k=10)]
    assert ranked == ["doc1", "doc3", "doc2"]
    # doc1 beats doc3 purely from length normalization: same term matches,
    # doc1 is shorter.
    assert expected["doc1"] > expected["doc3"] > expected["doc2"]


def test_bm25_search_returns_empty_for_query_with_no_content_terms():
    idx = BM25Index.build("tiny", [("doc1", "the cat sat on the mat")])
    assert idx.search("the on", k=5) == []  # both terms are stopwords


# --------------------------------------------------------------------------
# BM25: filter correctness (filter_ids and ciks)
# --------------------------------------------------------------------------


def test_bm25_filter_by_ids():
    rows = [
        ("d1", "revenue growth tariffs Vietnam", "0001"),
        ("d2", "revenue growth tariffs Vietnam", "0002"),
        ("d3", "revenue growth tariffs Vietnam", "0003"),
    ]
    idx = BM25Index.build("tiny", rows)
    results = idx.search("tariffs Vietnam", k=10, filter_ids=["d1", "d3"])
    ids = [doc_id for doc_id, _ in results]
    assert set(ids) == {"d1", "d3"}
    assert "d2" not in ids


def test_bm25_filter_by_ciks():
    rows = [
        ("d1", "revenue growth tariffs Vietnam", "cikA"),
        ("d2", "revenue growth tariffs Vietnam", "cikB"),
        ("d3", "revenue growth tariffs Vietnam", "cikA"),
    ]
    idx = BM25Index.build("tiny", rows)
    results = idx.search("tariffs Vietnam", k=10, ciks=["cikA"])
    ids = {doc_id for doc_id, _ in results}
    assert ids == {"d1", "d3"}


def test_bm25_filter_ids_and_ciks_intersect():
    rows = [
        ("d1", "revenue growth tariffs Vietnam", "cikA"),
        ("d2", "revenue growth tariffs Vietnam", "cikA"),
        ("d3", "revenue growth tariffs Vietnam", "cikB"),
    ]
    idx = BM25Index.build("tiny", rows)
    results = idx.search("tariffs Vietnam", k=10, filter_ids=["d1", "d3"], ciks=["cikA"])
    ids = {doc_id for doc_id, _ in results}
    assert ids == {"d1"}


def test_bm25_save_and_load_round_trip(tmp_path):
    rows = [("d1", "revenue growth tariffs Vietnam", "cikA")]
    idx = BM25Index.build("tiny", rows)
    path = tmp_path / "tiny.pkl"
    idx.save(path)
    loaded = BM25Index.load("tiny", path=path)
    assert loaded.search("tariffs", k=5) == idx.search("tariffs", k=5)


# --------------------------------------------------------------------------
# RRF: the worked example from the plan
# --------------------------------------------------------------------------


def test_rrf_worked_example_chunk_a_ranks_first():
    """From the plan: score(chunk) = sum of 1 / (60 + rank in each list).

    BM25 list:   A (rank 1), C (rank 2), B (rank 3)
    Vector list: B (rank 1), A (rank 2), C (rank 3)

    A is never rank 1 in the vector list and B is rank 1 there, but A's
    consistently-good rank (1 and 2) beats B's inconsistent rank (3 and 1)
    once both lists are combined by RRF: chunk A ranks first.
    """
    bm25_list = [("A", 9.1), ("C", 5.0), ("B", 1.2)]
    vector_list = [("B", 0.1), ("A", 0.2), ("C", 0.3)]  # smaller distance = better

    fused = fusion.rrf([bm25_list, vector_list], k=60)
    fused_map = dict(fused)

    expected_a = 1 / 61 + 1 / 62
    expected_b = 1 / 63 + 1 / 61
    expected_c = 1 / 62 + 1 / 63

    assert fused_map["A"] == pytest.approx(expected_a)
    assert fused_map["B"] == pytest.approx(expected_b)
    assert fused_map["C"] == pytest.approx(expected_c)

    ranked_ids = [doc_id for doc_id, _ in fused]
    assert ranked_ids[0] == "A"
    assert ranked_ids == ["A", "B", "C"]


def test_rrf_id_missing_from_one_list_still_scored():
    fused = dict(fusion.rrf([[("A", 1.0), ("B", 2.0)], [("A", 1.0)]], k=60))
    assert fused["A"] == pytest.approx(1 / 61 + 1 / 61)
    assert fused["B"] == pytest.approx(1 / 62)


# --------------------------------------------------------------------------
# Router: OracleRouter
# --------------------------------------------------------------------------


def test_oracle_router_returns_case_companies_unchanged():
    case = {"companies": ["0000320193"]}
    router = OracleRouter(case=case)
    assert router.route("anything") == {"companies": ["0000320193"], "unresolved": []}


def test_oracle_router_general():
    class FakeCase:
        companies = "general"

    router = OracleRouter(case=FakeCase())
    assert router.route("anything") == {"companies": "general", "unresolved": []}


# --------------------------------------------------------------------------
# Router: LLMRouter with FakeLLMClient, the 6 edge cases
# --------------------------------------------------------------------------

DIRECTORY = [
    {"cik": "0000320193", "company": "Apple Inc.", "ticker": "AAPL"},
    {"cik": "0001018724", "company": "Amazon.com, Inc.", "ticker": "AMZN"},
]


def _router(response: str) -> LLMRouter:
    return LLMRouter(client=FakeLLMClient(response), companies=DIRECTORY)


def test_llm_router_ticker():
    resp = json.dumps({"candidates": [{"name": None, "ticker": "AAPL"}], "none": False})
    result = _router(resp).route("What did AAPL say about supply chain risk?")
    assert result == {"companies": ["0000320193"], "unresolved": []}


def test_llm_router_full_name():
    resp = json.dumps({"candidates": [{"name": "Apple Inc.", "ticker": None}], "none": False})
    result = _router(resp).route("What did Apple Inc. say about supply chain risk?")
    assert result == {"companies": ["0000320193"], "unresolved": []}


def test_llm_router_unknown_company_flagged():
    resp = json.dumps(
        {"candidates": [{"name": "Untracked Robotics Co", "ticker": "UNTR"}], "none": False}
    )
    result = _router(resp).route("What did Untracked Robotics say about its supply chain?")
    assert result["companies"] == []
    assert result["unresolved"] == ["Untracked Robotics Co"]


def test_llm_router_vague_reference_resolved_via_alias():
    assert "the iphone maker" in DEFAULT_ALIASES
    resp = json.dumps({"candidates": [{"name": "the iPhone maker", "ticker": None}], "none": False})
    result = _router(resp).route("What did the iPhone maker say about component costs?")
    assert result == {"companies": ["0000320193"], "unresolved": []}


def test_llm_router_no_company_is_general():
    resp = json.dumps({"candidates": [], "none": True})
    result = _router(resp).route("What is a 10-K?")
    assert result == {"companies": "general", "unresolved": []}


def test_llm_router_two_companies():
    resp = json.dumps(
        {
            "candidates": [
                {"name": "Apple Inc.", "ticker": None},
                {"name": "Amazon.com, Inc.", "ticker": None},
            ],
            "none": False,
        }
    )
    result = _router(resp).route("Compare Apple Inc. and Amazon.com, Inc. on R&D spend.")
    assert set(result["companies"]) == {"0000320193", "0001018724"}
    assert result["unresolved"] == []


def test_llm_router_unparsable_response_falls_back_to_general():
    result = _router("not json").route("anything")
    assert result == {"companies": "general", "unresolved": []}


def test_normalize_name_strips_suffix_and_punctuation():
    assert normalize_name("Amazon.com, Inc.") == "amazon com"
    assert normalize_name("Apple Inc.") == "apple"


def test_ollama_client_exists_but_is_never_called_here():
    client = OllamaClient()
    assert client.model
    assert hasattr(client, "complete")


# --------------------------------------------------------------------------
# Postgres-backed fixture: schema `test_wave5`, table `chunks_fx`
# --------------------------------------------------------------------------

TEST_SCHEMA = "test_wave5"
TEST_INDEX = "fx"

try:
    import psycopg

    from citation_rag.settings import Settings
    from citation_rag.search import vector as vector_mod

    _settings = Settings()
    _conn = psycopg.connect(_settings.database_url, autocommit=True)
    _conn.close()
    POSTGRES_AVAILABLE = True
except Exception:
    POSTGRES_AVAILABLE = False

pg_required = pytest.mark.skipif(not POSTGRES_AVAILABLE, reason="Postgres at DATABASE_URL is not reachable")


@pytest.fixture(scope="module")
def search_chunks() -> list[dict]:
    return _load_search_chunks()


@pytest.fixture(scope="module")
def pg_schema(search_chunks):
    """Create schema test_wave5 with chunks_fx loaded from the 200-chunk fixture, then drop it."""
    if not POSTGRES_AVAILABLE:
        pytest.skip("Postgres at DATABASE_URL is not reachable")

    settings = Settings()
    conn = psycopg.connect(settings.database_url, autocommit=True)
    cur = conn.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    cur.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
    cur.execute(f"CREATE SCHEMA {TEST_SCHEMA}")
    cur.execute(
        f"""
        CREATE TABLE {TEST_SCHEMA}.chunks_{TEST_INDEX} (
            id INTEGER PRIMARY KEY,
            accession_no TEXT NOT NULL,
            cik TEXT NOT NULL,
            item TEXT,
            section_id TEXT,
            table_id TEXT,
            seq INTEGER,
            page_start INTEGER,
            page_end INTEGER,
            page_label TEXT,
            is_table BOOLEAN,
            text TEXT,
            embed_text TEXT,
            embedding vector(384),
            token_count INTEGER
        )
        """
    )
    insert_sql = f"""
        INSERT INTO {TEST_SCHEMA}.chunks_{TEST_INDEX}
            (id, accession_no, cik, item, section_id, table_id, seq, page_start, page_end,
             page_label, is_table, text, embed_text, embedding, token_count)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s)
    """
    for row in search_chunks:
        cur.execute(
            insert_sql,
            (
                row["id"],
                row["accession_no"],
                row["cik"],
                row["item"],
                row["section_id"],
                row["table_id"],
                row["seq"],
                row["page_start"],
                row["page_end"],
                row["page_label"],
                row["is_table"],
                row["text"],
                row["embed_text"],
                vector_mod.to_pgvector_literal(row["embedding"]),
                row["token_count"],
            ),
        )
    cur.execute(
        f"CREATE INDEX ON {TEST_SCHEMA}.chunks_{TEST_INDEX} "
        f"USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )

    yield TEST_SCHEMA, TEST_INDEX

    cur.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
    conn.close()


@pytest.fixture(scope="module")
def bm25_fixture_index(search_chunks) -> BM25Index:
    rows = [(row["id"], row["embed_text"], row["cik"]) for row in search_chunks]
    return BM25Index.build(TEST_INDEX, rows)


@pg_required
def test_vector_search_finds_semantically_closest_chunk(pg_schema):
    schema, index_name = pg_schema
    qvec = embed_query("What did the company say about tariffs on components from Vietnam?")
    results = vector_mod.search(index_name, qvec, k=5, ciks=["0000000001"], schema=schema)
    assert results, "expected at least one hit"
    top_id, _ = results[0]
    rows = vector_mod.fetch_rows(index_name, [top_id], schema=schema)
    assert "tariffs" in rows[top_id]["text"].lower()
    assert rows[top_id]["cik"] == "0000000001"


@pg_required
def test_vector_search_ciks_filter_is_respected(pg_schema):
    schema, index_name = pg_schema
    qvec = embed_query("revenue growth and results of operations")
    results = vector_mod.search(index_name, qvec, k=50, ciks=["0000000002"], schema=schema)
    ids = [doc_id for doc_id, _ in results]
    rows = vector_mod.fetch_rows(index_name, ids, schema=schema)
    assert all(rows[i]["cik"] == "0000000002" for i in ids)


@pg_required
def test_exact_search_and_hnsw_search_agree_closely(pg_schema):
    schema, index_name = pg_schema
    qvec = embed_query("cybersecurity attacks and data breaches")
    exact = vector_mod.exact_search(index_name, qvec, k=10, schema=schema)
    approx = vector_mod.search(index_name, qvec, k=10, ef_search=100, schema=schema)
    exact_ids = {doc_id for doc_id, _ in exact}
    approx_ids = {doc_id for doc_id, _ in approx}
    # 200 vectors, ef_search=100: HNSW should match the exact top-10 closely.
    overlap = len(exact_ids & approx_ids) / len(exact_ids)
    assert overlap >= 0.8


@pg_required
def test_retriever_hybrid_per_company_loop_two_companies(pg_schema, bm25_fixture_index):
    schema, index_name = pg_schema
    retriever = Retriever(
        index_name=index_name,
        method="hybrid",
        k=50,
        per_company_top=3,
        general_cap=2,
        schema=schema,
        bm25_index=bm25_fixture_index,
        embed_query_fn=embed_query,
    )
    results = retriever(
        "What does the company say about revenue growth and its results of operations?",
        companies=["0000000001", "0000000002"],
    )
    assert all(isinstance(r, ScoredResult) for r in results)
    by_cik_count: dict[str, int] = {}
    for r in results:
        cik = r.accession_no.split("-")[0]
        by_cik_count[cik] = by_cik_count.get(cik, 0) + 1
    assert by_cik_count.get("0000000001") == 3  # per_company_top respected
    assert by_cik_count.get("0000000002") == 3
    assert sum(by_cik_count.values()) == 6
    for r in results:
        assert r.scores["bm25"] is not None or r.scores["vector"] is not None
        assert r.scores["rrf"] is not None  # hybrid: every kept result was fused


@pg_required
def test_retriever_bm25_only_per_company(pg_schema, bm25_fixture_index):
    schema, index_name = pg_schema
    retriever = Retriever(
        index_name=index_name,
        method="bm25",
        k=50,
        per_company_top=4,
        schema=schema,
        bm25_index=bm25_fixture_index,
    )
    results = retriever("tariffs Vietnam supply chain components", companies=["0000000003"])
    assert 1 <= len(results) <= 4
    for r in results:
        assert r.accession_no.startswith("0000000003")
        assert r.scores["vector"] is None
        assert r.scores["bm25"] is not None


@pg_required
def test_retriever_general_cap_respected(pg_schema, bm25_fixture_index):
    schema, index_name = pg_schema
    general_cap = 2
    retriever = Retriever(
        index_name=index_name,
        method="hybrid",
        k=50,
        general_cap=general_cap,
        schema=schema,
        bm25_index=bm25_fixture_index,
        embed_query_fn=embed_query,
    )
    results = retriever("revenue growth risk factors and company operations", companies="general")
    assert results, "expected some results for a broad general query"

    rows = vector_mod.fetch_rows(index_name, [r.chunk_id for r in results], schema=schema)
    counts: dict[str, int] = {}
    for r in results:
        cik = rows[r.chunk_id]["cik"]
        counts[cik] = counts.get(cik, 0) + 1
    assert all(c <= general_cap for c in counts.values())
    # a broad query should pull from more than one company
    assert len(counts) > 1


@pg_required
def test_retriever_uses_oracle_router_when_companies_not_given(pg_schema, bm25_fixture_index):
    schema, index_name = pg_schema

    class FakeCase:
        companies = ["0000000004"]

    retriever = Retriever(
        index_name=index_name,
        method="bm25",
        per_company_top=2,
        schema=schema,
        bm25_index=bm25_fixture_index,
        router=OracleRouter(case=FakeCase()),
    )
    results = retriever("employees workforce talent")
    assert results
    assert all(r.accession_no.startswith("0000000004") for r in results)


# --------------------------------------------------------------------------
# experiments.py: dry-run matrix
# --------------------------------------------------------------------------


def test_experiment_a_matrix_has_21_runs():
    combos = experiments.matrix_a()
    assert len(combos) == 21
    assert len(experiments.INDEXES) == 7
    assert len(experiments.METHODS) == 3
    assert {c["search"] for c in combos} == {"bm25", "vector", "hybrid"}
    assert len({c["index"] for c in combos}) == 7


def test_experiments_main_dry_run_prints_matrix(capsys):
    rc = experiments.main(["--exp", "A", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "21 runs" in out
    for idx in experiments.INDEXES:
        assert idx in out


def test_experiments_main_refuses_without_dry_run():
    rc = experiments.main(["--exp", "A"])
    assert rc == 1


def test_experiments_main_dry_run_hnsw(capsys):
    rc = experiments.main(["--exp", "hnsw", "--dry-run", "--index", "bge_small__s1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "bge_small__s1" in out
    assert "40" in out and "100" in out and "200" in out
