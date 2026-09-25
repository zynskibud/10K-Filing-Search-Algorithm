# Open questions for the human

One entry per decision that needs you. The build does not block on these. I take the default, record it here, and continue. Reverse any of them and tell me; I say what re-runs.

Format: wave, question, default taken, effect of the default.

## Before wave 0

- **Wave 0 / plan.** Judge model is gpt-oss-20b (local, free). If calibration in wave 7 shows low agreement with your 40 labels, the fallback is Haiku 4.5 for correctness and refusals only (a few cents per run). Default: local judge first. Effect: no API cost unless calibration fails; then under $1 per run.
- **Wave 0 / plan.** The test set (about 50 questions) is sealed until wave 8. Default: no run touches it before then, and the harness refuses `--split test` without `--unseal`. Effect: the final numbers are honest; no early test-set peeks.

## After wave 0

- **Wave 0 / disk.** Free disk is 33 GB. The rest of the build needs about 25 GB (corpus 6, parsed 1, vectors 5, gpt-oss:20b 13), which would leave about 8 GB, under the 15 GB floor. Default (set by the coordinator): store the raw HTML gzip-compressed (`.htm.gz`, about 5 to 8 times smaller, so about 1 GB), and the parser reads the compressed files. The gpt-oss:20b pull waits for the coordinator's GO at wave 7. Effect: the raw HTML stays available for parser fixes; no re-download needed.
- **Wave 0 / isolation.** The first model download wrote 7.3 GB into `~/.cache/huggingface` (outside the project). I deleted those files (only the ones created during wave 0) and fixed the script. Nothing for you to do; noted for the record.

- **Wave 0 / isolation, second case.** The subagent's smoke run also wrote a 6.5 GB model copy into the project root (not the cache folder), because `HF_HOME` was unset in that run. Deleted. The fixed smoke script cannot do this again.
- **Wave 0 / coordinator.** The coordinator session restarted; reports now go to `ai-engineering-6c` instead of `ai-engineering-c7`. The coordinator confirmed: keep `data/raw`, gzip it (done at download time, so it is never uncompressed on disk), and pull gpt-oss:20b only after the coordinator's GO for wave 7.

## Wave 1a

- **Wave 1a / filer rule.** Grantor trusts and commodity or ETF trusts (for example SPDR Gold Trust) have no reliable field to exclude them: they carry SIC 6221 and `entityType = operating`, the same as normal companies, and a name match on "Trust" would also drop real operating companies and REITs. Default: include them in the candidate pool. The parser's checks (required Items, Item sizes) will fail most of them, and they land on the failure list instead of the index. Effect: a few non-operating trusts may stay in the corpus if their 10-K follows the standard Item list; that is the condition you set ("as long as the 10-K is the same"), so it is acceptable.
- **Wave 1a / time.** The SEC submissions API answers at about 2 to 4 requests per second, not 10, so the metadata fetch takes about 40 minutes instead of 12. No action needed.
- **Wave 1a / page markers.** About 3% of eligible filings have no page-break markup at all (1 of 30 in the sample; 6 others use the modern CSS syntax `break-after: page`, which the parser will support). Default: exclude filings with fewer than 15 page markers, because they cannot give page citations. The download fetches 1,350 and keeps the first 1,100 with markers. Effect: the corpus is a random sample of filings that have page structure, which is the population the project is about.
- **Wave 1a / co-registrants.** 138 eligible rows are duplicates: two registrants (for example a REIT and its operating partnership) file one document under one accession number. Default: keep one row per accession number, the parent registrant (the row with a filer category). Effect: no document is indexed twice.
- **Wave 1a / one odd filing.** Sample filing `0001696411_0001477932-26-005753` shows Item headings 1 to 6 only. Default: no action; the parser's required-Items check decides. Effect: it lands on the failure list if it is a truncated 10-K.

## Wave 2

- **Wave 2a / thresholds.** Three parser checks used guessed limits that correct parses failed: the Item order check (filers put Item 16 before 15), the 0.95 text-coverage floor (short shell filings), and the 200-character "not required" rule. Defaults: exempt Items 15 and 16 from the order check, coverage floor 0.90, "not required" limit 1,000 characters. Effect: fewer false failures; a real truncated filing still fails the required-Items check.
- **Wave 2a / a 10-Q-shaped "10-K".** Crona Corp filed a document indexed as 10-K whose Items follow the 10-Q structure. Default: it fails the parser's required-Items check and lands on the failure list, counted under `not_a_10k_structure`. Effect: excluded from the index, no manual fix.
