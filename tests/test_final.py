"""Tests for wave 8a: final/rag.py.

LIGHT/CPU only: a fake LLM stands in for Qwen (no Ollama calls), and
Postgres tests use a throwaway schema, `test_wave8a`, that this file
creates and drops itself, loaded from the 200-chunk fixture
(tests/fixtures/search_chunks.jsonl, real 384-d bge-small vectors). The
bge-small embedder and the bge-reranker-v2-m3 cross-encoder are real,
CPU, offline models already in the project's HF cache; if either fails to
load, the tests that need it are skipped with the reason (the only allowed
skip, per the project's testing convention -- see tests/test_rerank.py).

`final/rag.py` is loaded by file path (it is not a package, and the module
under test is the actual file the wave-8a contract caps at 300 lines).
"""

from __future__ import annotations

import importlib.util
import io
import json
import pickle
import re
import shutil
import sys
from pathlib import Path

import psycopg
import pytest

from citation_rag.llm import FakeLLMClient
from citation_rag.search.bm25 import BM25Index
from citation_rag.search.vector import to_pgvector_literal
from citation_rag.settings import Settings

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SEARCH_CHUNKS_PATH = FIXTURES_DIR / "search_chunks.jsonl"
RAG_PATH = Path(__file__).parent.parent / "final" / "rag.py"
TEST_SCHEMA = "test_wave8a"
TEST_INDEX = "fx"


def _load_search_chunks() -> list[dict]:
    with SEARCH_CHUNKS_PATH.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _load_rag_module(path: Path = RAG_PATH, name: str = "final_rag"):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _prefix_fields(row: dict) -> tuple[str, int, str]:
    """Pull company/fiscal_year/section-title back out of a fixture row's
    own `embed_text` prefix ("Company | FY2025 | Item X | title\\n...")."""
    prefix = row["embed_text"].split("\n", 1)[0]
    company, fy, _item, title = [p.strip() for p in prefix.split(" | ")]
    return company, int(fy.replace("FY", "")), title


rag = _load_rag_module()

try:
    _probe = psycopg.connect(Settings().database_url, autocommit=True)
    _probe.close()
    POSTGRES_AVAILABLE = True
except Exception:
    POSTGRES_AVAILABLE = False

pg_required = pytest.mark.skipif(not POSTGRES_AVAILABLE, reason="Postgres at DATABASE_URL is not reachable")


def _blk(ref: int, text: str) -> dict:
    return {"ref": ref, "text": text, "accession_no": "a", "company": "c", "fiscal_year": 2025,
            "item": "1A", "section_title": "t", "page_start": 1, "page_end": 1}


# --------------------------------------------------------------------------
# Postgres-backed fixtures: schema test_wave8a, table chunks_fx + sections/
# filings/tables_parsed, all derived from the 200-chunk fixture.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def search_chunks() -> list[dict]:
    return _load_search_chunks()


