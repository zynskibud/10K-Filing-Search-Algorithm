# Wave 1a report: EDGAR survey and filer rule

Owner: orchestrator session citation-rag-41. This report covers steps 3-5 of the contract
(`reports/contracts/wave-1a.md`); steps 1-2 (index and metadata fetch) were done by an
earlier subagent and are verified below.

## Step 2 verification: candidates.jsonl

`data/survey/candidates.jsonl` has 7,052 rows, one per row of `index_10k.jsonl` (also
7,052 rows). Every row has all 22 fields the contract lists (no absent keys in any row).
Core filer metadata (`name`, `sic`, `category`) is present (non-null) in 100% of rows, well
under the 1% missing threshold. No rerun of `metadata.py` was needed.

Filings per quarter (from the index, confirmed against candidates.jsonl):

| Quarter | Filings |
|---|---|
| 2025QTR3 | 392 |
| 2025QTR4 | 363 |
| 2026QTR1 | 5,387 |
| 2026QTR2 | 621 |
| 2026QTR3 | 289 |
| **Total** | **7,052** |

Distinct CIKs: 6,625 (matches the 6,625 cached submission files).

## Step 3: filer rule and eligibility

`citation_rag/corpus/filer_rule.py` implements `is_eligible(row) -> tuple[bool, str]`,
checked in this fixed priority order (first hit wins, so exclusion counts partition
cleanly):

```python
def is_eligible(row: dict) -> tuple[bool, str]:
    sic = (row.get("sic") or "").strip()
    sic_description = (row.get("sicDescription") or "").strip().lower()
    entity_type = (row.get("entityType") or "").strip().lower()
    primary_document = (row.get("primaryDocument") or "").strip().lower()
    is_inline_xbrl = row.get("isInlineXBRL")

    if entity_type == "asset-backed":
        return False, "entity_type_asset_backed"
    if sic == "6189":
        return False, "sic_6189_asset_backed"
    if sic == "6792":
        return False, "sic_6792_royalty_trust"
    if sic == "6770" or sic_description == "blank checks":
        return False, "sic_6770_blank_check"
    if not is_inline_xbrl:
        return False, "not_inline_xbrl"
    if not primary_document.endswith((".htm", ".html")):
        return False, "primary_doc_not_html"
    return True, "eligible"
```

Exclusion table (7,052 candidates total):

| Reason | Count |
|---|---|
| eligible | 5,749 |
| sic_6189_asset_backed | 992 |
| sic_6770_blank_check | 253 |
| not_inline_xbrl | 48 |
| sic_6792_royalty_trust | 10 |
| entity_type_asset_backed | 0 (subsumed by sic_6189 in this data) |
| primary_doc_not_html | 0 |

Eligible count is 5,749, well above the 3,000 floor.

### Judgment calls

- **Asset-backed issuers**: matched on SIC 6189 or EDGAR's own `entityType == "asset-backed"`.
  The contract's name-pattern language ("Trust 20", "Receivables", "Funding LLC" *with* SIC
  6189) adds no filings beyond the SIC check read literally, so it was not implemented as a
  separate rule; it was only used to sanity-check the SIC 6189 rows.
- **Royalty trusts**: matched on SIC 6792 ("Oil Royalty Traders"), the standard code for
  oil and gas royalty trusts.
- **Blank-check/SPACs**: matched on SIC 6770 or `sicDescription == "Blank Checks"`.
- **Grantor trusts and commodity/ETF trusts (resolved, not an open question)**: these have
  no reliable field-based signal. A probe of SPDR Gold Trust (CIK 1222333) shows SIC 6221
  and `entityType = "operating"` — identical to the SIC the contract says must stay included
  (commodity pools) and to a normal operating company's `entityType`. A name-pattern
  exclusion would also drop legitimate operating companies and REITs named "... Trust". Per
  the orchestrator's decision, **these filers stay in the candidate pool**. The parser's
  Item-list and Item-size checks are the backstop: a trust whose 10-K does not follow the
  standard Item structure will fail parsing and land on the failure list instead of the
  corpus index, rather than being excluded here.

## Step 4: sample survey (30 filings, `random.Random(20260925)`)

`sample.py` ran with **no code changes needed** — it completed on the first run, downloaded
all 30 primary documents into `data/survey/sample/`, and wrote all 30 rows to
`data/survey/sample_survey.jsonl`.

