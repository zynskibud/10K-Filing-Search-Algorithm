# Wave 3b contract: eval harness code

Implementer: Sonnet subagent. Depends on: wave 0. Independent of the parser (it works from the schemas). Schemas: `reports/contracts/schemas.md` sections 2 and 3.
Plan reference: plan.html, Plan tab, section 4.

## Rules
- LIGHT work. No Ollama inference in this wave (the judge module is written and unit-tested with a fake client; real calls happen in wave 7 under the heavy lock).
- Files you own: `citation_rag/evals/`, `tests/test_evals.py`, `evals/` folder (create `evals/golden/`, `evals/results/`, `evals/prompts/`), `reports/wave-3b.md`.
- No commits.

## Deliverables
1. `citation_rag/evals/golden.py`: load and validate golden JSONL against schema section 2. `validate(golden_path, parsed_dir) -> list[Problem]`: for each case, check that the evidence is an exact substring of the named section `text` or table `text` in `data/parsed/{accession_no}.json`, that the page is inside that section/table's page range, that `companies` CIKs match the filing, that `type` is valid, and that ids are unique. `unanswerable` cases skip the evidence check. Returns problems with case id and reason. CLI: `uv run python -m citation_rag.evals.golden validate evals/golden/dev.jsonl`.
2. `citation_rag/evals/retrieval_metrics.py`:
   - `is_hit(chunk_text, evidence) -> bool`: true if at least 80% of the evidence's characters (after whitespace normalization) are covered by the longest common substring(s) with the chunk. Implement as: normalized evidence found in normalized chunk (full match) OR the longest common substring covers >= 80% of the evidence length. Keep it simple and deterministic.
   - `recall_at_k(results, evidence, k)`, `mrr(results, evidence)`, `recall_at_token_budget(results, evidence, budget_tokens, token_counts)` (walk results in order, stop when the running token sum exceeds the budget).
   - Split metrics by question type and by prose vs table (`table_id` present in the case).
   - `bootstrap_ci(values, n=1000, seed=0) -> (lo, hi)` for a mean, and `bootstrap_diff_ci(values_a, values_b)` for paired differences (same questions).
3. `citation_rag/evals/runner.py`: `run_eval(config, split, retriever) -> RunRecord`. `retriever` is any callable `(question, companies) -> list[Result]` with `Result(chunk_id, text, token_count, section_id, table_id, page_start, page_end, accession_no)`. Computes all metrics with CIs, writes one line to `evals/results/runs.jsonl` (schema section 3, include `git_commit` from `git rev-parse --short HEAD`), and a per-question detail file `evals/results/{run_id}.jsonl` (question id, hit rank, top result ids). `split` must be `dev`; refuse `test` unless `--unseal` is passed (wave 8 only). CLI: `uv run python -m citation_rag.evals.runner --split dev --retriever random` where `random` is a built-in fake retriever over the parsed corpus (used for the smoke test).
4. `citation_rag/evals/report.py`: `compare(run_ids) -> markdown table` with metric, value, CI per run, and the paired-difference CI between the first run and each other run. CLI prints the table.
5. `citation_rag/evals/judge.py`: four judgments, each a separate prompt file in `evals/prompts/{correctness,faithfulness,citation_support,refusal}.v1.md`, with labels and written definitions:
   - correctness: `correct` / `partial` / `incorrect`, given question, reference answer, model answer.
   - faithfulness: first a prompt that splits the answer into atomic claims (JSON list), then one call per claim with the retrieved text: `supported` / `not_supported` / `contradicted`. Score = supported / total.
   - citation_support: per citation, given the claim sentence and the cited chunk text: `supports` / `does_not_support`.
   - refusal: given question and answer, for unanswerable cases: `refused` / `answered_anyway`; for answerable cases: `answered` / `wrongly_refused`.
   Every prompt asks for JSON `{"reason": "...", "label": "..."}` with the reason first. Client: `OllamaJudge(model="gpt-oss:20b", temperature=0)` using the Ollama HTTP API with `format: "json"`, plus `FakeJudge` for tests. Every judgment record stores `prompt_version`, `model`, `raw_response`.
6. `citation_rag/evals/calibration.py`: `export_sheet(answers_path, n=40, seed=0) -> evals/calibration/sheet.csv` (stratified across runs and types, columns: id, question, reference, answer, retrieved_text_excerpt, human_label blank); `agreement(sheet_with_labels, judge_labels) -> {accuracy, cohen_kappa, confusion}`; `consistency(judge, cases, runs=3) -> share of cases with identical labels across runs`; holdout support: `--holdout 15` keeps 15 rows out of the tuning set.
7. `citation_rag/evals/router_metrics.py`: `router_accuracy(predicted_companies, golden_companies)` exact-set match, with CI.
8. `tests/test_evals.py`: unit tests for `is_hit` edge cases (exact, 80% partial, split across two chunks fails, table line), metric math on a tiny fixture, bootstrap determinism with seed, golden validation against a small fake parsed JSON, judge JSON parsing with `FakeJudge`, the `test` split refusal, and the runner end to end with the random retriever on a fixture corpus.
9. `reports/wave-3b.md`: the CLI commands, the metric definitions in one paragraph each, and the output of the smoke run.

## Definition of done
- `uv run pytest tests/test_evals.py` passes.
- `uv run python -m citation_rag.evals.runner --split dev --retriever random` runs end to end on whatever golden and parsed files exist (or a fixture if none) and writes a run line with CIs.
