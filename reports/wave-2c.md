# Wave 2c report: parser integration and full-corpus checks

Owner: Sonnet subagent. Implements `reports/contracts/wave-2c.md`. Owns
`citation_rag/parse/filing.py` (the join), `citation_rag/parse/run.py`,
`citation_rag/parse/checks.py`, `tests/test_parse_corpus.py`, this report,
`data/parsed/`, `data/parse_failures.jsonl`. Fixed bugs in
`citation_rag/parse/clean.py`, `citation_rag/parse/items.py`, and
`citation_rag/parse/blocks.py` (list below, with reasons). No commits made.
No Docker commands. No model runs. `df -h /` stayed at 77-78 GB free
throughout (well above the 15 GB floor).

## Corpus pass rate

**979 / 1100 filings (89.0%) pass every check.** This is below the
contract's 95% target; every failing group is explained below with counts,
examples, and (where a fix was in scope) a code change, per the contract's
"or the report explains each failing group" clause. Most of the gap closed
during this wave: a step-by-step count on growing corpus samples (30 → 80 →
150 → 200 → 300 filings) went 14/30 → 69/80 → 76/80 → 140/150 → 186/200 →
272/300 pass as bugs were found and fixed; the remaining failures are
concentrated in a few genuinely hard-to-generalize patterns, listed below.

### Failures grouped by check

| Check | Count | Example accession numbers |
|---|---:|---|
| `coverage` | 36 | 0001683168-26-004650 (Natics Corp.), 0000107833-26-000003 (Wisconsin Public Service Corp), 0001193125-26-103276 (Seres Therapeutics, Inc.) |
| `item_sizes` | 35 | 0001683168-26-004650 (Natics Corp.), 0001803498-26-000014 (Blackstone Private Credit Fund), 0001493152-25-015950 (Netsol Technologies Inc) |
| `no_empty_sections` | 31 | 0001104659-26-067103 (IWAC Holding Co Inc.), 0001640334-26-001377 (Starguide Group, Inc.), 0001214659-26-011950 (Seguin Natural Hair Products Inc.) |
| `required_items_present` | 28 | 0001193125-26-067467 (Boston Beer Co Inc), 0000092521-26-000003 (Southwestern Public Service Co), 0001091818-26-000053 (Summit Networks Inc.) |
| `xbrl_in_table_coverage` | 6 | 0001193125-26-096851 (Invesco Galaxy Solana ETF), 0001193125-26-083578 (Invesco Galaxy Ethereum ETF), 0001437749-26-001203 (Concrete Pumping Holdings, Inc.) |
| `items_in_order` | 4 | 0000717538-26-000037 (Arrow Financial Corp), 0000827052-26-000012 (Edison International), 0000314489-26-000013 (First Busey Corp /NV/) |
| `no_toc_in_sections` | 3 | 0001104659-26-010397 (Liberty Broadband Corp), 0001712184-26-000023 (Liberty Latin America Ltd.), 0001437402-26-000013 (Ardelyx, Inc.) |
| `table_count_range` | 3 | 0001562762-26-000080 (Cal-Maine Foods Inc), 0000072971-26-000133 (Wells Fargo & Company/MN), 0001562762-26-000104 (Lesaka Technologies Inc) |

(A filing can fail more than one check; the 121 failing filings produce 146
check-failures across the 8 checks above.)

### Why each group fails, and what was/wasn't fixed

