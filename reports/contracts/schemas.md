# Shared schemas

All contracts reference these. Do not change a field without the orchestrator.

## 1. Normalized filing: `data/parsed/{accession_no}.json`

`accession_no` uses dashes, for example `0000320193-25-000079`.

```json
{
  "accession_no": "0000320193-25-000079",
  "cik": "0000320193",
  "company": "Apple Inc.",
  "ticker": "AAPL",
  "fiscal_year_end": "2025-09-27",
  "fiscal_year": 2025,
  "filed_date": "2025-10-31",
  "filer_category": "Large accelerated filer",
  "sic": "3571",
  "source_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
  "pages": [{"index": 1, "label": null}, {"index": 2, "label": "1"}, {"index": 60, "label": "F-3"}],
  "items": [
    {
      "item": "1A",
      "part": "I",
      "title": "Risk Factors",
      "status": "present",
      "page_start": 6,
      "page_end": 17,
      "sections": [
        {
          "id": "0000320193-25-000079:1A:003",
          "seq": 3,
          "title": "The Company depends on component and product manufacturing and logistical services provided by outsourcing partners",
          "page_start": 8,
          "page_end": 9,
          "text": "Substantially all of the Company's manufacturing ...\n\n[Table: t017]\n\nMore prose ...",
          "tables": ["t017"]
        }
      ]
    }
  ],
  "tables": [
    {
      "id": "t017",
      "item": "1A",
      "section_id": "0000320193-25-000079:1A:003",
      "title": "Net sales by reportable segment",
      "units": "in millions",
      "headers": ["", "2025", "2024", "2023"],
      "rows": [
        {"label": "Americas", "indent": 0, "cells": ["167,045", "162,560", "158,364"]},
        {"label": "Total net sales", "indent": 0, "cells": ["391,035", "383,285", "394,328"]}
      ],
      "text": "Net sales by reportable segment (in millions)\nAmericas | 2025: 167,045 | 2024: 162,560 | 2023: 158,364\nTotal net sales | 2025: 391,035 | ...",
      "page_start": 9,
      "page_end": 9,
      "position": 1412,
      "xbrl_values_matched": 12,
      "xbrl_values_total": 12
    }
  ],
  "checks": {
    "passed": true,
    "failures": [],
    "stats": {"prose_chars": 412000, "body_chars": 431000, "coverage": 0.985, "data_tables": 92, "layout_tables": 71, "xbrl_nonfraction_total": 1170, "xbrl_nonfraction_matched": 1158}
  }
}
```

Rules:
- `pages[].index` is 1-based position. `label` is the printed page number as a string, or null.
- `items[].status` is one of `present`, `absent`, `not_required`, `incorporated_by_reference`. Every Item in the standard list appears once in `items`, in SEC order, whatever its status: 1, 1A, 1B, 1C, 2, 3, 4, 5, 6, 7, 7A, 8, 9, 9A, 9B, 9C, 10, 11, 12, 13, 14, 15, 16.
- `part` is "I", "II", "III", or "IV".
- Section `id` is `{accession_no}:{item}:{seq:03d}`. `seq` starts at 1 inside each Item.
- Section `text` is clean prose: no page numbers, no running headers or footers, no table-of-contents lines. Paragraphs are separated by one blank line. A table is replaced at its position by the line `[Table: {table_id}]` on its own line.
- `tables[].position` is the character offset of the placeholder line in the section `text`.
- `tables[].text` is the row form used for BM25 and embedding: title and units on the first line, then one line per row: `label | header1: cell1 | header2: cell2`. Number cells are plain strings as printed, with parentheses kept for negatives.
- A layout table (fewer than 2 columns, or fewer than 3 rows, or fewer than 6 numeric cells) is not in `tables`. Its text is unwrapped into the prose.

## 2. Golden set: `evals/golden/dev.jsonl` and `evals/golden/test.jsonl`

One JSON object per line.

```json
{
  "id": "g0042",
  "question": "What share of Apple's fiscal 2025 net sales came from the Americas segment?",
  "type": "number_from_table",
  "companies": ["0000320193"],
  "accession_no": "0000320193-25-000079",
  "item": "8",
  "section_id": "0000320193-25-000079:8:014",
  "table_id": "t017",
  "evidence": "Americas | 2025: 167,045",
  "page": 26,
  "answer": "About 42.7% (167,045 of 391,035 million)",
  "answer_kind": "number",
  "answer_value": 42.7,
  "notes": "Requires two cells from the same table."
}
```

Rules:
- `type` is one of `fact_lookup`, `number_from_table`, `paraphrased`, `exact_term`, `general`, `unanswerable`, `multi_part`.
- `companies` is a list of CIK strings, or the string `"general"`.
- `evidence` is copied exactly from the parsed JSON: from a section `text` (a contiguous substring of 40 to 400 characters), or from a table `text` (one or more whole lines). The validator checks this by exact substring match.
- `page` is the `pages[].index` (position), not the printed label. The validator checks that the evidence lies inside a section or table whose page range contains it.
- For `unanswerable`: `evidence` is null, `answer` is "The filing does not state this.", `answer_kind` is `none`, and `notes` says what nearby content exists (so the question is plausible, not random).
- For `multi_part`: `evidence` may be a list of 2 to 3 strings, each validated. `answer` covers all parts.
- For `general`: `companies` is `"general"`, and `evidence` is a list of strings from at least 3 different filings, each with its own `accession_no` in a parallel list `accession_nos`.
- `answer_kind` is `number`, `text`, or `none`. When `number`, `answer_value` is the numeric value, and `answer_unit` (optional) gives the unit.

## 3. Results log: `evals/results/runs.jsonl`

One line per eval run. Written by the harness only.

```json
{
  "run_id": "2026-09-27T14:03:11Z-a1b2",
  "git_commit": "abc1234",
  "split": "dev",
  "config": {"index": "bge-small/s2", "search": "hybrid", "reranker": "none", "k": 50, "top": 8, "router": true},
  "metrics": {
    "recall@8": {"value": 0.71, "ci95": [0.62, 0.79]},
    "mrr": {"value": 0.55, "ci95": [0.47, 0.63]},
    "recall@2000tok": {"value": 0.66, "ci95": [0.57, 0.74]},
    "recall@8_prose": {"value": 0.75, "ci95": [0.65, 0.84]},
    "recall@8_table": {"value": 0.58, "ci95": [0.41, 0.74]},
    "router_accuracy": {"value": 0.94, "ci95": [0.88, 0.98]},
    "latency_ms_p50": 210
  },
  "n_questions": 100,
  "notes": ""
}
```

## 4. Chunk record (Postgres, one table per index, defined in wave 4)

Columns: `id`, `accession_no`, `cik`, `item`, `section_id`, `table_id` (null for prose), `seq`, `page_start`, `page_end`, `page_label`, `is_table`, `text` (clean, for citations), `embed_text` (prefix + text, what was embedded), `embedding vector(d)`, `token_count`.