@pytest.fixture(scope="module")
def pg_schema(search_chunks):
    if not POSTGRES_AVAILABLE:
        pytest.skip("Postgres at DATABASE_URL is not reachable")
    settings = Settings()
    conn = psycopg.connect(settings.database_url, autocommit=True)
    cur = conn.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    cur.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
    cur.execute(f"CREATE SCHEMA {TEST_SCHEMA}")
    cur.execute(f"""
        CREATE TABLE {TEST_SCHEMA}.chunks_{TEST_INDEX} (
            id INTEGER PRIMARY KEY, accession_no TEXT NOT NULL, cik TEXT NOT NULL,
            item TEXT, section_id TEXT, table_id TEXT, seq INTEGER,
            page_start INTEGER, page_end INTEGER, page_label TEXT, is_table BOOLEAN,
            text TEXT, embed_text TEXT, embedding vector(384), token_count INTEGER
        )
    """)
    cur.execute(f"""
        CREATE TABLE {TEST_SCHEMA}.sections (
            id TEXT PRIMARY KEY, accession_no TEXT NOT NULL, item TEXT NOT NULL,
            title TEXT NOT NULL, page_start INT NOT NULL, page_end INT NOT NULL, text TEXT NOT NULL
        )
    """)
    cur.execute(f"""
        CREATE TABLE {TEST_SCHEMA}.filings (
            accession_no TEXT PRIMARY KEY, company TEXT NOT NULL, fiscal_year INT NOT NULL
        )
    """)
    cur.execute(f"CREATE TABLE {TEST_SCHEMA}.tables_parsed (id TEXT PRIMARY KEY, text TEXT NOT NULL)")

    insert_chunk = f"""
        INSERT INTO {TEST_SCHEMA}.chunks_{TEST_INDEX}
            (id, accession_no, cik, item, section_id, table_id, seq, page_start, page_end,
             page_label, is_table, text, embed_text, embedding, token_count)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s)
    """
    sections: dict[str, tuple] = {}
    filings: dict[str, tuple] = {}
    for row in search_chunks:
        cur.execute(insert_chunk, (
            row["id"], row["accession_no"], row["cik"], row["item"], row["section_id"],
            row["table_id"], row["seq"], row["page_start"], row["page_end"], row["page_label"],
            row["is_table"], row["text"], row["embed_text"],
            to_pgvector_literal(row["embedding"]), row["token_count"],
        ))
        company, fiscal_year, title = _prefix_fields(row)
        filings[row["accession_no"]] = (company, fiscal_year)
        sections.setdefault(
            row["section_id"],
            (row["accession_no"], row["item"], title, row["page_start"], row["page_end"], row["text"]),
        )

    for acc, (company, fy) in filings.items():
        cur.execute(f"INSERT INTO {TEST_SCHEMA}.filings VALUES (%s,%s,%s)", (acc, company, fy))
    for sid, (acc, item, title, ps, pe, text) in sections.items():
        cur.execute(
            f"INSERT INTO {TEST_SCHEMA}.sections VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (sid, acc, item, title, ps, pe, text),
        )
    cur.execute(
        f"CREATE INDEX ON {TEST_SCHEMA}.chunks_{TEST_INDEX} "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )

    yield TEST_SCHEMA, TEST_INDEX

    cur.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
    conn.close()


@pytest.fixture(scope="module")
def bm25_index(search_chunks) -> BM25Index:
    rows = [(r["id"], r["embed_text"], r["cik"]) for r in search_chunks]
    return BM25Index.build(TEST_INDEX, rows)


@pytest.fixture(scope="module")
def models_ready():
    """Force the real embedder and cross-encoder to load once, or skip
    (the only allowed skip -- both models are already in the offline cache
    per tests/test_rerank.py, so this should not normally trigger)."""
    try:
        rag._get_embedder()
        rag._get_reranker()
    except Exception as exc:  # pragma: no cover - only hit on a broken cache
        pytest.skip(f"model load failed: {exc}")


# --------------------------------------------------------------------------
# Line count (the contract's hard cap)
# --------------------------------------------------------------------------


def test_line_count_at_most_300():
    with RAG_PATH.open() as f:
        n = sum(1 for _ in f)
    assert n <= 300


# --------------------------------------------------------------------------
# CONFIG: plan defaults, and the winners.json override mechanism
# --------------------------------------------------------------------------


def test_config_has_required_keys_and_falls_back_to_plan_defaults():
    for key in ("index", "search", "reranker", "thinking", "table_option"):
        assert key in rag.CONFIG
    winners = [rag.PROJECT_ROOT / "runs" / w / "winners.json" for w in ("wave-5", "wave-6", "wave-7")]
    if not any(p.exists() for p in winners):
        assert rag.CONFIG == {
            "index": "bge_small__s3", "search": "hybrid", "reranker": "cross_encoder",
            "thinking": False, "table_option": "labels_only",
        }


def test_winners_json_overrides_config_defaults(tmp_path):
    dest_dir = tmp_path / "final"
    dest_dir.mkdir(parents=True)
    shutil.copy(RAG_PATH, dest_dir / "rag.py")
    (tmp_path / "runs" / "wave-6").mkdir(parents=True)
    (tmp_path / "runs" / "wave-6" / "winners.json").write_text(json.dumps({"reranker": "none", "thinking": True}))

    mod = _load_rag_module(dest_dir / "rag.py", name="final_rag_winners_test")

    assert mod.CONFIG["reranker"] == "none"
    assert mod.CONFIG["thinking"] is True
    assert mod.CONFIG["search"] == "hybrid"  # untouched default


# --------------------------------------------------------------------------
# BM25: tokenizer, the unpickle-without-citation_rag loader, and scoring
# --------------------------------------------------------------------------