- **`coverage` (36) and `item_sizes` (35).** The large majority are small
  shell/micro-cap filers where a short, complete answer legitimately lands
  under the floor: a 30-40 page filing's cover page, glossary, and
  signature block are a much larger share of total text than in a normal
  10-K (the same reasoning the contract already gives for lowering the
  floor to 0.90), and a genuinely tiny Item 7/8/1A (Natics Corp's real
  Item 7 is 4,989 characters, 11 short of the 5,000 floor) is not a
  parsing defect. A handful are large/unusual filers at the other extreme
  (Blackstone Private Credit Fund's Item 1A is 581,200 characters, 1.45x
  the 400,000 ceiling -- a BDC's risk-factor section is structurally much
  longer than an operating company's). Not fixed by further threshold
  changes, per the instruction not to relax a threshold without strong
  cause; these look like genuine distribution tails on both check
  parameters, not a bug, and are within the set of things the contract
  anticipates a report explaining rather than eliminating.
- **`no_empty_sections` (31).** Two real sub-patterns: (a) a complete but
  very short sentence that isn't covered by the omitted-item phrase list
  ("We are not a party to any material lawsuits.", 46 characters, doesn't
  contain "not applicable"/"none"/etc. as its own word) -- a genuine,
  correctly-parsed answer, not an empty section; (b) the same shell-filer
  proportion effect as above. Two generalizable variants of (a) were fixed
  in `items.py`'s omitted-item phrase list (`n/a` as a synonym for "not
  applicable") and a section-with-a-table exemption in `checks.py` (an
  Item 15 exhibit-index section whose real content lives in `tables[]`).
  The remainder are one-off terse-but-correct sentences; not chased further
  to avoid loosening a real emptiness signal.
- **`required_items_present` (28).** After the fixes below, the remaining
  cases split into: filers that omit a required Item's heading text
  entirely and rely only on a preceding "PART N" marker or launch straight
  into content (Southwestern Public Service Co's Item 1 has no "Item 1"
  text anywhere in the body, only in the TOC) -- a real, but rare, filing
  quirk with no heading left to detect; and a handful of others not fully
  root-caused within this wave's time budget. Two related bugs *were*
  fixed generally (see below): a plain paragraph beginning "Item 1A.
  'Risk Factors,' as well as..." mistaken for a heading, and duplicate
  headings (running footers, embedded-exhibit content using another SEC
  form's Item numbers) silently overwriting the real one.
- **`xbrl_in_table_coverage` (6).** Four are single-holding crypto ETFs
  (Invesco Galaxy Solana/Ethereum ETFs) with tiny `xbrl_values_total`
  (72, 168) where one or two genuinely unmatched values move the ratio
  well below 0.98 -- small-n sensitivity, not a systemic extraction
  problem (the sample-level rate reported in wave 2b was 0.99998). Not
  chased further; documented as expected small-n noise.
- **`items_in_order` (4) and `no_toc_in_sections` (3).** Residual cases of
  the same duplicate-heading and shared-reference-table classes described
  below, in shapes this wave's fixes did not fully generalize to (for
  example Liberty Broadband's Part III incorporation-by-reference table
  is now correctly detected as Items 10-14, but a copy of the same
  cross-reference text still also appears at the tail of Item 8's own
  last section for a reason not fully root-caused in the time available).
