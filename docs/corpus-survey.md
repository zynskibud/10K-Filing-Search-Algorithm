# 10-K corpus survey

Source: `ingest/survey.py` over all 1,056 filings (106 companies, fiscal years 2016 to 2026). Per-filing results are in `data/survey.jsonl` (not in Git).

## Findings

### 1. File format

- All filings are HTML. 752 of 1,056 (71%) are inline XBRL.
- Most filings come from Workiva (about 800). The rest come from DFIN, Toppan Merrill, and in-house tools.
- Inline XBRL adds a hidden `<ix:header>` block with machine data. The parser must delete it.
- The file starts with `<?xml ... encoding=...?>`. `lxml` rejects a string with this declaration, so the parser must strip it or read bytes. The first survey run lost the cover page because of this.

### 2. Pages

- Every filing has page breaks. The minimum is 17. The median is 115.
- Page breaks are CSS: `page-break-before:always` or `page-break-after:always`, usually on an `<hr>` or `<div>`.
- No table crosses a page break in a 60-filing sample. Pages are a safe first split.
- The printed page number is in different places:

| Pattern | Example |
|---|---|
| Last line, number only | `19` (JPM) |
| Last line, text then number | `Apple Inc. \| 2025 Form 10-K \| 12` |
| Last line, number then text | `19    Honeywell International Inc.`, `57 \| 2025 10-K` (MU) |
| Number line, then a footer line | `19` then `Comcast 2024 Annual Report on Form 10-K` |
| Header, not footer | `The Procter & Gamble Company        19` |
| Financial statement pages | `F-1`, `F-2` |
| Front matter | `i`, `ii` |

- If we check the last line only, the median filing has 99% of pages numbered. 168 filings have under 70%. These 168 match the patterns above.
- Some pages have no number, for example the cover page and dividers.

### 3. Items (sections)

- `Item 1A`, `Item 7`, and the others each appear 2 times in most filings. The first hit is the table of contents. The second hit is the real heading.
- Some filings repeat the Item name in each page header (16 to 18 hits).
- Some filings combine Items: `Items 7. and 7A. Management's Discussion...` (FCX).
- Banks (C, MS, and partly JPM) use an annual-report layout. Sections have titles such as `RISK FACTORS` and `MANAGEMENT'S DISCUSSION AND ANALYSIS`, and a cross-reference table maps Items to page ranges. No `Item N` heading exists in the body.
- 29 filings (C, FCX, MS) have no `Item 1A` or `Item 7` heading.

### 4. Tables

- The median filing has 163 tables. 92 of them are numeric (financial) tables.
- Tables hold about 20% of the text.
- About 13% of tables are under 20 tokens. These are layout tables for bullets or headings, not data.
- About 20% of data tables are over 500 tokens. The largest is about 10,000 tokens.

### 5. Size

- Tokens per filing: 10th percentile 53k, median 109k, 90th percentile 200k.
- The corpus has about 115M tokens. At 500 tokens per chunk, that is about 230k chunks.

## Chunking proposal

1. **Clean.** Strip the XML declaration and delete `<ix:header>`. Unwrap the other `ix:` tags and keep their text.
2. **Split into pages** at page-break elements. Each page gets a `page_index` (its position, 1 to N) and a `page_label` (the printed number, for example `47` or `F-3`).
3. **Find page labels.** Check the last 3 lines and the first 2 lines for a number pattern. Then check the sequence: labels must increase by 1. Fill gaps and drop outliers from the sequence. The UI uses `page_index` to show the page, and the citation shows `page_label`.
4. **Remove page furniture.** Delete the page number line, repeated running headers and footers (for example `Table of Contents`, `Pfizer Inc. 2024 Form 10-K`), and table-of-contents pages.
5. **Find sections.** Match `Item N` headings and skip the table-of-contents hits. If no Item headings exist, match the standard section titles (for example `RISK FACTORS` maps to Item 1A). Also keep the sub-heading (for example a risk factor title or a Note title) as `section_path`.
6. **Chunk text** inside one section. Target 400 to 500 tokens with about 50 tokens of overlap. Split on paragraph boundaries, then on sentence boundaries. A chunk can cross a page break, so it stores `page_start` and `page_end`.
7. **Chunk tables** apart from text.
   - A table under 20 tokens, or with only one column, is layout. Treat it as text.
   - Convert a data table to Markdown rows.
   - A table up to about 800 tokens is one chunk.
   - A larger table is split by rows. Each part repeats the header rows.
   - Each table chunk gets the text line above the table as its caption, for example "Consolidated Statements of Operations".
8. **Add context to each chunk.** Prefix the embedded text with `Company, FY, Item, section_path`. The model sees which filing a chunk is from. The stored `text` stays clean for citations.

## Open question

The exhibit index and signature pages (Items 15 and 16) are long and have little content for questions. The proposal indexes them. We can drop them later if evals show that they add noise.
