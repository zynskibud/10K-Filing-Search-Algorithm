# Wave 3a contract: golden set

Implementers: up to 4 Sonnet writer subagents in parallel, then 1 Sonnet validator subagent. Depends on: wave 2c (`data/parsed/`, passing filings only) and wave 3b (`citation_rag/evals/golden.py`). Schema: `reports/contracts/schemas.md` section 2.
Plan reference: plan.html, Plan tab, section 4 (golden set), and System tab, "Limits of project 1" (multi-part questions).

## Rules
- LIGHT work. Reading JSON files and writing JSONL. No model runs on this machine. The writers are Claude subagents; they read the parsed filings directly.
- Writers own `evals/golden/drafts/writer-{n}.jsonl` only. The validator owns `evals/golden/dev.jsonl`, `evals/golden/test.jsonl`, `evals/golden/rejected.jsonl`, `reports/wave-3a.md`.
- No commits.

## Writer task (each of 4 writers)
Input: a disjoint slice of passing filings from `data/parsed/` (the orchestrator assigns slices by accession-number order: writer n takes rows where `index % 4 == n`). Sample 10 to 12 filings from the slice with `random.Random(100 + n)` and read them.

Write 40 questions per writer (160 total), with this quota per writer:

| type | count | rule |
|---|---|---|
| fact_lookup | 8 | one fact stated in prose; answer is a short phrase |
| number_from_table | 8 | the answer is a cell (or two cells) in a data table; `table_id` set; evidence is the exact table text line(s) |
| paraphrased | 6 | the question uses none of the key nouns and verbs of the evidence sentence (write the evidence first, then rephrase with synonyms); answer in prose |
| exact_term | 5 | the question contains a rare exact term from the filing: a product name, a place, a legal case, a defined term in quotes, or an Item reference such as "Item 1C" |
| general | 4 | no company named; evidence from at least 3 different filings in the slice; `companies: "general"`, `accession_nos` list |
| unanswerable | 5 | asks for something the filing plausibly could state but does not (check by searching the parsed text); `evidence: null`; `notes` names the nearby content that makes it plausible |
| multi_part | 4 | two or three linked sub-questions about one filing, each with its own evidence string in a list; the parts must need different sections |

Rules for every question:
- One definitive answer that a careful reader of the filing would give. No opinions, no "why do you think".
- `evidence` is copied exactly from the parsed JSON (section `text` or table `text`), 40 to 400 characters, contiguous. Do not fix typos or whitespace.
- `page` is the `pages[].index` of the section or table that holds the evidence (use `page_start` if the range spans pages and the evidence is near the start; otherwise pick the page inside the range).
- `companies` is the filing's CIK in a list, except `general`.
- Vary the Items: at most 40% of a writer's questions from Item 1A, at least 8 from Item 7 or 8, at least 3 from Items 1, 2, 3, 5, 9A, or 10 to 15.
- Vary the wording: no two questions with the same first three words.
- `answer` is short (under 40 words). `answer_kind` and `answer_value` per schema.
- Self-check before writing: for each question, confirm with Python that `evidence in section_text` (or table text) is true. Drop questions that fail.

Output: `evals/golden/drafts/writer-{n}.jsonl`, ids `w{n}-{001..040}`.

## Validator task
1. Run `citation_rag.evals.golden.validate` on each draft. Reject any case with a problem, into `rejected.jsonl` with the reason.
2. Duplicate check: reject a question whose normalized text has Jaccard similarity >= 0.6 (word sets) with an earlier one, or the same evidence string as an earlier one.
3. Quality read: for each case, confirm the answer follows from the evidence alone, the type rule holds (for `paraphrased`, check word overlap with the evidence is low; for `exact_term`, the term is present; for `unanswerable`, search the filing for the fact and confirm it is absent). Reject with a reason.
4. Split: shuffle with `random.Random(7)`, stratified by type; test gets one third of each type (about 50), dev gets the rest (about 100). Renumber ids `g0001...`. Write `dev.jsonl` and `test.jsonl`.
5. `reports/wave-3a.md`: counts per type per split, rejection reasons with counts, Item and filer-category distribution, and 15 dev questions picked with `random.Random(11)` for the human spot-check (id, question, answer, evidence, company).

## Definition of done
- `dev.jsonl` >= 95 cases and `test.jsonl` >= 45 cases, every type present in both.
- `uv run python -m citation_rag.evals.golden validate evals/golden/dev.jsonl --parsed-dir data/parsed` reports 0 problems; same for test.
- No two cases share an evidence string.
