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

## 10-K structure

The SEC fixes the Items of Form 10-K and most of their content (Regulation S-K, Regulation S-X, US GAAP). The company fixes the headings, the layout, the sub-sections, and, in an integrated annual report, the order of sections. The Items are:

| Part | Items |
|---|---|
| I | 1 Business, 1A Risk Factors, 1B Unresolved Staff Comments, 1C Cybersecurity (from FY2023), 2 Properties, 3 Legal Proceedings, 4 Mine Safety |
| II | 5 Market for Common Equity, 6 [Reserved], 7 MD&A, 7A Market Risk, 8 Financial Statements, 9 / 9A / 9B / 9C |
| III | 10 to 14, usually incorporated by reference from the proxy statement |
| IV | 15 Exhibits, 16 Form 10-K Summary |

In Item 8, inline XBRL wraps each Note in a standard `us-gaap:...TextBlock` tag. This gives exact Note boundaries with standard names in filings from 2019 on.

## Chunking design: small-to-big

The unit of structure is the SEC hierarchy. Pages are metadata only.

```
Filing -> Part -> Item -> section -> paragraph or table
```

1. **Clean.** Strip the XML declaration. Delete `<ix:header>`. Keep the text of other `ix:` tags, and keep the XBRL TextBlock names as section markers.
2. **Pages as metadata.** Record `page_index` and `page_label` at each page break. Delete page numbers, running headers and footers, and table-of-contents pages from the text.
3. **Items.** Match `Item N` headings, skipping table-of-contents hits. For integrated annual reports (C, MS, JPM), map sections to Items through the cross-reference index or the standard section titles.
4. **Sections (parents).** One section per sub-section of an Item:
   - Item 1A: one section per risk factor heading.
   - Item 7 and 7A: one section per MD&A heading.
   - Item 8: one section per financial statement and per Note (XBRL TextBlock where present).
   - Other Items: one section per heading, or the whole Item if it is short.
5. **Chunks (children).** Inside one section, join paragraphs and tables up to 300 to 400 tokens, with about 50 tokens of overlap. A chunk never crosses a section. The embedding model (`bge-small-en-v1.5`) reads 512 tokens at most.
6. **Tables.** Layout tables become text. Data tables become Markdown. A table over the chunk limit is split by rows, and each part repeats the header rows.
7. **Context prefix.** Embed `Company | FY | Item | section title` + chunk text. Store the clean text for citations.
8. **Retrieval.** Hybrid search and rerank on chunks. Group hits by section. Send each parent section to the LLM up to about 4,000 tokens. If a section is larger, send a window around the matched chunks. Stop at a total budget of about 20,000 tokens. Evals tune these numbers.

## Open question

Items 15 and 16 (exhibits and summary) have little content for questions. They are indexed for now. Evals decide if we drop them.
