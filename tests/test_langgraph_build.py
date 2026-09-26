"""Tests for wave 8b: the LangGraph comparison build.

Protocol: LIGHT. CPU only, no Ollama calls -- every test uses `FakeChatModel`
(below) in place of `langchain_ollama.ChatOllama`. The embedding model
(bge-small, through `langchain_huggingface.HuggingFaceEmbeddings`) is loaded
for real, from the project's offline cache -- the same thing every other
wave's tests do with `citation_rag.index.models.load_embedder` (see
tests/test_embed.py), just through the framework's wrapper instead. Vector
storage uses a throwaway Postgres schema, `test_wave8b`, created and dropped
by this file, exactly like every other wave's `test_wave{N}` schema.

The fixture corpus is `tests/fixtures/parsed/0001111111-25-000001.json`
("Fixture Corp"), the same one waves 4a and 7a use, paired with its golden
questions in `tests/fixtures/golden_dev.jsonl`.

The `langgraph`/`langchain*` packages live in the project's separate uv
dependency group `langgraph` (contract point: "Uses a separate uv group...
so the main environment stays framework-free"), so they are not installed
in the default environment. Importing them at module level would abort
collection of the *entire* test suite (a plain `pytest tests/` run turns
one missing-module ImportError into "Interrupted: 1 error during
collection", with zero other tests run) for anyone who runs `uv run
pytest` without `--group langgraph`. The `pytest.importorskip` below turns
that into a clean, single-file skip instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip(
    "langchain_core",
    reason=(
        "wave 8b needs the `langgraph` uv dependency group: "
        "run with `uv run --group langgraph pytest tests/test_langgraph_build.py`"
    ),
)

import psycopg
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from citation_rag.evals.runner import run_eval
from citation_rag.index.schema import create_test_schema, drop_test_schema
from citation_rag.settings import Settings
from langgraph_build import adapters
from langgraph_build.ingest import (
    build_vectorstore,
    iter_section_documents,
    load_and_split,
    load_embeddings,
)
from langgraph_build.pipeline import (
    answer_question,
    build_graph,
    format_context,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_PARSED_DIR = FIXTURES_DIR / "parsed"
FIXTURE_GOLDEN = FIXTURES_DIR / "golden_dev.jsonl"

TEST_SCHEMA = "test_wave8b"
TEST_COLLECTION = "test_wave8b_chunks"


class FakeChatModel:
    """Test double for `langchain_ollama.ChatOllama`: `.invoke(prompt)`
    returns an object with a `.content` string, and never makes a network
    call. `responses` is a single string (always returned) or a
    callable(prompt) -> str, mirroring `citation_rag.llm.FakeLLMClient`."""

    class _Message:
        def __init__(self, content: str) -> None:
            self.content = content

    def __init__(self, responses):
        self.responses = responses
        self.prompts_seen: list[str] = []

    def invoke(self, prompt: str) -> FakeChatModel._Message:
        self.prompts_seen.append(prompt)
        if callable(self.responses):
            return self._Message(self.responses(prompt))
        return self._Message(self.responses)


# ---------------------------------------------------------------------------
# ingest.py: section -> Document, table substitution, splitting
# ---------------------------------------------------------------------------


def test_iter_section_documents_substitutes_table_text() -> None:
    docs = list(iter_section_documents(FIXTURE_PARSED_DIR))
    assert len(docs) == 2  # two sections in the fixture (1A:001 and 7:001)

    by_section = {d.metadata["section_id"]: d for d in docs}
    revenue_doc = by_section["0001111111-25-000001:7:001"]
    assert "[Table: t001]" not in revenue_doc.page_content
    assert "Total revenue | 2025: 13,845 | 2024: 11,800" in revenue_doc.page_content

    risk_doc = by_section["0001111111-25-000001:1A:001"]
    assert risk_doc.metadata["accession_no"] == "0001111111-25-000001"
    assert risk_doc.metadata["cik"] == "0001111111"
    assert risk_doc.metadata["item"] == "1A"
    assert risk_doc.metadata["page_start"] == 2


def test_iter_section_documents_skips_failed_filings(tmp_path: Path) -> None:
    import json

    bad = {
        "accession_no": "0000000000-00-000000",
        "cik": "0000000000",
        "company": "Bad Corp",
        "checks": {"passed": False},
        "items": [
            {
                "item": "1A",
                "sections": [{"id": "x:1A:001", "seq": 1, "text": "should not appear", "tables": []}],
            }
        ],
        "tables": [],
    }
    (tmp_path / "0000000000-00-000000.json").write_text(json.dumps(bad))
    assert list(iter_section_documents(tmp_path)) == []


def test_load_and_split_keeps_metadata_and_assigns_chunk_ids() -> None:
    tiny_splitter = RecursiveCharacterTextSplitter(chunk_size=60, chunk_overlap=10)
    docs = load_and_split(FIXTURE_PARSED_DIR, splitter=tiny_splitter)
    assert len(docs) > 2  # the 60-char splitter must cut at least one section into pieces

    risk_chunks = [d for d in docs if d.metadata["section_id"] == "0001111111-25-000001:1A:001"]
    assert len(risk_chunks) > 1
    chunk_ids = [d.metadata["chunk_id"] for d in risk_chunks]
    assert chunk_ids == [f"0001111111-25-000001:1A:001:{i:03d}" for i in range(len(risk_chunks))]
    for d in risk_chunks:
        assert d.metadata["accession_no"] == "0001111111-25-000001"


def test_load_and_split_default_chunking_leaves_short_sections_whole() -> None:
    docs = load_and_split(FIXTURE_PARSED_DIR)
    # every fixture section is well under 1600 chars, so the default splitter
    # (chunk_size=1600) must not cut any of them.
    assert len(docs) == 2


# ---------------------------------------------------------------------------
# pipeline.py: prompt formatting, graph shape
# ---------------------------------------------------------------------------


def test_format_context_includes_citation_metadata() -> None:
    doc = Document(
        page_content="Revenue grew.",
        metadata={"company": "Fixture Corp", "item": "7", "page_start": 3},
    )
    text = format_context([doc])
    assert "[1] Fixture Corp | Item 7 | page 3" in text
    assert "Revenue grew." in text


def test_build_graph_runs_end_to_end_with_fake_llm() -> None:
    """No vector store, no Postgres: a stub retriever object stands in for
    `vectorstore.as_retriever(...)`, isolating the graph wiring itself
    (retrieve -> generate) from storage."""

    doc = Document(
        page_content="A small number of customers account for a large share of our revenue.",
        metadata={"company": "Fixture Corp", "item": "1A", "page_start": 2, "cik": "0001111111"},
    )

    class StubRetriever:
        def invoke(self, question: str) -> list[Document]:
            return [doc]

    class StubVectorstore:
        def as_retriever(self, **kwargs):
            return StubRetriever()

    fake_llm = FakeChatModel("A small number of customers make up most of its revenue (p. 2).")
    graph = build_graph(StubVectorstore(), fake_llm, k=8)

    state = answer_question(graph, "What customer risk does Fixture Corp describe?")

    assert state["context"] == [doc]
    assert state["answer"] == "A small number of customers make up most of its revenue (p. 2)."
    assert "(p. 2)" in fake_llm.prompts_seen[0] or "page 2" in fake_llm.prompts_seen[0]
    assert "Fixture Corp" in fake_llm.prompts_seen[0]


# ---------------------------------------------------------------------------
# End to end: ingest into a throwaway Postgres schema, retrieve, graph, adapters
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bge_small_embeddings():
    return load_embeddings(device="cpu")


@pytest.fixture()
def pg_schema():
    settings = Settings()
    conn = psycopg.connect(settings.database_url)
    try:
        create_test_schema(conn, TEST_SCHEMA)
        yield settings.database_url
    finally:
        try:
            drop_test_schema(conn, TEST_SCHEMA)
        finally:
            conn.close()


@pytest.fixture()
def vectorstore(pg_schema: str, bge_small_embeddings):
    store = build_vectorstore(
        FIXTURE_PARSED_DIR,
        bge_small_embeddings,
        pg_schema,
        collection_name=TEST_COLLECTION,
        schema=TEST_SCHEMA,
    )
    yield store


def test_vectorstore_ingest_and_similarity_search(vectorstore) -> None:
    docs = vectorstore.similarity_search("customer concentration risk", k=8)
    assert len(docs) == 2  # only two chunks exist in the whole fixture corpus
    section_ids = {d.metadata["section_id"] for d in docs}
    assert section_ids == {"0001111111-25-000001:1A:001", "0001111111-25-000001:7:001"}


def test_retriever_adapter_matches_eval_harness_result_shape(vectorstore) -> None:
    retriever = adapters.make_retriever(vectorstore, k=8)
    results = retriever("What customer risk does Fixture Corp describe?", ["0001111111"])
    assert len(results) == 2
    r = results[0]
    assert r.accession_no == "0001111111-25-000001"
    assert r.section_id is not None
    assert r.token_count > 0
    assert isinstance(r.text, str) and r.text


def test_retriever_adapter_filters_by_company(vectorstore) -> None:
    retriever = adapters.make_retriever(vectorstore, k=8)
    results = retriever("anything", ["0009999999"])  # a CIK that does not exist
    assert results == []


def test_run_eval_scores_the_langgraph_retriever(vectorstore, tmp_path: Path) -> None:
    """The compatibility check the contract asks for (point 4): the harness
    that scores every other retriever in this project scores this one too,
    with no changes to citation_rag.evals.runner."""
    retriever = adapters.make_retriever(vectorstore, k=8)
    record = run_eval(
        config={"index": "langgraph_wave8b", "search": "vector", "reranker": "none", "k": 8, "top": 8},
        split="dev",
        retriever=retriever,
        golden_path=FIXTURE_GOLDEN,
        parsed_dir=FIXTURE_PARSED_DIR,
        results_dir=tmp_path,
    )
    assert record.n_questions == 5
    assert "recall@8" in record.metrics
    assert record.metrics["recall@8"]["value"] > 0.0  # a real hit, not a placeholder


def test_graph_end_to_end_with_real_retrieval_and_fake_llm(vectorstore) -> None:
    fake_llm = FakeChatModel(
        "A small number of customers make up most of its revenue (p. 2)."
    )
    graph = build_graph(vectorstore, fake_llm, k=8)
    state = answer_question(
        graph, "What customer risk does Fixture Corp describe?", companies=["0001111111"]
    )
    assert len(state["context"]) == 2
    assert state["answer"] == "A small number of customers make up most of its revenue (p. 2)."


def test_answer_adapter_returns_answer_and_context(vectorstore) -> None:
    fake_llm = FakeChatModel("I do not know.")
    graph = build_graph(vectorstore, fake_llm, k=8)
    answer_fn = adapters.make_answer_fn(graph)
    result = answer_fn("What guidance did Fixture Corp give for fiscal 2026?", companies=["0001111111"])
    assert result["answer"] == "I do not know."
    assert len(result["context"]) == 2
    assert all(r.accession_no == "0001111111-25-000001" for r in result["context"])


# ---------------------------------------------------------------------------
# variants/: the two effort-log changes (NOTES.md), each on its own branch
# of the code -- neither touches pipeline.py or ingest.py.
# ---------------------------------------------------------------------------


def test_bm25_hybrid_variant_dedupes_across_both_retrievers(vectorstore) -> None:
    from langgraph_build.variants.bm25_hybrid import build_hybrid_retriever

    docs = load_and_split(FIXTURE_PARSED_DIR)
    hybrid = build_hybrid_retriever(docs, vectorstore, k=8)
    results = hybrid.invoke("customer concentration risk")
    # both retrievers see the same two chunks; the ensemble must merge them
    # into two results (by chunk_id), not return duplicates.
    assert len(results) == 2
    chunk_ids = {d.metadata["chunk_id"] for d in results}
    assert chunk_ids == {
        "0001111111-25-000001:1A:001:000",
        "0001111111-25-000001:7:001:000",
    }


def test_page_citations_variant_flags_a_quote_not_in_the_text() -> None:
    import json

    from langgraph_build.variants.page_citations import build_graph_with_citation_check

    doc = Document(
        page_content="A small number of customers account for a large share of our revenue.",
        metadata={"company": "Fixture Corp", "item": "1A", "page_start": 2},
    )

    class StubRetriever:
        def invoke(self, question: str) -> list[Document]:
            return [doc]

    class StubVectorstore:
        def as_retriever(self, **kwargs):
            return StubRetriever()

    bad_quote = json.dumps(
        {
            "answer": "Customers are concentrated [1].",
            "citations": [{"ref": 1, "quote": "this text is not in the excerpt"}],
        }
    )
    graph = build_graph_with_citation_check(StubVectorstore(), FakeChatModel(bad_quote), k=8)
    state = graph.invoke({"question": "What customer risk does Fixture Corp describe?"})
    assert state["citation_check"].valid is False
    assert state["citation_check"].quotes_not_found == [1]


def test_page_citations_variant_valid_quote_passes() -> None:
    import json

    from langgraph_build.variants.page_citations import build_graph_with_citation_check

    doc = Document(
        page_content="A small number of customers account for a large share of our revenue.",
        metadata={"company": "Fixture Corp", "item": "1A", "page_start": 2},
    )

    class StubRetriever:
        def invoke(self, question: str) -> list[Document]:
            return [doc]

    class StubVectorstore:
        def as_retriever(self, **kwargs):
            return StubRetriever()

    good_quote = json.dumps(
        {
            "answer": "Customers are concentrated [1].",
            "citations": [
                {
                    "ref": 1,
                    "quote": "A small number of customers account for a large share of our revenue.",
                }
            ],
        }
    )
    graph = build_graph_with_citation_check(StubVectorstore(), FakeChatModel(good_quote), k=8)
    state = graph.invoke({"question": "What customer risk does Fixture Corp describe?"})
    assert state["citation_check"].valid is True


def test_page_citations_variant_degrades_on_unparsable_output() -> None:
    from langgraph_build.variants.page_citations import build_graph_with_citation_check

    doc = Document(page_content="Some text.", metadata={"company": "Fixture Corp", "item": "1A", "page_start": 2})

    class StubRetriever:
        def invoke(self, question: str) -> list[Document]:
            return [doc]

    class StubVectorstore:
        def as_retriever(self, **kwargs):
            return StubRetriever()

    graph = build_graph_with_citation_check(StubVectorstore(), FakeChatModel("not json at all"), k=8)
    state = graph.invoke({"question": "Any question"})
    assert state["answer"] == "not json at all"
    assert state["citation_check"].valid is True  # degrades cleanly, no citations to check
