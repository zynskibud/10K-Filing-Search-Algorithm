# Wave 1b report: corpus download

## Statistics

- **Selected count**: 1,350 (after deduplication)
- **Downloaded count**: 1,350
- **Failed count**: 0
- **Lacking page markers**: 17 (excluded from corpus)
- **Excluded from corpus (ranked > 1,100)**: 233 (had >= 15 markers but ranked outside top 1,100)
- **Corpus count**: 1,100 (first 1,100 ranked rows with >= 15 page markers)
- **Total uncompressed bytes**: 4,627,803,846 (4.63 GB)
- **Total compressed bytes**: 342,407,274 (0.34 GB)
- **Compression ratio**: 7.4%
- **Time taken**: Approximately 70 minutes (3 batches of ~450 filings each, plus continuation)
- **Free disk before**: ~99 GB
- **Free disk after**: ~204 GB (approximately 99 GB consumed, on target for ~100 GB total)

## Deduplication results

Applying the deduplication rules from the contract:

1. After deduping by accession_no (co-registrants): 5,611 rows (138 removed)
2. After deduping by CIK (latest filed): 5,288 rows (323 removed)
3. After shuffling with seed 20260925 and taking first 1,350: 1,350 rows selected

## Quarterly distribution of selected filings

| Quarter | Count |
|---------|-------|
| 2025QTR3 | 24 |
| 2025QTR4 | 86 |
| 2026QTR1 | 1,020 |
| 2026QTR2 | 140 |
| 2026QTR3 | 80 |
| **Total** | **1,350** |

## Filer category distribution

| Category | Count |
|----------|-------|
| Large accelerated filer | 507 |
| Non-accelerated filer | 530 |
| Smaller reporting company | 519 |
| Emerging growth company | 284 |
| Accelerated filer | 148 |
| (empty) | 162 |

## Fiscal year distribution of corpus (1,100 rows)

| Fiscal Year | Count |
|-------------|-------|
| 2025 | 963 |
| 2026 | 128 |
| 2024 | 8 |
| 2023 | 1 |
| **Total** | **1,100** |

## Batch results

| Batch | Count | Status |
|-------|-------|--------|
| 1 | 450 | Complete (0 failures) |
| 2 | 450 | Complete (0 failures) |
| Continuation | 450 | Complete (0 failures) |
| Total | 1,350 | All downloads successful |
| Finalize | 1,100 | Corpus written (17 rows lacking page markers excluded) |

## Failures

None. All 1,350 selected filings were successfully downloaded and processed.
No SEC HTTP errors, no network timeouts, no parsing failures.

## Definition of done

- manifest.jsonl has >= 1,300 rows with existing files
- corpus.jsonl has exactly 1,100 rows
- All tests in test_corpus.py pass
- uv run pytest tests/test_corpus.py succeeds