def test_tokenize_drops_stopwords_keeps_numbers():
    tokens = rag._tokenize("The Company depends on 1 supplier in Vietnam, for Tariffs.")
    assert tokens == ["company", "depends", "1", "supplier", "vietnam", "tariffs"]


def test_bm25_unpickler_loads_plain_object_never_importing_citation_rag():
    docs = {
        "d1": "the cat sat on the mat",
        "d2": "the dog sat on the log",
        "d3": "cats and dogs sat together on the mat again",
    }
    idx = BM25Index.build("tiny", list(docs.items()))
    buf = io.BytesIO()
    pickle.dump(idx, buf)
    buf.seek(0)
    loaded = rag._BM25Unpickler(buf).load()

    assert type(loaded) is rag._PlainBM25
    assert type(loaded).__module__ != "citation_rag.search.bm25"
    assert loaded.n_docs == idx.n_docs
    assert loaded.avgdl == pytest.approx(idx.avgdl)

    expected = idx.search("sat mat", k=10)
    got = rag._bm25_search(loaded, "sat mat", k=10, ciks=None)
    assert [d for d, _ in got] == [d for d, _ in expected]
    for (d1, s1), (d2, s2) in zip(got, expected):
        assert d1 == d2
        assert s1 == pytest.approx(s2, rel=1e-9)


def test_load_bm25_from_disk_via_project_root(tmp_path, monkeypatch, bm25_index):
    bm25_dir = tmp_path / "data" / "bm25"
    bm25_dir.mkdir(parents=True)
    bm25_index.save(bm25_dir / "fx.pkl")
    monkeypatch.setattr(rag, "PROJECT_ROOT", tmp_path)

    loaded = rag._load_bm25("fx")

    assert type(loaded).__module__ != "citation_rag.search.bm25"
    assert loaded.n_docs == bm25_index.n_docs


# --------------------------------------------------------------------------
# RRF
# --------------------------------------------------------------------------


def test_rrf_matches_the_formula():
    a = [("x", 0.9), ("y", 0.5)]
    b = [("y", 0.8), ("z", 0.4)]
    totals = dict(rag.rrf([a, b], k=60))
    assert totals["x"] == pytest.approx(1 / 61)
    assert totals["y"] == pytest.approx(1 / 62 + 1 / 61)
    assert totals["z"] == pytest.approx(1 / 62)
    order = [d for d, _ in rag.rrf([a, b], k=60)]
    assert order[0] == "y"  # in both lists, beats a single-list hit


# --------------------------------------------------------------------------
# Retrieval against the Postgres fixture
# --------------------------------------------------------------------------


@pg_required
def test_retrieve_vector_only_filters_by_company(pg_schema, models_ready, monkeypatch):
    schema, index_name = pg_schema
    monkeypatch.setitem(rag.CONFIG, "search", "vector")
    monkeypatch.setitem(rag.CONFIG, "reranker", "none")
    conn = rag._get_conn(schema)
    table = f"chunks_{index_name}"

    results = rag.retrieve(conn, table, None, "tariffs on Vietnam imports", ["0000000001"])

    assert results
    assert all(r["cik"] == "0000000001" for r in results)
    assert len(results) <= rag.TOP


@pg_required
def test_retrieve_hybrid_uses_bm25_and_vector_then_reranks(pg_schema, bm25_index, models_ready, monkeypatch):
    schema, index_name = pg_schema
    monkeypatch.setitem(rag.CONFIG, "search", "hybrid")
    monkeypatch.setitem(rag.CONFIG, "reranker", "cross_encoder")
    buf = io.BytesIO()
    pickle.dump(bm25_index, buf)
    buf.seek(0)
    loaded = rag._BM25Unpickler(buf).load()
    conn = rag._get_conn(schema)
    table = f"chunks_{index_name}"

    results = rag.retrieve(conn, table, loaded, "tariffs on Vietnam imports", ["0000000001"])

    assert results
    assert all(r["cik"] == "0000000001" for r in results)
    assert len(results) <= rag.TOP


# --------------------------------------------------------------------------
# Small-to-big context assembly
# --------------------------------------------------------------------------


