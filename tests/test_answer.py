"""Unit tests for wave 7a: answer generation, citations, run log.

All tests use `FakeLLMClient` (no Ollama calls). The pipeline end-to-end
test uses a throwaway Postgres schema (`test_wave7a`), created and dropped
by the test itself, loaded from the existing Fixture Corp fixture
(`tests/fixtures/parsed/0001111111-25-000001.json`, already used by wave
4a's and the eval harness's own tests).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest

from citation_rag.answer import run_all
from citation_rag.answer.cite import check_citations
from citation_rag.answer.context import ContextBlock, _window_section, build_context
from citation_rag.answer.pipeline import answer
from citation_rag.answer.prompt import build_prompt
from citation_rag.chunk.tokens import count_tokens
from citation_rag.index.load import load_filings
from citation_rag.index.schema import (
    create_test_schema,
    drop_test_schema,
    init_database,
)
from citation_rag.search.router import FakeLLMClient
from citation_rag.settings import Settings

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_PARSED_DIR = FIXTURES_DIR / "parsed"
LONG_SECTION_FIXTURE = json.loads((FIXTURES_DIR / "answer_long_section.json").read_text())

TEST_SCHEMA = "test_wave7a"


@dataclass
class FakeResult:
    """Matches the `Result`/`ScoredResult` attribute shape (chunk_id, text,
    token_count, section_id, table_id, page_start, page_end, accession_no)."""

    chunk_id: object
    text: str
    token_count: int
    section_id: "str | None"
    table_id: "str | None"
    page_start: "int | None"
    page_end: "int | None"
    accession_no: str


def make_block(ref: int, text: str = "The quick brown fox jumps over the lazy dog.", **overrides) -> ContextBlock:
    defaults = dict(
        ref=ref,
        chunk_ids=[ref * 10],
        section_id=f"acc-1:1A:{ref:03d}",
        accession_no="acc-1",
        company="Fixture Corp",
        fiscal_year=2025,
        item="1A",
        section_title="Risk Factors",
        page_start=1,
        page_end=2,
        text=text,
    )
    defaults.update(overrides)
    return ContextBlock(**defaults)


# ---------------------------------------------------------------------------
# context.py: window logic on a long fixture section
# ---------------------------------------------------------------------------


def test_window_section_stays_under_budget_and_keeps_both_matches() -> None:
    full_text = LONG_SECTION_FIXTURE["text"]
    chunk_texts = LONG_SECTION_FIXTURE["chunk_texts"]
    assert count_tokens(full_text) > 4000  # the fixture is deliberately oversized

    windowed = _window_section(full_text, chunk_texts, budget_tokens=4000)

    assert count_tokens(windowed) <= 4000
    for ct in chunk_texts:
        assert ct in windowed
    # the two matches are far apart in the fixture (paragraph 10 and 70 of
    # 90), so a bounded window must skip the middle -> a gap marker appears.
    assert "[...]" in windowed


def test_window_section_short_text_is_unchanged() -> None:
    text = "One paragraph only, well under budget."
    assert _window_section(text, [text], budget_tokens=4000) == text


# ---------------------------------------------------------------------------
# context.py: build_context grouping, ordering, table substitution, budget
# ---------------------------------------------------------------------------


def test_build_context_groups_orders_and_stops_at_budget() -> None:
    text_a = "Alpha section text about revenue growth in the product segment during fiscal 2025."
    text_b = "Beta section text about risk factors related to overseas suppliers and shipment delays."
    text_c = "Gamma section text about litigation and regulatory compliance matters across jurisdictions."
    budget = count_tokens(text_a) + count_tokens(text_b)  # room for exactly A and B, not C

    sections = {
        "sec-a": {"accession_no": "acc-1", "item": "1A", "title": "A title", "page_start": 1, "page_end": 1, "text": text_a},
        "sec-b": {"accession_no": "acc-1", "item": "7", "title": "B title", "page_start": 2, "page_end": 2, "text": text_b},
        "sec-c": {"accession_no": "acc-2", "item": "1A", "title": "C title", "page_start": 1, "page_end": 1, "text": text_c},
    }
    filings = {
        "acc-1": {"accession_no": "acc-1", "company": "Alpha Beta Corp", "fiscal_year": 2025},
        "acc-2": {"accession_no": "acc-2", "company": "Gamma Inc", "fiscal_year": 2024},
    }

    results = [
        FakeResult(1, text_a, 10, "sec-a", None, 1, 1, "acc-1"),
        FakeResult(2, text_b, 10, "sec-b", None, 2, 2, "acc-1"),
        FakeResult(3, text_c, 10, "sec-c", None, 1, 1, "acc-2"),
        FakeResult(4, text_b, 10, "sec-b", None, 2, 2, "acc-1"),  # second chunk in section B
    ]

    blocks = build_context(
        results,
        budget_tokens=budget,
        section_window_tokens=1000,
        fetch_sections=lambda ids: {i: sections[i] for i in ids if i in sections},
        fetch_filings=lambda accs: {a: filings[a] for a in accs if a in filings},
        fetch_tables=lambda tids, acc: {},
    )

    assert [b.section_id for b in blocks] == ["sec-a", "sec-b"]  # rank order, C excluded by budget
    assert blocks[0].ref == 1
    assert blocks[1].ref == 2
    assert blocks[1].chunk_ids == [2, 4]  # both chunks in section B collected
    assert blocks[0].company == "Alpha Beta Corp"
    assert blocks[0].fiscal_year == 2025
    assert blocks[0].item == "1A"
    assert blocks[0].section_title == "A title"
    assert blocks[0].page_start == 1 and blocks[0].page_end == 1


def test_build_context_substitutes_table_text() -> None:
    section_text = "Revenue grew this year.\n\n[Table: t001]\n\nServices revenue declined."
    sections = {
        "sec-t": {"accession_no": "acc-1", "item": "7", "title": "Revenue", "page_start": 3, "page_end": 3, "text": section_text},
    }
    filings = {"acc-1": {"accession_no": "acc-1", "company": "Fixture Corp", "fiscal_year": 2025}}
    table_rows = {"acc-1:t001": {"id": "acc-1:t001", "text": "Product | 2025: 12,345 | 2024: 10,000"}}

    results = [FakeResult(1, "Product | 2025: 12,345", 5, "sec-t", "t001", 3, 3, "acc-1")]

    blocks = build_context(
        results,
        fetch_sections=lambda ids: {i: sections[i] for i in ids if i in sections},
        fetch_filings=lambda accs: {a: filings[a] for a in accs if a in filings},
        fetch_tables=lambda tids, acc: table_rows,
    )

    assert len(blocks) == 1
    assert "[Table: t001]" in blocks[0].text
    assert "Product | 2025: 12,345 | 2024: 10,000" in blocks[0].text


def test_build_context_empty_results() -> None:
    assert build_context([]) == []


# ---------------------------------------------------------------------------
# prompt.py: every block header is present
# ---------------------------------------------------------------------------


def test_prompt_contains_every_block_header() -> None:
    blocks = [
        make_block(1, text="First block text.", company="Alpha Corp", fiscal_year=2025, item="1A", section_title="Risk Factors", page_start=1, page_end=2),
        make_block(2, text="Second block text.", company="Beta Inc", fiscal_year=2024, item="7", section_title="MD&A", page_start=5, page_end=6),
    ]
    prompt = build_prompt("What risks are named?", blocks, mode="rag")

    assert "[1] Alpha Corp | FY2025 | Item 1A | Risk Factors | pages 1-2" in prompt
    assert "[2] Beta Inc | FY2024 | Item 7 | MD&A | pages 5-6" in prompt
    assert "First block text." in prompt
    assert "Second block text." in prompt
    assert "What risks are named?" in prompt


def test_prompt_no_docs_mode_has_no_blocks() -> None:
    prompt = build_prompt("What risks are named?", [], mode="no_docs")
    assert "What risks are named?" in prompt
    assert "[1]" not in prompt


# ---------------------------------------------------------------------------
# cite.py: valid, invalid ref, quote not found, unquoted marker, refusal
# ---------------------------------------------------------------------------


def test_citation_check_valid() -> None:
    block = make_block(1, text="Revenue grew due to strong product demand.")
    result = check_citations(
        "Revenue grew [1].",
        [{"ref": 1, "quote": "Revenue grew due to strong product demand."}],
        answerable=True,
        blocks=[block],
    )
    assert result.valid is True
    assert result.invalid_refs == []
    assert result.quotes_not_found == []
    assert result.unquoted_markers == []
    assert result.refused_with_citations is False
    assert result.citation_map[1]["company"] == "Fixture Corp"


def test_citation_check_invalid_ref() -> None:
    block = make_block(1, text="Revenue grew due to strong product demand.")
    result = check_citations(
        "Revenue grew [2].",
        [{"ref": 2, "quote": "Revenue grew due to strong product demand."}],
        answerable=True,
        blocks=[block],
    )
    assert result.valid is False
    assert result.invalid_refs == [2]


def test_citation_check_quote_not_found() -> None:
    block = make_block(1, text="Revenue grew due to strong product demand.")
    result = check_citations(
        "Revenue grew [1].",
        [{"ref": 1, "quote": "this text is not in the block anywhere"}],
        answerable=True,
        blocks=[block],
    )
    assert result.valid is False
    assert result.quotes_not_found == [1]


def test_citation_check_unquoted_marker() -> None:
    block = make_block(1, text="Revenue grew due to strong product demand.")
    result = check_citations(
        "Revenue grew [1] and margins improved [2].",
        [{"ref": 1, "quote": "Revenue grew due to strong product demand."}],
        answerable=True,
        blocks=[block],
    )
    assert result.valid is False
    assert result.unquoted_markers == [2]


def test_citation_check_refusal_with_citations_is_flagged() -> None:
    block = make_block(1, text="Revenue grew due to strong product demand.")
    result = check_citations(
        "The filings do not contain this information.",
        [{"ref": 1, "quote": "Revenue grew due to strong product demand."}],
        answerable=False,
        blocks=[block],
    )
    assert result.valid is False
    assert result.refused_with_citations is True


def test_citation_check_clean_refusal_is_valid() -> None:
    block = make_block(1, text="Revenue grew due to strong product demand.")
    result = check_citations(
        "The filings do not contain this information.",
        [],
        answerable=False,
        blocks=[block],
    )
    assert result.valid is True
    assert result.refused_with_citations is False


# ---------------------------------------------------------------------------
# pipeline.py: end to end on the fixture corpus, throwaway Postgres schema
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_db():
    settings = Settings()
    conn = psycopg.connect(settings.database_url)
    try:
        create_test_schema(conn, TEST_SCHEMA)
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {TEST_SCHEMA}, public")
        conn.commit()
        init_database(conn)
        load_filings(FIXTURE_PARSED_DIR, conn)
        conn.commit()
        yield conn
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("SET search_path TO public")
            conn.commit()
            drop_test_schema(conn, TEST_SCHEMA)
        except Exception:
            pass
        finally:
            conn.close()


def _fixture_retriever(question: str, companies) -> list[FakeResult]:
    text = (
        "A small number of customers account for a large share of our revenue, "
        "and the loss of any one of them would materially harm our results."
    )
    return [FakeResult(1, text, 30, "0001111111-25-000001:1A:001", None, 2, 2, "0001111111-25-000001")]


def test_pipeline_end_to_end_writes_run_log(test_db: psycopg.Connection) -> None:
    from psycopg_pool import ConnectionPool

    settings = Settings()
    pool = ConnectionPool(settings.database_url, min_size=1, max_size=2, open=True)
    try:
        fake_answer = json.dumps(
            {
                "answer": "A small number of customers make up most of its revenue [1].",
                "citations": [
                    {
                        "ref": 1,
                        "quote": (
                            "A small number of customers account for a large share of our "
                            "revenue, and the loss of any one of them would materially harm "
                            "our results."
                        ),
                    }
                ],
                "answerable": True,
            }
        )
        llm_client = FakeLLMClient(fake_answer, model="fake-qwen")

        config = {"mode": "rag", "top": 1, "model": "fake-qwen", "thinking": False}
        record = answer(
            "What customer risk does Fixture Corp describe?",
            config,
            companies=["0001111111"],
            retriever=_fixture_retriever,
            llm_client=llm_client,
            pool=pool,
            schema=TEST_SCHEMA,
        )

        assert record.run_log_id is not None
        assert record.answerable is True
        assert record.citation_check.valid is True
        assert record.section_ids == ["0001111111-25-000001:1A:001"]
        assert record.context_tokens > 0
        assert "llm_ms" in record.latency_ms

        with test_db.cursor() as cur:
            cur.execute(f"SET search_path TO {TEST_SCHEMA}, public")
            cur.execute("SELECT question, model, thinking, context_tokens FROM run_log WHERE id = %s", (record.run_log_id,))
            row = cur.fetchone()
        test_db.commit()
        assert row is not None
        assert row[1] == "fake-qwen"
        assert row[2] is False
    finally:
        pool.close()


def test_pipeline_no_docs_mode_skips_retrieval_and_citations() -> None:
    llm_client = FakeLLMClient(json.dumps({"answer": "I do not know.", "answerable": False}))
    config = {"mode": "no_docs", "model": "fake-qwen", "thinking": True}
    record = answer(
        "What is Fixture Corp's fiscal 2026 guidance?",
        config,
        companies=["0001111111"],
        llm_client=llm_client,
        log=False,
    )
    assert record.answer == "I do not know."
    assert record.answerable is False
    assert record.citation_check.valid is True
    assert record.retrieved_ids == []
    assert record.section_ids == []


def test_pipeline_degrades_on_unparsable_llm_output() -> None:
    llm_client = FakeLLMClient("not json at all")
    config = {"mode": "no_docs", "model": "fake-qwen"}
    record = answer(
        "Any question",
        config,
        companies="general",
        llm_client=llm_client,
        log=False,
    )
    assert record.answerable is False
    assert record.answer == "not json at all"
    assert record.citation_check.valid is True  # no_docs mode: trivially valid


# ---------------------------------------------------------------------------
# run_all.py: --dry-run prints the four runs
# ---------------------------------------------------------------------------


def test_run_all_plan_lists_four_runs() -> None:
    items = run_all.plan("dev", run_all.RUN_NAMES, {"index": "bge_small__s1", "search": "hybrid", "top": 8})
    assert [i["run_name"] for i in items] == ["A_think", "A_nothink", "B_think", "B_nothink"]
    assert items[0]["config"]["mode"] == "no_docs"
    assert items[0]["config"]["thinking"] is True
    assert items[1]["config"]["thinking"] is False
    assert items[2]["config"]["mode"] == "rag"
    assert all(i["n_questions"] > 0 for i in items)


def test_run_all_main_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    rc = run_all.main(["--dry-run"])
    assert rc == 0
    out_lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(out_lines) == 4
    run_names = [json.loads(line)["run_name"] for line in out_lines]
    assert run_names == run_all.RUN_NAMES