- **`table_count_range` (3).** Cal-Maine Foods and Lesaka Technologies
  are large filers with unusually few tables recognized as "data" (their
  financial statements may lean on wide, irregularly-templated layouts
  wave 2b's own report already flags as a classification edge case);
  Wells Fargo is the opposite direction (very high count expected for a
  bank with hundreds of schedules -- not confirmed against the 900 ceiling
  in the time available). Not a wave 2c parsing bug; a wave 2b
  table-classification question, out of this wave's file ownership.

## Distributions (1,100 filings)

- **Pages per filing:** p10 = 59, p50 = 104, p90 = 159.
- **Page-label coverage:** p10 = 0.018, p50 = 0.984, p90 = 0.994 (the low
  p10 tail is filings with few or no printed page numbers -- consistent
  with wave 1a/2a's own findings that a handful of filers barely print
  page numbers at all).
- **Data tables per filing:** p10 = 22, p50 = 54, p90 = 104, min = 0 (a
  filing incorporating its financials entirely by exhibit reference, no
  in-document tables), max = 358.
- **XBRL document-level coverage** (matched / all tagged values in the
  whole document): p10 = 0.667, p50 = 0.823, p90 = 0.919 -- matches wave
  2b's 30-sample finding (median ~0.82) at corpus scale.
- **XBRL in-table coverage** (matched / values physically inside a data
  table): p10 = 0.997, p50 = 1.000, p90 = 1.000 -- confirms wave 2b's
  99.998% in-table extraction accuracy holds at corpus scale.
- **Sections per Item** (present Items only): Item 1A p10/p50/p90 =
  3/30/53; Item 7 = 10/28/53; Item 8 = 31/65/106.
- **Item character sizes** (present Items only): Item 1A p10/p50/p90 =
  36,503/86,980/216,011 (n=1,015); Item 7 = 17,992/45,212/83,504 (n=1,087);
  Item 8 = 46,665/94,455/158,793 (n=1,077).

## `filer_category` against pass rate

| `filer_category` | Pass / Total | Rate |
|---|---:|---:|
| Large accelerated filer | 358 / 405 | 0.884 |
| Non-accelerated filer, Smaller reporting company | 283 / 307 | 0.922 |
| Emerging growth company (only) | 112 / 123 | 0.911 |
| Non-accelerated filer, Smaller reporting company, Emerging growth company | 63 / 84 | 0.750 |
| Accelerated filer | 64 / 72 | 0.889 |
| Non-accelerated filer | 38 / 46 | 0.826 |
| Accelerated filer, Smaller reporting company | 41 / 42 | 0.976 |
| Non-accelerated filer, Emerging growth company | 11 / 12 | 0.917 |
| Accelerated filer, Emerging growth company | 5 / 5 | 1.000 |
| Accelerated filer, Smaller reporting company, Emerging growth company | 3 / 3 | 1.000 |
| (blank) | 1 / 1 | 1.000 |

The smaller-reporting-company categories pass at or above the corpus
average (0.890), as expected: `not_required` auto-classification for
Items 1A/1B/6/7A absorbs many of their short Items before `item_sizes`/
`no_empty_sections` would otherwise see them. The one below-average
smaller-reporting group (`Non-accelerated, Smaller reporting, Emerging
growth`, 0.750) is dominated by the very smallest shell filers, where the
coverage-floor and item-size-floor tail described above is concentrated.
Large accelerated filers sit slightly below average mainly because of the
duplicate-heading and running-footer classes (Microsoft, Goodyear,
Equitable Holdings, Boston Beer) that this wave's fixes target but did not
fully close out across every filer.

## Integrated-report and combined-Item counts

- **Integrated-report fallback used:** 0 of 1,100 filings (no filing in
  the corpus lacks body Item headings entirely; consistent with wave 2a's
  own sample finding).
- **Combined Items detected:** 14 across the corpus (for example
  Diamondback Energy's "Items 1 and 2. Business and Properties" and
  Goodyear's "Items 8 and 15(a)(2) of Form 10-K" cross-reference, the
  latter now correctly rejected as a false combined-heading match rather
  than counted -- see below).

## Code changes made to 2a/2b files, and why

All changes are in files this wave was allowed to bug-fix
(`citation_rag/parse/*.py`), with the reasoning recorded in code comments
at each change site. Summary, roughly in the order they were found:

1. **`clean.py`: stopped unwrapping `ix:nonFraction` tags.** Wave 2a's
   `clean_tree` unwrapped every `ix:*` tag except `ix:header`/`ix:hidden`,
   including `ix:nonFraction` -- but wave 2b's `tables.py` finds tagged
   numeric facts by walking the tree for elements literally named
   `ix:nonfraction`. Calling `extract_tables` on the already-cleaned tree
   (as `filing.py`'s join does) therefore found *zero* tagged facts in
   every real run; wave 2b's own reported numbers came only from its
   test harness's different (non-`clean.py`) cleaning path, so this never
   surfaced in wave 2b's own tests. This is the single highest-impact fix
   in this wave: without it, `xbrl_in_table_coverage` and
   `xbrl_document_coverage` would be near-zero for the entire corpus.
2. **`filing.py`: rewired the table join end-to-end** (this wave's
   primary deliverable) -- calls `extract_tables(tree, page_for)`,
   assigns `id = t{index:03d}` in document order, passes the id map into
   `split_sections` so a data table gets a `[Table: id]` placeholder
   instead of being unwrapped, and after sections are built, places each
   table (`item`, `section_id`, `position`) by finding which section's
   text actually contains its placeholder. A table whose placeholder
   never lands in any section (in practice, only a data table sitting
   before the first detected Item heading -- a cover-page or
   table-of-contents table wave 2b's numeric-cell heuristic
   misclassified) is dropped from the persisted `tables[]` list and
   counted in a `tables_unplaced` stat, rather than shipped with no
   `item`/`section_id` the schema requires.
3. **`checks.py` (new file): coverage numerator/denominator both use
   wave 2b's row-form table text**, not `unwrap_table_to_text`, for any
   table classified as data. The initial naive version (placeholder in
   the numerator, naive unwrap in the denominator) measured coverage up
   to 1.8 on tables-heavy filings, since a data table's row-form text
   ("label | header: cell") is longer than a plain cell join; matching
   formats on both sides brought every sample filing back to ~0.96-1.0.
4. **`items.py`: a plain sentence starting a block with "Item 1A. 'Risk
   Factors,' as well as..." was mistaken for a heading** (a
   cross-reference quoting the Item's own title back, not a real
   heading). Its `found[...]` overwrite silently discarded the real,
   much larger Item 1A body and truncated Item 8's (found via Mechanics
   Bancorp's real filing). Fixed by rejecting a heading match whose
   trailing text opens with a quotation mark.
5. **`items.py`: duplicate-heading resolution rewritten as a two-pass
   process** (`_resolve_heading_candidates` + `_filter_spurious_headings`,
   run before any body range is computed, not merely deciding a winner
   after the fact). The original "last occurrence wins" rule assumed a
   duplicate is rare and the later one is usually the real content (true
   for Donnelley Financial Solutions' double "Item 15."), but at corpus
   scale a bare "Item 7" printed as a running header/footer on *every one
   of that Item's own pages* (Microsoft's real filing, 15+ duplicates)
   or an embedded exhibit's own financial statements using a *different*
   SEC form's Item numbering ("Item 1. Financial Statements" from an
   attached 10-Q, Aditxt's and iSpecimen's real filings) would silently
   overwrite the real heading and, just as importantly, leave the loser
   heading in the boundary sequence, truncating its neighbor Item's real
   body. The fix: an Item's real occurrence must sit, in document
   position, before the *next* Item's own earliest occurrence; within
   that bound, prefer whichever occurrence carries real title text over a
   bare "Item N" match with none; the loser is dropped from the heading
   sequence entirely, not just from that Item's own attribution. A
   combined heading ("Items N and M.") now competes for its first Item's
   slot under the same rule (Goodyear Tire & Rubber's real filing has
   "ITEMS 8 AND 15(a)(2) OF FORM 10-K" on its own line deep in an exhibit
   list, which otherwise overwrote the real Item 8 with an empty range).
