"""Wave 8b, contract point 2: load the same passing filings' section texts
into the framework's own table.

`data/parsed/{accession_no}.json` (schemas.md section 1) is read directly --
no `citation_rag/` import -- one `Document` per section. A section's prose
stays prose; each `[Table: id]` placeholder is replaced with that table's
own row-text (schemas.md: "the row form used for BM25 and embedding"), so
table content is indexed as text, never skipped or kept as a raw object.
This is what contract point 2 means by "prose only, tables as text": the
corpus content is identical to what `final/rag.py` (wave 8a) and the eval
harness read from the same JSON, only the loading and storage code differs.

The embedding model is the same `bge-small-en-v1.5` the rest of the project
uses (from the project's offline Hugging Face cache), through the
framework's own `HuggingFaceEmbeddings` wrapper instead of
`citation_rag.index.models.load_embedder`. The splitter is the framework's
own default, `RecursiveCharacterTextSplitter`, at `chunk_size=1600`
characters / `chunk_overlap=200` (about 400 tokens -- this splitter has no
token-based default, so the contract gives the character equivalent).
Storage is `langchain_postgres.PGVector`, LangChain's own vector store
wrapper over Postgres + pgvector.

CLI:
    uv run --group langgraph python -m langgraph_build.ingest \\
        --parsed-dir tests/fixtures/parsed_samples --collection wave8b
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path

from langchain_core.documents import Document
from langchain_postgres import PGVector
from langchain_text_splitters import RecursiveCharacterTextSplitter

DEFAULT_CHUNK_SIZE = 1600  # characters, ~400 tokens (contract point 1)
DEFAULT_CHUNK_OVERLAP = 200
DEFAULT_COLLECTION_NAME = "wave8b"
BGE_SMALL_MODEL_ID = "BAAI/bge-small-en-v1.5"

_TABLE_PLACEHOLDER_RE = re.compile(r"^\[Table: (?P<id>.+?)\]$", re.MULTILINE)


def _substitute_tables(text: str, tables_by_id: dict[str, dict]) -> str:
    """Replace every `[Table: id]` placeholder line with that table's own
    `text` field. A placeholder whose id is not in `tables_by_id` (should
    not happen for a filing that passed its checks) is left as-is."""

    def _sub(match: re.Match[str]) -> str:
        table = tables_by_id.get(match.group("id"))
        return table["text"] if table else match.group(0)

    return _TABLE_PLACEHOLDER_RE.sub(_sub, text)


def iter_section_documents(parsed_dir: Path | str) -> Iterator[Document]:
    """One `Document` per parsed section of every *passing* filing.

    `filing["checks"]["passed"]` must be true (contract point 2: "the same
    passing filings"). A section with empty text (should not occur) is
    skipped rather than indexed as a blank chunk.
    """
    parsed_dir = Path(parsed_dir)
    for fp in sorted(parsed_dir.glob("*.json")):
        filing = json.loads(fp.read_text(encoding="utf-8"))
        if not filing.get("checks", {}).get("passed", False):
            continue
        tables_by_id = {t["id"]: t for t in filing.get("tables", [])}
        for item in filing.get("items", []):
            for section in item.get("sections", []):
                text = _substitute_tables(section.get("text", ""), tables_by_id)
                if not text.strip():
                    continue
                yield Document(
                    page_content=text,
                    metadata={
                        "accession_no": filing["accession_no"],
                        "cik": filing["cik"],
                        "company": filing.get("company"),
                        "fiscal_year": filing.get("fiscal_year"),
                        "item": item["item"],
                        "section_id": section["id"],
                        "section_title": section.get("title"),
                        "page_start": section.get("page_start"),
                        "page_end": section.get("page_end"),
                        "table_ids": section.get("tables", []) or [],
                    },
                )


def load_and_split(
    parsed_dir: Path | str,
    splitter: RecursiveCharacterTextSplitter | None = None,
) -> list[Document]:
    """Section documents, cut by the framework's own default splitter.

    Every split keeps its parent section's metadata (LangChain's own
    `split_documents` behavior) plus a `chunk_id`
    (`"{section_id}:{NNN}"`), added here so the retriever adapter
    (`langgraph_build/adapters.py`) can hand the eval harness a stable
    `Result.chunk_id` per chunk.
    """
    if splitter is None:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=DEFAULT_CHUNK_SIZE, chunk_overlap=DEFAULT_CHUNK_OVERLAP
        )
    docs = list(iter_section_documents(parsed_dir))
    split_docs = splitter.split_documents(docs)
    counters: dict[str, int] = {}
    for doc in split_docs:
        section_id = doc.metadata.get("section_id")
        idx = counters.get(section_id, 0)
        doc.metadata["chunk_id"] = f"{section_id}:{idx:03d}"
        counters[section_id] = idx + 1
    return split_docs


def load_embeddings(device: str = "cpu"):
    """The project's bge-small model, through the framework's own
    `HuggingFaceEmbeddings` wrapper (contract point 1: "the same bge-small
    model through HuggingFaceEmbeddings"). Reads the cache folder from
    `citation_rag.settings.Settings` (never prints `.env`), matching how
    `citation_rag.index.models.load_embedder` finds the same cache.
    """
    from langchain_huggingface import HuggingFaceEmbeddings

    from citation_rag.settings import Settings

    settings = Settings()
    os.environ.setdefault("HF_HOME", settings.hf_home)
    os.environ["HF_HUB_OFFLINE"] = "1"
    return HuggingFaceEmbeddings(
        model_name=BGE_SMALL_MODEL_ID,
        cache_folder=settings.hf_home,
        model_kwargs={"device": device},
        encode_kwargs={"normalize_embeddings": True},
    )


def _psycopg_connection_string(database_url: str) -> str:
    """SQLAlchemy (which `langchain_postgres.PGVector` builds its engine on)
    needs a driver name in the URL. The project's psycopg (v3) driver is
    already a dependency, so `postgresql://` becomes `postgresql+psycopg://`."""
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url[len("postgresql://") :]
    return database_url


def build_vectorstore(
    parsed_dir: Path | str,
    embeddings,
    database_url: str,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    schema: str | None = None,
    splitter: RecursiveCharacterTextSplitter | None = None,
    pre_delete_collection: bool = True,
) -> PGVector:
    """Ingest every passing filing under `parsed_dir` into a `PGVector` store.

    `schema`, when given, goes first in the connection's `search_path`
    (with `public` kept right after it, so the pgvector extension's
    `vector` type still resolves). This is how the wave-8b tests get
    `PGVector`'s own `langchain_pg_collection` / `langchain_pg_embedding`
    tables created inside a throwaway schema (`test_wave8b`) instead of the
    project's default `public` schema -- the framework's storage code is
    otherwise untouched.
    """
    docs = load_and_split(parsed_dir, splitter=splitter)
    connection = _psycopg_connection_string(database_url)
    engine_args: dict = {}
    if schema is not None:
        engine_args["connect_args"] = {"options": f"-csearch_path={schema},public"}
    vectorstore = PGVector(
        embeddings=embeddings,
        collection_name=collection_name,
        connection=connection,
        use_jsonb=True,
        pre_delete_collection=pre_delete_collection,
        engine_args=engine_args,
    )
    if docs:
        vectorstore.add_documents(docs)
    return vectorstore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="langgraph_build.ingest")
    parser.add_argument("--parsed-dir", required=True)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION_NAME)
    parser.add_argument("--schema", default=None)
    parser.add_argument("--device", default="cpu", choices=["cpu", "mps"])
    args = parser.parse_args(argv)

    from citation_rag.settings import Settings

    settings = Settings()
    embeddings = load_embeddings(device=args.device)
    vectorstore = build_vectorstore(
        args.parsed_dir,
        embeddings,
        settings.database_url,
        collection_name=args.collection,
        schema=args.schema,
    )
    n_docs = len(load_and_split(args.parsed_dir))
    print(f"collection={args.collection} schema={args.schema} chunks_ingested={n_docs}")
    del vectorstore
    return 0


if __name__ == "__main__":
    sys.exit(main())