### Generators (first 3,000 bytes)

| Generator | Count |
|---|---|
| other | 10 |
| Workiva | 10 |
| DFIN | 8 |
| Toppan | 2 |

("other" includes at least one distinct third-party generator, CompSci Transform, seen by
its HTML comment header — see parser cases below.)

### Page breaks

- Range: 0 to 197 explicit `page-break-before/after: always` markers.
- 7 of 30 filings (23%) have **zero** page-break markers despite being real multi-page
  10-Ks.
- The remaining 23 filings range from 20 to 197 breaks (roughly evenly spread, median
  around 110).

### Printed page-number coverage

- Average coverage across all 30 filings: 0.889.
- 3 filings score 0.0 (the same 3 zero-page-break "CompSci Transform" filings, where the
  whole document is treated as a single segment).
- Excluding those 3, coverage is 0.76-1.0, with 20 of 27 remaining filings at exactly 1.0.

### Item headings

All of Items 1, 1A, 1B, 1C, 2, 3, 4, 5, 6, 7, 7A, 8, 9, 9A, 9B, 9C, 10-16 were found at
least once. Most filings show each Item twice (TOC entry + body heading), consistent with
the contract's note. Item 4A ("Executive Officers of the Registrant," a legacy heading most
filers now fold into Part I or Item 10) appeared in only one filing
(`0000017843_0000017843-25-000021.htm`), twice (TOC + body) — a rare case the parser should
tolerate but not require.

### Tables

- Total `<table>` count: 40 to 320 per filing, average 137.
- "Data tables" (>=3 rows, >=2 columns, >=6 numeric cells): 8 to 133 per filing, average 57.8.
- `ix:nonFraction` tag count: 256 to 5,826 per filing, average 1,495. All 30 filings have at
  least one `<ix:` tag (consistent with the eligibility filter on `isInlineXBRL`).

### Filer categories

