# Wave 2e report: heading rule without "Item", absent-Item check, re-parse

Owner: Sonnet subagent. Implements `reports/contracts/wave-2e.md`. Owns
`citation_rag/parse/items.py`, `citation_rag/parse/checks.py`,
`tests/test_parse_prose.py`, this report. No commits made. No Docker. No
model runs. No corpus parse and no multi-process job (the full re-parse
was not run, per the machine rule in effect during the TIMING window). One
file at a time was parsed, for verification only (Spruce Power, BancFirst
Corp, and a lightweight raw-text scan of the other 49 filings with an
absent required Item). `df -h /` showed 54 GB free throughout, well above
the 15 GB floor.

## Why

Spruce Power Holding Corp (`0001628280-26-022506`) heads its risk-factors
section "1A. Risk Factors" in the body -- no word "Item" anywhere in that
heading. "Item 1A" (with the word) appears only in the table of contents
(a `<td>`) and in cross-references; the body heading is a `<div>`. `ITEM_RE`
requires "Item" (or wave 2d's I/l typo forms), so the heading was never
detected, and `checks.py`'s `required_items_present` also exempted the
filing from failing on the resulting `absent` Item 1A because it is a
smaller reporting company (see `reports/wave-2d.md`'s "Spruce Power" gap).
Net effect: a real, missing risk-factors section passed every check.

## 1. Heading rule (`items.py`)

Added a second heading pattern, tried only when neither `COMBINED_RE` nor
`ITEM_RE` matches a candidate block: `_match_numbered_title_heading`
matches `^\s*(\d{1,2}[A-C]?)\s*[.:\-–—]\s*(...)$` and then requires the
trailing text to open with that Item's own canonical title, matched on the
title's first 3 words (`_title_prefix_re`, `_NUMBERED_HEADING_TITLE_RE`).
This is a general rule keyed off `ITEM_TITLES`, not a per-filing special
case -- it fires for any Item number whose title the trailing text opens
with. Item 6 accepts either "Reserved" or "Selected Financial Data" as its
title (`_NUMBERED_HEADING_TITLE_OVERRIDES`), since Item 6 has been
"[Reserved]" only since the SEC dropped "Selected Financial Data" as a
required Item in 2021 and older-vintage filings may still use the legacy
title.

The new match is inserted into `build_streams`'s existing heading-candidacy
check, producing the same two-group match shape (`group(1)` = number,
`group(2)` = trailing text) as `ITEM_RE`, so it flows through the same
`headings` list as every other candidate. TOC-skipping (`filter_headings`,
`_table_has_many_item_rows`) and duplicate-heading resolution
(`_resolve_heading_candidates`, `_filter_spurious_headings`, `_pick_heading`)
apply to it identically, with no separate code path, per the contract.

Concrete evidence this is a real, general detector gap and not one-off:
scanning the raw text of the 49 other filings with an absent required Item
(see impact estimate below) turned up a second, unrelated filing --
BancFirst Corp /OK/ (`0001193125-26-075954`, a *large accelerated* filer,
not smaller reporting, so its Item 1A `absent` was already on
`data/parse_failures.jsonl` under the old rule) -- with the same defect: a
body heading with no word "Item" at all. A single-file parse confirms
Item 1A goes from `absent`/failing to `present` (32 sections) and the
filing's `checks.passed` goes from `False` to `True`.

## 2. Check (`checks.py`)

`check_required_items_present` no longer exempts a smaller reporting
company from failing on an `absent` required Item. The old rule (`if
it["status"] == "absent" and not smaller_reporting`) exempted *any*
required Item for *any* smaller reporting company, not just the four
Items such a company may legitimately omit (`SMALLER_REPORTING_OMITTABLE`
= 1A/1B/6/7A) -- an `absent` status already means items.py could not find
a heading *and* (for an omittable Item) could not back up `not_required`
from the raw text either, so it is items.py's own signal of a probable
missed heading, not a legitimate omission. `not_required` is a distinct
status and is untouched by this change -- it still passes. The
`smaller_reporting` parameter is kept (filing.py, which this wave does not
own, still passes it) but no longer changes the outcome; this is noted in
the function's docstring.

## 3. Tests (`tests/test_parse_prose.py`)

Three new tests, plus the existing 30-sample-filing test (unchanged, still
passing):

- `test_numbered_title_heading_without_item_word_is_detected`: synthetic
  HTML with `1A. Risk Factors` and `7. Management's Discussion and
  Analysis` headings, neither containing the word "Item" -- both resolve
  to `present`.
- `test_smaller_reporting_company_absent_item_1a_fails_required_check`: a
  smaller reporting company's items list with Item 1A `absent` now fails
  `check_required_items_present`.
- `test_not_required_item_passes_required_check_for_any_filer`: Item 1A
  `not_required` still passes the check for both a smaller reporting and a
  non-smaller-reporting filer.

```
uv run pytest tests/test_parse_prose.py tests/test_parse_corpus.py -q
40 passed in 28.75s
```

Run single-process (no `-n`/xdist flag), per the TIMING-window machine
rule. This includes `test_all_sample_filings_produce_22_items_in_order`,
confirming the 30 sample filings still parse with 22 Items each, in
order.

## Spruce Power: single-file verification

```
uv run python -m citation_rag.parse.filing data/raw/0001772720/0001628280-26-022506.htm.gz --meta '...' --out spruce.json
```

- Item 1A: `present`, title "Risk Factors", **32 sections**.
- `checks.passed`: `True`, `failures`: `[]`.

(Before this wave: Item 1A `absent`, `checks.passed` `True` anyway, due to
the now-removed `smaller_reporting` exemption -- see `reports/wave-2d.md`.)

## 4. Impact estimate (no re-parse)

Over the current `data/parsed/` (1,333 filings, produced before this
wave's fix):

**51 filings have at least one required Item (`REQUIRED_ITEMS` = 1, 1A, 2,
3, 5, 7, 7A, 8, 9A, 15) with status `absent`.**

- **10 of the 51 were passing** under the old check, silently, only
  because of the `smaller_reporting` exemption this wave removes: Regis
  Corp (Item 8), Spruce Power Holding Corp (1A), Skytech Orion Global
  Corp. (8), Elite Performance Holding Corp (8), Powerdyne International
  (9A), NetBrands Corp. (2), Bioadaptives, Inc. (9A), DHI Group, Inc. (8,
  15), Cohen & Co Inc. (8), Good Gaming, Inc. (3). Under the corrected
  check, all 10 move onto the failure list unless rescued.
- **The other 41 of the 51 were already on `data/parse_failures.jsonl`**
  (already failing `required_items_present` or another check) before this
  wave; the check fix does not change their status, only the heading rule
  could.

**The heading rule rescues 2 of the 51** (confirmed by running each
through a full single-file parse, not just a text scan):

| Accession | Company | Item rescued | Was passing/failing before |
|---|---|---|---|
| 0001628280-26-022506 | Spruce Power Holding Corp | 1A | Passing (exempted) |
| 0001193125-26-075954 | BancFirst Corp /OK/ | 1A | Failing (large accelerated filer, not exempted) |

Both had the exact Spruce Power defect: a body heading naming the Item's
number and title with no word "Item" at all. Method: for the raw HTML of
all 51 filings, ran `clean_document` + `build_streams` (no full parse) and
checked whether any heading candidate now matches
`_match_numbered_title_heading` for one of that filing's absent required
Items; the 2 positive hits were then confirmed with a full single-file
`parse_filing` run showing the Item turn `present` and (for BancFirst) the
filing's `checks.passed` turn `True`.

**Net effect on a re-parse:** of the 51 filings with an absent required
Item today, 2 are rescued to `present` (1 previously passing, 1 previously
failing -> now passing); the other 49 stay `absent`. Of those 49, 9 were
previously passing under the removed exemption and will newly appear on
the failure list; the remaining 40 were already there. So the corpus pass
count is expected to move by roughly **-9 net** from this wave's check
fix, offset by **+1 net** from the heading-rule rescue of a previously
failing filing (BancFirst) -- a net change of about **-8** passing
filings, pending the actual re-parse (other checks, table placement, and
section splitting can shift a filing's overall pass/fail independent of
this one check, so this is an estimate, not the exact post-re-parse
count).

The 49 not rescued fail for reasons outside this wave's general rule --
for example Item 8's real body sitting in unplaced back-matter for a large
filer (American Airlines, CSX, UnitedHealth Group), an Item 15 exhibit
index with no on-page heading at all (J M Smucker, Cadence Design
Systems), or a filer that never prints several Item headings at all and
instead incorporates by reference without an on-page numbered-title stub
(General Electric, Federal Home Loan Mortgage Corp, Navient, Cal-Maine
Foods, Lesaka Technologies, Elanco Animal Health -- each missing most or
all required Items). None of these carry a "<number>. <canonical title>"
heading anywhere in their raw text, so the wave 2e rule correctly leaves
them `absent` rather than guessing.

## Re-parse command (not run)

```
scripts/parse.sh --force
```

## Definition of done, checked against the contract

- `uv run pytest tests/test_parse_prose.py tests/test_parse_corpus.py -q`
  passes single-process: **40 passed**.
- Spruce Power parses with Item 1A `present` on a single-file run: **yes**
  (32 sections, `checks.passed: True`).
- `reports/wave-2e.md` has the impact estimate: **yes**, above (51
  absent-required-Item filings; 2 rescued by the new heading rule).

## Re-parse result (orchestrator)

- `scripts/parse.sh --force` over 1,333 filings, 4 workers, 322 seconds.
- 1,194 pass, 139 fail (wave 2d: 1,202 pass, 131 fail). Net -8, as estimated: 10 silent gaps became visible failures, 2 filings were rescued.
- Spruce Power: Item 1A `present`, 32 sections, passes. BancFirst: Item 1A `present`, 32 sections, passes.

## Orchestrator spot check against sec.gov (cumulative, 5 filings)

| Filing | Item 1A start page (parsed / sec.gov) | First risk heading | Item 7 start page |
|---|---|---|---|
| Energy Transfer | 47 / 47 | (first section untitled) | 104 / 104 |
| CytomX | 34 (label none) / 32 | matches | 79 / TOC 78 |
| Spruce Power (after 2e) | present, 32 sections | matches | 36 |
| HNI Corp | 11 / 11 | exact match, second heading also exact | 26 / 26 |
| Franklin Covey | 13 / 13 | exact match; the second parsed heading differs from the second heading sec.gov's reader reported (order or a summary block) | 27 / 27 |

Two notes: (1) the first section of an Item often has no title (the text between the Item heading and the first sub-heading); the chunker handles it, but the citation card will show the Item title instead. (2) CytomX's Item 1A page has no printed label detected; the page index is right, the label is missing. Both are cosmetic for citations and recorded here for later polish.
