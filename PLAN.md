# Citation RAG over SEC 10-K filings: build plan

Goal: ask a question about 10-K filings and get an answer where each claim cites a company, a fiscal year, an Item, and a page number. The UI opens the cited page.

## Stack

| Part | Choice |
|---|---|
| Corpus | SEC 10-K filings from EDGAR, 1,000+ filings (about 100 companies x 10 years) |
| Database | Postgres 17 + pgvector. Local, in Docker. |
| Keyword search | Postgres full-text search (`tsvector`, `ts_rank_cd`) |
| Embeddings | `BAAI/bge-small-en-v1.5`, local, 384 dimensions |
| Reranker | `BAAI/bge-reranker-base`, local cross-encoder |
| LLM | Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) |
| API | Python 3.12, FastAPI |
| UI | Next.js, local |
| Audit log | `query_log` table, one row per query |

## Known problem: page numbers

EDGAR 10-K filings are HTML, not PDF. The HTML has no pages. Page breaks are CSS markers (`page-break-before` or `page-break-after`), and the printed page number is text in the footer. The parser must split on these markers and read the footer number. If a filing has no markers, it falls back to the PDF render of the filing.

## Phases

1. **Corpus download.** Pick about 100 companies. Download their 10-K filings from EDGAR with a rate limit of 10 requests per second and a `User-Agent` header. Store raw HTML in `data/raw/`.
2. **Parse and chunk.** Split each filing into pages, then into Items (Item 1, 1A, 7, 7A, 8). Chunk inside an Item at about 500 tokens with 50 tokens of overlap. Keep each table as one chunk. Store `page_start` and `page_end` on each chunk.
3. **Embed and load.** Embed chunks on the Mac (M4, MPS backend). Write filings and chunks to Postgres.
4. **Retrieval.** BM25-style top 50 + vector top 50, merged with Reciprocal Rank Fusion, then reranked to the top 8. Filters: ticker, fiscal year, Item.
5. **Answer with citations.** Send the top chunks to Haiku with chunk IDs. Haiku cites chunk IDs. The API validates each cited ID against the retrieved set and maps it to company, year, Item, and page. Write the full trace to `query_log`.
6. **Deploy.** Out of scope for now. Everything runs locally.
7. **Evals.** Build a golden set of about 50 questions with known answer pages. Measure recall@k for BM25 only, vector only, hybrid, and hybrid + rerank. Measure citation accuracy and answer faithfulness with an LLM judge.
8. **UI.** Question box, filters, streamed answer, clickable citations that open the source page.

## Repository layout

```
db/        schema.sql
ingest/    download, parse, chunk, embed, load
api/app/   FastAPI app: retrieval, rerank, generation, logging
evals/     golden set and eval scripts
web/       Next.js UI
data/      raw and parsed filings (not in git)
```
