# Wave 2c contract: parser integration and full-corpus checks

Implementer: Sonnet subagent. Depends on: wave 2a (prose parser), wave 2b (table parser), wave 1b (`data/raw/manifest.jsonl`). Schema: `reports/contracts/schemas.md` section 1.

## Rules
- LIGHT work, CPU only. Parsing 1,100 filings takes minutes with `multiprocessing` (use at most 4 processes; the machine is shared).
- Files you own: `citation_rag/parse/filing.py` (the join), `citation_rag/parse/run.py`, `citation_rag/parse/checks.py`, `tests/test_parse_corpus.py`, `reports/wave-2c.md`, `data/parsed/`, `data/parse_failures.jsonl`. You may fix bugs in any `citation_rag/parse/*` file, but record every change in the report with the reason.
- No commits.

## Deliverables
1. **Join.** `parse_filing(path, meta)` cleans, splits pages, extracts tables (2b), unwraps layout tables into prose, detects Items and sections (2a), inserts `[Table: id]` placeholders at the table positions with `position` set, assigns `section_id` and `item` to each table, computes coverage over prose + table text, and runs all checks. Reads `.htm` and `.htm.gz`.
2. **Checks** (`checks.py`), each returning pass/fail with a detail string. The prose checks from 2a, plus:
   - `xbrl_coverage`: matched / total `ix:nonFraction` values >= 0.85 (threshold may be tuned from the 2b report; record the final value).
   - `table_count_range`: data tables between 15 and 900.
   - `placeholders_consistent`: every table id in `tables[]` appears exactly once as a placeholder in some section text, and vice versa.
   - `no_empty_sections`: no section text under 50 characters (after placeholders are excluded) unless the Item status is not `present`.
3. **Run.** `uv run python -m citation_rag.parse.run --manifest data/raw/manifest.jsonl --out data/parsed --workers 4`. Writes one JSON per filing. Filings that fail any check are still written (with `checks.passed = false`) and also listed in `data/parse_failures.jsonl` with `accession_no`, `company`, `filer_category`, and the failure list. Resumable: skip filings whose JSON exists unless `--force`.
4. **Quality report** `reports/wave-2c.md`:
   - pass rate; failures grouped by check with counts and 3 example accession numbers each;
   - distributions: pages per filing, page-label coverage, sections per Item (1A, 7, 8), Item character sizes (p10/p50/p90), data tables per filing, xbrl coverage;
   - `filer_category` against pass rate (smaller reporting companies are expected to have `not_required` Items);
   - integrated-report and combined-Item counts;
   - the list of code changes you made to 2a/2b files and why;
   - the 10 filings the orchestrator will spot-check: pick 10 at random from the passing set with `random.Random(2)`, and list accession, company, and the sec.gov URL.
5. `tests/test_parse_corpus.py`: parses 20 fixed filings from the manifest (first 20 by accession order) and asserts the schema shape, 22 Items in order, placeholders consistent, and checks present.

## Definition of done
- >= 95% of filings pass every check, or the report explains each failing group and proposes the fix (do not silently relax a threshold; if you change one, say so and why).
- `uv run pytest tests/test_parse_corpus.py` passes.
- `data/parsed/` has one JSON per manifest row.
