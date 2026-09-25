# Wave 3b report: eval harness code

Status: done. Implemented by a Sonnet subagent (killed by a session restart after writing all files; the orchestrator verified the work and wrote this report).

## What exists

- `citation_rag/evals/golden.py`: loads and validates golden JSONL against `reports/contracts/schemas.md` section 2 (exact evidence substring, page inside the section or table range, CIK match, valid type, unique ids). CLI: `uv run python -m citation_rag.evals.golden validate <golden.jsonl> --parsed-dir <dir>`.
- `citation_rag/evals/retrieval_metrics.py`: `is_hit` (full normalized substring, or longest common substring covering >= 80% of the evidence), `recall_at_k`, `mrr`, `recall_at_token_budget`, per-type and prose/table splits, `bootstrap_ci` and `bootstrap_diff_ci` (1,000 resamples, seeded, percentile 2.5 to 97.5).
- `citation_rag/evals/runner.py`: `run_eval(config, split, retriever)`; writes one line to `evals/results/runs.jsonl` (schema section 3, with git commit) and a per-question detail file. The `test` split raises `PermissionError` unless `--unseal` is passed. CLI: `uv run python -m citation_rag.evals.runner --split dev --retriever random`.
- `citation_rag/evals/report.py`: `compare(run_ids)` markdown table with values, intervals, and paired-difference intervals against the first run.
- `citation_rag/evals/judge.py`: four judgments with prompts in `evals/prompts/*.v1.md` (correctness, faithfulness split + per-claim, citation_support, refusal). Labels with written definitions, reason before label, JSON only. `OllamaJudge` (gpt-oss:20b, temperature 0, `format: json`) and `FakeJudge` for tests. Every record stores prompt version, model, raw response.
- `citation_rag/evals/calibration.py`: labeling sheet export (stratified, 40 rows, holdout support), agreement (accuracy, Cohen's kappa, confusion), 3-run consistency.
- `citation_rag/evals/router_metrics.py`: exact-set router accuracy with interval.
- `tests/test_evals.py`: 36 tests. Fixtures: `tests/fixtures/parsed/0001111111-25-000001.json`, `tests/fixtures/golden_dev.jsonl`.

## Orchestrator checks

- `uv run pytest tests/test_evals.py -q`: 36 passed.
- Runner smoke run on the fixtures: writes a run line with all metrics and intervals (`n_questions: 5`, notes say fixtures were used).
- `--split test` without `--unseal`: refused with `PermissionError`.
- Golden validation on the fixture: `OK: 5 cases, 0 problems`.
- Code review of `is_hit`, the bootstrap, the judge client, and the faithfulness prompt: match the contract.

## Notes for later waves

- Wave 7: raise the `OllamaJudge` HTTP timeout from 120 s to at least 600 s. The first request after loading gpt-oss:20b takes longer than 120 s.
- No Ollama call was made in this wave.