@pg_required
def test_build_context_small_to_big_from_pg_schema(pg_schema, search_chunks, models_ready):
    schema, _index_name = pg_schema
    conn = rag._get_conn(schema)
    row0, row1 = search_chunks[0], search_chunks[1]
    results = [
        {"section_id": row0["section_id"], "table_id": row0["table_id"]},
        {"section_id": row1["section_id"], "table_id": row1["table_id"]},
    ]

    blocks = rag.build_context(conn, results)

    assert [b["ref"] for b in blocks] == [1, 2]
    assert blocks[0]["text"] == row0["text"]
    assert blocks[0]["item"] == row0["item"]
    assert blocks[0]["page_start"] == row0["page_start"]
    company, fiscal_year, title = _prefix_fields(row0)
    assert blocks[0]["company"] == company
    assert blocks[0]["fiscal_year"] == fiscal_year
    assert blocks[0]["section_title"] == title


@pg_required
def test_build_context_cuts_a_section_that_alone_exceeds_the_budget(pg_schema, search_chunks, models_ready, monkeypatch):
    schema, _index_name = pg_schema
    conn = rag._get_conn(schema)
    monkeypatch.setattr(rag, "CONTEXT_BUDGET", 5)
    row0, row1 = search_chunks[0], search_chunks[1]
    results = [
        {"section_id": row0["section_id"], "table_id": row0["table_id"]},
        {"section_id": row1["section_id"], "table_id": row1["table_id"]},
    ]

    blocks = rag.build_context(conn, results)

    assert len(blocks) == 1
    assert blocks[0]["text"].endswith("[...]")


# --------------------------------------------------------------------------
# Prompt building
# --------------------------------------------------------------------------


def test_format_block_and_build_prompt_include_all_fields():
    block = {
        "ref": 1, "company": "Acme Robotics Inc.", "fiscal_year": 2025, "item": "1A",
        "section_title": "Tariffs and sourcing from Vietnam", "page_start": 5, "page_end": 5,
        "text": "Acme depends on suppliers in Vietnam.",
    }
    formatted = rag.format_block(block)
    assert formatted.startswith("[1] Acme Robotics Inc. | FY2025 | Item 1A |")
    assert "pages 5-5" in formatted
    assert formatted.endswith("Acme depends on suppliers in Vietnam.")

    prompt = rag.build_prompt("What risk?", [block])
    assert "What risk?" in prompt
    assert "Acme depends on suppliers in Vietnam." in prompt
    assert "Reply with JSON only" in prompt


# --------------------------------------------------------------------------
# Response parsing
# --------------------------------------------------------------------------


def test_parse_response_valid_json():
    raw = json.dumps({"answer": "X [1].", "citations": [{"ref": 1, "quote": "q"}], "answerable": True})
    parsed = rag.parse_response(raw)
    assert parsed == {"answer": "X [1].", "citations": [{"ref": 1, "quote": "q"}], "answerable": True}


def test_parse_response_invalid_json_degrades_instead_of_raising():
    parsed = rag.parse_response("not json")
    assert parsed == {"answer": "not json", "citations": [], "answerable": False}


def test_parse_response_defaults_answerable_and_citations():
    parsed = rag.parse_response(json.dumps({"answer": "ok"}))
    assert parsed["answerable"] is True
    assert parsed["citations"] == []


# --------------------------------------------------------------------------
# Citation check
# --------------------------------------------------------------------------


def test_check_citations_valid():
    blocks = [_blk(1, "Acme depends on suppliers in Vietnam.")]
    check = rag.check_citations(
        "Acme faces supply risk [1].", [{"ref": 1, "quote": "depends on suppliers in Vietnam"}], True, blocks
    )
    assert check["valid"] is True


def test_check_citations_invalid_ref():
    blocks = [_blk(1, "text")]
    check = rag.check_citations("Claim [2].", [{"ref": 2, "quote": "text"}], True, blocks)
    assert check["invalid_refs"] == [2]
    assert check["valid"] is False


def test_check_citations_quote_not_found():
    blocks = [_blk(1, "Acme depends on suppliers.")]
    check = rag.check_citations("Claim [1].", [{"ref": 1, "quote": "not in the block"}], True, blocks)
    assert check["quotes_not_found"] == [1]
    assert check["valid"] is False


