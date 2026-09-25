# Open questions for the human

One entry per decision that needs you. The build does not block on these. I take the default, record it here, and continue. Reverse any of them and tell me; I say what re-runs.

Format: wave, question, default taken, effect of the default.

## Before wave 0

- **Wave 0 / plan.** Judge model is gpt-oss-20b (local, free). If calibration in wave 7 shows low agreement with your 40 labels, the fallback is Haiku 4.5 for correctness and refusals only (a few cents per run). Default: local judge first. Effect: no API cost unless calibration fails; then under $1 per run.
- **Wave 0 / plan.** The test set (about 50 questions) is sealed until wave 8. Default: no run touches it before then, and the harness refuses `--split test` without `--unseal`. Effect: the final numbers are honest; no early test-set peeks.
