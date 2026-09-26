"""Unit tests for wave 4a: schema and loader."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import psycopg
import pytest

from citation_rag.index.load import load_filings, load_chunks, attach_vectors
from citation_rag.index.schema import (
    init_database,
    create_chunk_table,
    create_hnsw_index,
    create_test_schema,
    set_search_path,
    drop_test_schema,
)
from citation_rag.settings import Settings


FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_PARSED = FIXTURES_DIR / "parsed" / "0001111111-25-000001.json"

TEST_SCHEMA = "test_wave4a"


@pytest.fixture(scope="function")
def test_db() -> tuple[psycopg.Connection, str]:
    """Create a test database connection and schema.

    Yields a connection and the test schema name.
    Cleans up the schema after the test.
    """
    settings = Settings()

    # Connect to the main database
    conn = psycopg.connect(settings.database_url)

    try:
        # Create and select test schema
        create_test_schema(conn, TEST_SCHEMA)
        # Set search path to include both test schema and public (for pgvector)
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {TEST_SCHEMA}, public")
        conn.commit()

        # Initialize database in test schema
        init_database(conn)

        yield conn, TEST_SCHEMA
    finally:
        # Clean up: drop schema and close connection
        try:
            # Reset to default schema to drop the test schema
            with conn.cursor() as cur:
                cur.execute("SET search_path TO public")
            conn.commit()
            drop_test_schema(conn, TEST_SCHEMA)
        except Exception:
            pass
        finally:
            conn.close()


def test_schema_creation(test_db: tuple[psycopg.Connection, str]) -> None:
    """Test that schema tables are created."""
    conn, _ = test_db

    with conn.cursor() as cur:
        # Check filings table exists
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = CURRENT_SCHEMA()
                AND table_name = 'filings'
            )
        """)
        assert cur.fetchone()[0] is True

        # Check sections table exists
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = CURRENT_SCHEMA()
                AND table_name = 'sections'
            )
        """)
        assert cur.fetchone()[0] is True

        # Check tables_parsed table exists
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = CURRENT_SCHEMA()
                AND table_name = 'tables_parsed'
            )
        """)
        assert cur.fetchone()[0] is True

        # Check run_log table exists
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = CURRENT_SCHEMA()
                AND table_name = 'run_log'
            )
        """)
        assert cur.fetchone()[0] is True


def test_load_filing(test_db: tuple[psycopg.Connection, str]) -> None:
    """Test loading a fixture filing."""
    conn, _ = test_db

    # Load fixture
    load_filings(FIXTURES_DIR / "parsed", conn)

    # Verify filing was loaded
    with conn.cursor() as cur:
        cur.execute("SELECT accession_no, cik, company FROM filings")
        rows = cur.fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "0001111111-25-000001"
    assert rows[0][1] == "0001111111"
    assert rows[0][2] == "Fixture Corp"

    # Verify sections were loaded
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM sections WHERE accession_no = %s", ("0001111111-25-000001",))
        count = cur.fetchone()[0]

    assert count == 2  # Two sections in fixture (1A:001 and 7:001)

    # Verify tables were loaded
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM tables_parsed WHERE accession_no = %s", ("0001111111-25-000001",))
        count = cur.fetchone()[0]

    assert count == 1  # One table in fixture


def test_chunk_table_creation(test_db: tuple[psycopg.Connection, str]) -> None:
    """Test creation of a chunk table."""
    conn, _ = test_db

    # Create chunk table
    create_chunk_table(conn, "bge_small__s1", embedding_dim=384)

    # Verify table exists
    with conn.cursor() as cur:
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = CURRENT_SCHEMA()
                AND table_name = 'chunks_bge_small__s1'
            )
        """)
        assert cur.fetchone()[0] is True

        # Check columns
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = CURRENT_SCHEMA()
            AND table_name = 'chunks_bge_small__s1'
            ORDER BY ordinal_position
        """)
        columns = [row[0] for row in cur.fetchall()]

    expected_columns = [
        "id", "accession_no", "cik", "item", "section_id", "table_id",
        "seq", "page_start", "page_end", "page_label", "is_table",
        "text", "embed_text", "embedding", "token_count"
    ]
    assert columns == expected_columns


def test_load_chunks_and_vectors(test_db: tuple[psycopg.Connection, str]) -> None:
    """Test loading chunks and attaching vectors."""
    conn, _ = test_db

    # Create chunk table
    create_chunk_table(conn, "bge_small__s1", embedding_dim=384)

    # Create temporary JSONL file with 10 chunks
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        chunks_file = Path(f.name)
        for i in range(10):
            chunk = {
                "accession_no": "0001111111-25-000001",
                "cik": "0001111111",
                "item": "1A",
                "section_id": f"0001111111-25-000001:1A:00{i}",
                "table_id": None,
                "seq": i,
                "page_start": 1,
                "page_end": 2,
                "page_label": "1",
                "is_table": False,
                "text": f"This is chunk {i} of text.",
                "embed_text": f"Prefix\nThis is chunk {i} of text.",
                "token_count": 10 + i,
            }
            f.write(json.dumps(chunk) + "\n")

    try:
        # Load chunks
        load_chunks("bge_small__s1", chunks_file, conn)

        # Verify chunks were loaded
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM chunks_bge_small__s1")
            count = cur.fetchone()[0]

        assert count == 10

        # Create temporary numpy files for vectors
        ids = np.arange(1, 11, dtype=np.int64)  # IDs 1-10
        vectors = np.random.randn(10, 384).astype(np.float32)

        with tempfile.NamedTemporaryFile(suffix=".npy", delete=False) as f:
            ids_file = Path(f.name)
            np.save(ids_file, ids)

        with tempfile.NamedTemporaryFile(suffix=".npy", delete=False) as f:
            vectors_file = Path(f.name)
            np.save(vectors_file, vectors)

        try:
            # Attach vectors
            attach_vectors("bge_small__s1", ids_file, vectors_file, conn)

            # Verify embeddings were loaded
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM chunks_bge_small__s1 WHERE embedding IS NOT NULL")
                count = cur.fetchone()[0]

            assert count == 10

            # Test vector similarity search
            test_vector = vectors[0]  # Use first vector for search
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT id FROM chunks_bge_small__s1 ORDER BY embedding <=> %s::vector LIMIT 3",
                    (test_vector.tolist(),),
                )
                results = [row[0] for row in cur.fetchall()]

            assert len(results) == 3
            assert 1 in results  # First vector should be closest to itself

        finally:
            ids_file.unlink()
            vectors_file.unlink()

    finally:
        chunks_file.unlink()


def test_vector_search_ordering(test_db: tuple[psycopg.Connection, str]) -> None:
    """Test that vector search returns results in order of similarity."""
    conn, _ = test_db

    # Create chunk table
    create_chunk_table(conn, "bge_small__s2", embedding_dim=384)

    # Create temporary JSONL file with 5 chunks
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        chunks_file = Path(f.name)
        for i in range(5):
            chunk = {
                "accession_no": "0001111111-25-000001",
                "cik": "0001111111",
                "item": "7",
                "section_id": f"0001111111-25-000001:7:00{i}",
                "table_id": None,
                "seq": i,
                "page_start": 3,
                "page_end": 3,
                "page_label": "3",
                "is_table": False,
                "text": f"Revenue section {i}",
                "embed_text": f"Item 7 | Revenue section {i}",
                "token_count": 5 + i,
            }
            f.write(json.dumps(chunk) + "\n")

    try:
        load_chunks("bge_small__s2", chunks_file, conn)

        # Create vectors with known relationships
        # First vector is a unit vector in direction (1, 0, 0, ...)
        # Others are similar but not identical
        ids = np.arange(1, 6, dtype=np.int64)
        vectors = np.zeros((5, 384), dtype=np.float32)
        vectors[0, 0] = 1.0  # First vector: (1, 0, 0, ...)
        vectors[1, 0] = 0.9  # Similar to first
        vectors[2, 0] = 0.5  # Less similar
        vectors[3, 0] = 0.1  # Very different
        vectors[4, :] = np.random.randn(384)  # Random

        # Normalize vectors
        for i in range(5):
            norm = np.linalg.norm(vectors[i])
            if norm > 0:
                vectors[i] /= norm

        with tempfile.NamedTemporaryFile(suffix=".npy", delete=False) as f:
            ids_file = Path(f.name)
            np.save(ids_file, ids)

        with tempfile.NamedTemporaryFile(suffix=".npy", delete=False) as f:
            vectors_file = Path(f.name)
            np.save(vectors_file, vectors)

        try:
            attach_vectors("bge_small__s2", ids_file, vectors_file, conn)

            # Search with first vector
            test_vector = vectors[0]
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT id FROM chunks_bge_small__s2 ORDER BY embedding <=> %s::vector LIMIT 5",
                    (test_vector.tolist(),),
                )
                results = [row[0] for row in cur.fetchall()]

            # First result should be ID 1 (itself)
            assert results[0] == 1

        finally:
            ids_file.unlink()
            vectors_file.unlink()

    finally:
        chunks_file.unlink()


def test_multiple_embeddings(test_db: tuple[psycopg.Connection, str]) -> None:
    """Test creating multiple embedding tables with different dimensions."""
    conn, _ = test_db

    # Create tables for different embeddings
    create_chunk_table(conn, "bge_small__s1", embedding_dim=384)
    create_chunk_table(conn, "bge_m3__s1", embedding_dim=1024)

    # Verify both tables exist
    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = CURRENT_SCHEMA()
            AND table_name LIKE 'chunks_%'
            ORDER BY table_name
        """)
        tables = [row[0] for row in cur.fetchall()]

    assert "chunks_bge_small__s1" in tables
    assert "chunks_bge_m3__s1" in tables
