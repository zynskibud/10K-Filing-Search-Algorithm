CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE filings (
    id           BIGSERIAL PRIMARY KEY,
    cik          TEXT NOT NULL,
    ticker       TEXT,
    company      TEXT NOT NULL,
    fiscal_year  INT  NOT NULL,
    accession_no TEXT NOT NULL UNIQUE,
    source_url   TEXT NOT NULL,
    page_count   INT
);

CREATE TABLE chunks (
    id          BIGSERIAL PRIMARY KEY,
    filing_id   BIGINT NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
    item        TEXT,              -- for example 'Item 1A'
    page_start  INT  NOT NULL,
    page_end    INT  NOT NULL,
    is_table    BOOLEAN NOT NULL DEFAULT false,
    text        TEXT NOT NULL,
    embedding   vector(384) NOT NULL,   -- BAAI/bge-small-en-v1.5
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);

CREATE INDEX chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX chunks_filing_idx ON chunks (filing_id);

-- Audit log: one row per query. Feeds evals and debugging.
CREATE TABLE query_log (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    question        TEXT NOT NULL,
    filters         JSONB,
    bm25_ids        BIGINT[],
    vector_ids      BIGINT[],
    fused_ids       BIGINT[],
    reranked_ids    BIGINT[],
    rerank_scores   REAL[],
    answer          TEXT,
    citations       JSONB,
    model           TEXT,
    input_tokens    INT,
    output_tokens   INT,
    latency_ms      JSONB              -- per stage: retrieve, rerank, generate
);
