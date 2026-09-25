# Wave 2a contract: prose parser

Implementer: Sonnet subagent. Depends on: wave 0, wave 1a (30 sample filings in `data/survey/sample/`, survey report). Runs in parallel with wave 2b (table parser). Output schema: `reports/contracts/schemas.md` section 1.
Plan reference: plan.html, Plan tab, section 3.

## Scope
Everything in the normalized filing JSON except `tables[]` and the table placeholder lines. Wave 2b provides a function that, given the lxml tree, returns the data tables with their positions; wave 2c joins the two. In this wave, treat every `<table>` as follows: if wave 2b's `citation_rag/parse/tables.py` exists and imports, call `extract_tables(tree)`; if not, use a stub that returns no tables and unwraps all tables into prose. Write your code so that the join is one function call.

## Rules
- LIGHT work: CPU parsing only. Develop on the 30 sample filings. If `data/raw/` already has more filings, you may test on up to 200 of them.
- Files you own: `citation_rag/parse/` except `tables.py`, `tests/test_parse_prose.py`, `reports/wave-2a.md`. Do not touch `citation_rag/parse/tables.py`.
- No commits. The orchestrator commits after wave 2c.

## Deliverables
1. `citation_rag/parse/clean.py`: read bytes (if the path ends with `.gz`, read through `gzip.open`; the 30 sample files are plain `.htm`, the corpus files are `.htm.gz`), strip the XML declaration, parse with lxml (`lxml.html`, or `lxml.etree` with recover), delete `ix:header` (and `ix:hidden`), unwrap all other `ix:*` elements so their text stays, remove `<script>`, `<style>`, comments. Keep `<table>` elements intact for 2b. Record every `ix:nonFraction` element's text, `name`, `contextref`, `scale`, `sign`, and its document order, into a list (2b needs it for the coverage check).
2. `citation_rag/parse/pages.py`: split the document into pages at elements whose `style` contains `page-break-before: always` or `page-break-after: always` (case-insensitive, spaces optional), including `<hr>`. Assign `index` 1..N. Find the printed `label`: check the last 3 and first 2 non-empty text lines of each page for a token matching `^(\d{1,3}|[A-Z]{1,2}-\d{1,3}|[ivxlc]{1,6})$` standing alone, or at the start or end of a short line (under 80 chars) that also contains the company name or "Form 10-K" or "|". Then enforce sequence: labels must increase by 1 across consecutive numbered pages (arabic and `F-` series separately); a label that breaks the sequence is set to null. Remove the label line, and remove lines that repeat on more than 30% of pages (running headers and footers), from the page text.
3. `citation_rag/parse/items.py`: detect Item headings. A heading is a block element (not inside a `<table>` that is the table of contents) whose text matches `^\s*(?:PART\s+[IV]+\s*[,.\-–—]?\s*)?ITEM\s+(\d{1,2}[A-C]?)\s*[.:\-–—]?\s*(.*)$` (case-insensitive) and is under 200 characters. Rules:
   - Table-of-contents hits: skip headings that occur before the first heading whose following text (until the next heading) is longer than 1,500 characters, and skip any heading inside a table where more than 3 rows start with "Item".
   - Combined Items ("Items 7 and 7A", "Item 7 and 7A."): create both Items; give the text to the first and mark the second `present` with `page_start = page_end` of the first and one section whose text is "See Item {first}." Record the case in the checks stats.
   - Integrated annual reports (no body Item headings, but a cross-reference index table that maps Items to page labels): parse that index (rows "Item 1A. Risk Factors ... 50–64"), map page labels to page indexes, and cut Items at those pages. Mark stats `integrated_report: true`.
   - Incorporated by reference: if an Item's text is under 600 characters and contains "incorporated by reference" or "incorporated herein by reference", status is `incorporated_by_reference`.
   - Omitted Items: if an Item heading exists but the text is under 200 characters and matches "not applicable", "none", "not required", "reserved", or "omitted", status is `not_required` (keep the text). If the Item heading is missing entirely: status `absent`, and if `filer_category` contains "Smaller reporting" and the item is one of 1A, 1B, 6, 7A, status `not_required`.
   - Every standard Item appears once, in SEC order, with `part` set from the standard mapping.
4. `citation_rag/parse/sections.py`: split each Item's text into sections at heading blocks. A heading block is a block element whose text is under 200 characters and is either bold/italic/underlined (inline style `font-weight:bold|700`, `<b>`, `<strong>`, `text-decoration:underline`, or all caps with 3+ words), or in Item 8 a line that starts with "Note" followed by a number. Item 1A: each risk factor heading (bold or italic sentence) is a section. If an Item has no headings, the whole Item is one section. Sections shorter than 200 characters are merged into the following section (or the previous one at the end). Paragraphs are `<p>` or `<div>` blocks with text; join with a blank line. Section `text` is clean per the schema. Add the `[Table: id]` placeholder line at the table's position when 2b provides tables.
5. `citation_rag/parse/filing.py`: `parse_filing(path, meta) -> dict` producing the schema, `checks` included:
   - `required_items_present`: 1, 1A, 2, 3, 5, 7, 7A, 8, 9A, 15 are `present` or `not_required` or `incorporated_by_reference` (fails if any is `absent` for a non-smaller-reporting filer).
   - `items_in_order`: page_start is non-decreasing across `present` Items (integrated reports exempt).
   - `item_sizes`: Item 1A between 3,000 and 400,000 chars, Item 7 between 5,000 and 800,000, Item 8 between 5,000 and 3,000,000; others no check.
   - `coverage`: prose chars in all sections plus table text chars >= 0.95 of body text chars after cleaning, and <= 1.05.
   - `no_toc_in_sections`: no section text has 5 or more lines matching `^Item \d`.
   - `page_labels_monotonic`: labels increase within each series.
   Table checks come from 2b (`xbrl_coverage`, `table_count_range`) and are joined in 2c.
6. CLI: `uv run python -m citation_rag.parse.filing data/survey/sample/X.htm --meta '{...}'` writes the JSON to stdout or `--out`.
7. `tests/test_parse_prose.py`: unit tests on small synthetic HTML strings for page splitting, page labels, TOC skipping, combined Items, incorporated-by-reference, and section splitting. Plus one test that parses all 30 sample filings and asserts each produces a JSON with all 22 Items in order.
8. `reports/wave-2a.md`: pass rate on the sample, which checks failed where, the cases you handled, and the cases you could not handle (with the sample file as evidence).

## Definition of done
- `uv run pytest tests/test_parse_prose.py` passes.
- At least 27 of the 30 sample filings pass all prose checks. For each failure, the report names the reason.