def test_check_citations_unquoted_marker():
    blocks = [_blk(1, "Acme depends on suppliers.")]
    check = rag.check_citations("Claim [1] and [2].", [{"ref": 1, "quote": "depends on suppliers"}], True, blocks)
    assert check["unquoted_markers"] == [2]
    assert check["valid"] is False


def test_check_citations_refused_with_citations_is_invalid():
    blocks = [_blk(1, "Acme depends on suppliers.")]
    check = rag.check_citations(
        "The filings do not contain this information.",
        [{"ref": 1, "quote": "depends on suppliers"}],
        False,
        blocks,
    )
    assert check["refused_with_citations"] is True
    assert check["valid"] is False


# --------------------------------------------------------------------------
# call_llm: payload shape, no real network call
# --------------------------------------------------------------------------


def test_call_llm_sends_expected_payload_and_returns_response(monkeypatch):
    captured = {}

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": "hi"}

    def _fake_post(url, json, timeout):  # noqa: A002 - matches httpx.post's signature
        captured["url"] = url
        captured["json"] = json
        return _FakeResp()

    monkeypatch.setattr(rag.httpx, "post", _fake_post)

    out = rag.call_llm("PROMPT", "http://fake:11434")

    assert out == "hi"
    assert captured["url"] == "http://fake:11434/api/generate"
    assert captured["json"]["model"] == "qwen3:8b"
    assert captured["json"]["think"] == rag.CONFIG["thinking"]


# --------------------------------------------------------------------------
# main(): CLI wiring, without touching the real pipeline
# --------------------------------------------------------------------------


def test_main_parses_args_and_prints_the_answer(monkeypatch, capsys):
    captured = {}

    def _fake_answer_question(question, companies=None, *, schema=None, llm=None):
        captured["question"] = question
        captured["companies"] = companies
        return {
            "answer": "Answer text [1].", "citations": [{"ref": 1, "quote": "q"}],
            "answerable": True, "citation_check": {"valid": True}, "blocks": [],
        }

    monkeypatch.setattr(rag, "answer_question", _fake_answer_question)
    monkeypatch.setattr(sys, "argv", ["rag.py", "What is the risk?", "--company", "0000000001"])

    rag.main()

    out = capsys.readouterr().out
    assert "Answer text [1]." in out
    assert captured["question"] == "What is the risk?"
    assert captured["companies"] == ["0000000001"]


# --------------------------------------------------------------------------
# End to end: BM25 + vector + RRF + rerank + context + fake LLM + citation
# check, all against the throwaway Postgres schema.
# --------------------------------------------------------------------------


def _extract_first_block_quote(prompt: str) -> "str | None":
    m = re.search(r"^\[\d+\][^\n]*pages \d+-\d+\n", prompt, re.MULTILINE)
    if not m:
        return None
    rest = prompt[m.end():]
    end = rest.find(". ")
    return rest[: end + 1] if end != -1 else rest.split("\n\n")[0]


def _fake_llm() -> FakeLLMClient:
    def _respond(prompt: str) -> str:
        quote = _extract_first_block_quote(prompt)
        if not quote:
            return json.dumps({"answer": "The filings do not contain this information.",
                                "citations": [], "answerable": False})
        return json.dumps({"answer": "Acme faces this risk [1].",
                            "citations": [{"ref": 1, "quote": quote}], "answerable": True})

    return FakeLLMClient(_respond)


@pg_required
def test_answer_question_end_to_end_with_fake_llm(pg_schema, bm25_index, models_ready, monkeypatch, tmp_path):
    schema, index_name = pg_schema
    bm25_dir = tmp_path / "data" / "bm25"
    bm25_dir.mkdir(parents=True)
    bm25_index.save(bm25_dir / f"{index_name}.pkl")
    monkeypatch.setattr(rag, "PROJECT_ROOT", tmp_path)
    monkeypatch.setitem(rag.CONFIG, "index", index_name)
    monkeypatch.setitem(rag.CONFIG, "search", "hybrid")
    monkeypatch.setitem(rag.CONFIG, "reranker", "cross_encoder")

    result = rag.answer_question(
        "What does Acme Robotics say about tariffs on imports from Vietnam?",
        companies=["0000000001"],
        schema=schema,
        llm=_fake_llm(),
    )

    assert result["answer"]
    assert result["citations"]
    assert result["blocks"]
    assert result["citation_check"]["valid"] is True
