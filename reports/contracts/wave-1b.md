# Wave 1b contract: corpus download

Implementer: Haiku subagent. Depends on: wave 0 (venv) and wave 1a (`data/survey/eligible.jsonl`, `citation_rag/corpus/filer_rule.py`).

## Rules
- LIGHT work. Files are stored gzip-compressed (about 1 GB total). Run the download in 3 batches of about 370 filings each. Pause 60 seconds between batches and check free disk. Stop if free disk < 15 GB.
- SEC etiquette: User-Agent from `.env` (`SEC_USER_AGENT`), max 10 requests per second, retry with backoff on 429 and 503, stop on 403.
- Use the project venv: `uv run python -m citation_rag.corpus.download`.
- Do not edit files outside `citation_rag/corpus/`, `data/raw/`, `reports/`, `tests/`.

## Deliverables
1. `citation_rag/corpus/select.py`: reads `data/survey/eligible.jsonl`. Dedupe in this order: (a) one row per `accession_no` (co-registrants share a document); keep the row with a non-empty `category`, or the lowest CIK if both are empty; (b) one filing per CIK (the latest filed if a CIK has two). Then shuffle with `random.Random(20260925)` and write the first 1,350 to `data/raw/selection.jsonl` with all metadata fields, plus `rank` (position in the shuffle). Split the `category` string on `<br>` into a list `categories`. Print counts per quarter and per category.
2. `citation_rag/corpus/download.py`: for each selected filing, in `rank` order, download the primary document and write it gzip-compressed to `data/raw/{cik}/{accession_no}.htm.gz` (`gzip.open(..., 'wb', compresslevel=6)`); record `bytes` (uncompressed) and `bytes_gz`, then append one line to `data/raw/manifest.jsonl` with: cik, company, ticker (first ticker or null), accession_no, filed_date, report_date, fiscal_year (year of report_date), filer_category, sic, source_url, path, bytes, sha256. Also compute `page_markers`: the count of elements whose `style` matches `page-break-(?:before|after)\s*:\s*always` OR `break-(?:before|after)\s*:\s*page` (case-insensitive), plus `<hr>` elements with either style. Store it in the manifest row with `has_page_markers = page_markers >= 15`. Skip filings already in the manifest (resumable). `--batch N --batch-size 450` flags, or an equivalent way to run 3 batches.
   After all batches: write `data/raw/corpus.jsonl` = the manifest rows with `has_page_markers` true, in `rank` order, cut to the first 1,100. Filings without page markers cannot give page citations and are excluded. Report how many were excluded for this reason.
3. `tests/test_corpus.py`: manifest and corpus.jsonl have no duplicate accession_no and no duplicate cik; corpus.jsonl has exactly 1,100 rows, all with `has_page_markers` true; every path exists and its compressed size matches `bytes_gz`; every file, read through `gzip.open`, starts with `<?xml` or `<html` or `<!DOCTYPE` (case-insensitive) within the first 500 bytes.
4. `reports/wave-1b.md`: counts (selected, downloaded, failed with reasons), total bytes, time taken, free disk before and after, filer category distribution, fiscal year distribution.
5. One commit on `dev`: "Wave 1b: select and download 1,100 10-K filings". `data/` is gitignored, so only code, tests, and the report are committed. Do not push.

## Definition of done
- `data/raw/manifest.jsonl` has >= 1,300 rows with existing files, and `data/raw/corpus.jsonl` has 1,100 rows. If fewer than 1,100 have page markers, download the next ranks from `eligible.jsonl` until 1,100 is reached.
- `uv run pytest tests/test_corpus.py` passes.
