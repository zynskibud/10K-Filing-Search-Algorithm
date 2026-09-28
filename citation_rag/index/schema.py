"""Database schema for wave 4 index storage.

Defines:
- filings: filing metadata
- sections: section-level text from filings
- tables_parsed: parsed table records
- chunks_{index_name}: chunk records per embedding model/strategy
- run_log: query execution log for wave 7
"""

from __future__ import annotations

import psycopg


def init_database(conn: psycopg.Connection) -> None:
    """Create all schema tables and indexes.

    Idempotent: safe to call multiple times.
    Assumes pgvector extension is available (created in public schema).
    """
    with conn.cursor() as cur:
        # pgvector (in public schema for shared use)
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception:
            # Extension may already exist in public schema
            pass

        # filings table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS filings (
                accession_no TEXT PRIMARY KEY,
                cik TEXT NOT NULL,
                company TEXT NOT NULL,
                ticker TEXT NOT NULL,
                fiscal_year INT NOT NULL,
                filed_date TEXT NOT NULL,
                filer_category TEXT NOT NULL,
                sic TEXT NOT NULL,
                source_url TEXT NOT NULL,
                page_count INT NOT NULL
            )
        """)

        # sections table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sections (
                id TEXT PRIMARY KEY,
                accession_no TEXT NOT NULL REFERENCES filings(accession_no) ON DELETE CASCADE,
                item TEXT NOT NULL,
                part TEXT NOT NULL,
                seq INT NOT NULL,
                title TEXT NOT NULL,
                page_start INT NOT NULL,
                page_end INT NOT NULL,
                text TEXT NOT NULL,
                token_count INT NOT NULL
            )
        """)

        # Create indexes on sections
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sections_accession_no ON sections(accession_no)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sections_item ON sections(item)")

        # tables_parsed table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tables_parsed (
                id TEXT PRIMARY KEY,
                accession_no TEXT NOT NULL REFERENCES filings(accession_no) ON DELETE CASCADE,
                section_id TEXT NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
                item TEXT NOT NULL,
                title TEXT,
                units TEXT,
                headers JSONB NOT NULL,
                rows JSONB NOT NULL,
                text TEXT NOT NULL,
                page_start INT NOT NULL,
                page_end INT NOT NULL
            )
        """)

        # Create indexes on tables_parsed
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tables_accession_no ON tables_parsed(accession_no)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tables_item ON tables_parsed(item)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tables_section_id ON tables_parsed(section_id)")

        # run_log table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS run_log (
                id BIGSERIAL PRIMARY KEY,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                question TEXT NOT NULL,
                config JSONB,
                router JSONB,
                retrieved_ids BIGINT[],
                reranked_ids BIGINT[],
                section_ids TEXT[],
                context_tokens INT,
                answer TEXT,
                citations JSONB,
                model TEXT,
                thinking BOOL,
                latency_ms JSONB
            )
        """)

    conn.commit()


def create_chunk_table(
    conn: psycopg.Connection,
    index_name: str,
    embedding_dim: int,
) -> None:
    """Create a chunks table for a specific embedding index.

    Args:
        conn: Database connection
        index_name: Name of the index (e.g., 'bge_small__s1')
        embedding_dim: Embedding dimension (384 for bge_small, 1024 for bge_m3)

    Creates:
    - chunks_{index_name} table with columns matching schema section 4
    - HNSW index on embedding with m=16, ef_construction=64
    - B-tree indexes on (accession_no), (cik), (item), (section_id)
    """
    table_name = f"chunks_{index_name}"

    with conn.cursor() as cur:
        # Create table with embedding as null initially
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id BIGSERIAL PRIMARY KEY,
                chunk_key TEXT,
                accession_no TEXT NOT NULL,
                cik TEXT NOT NULL,
                item TEXT NOT NULL,
                section_id TEXT,
                table_id TEXT,
                seq INT NOT NULL,
                page_start INT NOT NULL,
                page_end INT NOT NULL,
                page_label TEXT,
                is_table BOOL NOT NULL,
                text TEXT NOT NULL,
                embed_text TEXT NOT NULL,
                embedding vector({embedding_dim}),
                token_count INT NOT NULL
            )
        """)

        # B-tree indexes
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_accession_no ON {table_name}(accession_no)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_cik ON {table_name}(cik)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_item ON {table_name}(item)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_section_id ON {table_name}(section_id)")

    conn.commit()


def create_hnsw_index(
    conn: psycopg.Connection,
    index_name: str,
) -> None:
    """Create HNSW index on embeddings after data is loaded.

    Args:
        conn: Database connection
        index_name: Name of the chunk table (e.g., 'bge_small__s1')
    """
    table_name = f"chunks_{index_name}"
    hnsw_index_name = f"idx_{table_name}_embedding"

    with conn.cursor() as cur:
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS {hnsw_index_name}
            ON {table_name}
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """)

    conn.commit()


def drop_test_schema(conn: psycopg.Connection, schema_name: str) -> None:
    """Drop a test schema and all its tables.

    Args:
        conn: Database connection
        schema_name: Name of the schema to drop
    """
    with conn.cursor() as cur:
        # Drop the schema and all its contents
        cur.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")

    conn.commit()


def create_test_schema(conn: psycopg.Connection, schema_name: str) -> None:
    """Create a test schema.

    Args:
        conn: Database connection
        schema_name: Name of the schema to create
    """
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")

    conn.commit()


def set_search_path(conn: psycopg.Connection, schema_name: str) -> None:
    """Set the search path to a specific schema.

    Args:
        conn: Database connection
        schema_name: Name of the schema
    """
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema_name}")

    conn.commit()
