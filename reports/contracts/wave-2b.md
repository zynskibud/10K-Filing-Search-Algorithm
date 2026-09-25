# Wave 2b contract: table parser

Implementer: Sonnet subagent. Depends on: wave 0, wave 1a sample filings. Runs in parallel with wave 2a. Output schema: `reports/contracts/schemas.md` section 1, `tables[]`.
Plan reference: plan.html, Plan tab, section 3, "Tables".

## Rules
- LIGHT work, CPU only. Develop on the 30 sample filings in `data/survey/sample/`.
- Files you own: `citation_rag/parse/tables.py`, `citation_rag/parse/numbers.py`, `tests/test_parse_tables.py`, `reports/wave-2b.md`. Do not touch other files in `citation_rag/parse/` (wave 2a owns them). If you need the cleaned tree, write your own minimal cleaning inside your test helpers; the production join happens in wave 2c.
- No commits.

## Deliverables
1. `citation_rag/parse/numbers.py`: `merge_number_cells(cells: list[str]) -> list[str]` that joins EDGAR's split cells: a cell that is only `$`, `(`, `)`, `%`, or empty attaches to its neighbor. `parse_number(s) -> float | None`: handles `1,204`, `(1,204)` as negative, `—`/`-`/`–` as null, `%`, footnote markers like `(1)` after a number, and `$`.
2. `citation_rag/parse/tables.py` with `extract_tables(tree, pages) -> list[dict]` where `tree` is the cleaned lxml root and `pages` maps each element to a page index (accept a callable `page_of(element) -> int`). For each `<table>` in document order:
   - **Layout or data.** Compute rows (`<tr>` with visible text), columns (max visible cells per row after merging empty spacer cells), and numeric cells (`parse_number` not None). Data table if rows >= 3 and columns >= 2 and numeric cells >= 6. Otherwise return it in a separate list `layout_tables` (element references), so 2a can unwrap it into prose.
   - **Title and units.** Walk backwards from the table through preceding siblings and ancestors' preceding siblings, up to 6 blocks, skipping empty ones. Title is the first block under 160 chars that is bold, all-caps, or ends without a period. Units: the first block or first table row matching `\((?:in|dollars in|amounts in)?\s*(thousands|millions|billions)[^)]*\)` or containing "except per share". Title may be null.
   - **Header rows.** Rows at the top where most non-empty cells are non-numeric (years like `2025` count as header text). Handle `colspan` by repeating the label across spanned columns, and multi-row headers by joining the rows' labels for the same column with a space ("Year Ended December 31 2025"). Keep at most 3 header rows. If no header row is found, headers are `["", "col1", "col2", ...]`.
   - **Body rows.** First non-empty cell is the label. Indent from `padding-left`/`margin-left`/`text-indent` in the cell style, or leading `&nbsp;` count, mapped to 0, 1, 2. A row whose label is bold and has no numeric cells is a group header: keep it as a row with empty cells. Merge split number cells so the cell count matches the header count; if it still does not match, pad with empty strings and record `misaligned: true` on the table.
   - **Text form.** First line: title (and units in parentheses). Then one line per body row: `label | header_i: cell_i` for non-empty cells. Group header rows appear as the label alone.
   - **Placement.** `position` is filled by 2c; return the table's element so 2c can locate it. Return `page_start`/`page_end` from `page_of`.
   - **XBRL coverage.** Given the list of `ix:nonFraction` records from `clean.py` (text, name, scale, order), count how many of their displayed texts appear in some data table cell (string match after whitespace normalization). Return per-table `xbrl_values_matched` and a document-level `xbrl_nonfraction_total`/`matched`. Values that appear only in prose (for example inside a sentence) are expected; report the unmatched ones so the threshold can be set from data.
   - **Splitting** is NOT done here. Large tables stay whole; chunking splits them by rows in wave 4.
3. `tests/test_parse_tables.py`: synthetic HTML tests for layout-vs-data, split-cell merge, negatives, colspan headers, multi-row headers, indent hierarchy, units detection. Plus a test over the 30 sample filings that asserts: data tables between 20 and 800 per filing, and xbrl match rate >= 0.85 per filing (if lower, the test prints the top unmatched values instead of failing, and the report explains).
4. `reports/wave-2b.md`: per-filing table counts, layout/data split, xbrl match rates, the most common unmatched value patterns, header patterns seen, and cases not handled.

## Definition of done
- `uv run pytest tests/test_parse_tables.py` passes.
- Median xbrl match rate over the 30 samples >= 0.90, or the report explains exactly what the unmatched values are.
