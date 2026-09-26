# Wave 2d contract: parser fixes and corpus top-up

CPU-BULK (Sonnet, at most 4 workers for the corpus run). Depends on wave 2c. Owns `citation_rag/parse/*`, `tests/test_parse_*.py`, `data/parsed/`, `data/parse_failures.jsonl`, `data/raw/corpus.jsonl`, `reports/wave-2d.md`.

## Why
1. The orchestrator's spot check against sec.gov found a silent drop: 9 of the 53 passing filings with Item 1A `not_required` have real risk factors (Spruce Power `0001628280-26-022506`, 1-800-Flowers `0001084869-26-000029`, NewLake Capital `0001854964-26-000006`, BTCS `0001493152-26-012916`, Mountain Crest V `0001829126-26-002303`, Magyar Bancorp `0002077096-25-000193`, OZOP `0001493152-26-023179`, Powerdyne `0001493152-26-015155`, Sativus `0001683168-26-002958`). The rule "smaller reporting company and no heading found means not_required" masked a heading-detection miss.
2. 979 passing filings is under the 1,000-document floor. The manifest holds 1,333 downloaded filings with page markers; 233 are not yet parsed.
3. 31 failures are complete one-sentence Items under the 50-character `no_empty_sections` floor.

## Items
1. **Fix the silent drop.** For Items 1A, 1B, 6, 7A (the ones a smaller reporting company may omit): mark `not_required` only when (a) a heading exists and its body matches the omitted phrases, or (b) no heading exists AND the raw document text has no run longer than 2,000 characters between a mention of that Item and the next Item mention. Otherwise a missing heading is `absent`, which fails `required_items_present` and goes to the failure list. Then find why the heading was missed in the 9 filings above (read them; the report names the pattern for each) and fix the detector where a general rule exists. Do not add per-filing special cases.
2. **Terse Items.** `no_empty_sections` floor becomes 20 characters (after placeholders are excluded). Record it.
3. **Corpus top-up.** `data/raw/corpus.jsonl` becomes every manifest row with `has_page_markers` true (1,333 rows), in rank order. Parse everything with `--force` (the fixes change outputs), 4 workers. Write the new `data/parse_failures.jsonl`.
4. **Two stale tests.** `tests/test_parse_tables.py`: change the table-count range to 5 to 900 (measured). Keep the xbrl print-instead-of-fail behavior.
5. **Report** `reports/wave-2d.md`: pass counts before and after, the 9 filings' outcomes (each now `present` with its section count, or `absent` on the failure list with the reason), failures grouped by check, the heading-detector changes with reasoning, and a fresh random 10-filing spot-check list (`random.Random(3)` over the passing set) with sec.gov URLs.

## Rules
- Do not relax any other threshold. If a check seems wrong, write it in the report; do not change it.
- No commits. No model runs. No Docker. Stop if `df -h /` shows under 15 GB free. At most 4 worker processes.

## Definition of done
- Passing filings >= 1,050.
- Of the 9 named filings, none is `not_required` for Item 1A; each is `present` or on the failure list.
- `uv run pytest tests/test_parse_prose.py tests/test_parse_tables.py tests/test_parse_corpus.py -q` passes.