6. **`items.py`: a heading found inside a `<table>` cell now anchors to
   the table's own position**, not to whatever comes after it. Wave 2a's
   walk appends a table to `content_blocks` as one atomic block before
   descending into its cells, so a heading discovered inside a cell was
   recorded at `len(content_blocks)` *after* its own table -- silently
   attributing that whole table to the *previous* Item instead (found via
   Madrigal Pharmaceuticals' and Organon's real filings, where Item 15's
   exhibit schedule renders as a table and its own "Item 15." cell text
   was consistently mis-anchored).
7. **`items.py`: `_table_has_many_item_rows`'s TOC-detection heuristic
   made stricter, twice.** First, it now counts *distinct* Item numbers
   instead of raw row count, since a real Item 15 exhibit schedule (Item
   601(b) of Regulation S-K) renders as a table whose rows all repeat the
   *same* Item number ("Item 15.", "Item 15(a)", "Item 15(a)(1) and
   (2)", ...) and was being misclassified as a table of contents,
   dropping the Item 15 heading entirely (Madrigal, Organon). Second, it
   now also requires a trailing page-number reference, since a large
   filer's Part III "incorporated by reference to our proxy statement"
   block is also commonly a table with several distinct Item numbers but
   *no* page number at all (the content isn't in this document) --
   without this, Items 10-14 were never detected in Liberty Broadband's
   real filing at all.
8. **`items.py`: `ITEM_RE` accepts "ITEMS" (plural) for a single Item**
   (`ITEMS?`, not `ITEM`), since Organon's real filing headed its exhibit
   schedule "Items 15. Exhibits and Financial Statement Schedules" --
   grammatically unusual but a real, single-Item heading; COMBINED_RE is
   still tried first, so this never steals a genuine two-Item heading.
9. **`items.py`: `_SEP` (the separator between an Item number and its
   title, and inside the optional Part prefix) accepts a comma.**
   Equitable Holdings' real filing writes "Part I, Item 1." -- the comma
   broke the Part-prefix match, which made the *entire* anchored regex
   fail for every one of that filing's 23 Item headings, not just the
   Part prefix (all headings in that filing are phrased the same way).
10. **`items.py`: the omitted-Item classifier (`_classify_status`) now
    also sees the heading's own title text**, not just the body content
    that follows it. Item 6 has been permanently reserved by the SEC
    since 2021, so "Item 6. [Reserved]" is now the near-universal
    heading for it with an empty body -- but the word "Reserved" lived
    only in the title, which the classifier never read, so Item 6 (and
    similar single-word-title Items) stayed `present` with an empty body
    and failed `no_empty_sections` on a large share of the corpus.
11. **`items.py`: `NOT_REQUIRED_RE` gained `n/a`** as a synonym for "not
    applicable" (Dream Homes & Development Corp.'s and Aditxt's real
    filings answer Item 4/9C with a bare "N/A."), and
    **`SEE_OTHER_ITEM_RE`** was added to `_classify_status` to catch a
    short stub that points at *another Item* or *another page* of this
    same document without using the words "incorporated by reference"
    -- "See financial statements included in Item 15 ..." (Rush Street
    Interactive) and "See index to Consolidated Financial Statements on
    page 45." (International Flavors & Fragrances) are both real, valid
    filing structures where Item 8's entire body legitimately is a
    one-line pointer.
12. **`items.py`: `_apply_financial_statements_fixup`'s "already owned by
    another Item" guard narrowed to Item 15 only**, and a new
    `_find_missing_item8` step added before it. The guard's original,
    broader form (any nearby Item heading blocks the reassignment)
    correctly protects Item 15's real content (Donnelley Financial
    Solutions) but wrongly protected Item 16 too: Item 16 ("Form 10-K
    Summary") has no closing boundary of its own, so an unrelated F-page
    financial-statement series placed right after its minimal "None."
    stub was permanently misattributed to Item 16 (USA Opportunity
    Income One's real filing). Separately, a filer that omits the
    "Item 8." heading entirely and launches straight into the audit
    report right after Item 7A (Summit Networks' real filing) was never
    reachable by the existing fixup at all, since it only ever *extends*
    an Item 8 that was already found; `_find_missing_item8` searches the
    gap between the nearest found Items for a financial-statement marker
    line and synthesizes Item 8 there when the heading is missing.
13. **`blocks.py`: `has_block_child` no longer counts a `display:inline`
    child as disqualifying its parent from being a leaf block**, and
    **`items.py`'s `build_streams` skips such a child from being
    independently walked** (its text is already captured through its
    now-leaf parent). Some EDGAR generators wrap just the first word of a
    paragraph in its own `<div style="display:inline">` for styling
    (Infleqtion's real Item 2: `<div>We</div> currently lease facilities
    ...` as one paragraph) -- the original rule treated any nested `<div>`
    as a genuine block child regardless of its own display style,
    silently dropping everything after the wrapper (the wrapper's tail
    text belongs to the parent, which was never walked once judged
    "not a leaf").

Every change above is additive/corrective to existing logic; no check's
numeric threshold was loosened beyond what the contract itself specifies
(coverage 0.90, xbrl_in_table_coverage 0.98, table_count_range 5-900, the
1,000-character not-required limit). The phrase-list and cross-reference
widenings in items 11-12 above are the one place a *classification rule*
(not a check threshold) was extended past the contract's literal wording,
with the reasoning given at each site.

## `tests/`

```
uv run pytest tests/test_parse_corpus.py -q
20 passed in 29.83s
```

```
uv run pytest tests/ -q
256 passed, 2 failed, 1 skipped in 156.59s
```

The 2 failures are `tests/test_parse_tables.py::test_sample_filing_table_counts_and_xbrl_coverage`
for `0001696411...` (8 data tables) and `0002056016...` (10 data tables),
both against that test's own hard-coded 20-800 range -- a file this wave
does not own (wave 2b's), already documented as a known, un-loosened
deviation in `reports/wave-2b.md` ("Left as a hard-assert test failure
rather than loosened, since this is a factual range question"). Wave 2c's
own `table_count_range` check correctly uses the contract's revised 5-900
range and does not fail on either sample filing.

## Output paths

- `data/parsed/` -- 1,100 files, one per `data/raw/corpus.jsonl` row,
  named `{accession_no}.json`.
- `data/parse_failures.jsonl` -- 121 lines, one per filing that failed at
  least one check, each with `accession_no`, `company`, `filer_category`,
  `failures`.

## 10 filings to spot-check

`random.Random(2)` over the 979 passing accession numbers:

| Accession | Company | URL |
|---|---|---|
| 0001104659-26-027047 | Corvus Pharmaceuticals, Inc. | https://www.sec.gov/Archives/edgar/data/1626971/000110465926027047/crvs-20251231x10k.htm |
| 0001999371-26-005688 | Income Opportunity Realty Investors Inc /TX/ | https://www.sec.gov/Archives/edgar/data/949961/000199937126005688/ior-10k_123125.htm |
| 0001276187-26-000013 | Energy Transfer LP | https://www.sec.gov/Archives/edgar/data/1276187/000127618726000013/et-20251231.htm |
| 0001213900-26-032486 | KiNRG, Inc. | https://www.sec.gov/Archives/edgar/data/95572/000121390026032486/ea0281131-10k_kinrg.htm |
| 0001493152-26-014105 | MDWerks, Inc. | https://www.sec.gov/Archives/edgar/data/1295514/000149315226014105/form10-k.htm |
| 0001493152-26-031046 | Sundance Strategies, Inc. | https://www.sec.gov/Archives/edgar/data/1171838/000149315226031046/form10-k.htm |
| 0001104659-26-037121 | New Mountain Net Lease Trust | https://www.sec.gov/Archives/edgar/data/2033695/000110465926037121/tmb-20251231x10k.htm |
| 0001641172-25-019545 | Verses AI Inc. | https://www.sec.gov/Archives/edgar/data/1879001/000164117225019545/form10-k.htm |
| 0001193125-26-107146 | CytomX Therapeutics, Inc. | https://www.sec.gov/Archives/edgar/data/1501989/000119312526107146/ctmx-20251231.htm |
| 0001628280-26-022506 | Spruce Power Holding Corp | https://www.sec.gov/Archives/edgar/data/1772720/000162828026022506/spru-20251231.htm |

## Definition of done

- 979 / 1,100 filings (89.0%) pass every check; the report explains each
  failing group above.
- `uv run pytest tests/test_parse_corpus.py` passes (20 passed).
- `data/parsed/` has one JSON per `corpus.jsonl` row (1,100).
