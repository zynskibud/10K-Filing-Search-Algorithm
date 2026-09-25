# Wave 1b contract: corpus download

Implementer: Haiku subagent. Depends on: wave 0 (venv) and wave 1a (`data/survey/eligible.jsonl`, `citation_rag/corpus/filer_rule.py`).

## Rules
- LIGHT work. Run the download in 3 batches of about 370 filings each; each batch is under 5 GB. Pause 60 seconds between batches and check free disk. Stop if free disk < 15 GB.
- SEC etiquette: User-Agent from `.env` (`SEC_USER_AGENT`), max 10 requests per second, retry with backoff on 429 and 503, stop on 403.
- Use the project venv: `uv run python -m citation_rag.corpus.download`.
- Do not edit files outside `citation_rag/corpus/`, `data/raw/`, `reports/`, `tests/`.

## Deliverables
1. `citation_rag/corpus/select.py`: reads `data/survey/eligible.jsonl`, keeps one filing per CIK (the latest filed if a CIK has two), samples 1,100 with `random.Random(20260925)`, writes `data/raw/selection.jsonl` with all metadata fields from eligible.jsonl. Print the count per quarter and per filer category.
2. `citation_rag/corpus/download.py`: for each selected filing, download the primary document to `data/raw/{cik}/{accession_no}.htm`, then append one line to `data/raw/manifest.jsonl` with: cik, company, ticker (first ticker or null), accession_no, filed_date, report_date, fiscal_year (year of report_date), filer_category, sic, source_url, path, bytes, sha256. Skip filings already in the manifest (resumable). `--batch N --batch-size 370` flags, or an equivalent way to run 3 batches.
3. `tests/test_corpus.py`: manifest has no duplicate accession_no and no duplicate cik; every path exists and its size matches; every file starts with `<?xml` or `<html` or `<!DOCTYPE` (case-insensitive) within the first 500 bytes.
4. `reports/wave-1b.md`: counts (selected, downloaded, failed with reasons), total bytes, time taken, free disk before and after, filer category distribution, fiscal year distribution.
5. One commit on `dev`: "Wave 1b: select and download 1,100 10-K filings". `data/` is gitignored, so only code, tests, and the report are committed. Do not push.

## Definition of done
- `data/raw/manifest.jsonl` has >= 1,050 rows with existing files.
- `uv run pytest tests/test_corpus.py` passes.
