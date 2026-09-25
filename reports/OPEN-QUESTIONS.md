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
