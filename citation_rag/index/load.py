"""Load parsed filings, sections, tables, and chunks into the database."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import psycopg
from psycopg.rows import DictRow

from .schema import create_chunk_table, create_hnsw_index


def load_filings(parsed_dir: str | Path, conn: psycopg.Connection) -> None:
    """Load filings from parsed JSON files with checks.passed=true.

    Upserts filings, sections (with token_count=0 for now), and tables_parsed
    from all JSON files in parsed_dir where checks.passed is true.

    Args:
        parsed_dir: Path to directory containing parsed JSON files
        conn: Database connection
    """
    parsed_dir = Path(parsed_dir)

    with conn.cursor() as cur:
        for json_file in sorted(parsed_dir.glob("*.json")):
            with json_file.open() as f:
                data = json.load(f)

            # Skip if checks did not pass
            if not data.get("checks", {}).get("passed", False):
                continue

            accession_no = data["accession_no"]

            # Upsert filing
            cur.execute("""
                INSERT INTO filings (
                    accession_no, cik, company, ticker, fiscal_year,
                    filed_date, filer_category, sic, source_url, page_count
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (accession_no) DO UPDATE SET
                    cik=EXCLUDED.cik,
                    company=EXCLUDED.company,
                    ticker=EXCLUDED.ticker,
                    fiscal_year=EXCLUDED.fiscal_year,
                    filed_date=EXCLUDED.filed_date,
                    filer_category=EXCLUDED.filer_category,
                    sic=EXCLUDED.sic,
                    source_url=EXCLUDED.source_url,
                    page_count=EXCLUDED.page_count
            """, (
                accession_no,
                data["cik"],
                data["company"],
                data["ticker"],
                data["fiscal_year"],
                data["filed_date"],
                data["filer_category"],
                data["sic"],
                data["source_url"],
                len(data.get("pages", [])),
            ))

            # Upsert sections
            for item in data.get("items", []):
                for section in item.get("sections", []):
                    cur.execute("""
                        INSERT INTO sections (
                            id, accession_no, item, part, seq, title,
                            page_start, page_end, text, token_count
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            accession_no=EXCLUDED.accession_no,
                            item=EXCLUDED.item,
                            part=EXCLUDED.part,
                            seq=EXCLUDED.seq,
                            title=EXCLUDED.title,
                            page_start=EXCLUDED.page_start,
                            page_end=EXCLUDED.page_end,
                            text=EXCLUDED.text,
                            token_count=EXCLUDED.token_count
                    """, (
                        section["id"],
                        accession_no,
                        item["item"],
                        item["part"],
                        section["seq"],
                        section["title"],
                        section["page_start"],
                        section["page_end"],
                        section["text"],
                        0,  # token_count=0 for now, will be filled by chunker
                    ))

            # Upsert tables_parsed
            for table in data.get("tables", []):
                # Construct composite key
                table_id = f"{accession_no}:{table['id']}"

                cur.execute("""
                    INSERT INTO tables_parsed (
                        id, accession_no, section_id, item, title, units,
                        headers, rows, text, page_start, page_end
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        accession_no=EXCLUDED.accession_no,
                        section_id=EXCLUDED.section_id,
                        item=EXCLUDED.item,
                        title=EXCLUDED.title,
                        units=EXCLUDED.units,
                        headers=EXCLUDED.headers,
                        rows=EXCLUDED.rows,
                        text=EXCLUDED.text,
                        page_start=EXCLUDED.page_start,
                        page_end=EXCLUDED.page_end
                """, (
                    table_id,
                    accession_no,
                    table["section_id"],
                    table["item"],
                    table["title"],
                    table.get("units"),
                    json.dumps(table.get("headers", [])),
                    json.dumps(table.get("rows", [])),
                    table["text"],
                    table["page_start"],
                    table["page_end"],
                ))

    conn.commit()


def load_chunks(
    index_name: str,
    chunks_jsonl: str | Path,
    conn: psycopg.Connection,
    vectors_npy: str | Path | None = None,
) -> None:
    """Load chunks into the database using INSERT.

    Args:
        index_name: Name of the embedding index (e.g., 'bge_small__s1')
        chunks_jsonl: Path to JSONL file with chunk records
        conn: Database connection
        vectors_npy: Optional path to numpy file with embeddings (not used in basic load)
    """
    chunks_jsonl = Path(chunks_jsonl)
    table_name = f"chunks_{index_name}"

    # Load and insert chunks
    with conn.cursor() as cur:
        with chunks_jsonl.open() as f:
            for line in f:
                chunk = json.loads(line)

                cur.execute(f"""
                    INSERT INTO {table_name} (
                        id, chunk_key, accession_no, cik, item, section_id, table_id, seq,
                        page_start, page_end, page_label, is_table, text, embed_text, token_count
                    ) VALUES (COALESCE(%s, nextval(pg_get_serial_sequence('{table_name}', 'id'))),
                              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    chunk.get("id"),
                    chunk.get("chunk_key"),
                    chunk.get("accession_no"),
                    chunk.get("cik"),
                    chunk.get("item"),
                    chunk.get("section_id"),
                    chunk.get("table_id"),
                    chunk.get("seq", 0),
                    chunk.get("page_start", 0),
                    chunk.get("page_end", 0),
                    chunk.get("page_label"),
                    chunk.get("is_table", False),
                    chunk.get("text", ""),
                    chunk.get("embed_text", ""),
                    chunk.get("token_count", 0),
                ))

        # Keep the serial in step with explicit ids, so later inserts do not collide.
        cur.execute(
            f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
            f"GREATEST((SELECT COALESCE(MAX(id), 1) FROM {table_name}), 1))"
        )
    conn.commit()


def attach_vectors(
    index_name: str,
    ids_npy: str | Path,
    vectors_npy: str | Path,
    conn: psycopg.Connection,
    batch_size: int = 1000,
) -> None:
    """Update embeddings from numpy arrays and build HNSW index.

    Args:
        index_name: Name of the embedding index (e.g., 'bge_small__s1')
        ids_npy: Path to numpy file with chunk IDs
        vectors_npy: Path to numpy file with embedding vectors
        conn: Database connection
        batch_size: Number of vectors to update per batch
    """
    table_name = f"chunks_{index_name}"

    # Load numpy arrays
    ids = np.load(ids_npy)
    vectors = np.load(vectors_npy)

    # Update embeddings in batches
    with conn.cursor() as cur:
        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i : i + batch_size]
            batch_vectors = vectors[i : i + batch_size]

            for chunk_id, vec in zip(batch_ids, batch_vectors):
                cur.execute(
                    f"UPDATE {table_name} SET embedding = %s WHERE id = %s",
                    (vec.tolist(), int(chunk_id)),
                )

    conn.commit()

    # Build HNSW index
    create_hnsw_index(conn, index_name)