Nine distinct category strings appeared, including combinations joined by a literal `<br>`
(EDGAR's own separator, not a parsing artifact), for example
`"Non-accelerated filer<br>Smaller reporting company<br>Emerging growth company"`. One row
has an **empty** category string (see the co-registrant case below). Smaller reporting
companies and emerging growth companies are well represented (13 of 30 rows), confirming
the parser will regularly see filings with omitted or reduced Items from these categories.

### Parser cases the parser must handle (with sample filename evidence)

1. **No page-break markup at all, from a third generator ("CompSci Transform").**
   `data/survey/sample/0001854368_0001213900-26-037677.htm` (Digi Power X Inc.),
   `0000721693_0001213900-26-037674.htm` (Smart Powerr Corp.), and
   `0001117057_0001213900-26-037637.htm` (Planet Green Holdings Corp.) are generated by
   "CompSci Transform" (seen in an HTML comment: `<!-- Generated by CompSci Transform (tm)
   ... -->`). None contain any `page-break-before/after` CSS; page separations are only
   implied by a `<!-- Field: Rule-Page -->` comment plus a horizontal-rule `<div>`, with no
   machine-readable page boundary. `page_number_coverage` is 0.0 and the whole document
   collapses to one segment. The parser cannot rely on CSS page breaks or printed page
   numbers for this generator family; it needs an Item-heading-based fallback for citation
   locations.
2. **A named generator does not guarantee page-break markup.**
   `0001518715_0001518715-26-000026.htm` (Mechanics Bancorp) is generated by the Workiva
   Platform (confirmed by its header comment) yet has **zero** page-break markers across an
   8.4 MB, 141-table document. The parser must not branch purely on the generator name to
   decide whether page-break-based chunking is available; it must always probe the document
   itself.
3. **Small-cap/shell-adjacent filers commonly omit page-break markup.**
   `0001498067_0001641172-25-026351.htm` (SKYTECH ORION GLOBAL CORP.),
   `0002056016_0001493152-26-032759.htm` (Radiant Strategies Corp), and
   `0001672571_0001641172-25-017604.htm` (Antiaging Quantum Living Inc.) also show 0 page
   breaks — all are "Non-accelerated filer / Smaller reporting company" (some also
   "Emerging growth company").
4. **Co-registrant joint filings duplicate the same document under two CIKs.**
   Evidence: `0001920791_0001705696-26-000034.htm` (VICI Properties L.P., CIK 1920791) and
   the corresponding candidate row for VICI PROPERTIES INC. (CIK 1705696) — both rows
   share the **same** `accession_no` (0001705696-26-000034) and the
   same primary document (a REIT/operating-partnership joint 10-K). The L.P. row's
   `category` is an empty string while the REIT's is "Large accelerated filer." Across all
   of `candidates.jsonl`, 206 accession numbers are shared by more than one CIK (261 extra
   rows); in `eligible.jsonl`, 91 accession numbers are shared (138 extra rows). Left
   as-is, the corpus would ingest the same document text twice under two different
   CIK/company labels. This needs a decision (see open questions).
5. **An eligible filing with an apparently truncated Item list.**
   `0001696411_0001477932-26-005753.htm` (the smallest sample file, 374,715 bytes,
   generator "other") shows `item_headings_found` of only `1, 1A, 2, 3, 4, 5, 6` — Items
   7 through 16 were not matched at all. This may be a genuinely minimal shell-company 10-K,
   or the later Items may be phrased or formatted in a way the heading regex misses (for
   example rendered inside a table or image). Worth a specific check in the parser wave.
6. **"Reserved" and rare Items are routinely missing and should not be treated as errors.**
   `0000103682_0001193125-26-063120.htm` and `0001641172-25-017604.htm` omit Item 6
   (SEC designated it "Reserved" in 2021 and most filers now skip it) and/or Item 9C (only
   applies to filers with a PCAOB-inaccessible foreign auditor under the HFCAA, so it is
   absent from most filings).
7. **The filer `category` field is a `<br>`-joined multi-value string, not a single label.**
   Seen throughout the sample, for example
   `"Accelerated filer<br>Smaller reporting company<br>Emerging growth company"`. Any code
   that reads `category` (parser expectations for omitted Items) must split on `<br>`
   rather than compare the whole string.
8. **Page-number coverage can be partial even with page breaks present.**
   `0001859392_0001859392-26-000016.htm` (Galaxy Digital Inc., Workiva) has 184 page
   breaks but only 0.762 page-number coverage over 185 segmented pages — the lowest
   non-zero score in the sample. Some page boundaries in this filing do not carry a bare
   page-number token in their first/last lines (likely table-heavy or front-matter pages).

## Open questions for the human

- **Co-registrant duplicate filings.** 206 accession numbers in `candidates.jsonl` (261
  rows) and 91 in `eligible.jsonl` (138 rows) are shared by more than one CIK — the same
  primary document filed jointly by a parent and a subsidiary/operating-partnership
  registrant (see VICI Properties Inc. / VICI Properties L.P. above). Should the corpus
  dedupe these by `accession_no` (keeping one row, and if so which registrant's metadata),
  or keep both rows since they are formally separate filings under EDGAR? Not decided here.
- **Apparently truncated Item list in one eligible sample filing**
  (`0001696411_0001477932-26-005753.htm`, only Items 1-6 detected). Flagging for the parser
  wave to check whether this is a real minimal 10-K or a heading-detection miss; not
  resolved here.

## Resolved decisions (recorded, not open)

- **Grantor trusts and commodity/ETF trusts stay in the candidate pool.** No field-based
  signal separates them from normal operating companies or REITs without also catching
  legitimate filers. The parser's Item-list/Item-size checks are the backstop; trusts whose
  10-K does not follow the standard Item structure will fail parsing rather than being
  excluded at this stage. (Matches the entry already in `reports/OPEN-QUESTIONS.md` under
  "Wave 1a / filer rule.")

## Bug fixes made

None. `citation_rag/corpus/sample.py` ran to completion on the first invocation: it built
`eligible.jsonl` (5,749 rows) and the exclusion table, sampled 30 filings with
`random.Random(20260925)`, downloaded all 30 primary documents, and wrote all 30 rows to
`sample_survey.jsonl` without errors.

## Definition of done

- `data/survey/index_10k.jsonl` (7,052 rows), `candidates.jsonl` (7,052 rows),
  `eligible.jsonl` (5,749 rows), `sample_survey.jsonl` (30 rows), and `sample/` (30 files)
  all exist.
- `citation_rag/corpus/filer_rule.py` exists with `is_eligible`.
- Eligible count (5,749) is well above the 3,000 floor.
- This report is written.
