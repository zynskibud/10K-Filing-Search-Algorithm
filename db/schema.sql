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

-- Parent units: one row per SEC sub-section (risk factor, MD&A heading, Note).
CREATE TABLE sections (
    id           BIGSERIAL PRIMARY KEY,
    filing_id    BIGINT NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
    part         TEXT,              -- 'I', 'II', 'III', 'IV'
    item         TEXT NOT NULL,     -- for example '1A'
    title        TEXT NOT NULL,     -- heading text as printed
    xbrl_block   TEXT,              -- for example 'us-gaap:IncomeTaxDisclosureTextBlock'
    seq          INT  NOT NULL,     -- order inside the filing
    page_start   INT  NOT NULL,     -- page_index
    page_end     INT  NOT NULL,
    token_count  INT  NOT NULL,
    text         TEXT NOT NULL
);

-- Child units: what search and rerank run on.
CREATE TABLE chunks (
    id          BIGSERIAL PRIMARY KEY,
    section_id  BIGINT NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    filing_id   BIGINT NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
    item        TEXT NOT NULL,     -- copied from sections for fast filters
    seq         INT  NOT NULL,     -- order inside the section
    page_start  INT  NOT NULL,     -- page_index
    page_end    INT  NOT NULL,
    page_label  TEXT,              -- printed page number, for example '47' or 'F-3'
    is_table    BOOLEAN NOT NULL DEFAULT false,
    text        TEXT NOT NULL,     -- clean text, used for citations
    embedding   vector(384) NOT NULL,   -- BAAI/bge-small-en-v1.5, of prefix + text
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);

CREATE INDEX chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX chunks_filing_idx ON chunks (filing_id, item);
CREATE INDEX chunks_section_idx ON chunks (section_id);
CREATE INDEX sections_filing_idx ON sections (filing_id, item);

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
    section_ids     BIGINT[],           -- parents sent to the LLM
    context_tokens  INT,
    rerank_scores   REAL[],
    answer          TEXT,
    citations       JSONB,
    model           TEXT,
    input_tokens    INT,
    output_tokens   INT,
    latency_ms      JSONB              -- per stage: retrieve, rerank, generate
);
