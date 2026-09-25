# Wave 2a report: prose parser

Owner: Sonnet subagent. Covers `citation_rag/parse/clean.py`, `pages.py`, `blocks.py` (new
shared helper, not itemized in the contract but owned by this wave), `items.py`,
`sections.py`, `filing.py`, `tests/test_parse_prose.py`.

Files NOT touched, as required: `citation_rag/parse/tables.py`, `citation_rag/parse/numbers.py`
(wave 2b landed these during this session), `citation_rag/corpus/`, `citation_rag/evals/`,
`pyproject.toml`, `.env`, `.gitignore`, `docker-compose.yml`. No commits were made.

## Status: complete for this wave's scope

The last full run (30 sample filings, single process, `.venv/bin/python3` script in the
scratchpad) and the last full test run both finished cleanly before the coordinator's pause
order arrived. Nothing was left running. No multi-process or long-running command was ever
started; everything in this wave was single-core `pytest`/`python3` invocations against local
files.

**Last completed pytest run:**

```
17 passed in 9.63s
```

(`uv run pytest tests/test_parse_prose.py`, equivalently `.venv/bin/python3 -m pytest tests/test_parse_prose.py`.)

A last full pass also ran `uv run pytest tests/` (the whole repo): 127 passed, 2 failed, and
both failures are in `tests/test_parse_tables.py`, which is wave 2b's file, testing wave 2b's
`citation_rag/parse/tables.py` (table-count-range assertions on two sample filings). Not this
wave's responsibility and not touched.

## Pass rate on the 29 in-scope sample filings

**24 of 29 pass all prose checks (83%).** The 30th sample filing
(`0001518715_0001518715-26-000026.htm`, Mechanics Bancorp) has 0 page-break markers of either
syntax and is out of scope per the contract's "fewer than 15 markers" rule; it still parses
without error (16 Items detected) but is not counted toward the 29.

Passing (24): INTERPARFUMS INC, Apellis Pharmaceuticals, Senior Credit Investments LLC,
Adaptive Biotechnologies Corp, Onfolio Holdings, VICI Properties L.P., VIASAT INC, CorVel Corp,
Digi Power X Inc., Virginia Electric & Power Co, S&P Global Inc., Tennessee Valley Authority,
Smart Powerr Corp., Fennec Pharmaceuticals, Carpenter Technology Corp, Galaxy Digital Inc.,
Skytech Orion Global Corp., J.Jill Inc., Columbus McKinnon Corp, Zevia PBC, Cytosorbents Corp,
Wisconsin Electric Power Co, Ascent Industries Co., Conduent Inc.

Failing (5), with the exact reason for each:

1. **Donnelley Financial Solutions** (`0001669811_0001193125-26-054586.htm`) --
   `items_in_order`: Item 16 starts at page 47, Item 15 (after this wave's fix) starts at page
   48. The filing's own heading text has "Item 15." appear twice: once at page 47 (a short
   stub, immediately followed by "Item 16. ... None.") and again at page 48, right before
   "INDEX TO CONSOLIDATED FINANCIAL STATEMENTS" and the real financial-statement content. The
   parser trusts the second, content-bearing "Item 15" heading (see judgment calls below), so
   Item 15's page range legitimately starts after Item 16's. This is a real quirk in the
   filer's own document order, not a detection miss; the `items_in_order` check has no
   exemption for it.
2. **Crona Corp.** (`0001696411_0001477932-26-005753.htm`) -- `coverage: 0.033`. This
   filing's detected headings are `ITEM 1. FINANCIAL STATEMENTS`,
   `ITEM 2. MANAGEMENT'S DISCUSSION AND ANALYSIS...`,
   `ITEM 3. QUANTITATIVE AND QUALITATIVE DISCLOSURES...`, `ITEM 4. CONTROLS AND PROCEDURES`,
   then Part II `ITEM 1. LEGAL PROCEEDINGS` through `ITEM 6. EXHIBITS` -- this is the **Form
   10-Q** Item structure (Part I Items 1-4, Part II Items 1-6), not the Form 10-K structure
   this parser targets. Wave 1a already flagged this file as having a suspiciously short Item
   list (`0001696411...`, "Items 7-16 not matched"); the heading text itself now confirms it
   is not a 10-K's Item list at all. This looks like an EDGAR index/eligibility issue from
   wave 1a (a 10-Q filed under a 10-K's accession, or a metadata mismatch), not something a
   10-K prose parser can fix. Recommend re-checking this filing's SEC form type in wave 1a's
   index before wave 2c re-runs it.
3. **Radiant Strategies Corp** (`0002056016_0001493152-26-032759.htm`) -- `coverage: 0.910`.
   A 30-page shell-company filing; front matter (cover page, before Item 1) and back matter
   (signatures) together are about 9% of total body text. For a document this short, a fixed
   few pages of non-Item boilerplate is a much larger share of the total than in a normal-size
   10-K, so the 0.95 floor is hard to clear even with correct parsing. See judgment calls.
4. **Planet Green Holdings Corp.** (`0001117057_0001213900-26-037637.htm`) -- `item_sizes:
   Item 1A: 708 chars (expected 3000-400000)`. Item 1A's body is a "smaller reporting company,
   not required to include risk factors" disclaimer, but the filer padded it with an extra
   sentence ("Investment in our securities involves a high degree of risk...") that pushes it
   to 708 characters -- over the contract's 200-character threshold for the `not_required`
   auto-classification, so it is left as `present` and fails the size floor. The contract's
   200-char threshold is followed literally per instructions; loosening it was not done
   without sign-off. See judgment calls.
5. **Antiaging Quantum Living Inc.** (`0001672571_0001641172-25-017604.htm`) -- `coverage:
   0.939`. Same small-filer front/back-matter proportion issue as #3 (39 pages total).

## Cases handled

- Legacy (`page-break-(before|after): always`) and modern (`break-(before|after): page`)
  page-break syntax, including on `<hr>`, including filings that mix both syntaxes in one
  document (`0000822663`) and the three CompSci Transform filings that use modern syntax only.
- Printed page labels: standalone tokens, tokens embedded in a short line with the company
  name/"Form 10-K"/"|", roman numerals, `F-N` series (including the "F - N" spaced-hyphen
  rendering some generators use), sequence enforcement per series with out-of-sequence labels
  nulled.
- Running header/footer removal (>30% of pages), with a minimum absolute occurrence count so
  the rule does not misfire on short documents (see judgment calls).
- Table-of-contents skipping via both contract rules: the "first heading with 1,500+ chars of
  following text" cutoff, and the ">3 rows starting with Item" table check -- including TOC
  rows rendered as `<td>Item 1.</td>` cells (title/page number in separate cells).
- Combined Items ("Items 7 and 7A."): both Items created, second marked `present` with
  `page_start == page_end` of the first and a "See Item {first}." section.
- Incorporated-by-reference and omitted-Item (`not_required`) detection, `filer_category`
  `<br>`-splitting, and the smaller-reporting-company auto-`not_required` rule for 1A/1B/6/7A
  when the heading is missing entirely.
- Section splitting on bold/italic/underlined/all-caps headings and Item 8's "Note N"
  headings; short-section merging forward (or backward at the end of an Item); the heading
  line is kept as the section's first paragraph (not just its `title`) so no body text is
  lost to classification (see judgment calls -- this was a real bug fix).
- The common "Item 8's body is a one-line stub pointing at the real financial statements"
  pattern, in two forms: a separate `F-1, F-2, ...` page-label series, and continuing arabic
  page numbers with a "CONSOLIDATED BALANCE SHEETS" / "REPORT OF INDEPENDENT REGISTERED
  PUBLIC ACCOUNTING FIRM" / "INDEX TO FINANCIAL STATEMENTS" marker heading later in the
  document -- with a safety check that backs off if the marker turns out to be the start of
  another Item's own legitimate heading, rather than corrupting that Item's content.
- The table join is one function call (`join_tables` in `filing.py`), tries
  `citation_rag.parse.tables.extract_tables`, and degrades safely to "no tables, unwrap
  everything into prose" if the import fails, the call raises, or (as turned out to be the
  actual case) the function's return shape isn't what wave 2a's own contract text
  anticipated (see judgment calls).
- Integrated-report cross-reference-table fallback (implemented per spec; not exercised by
  any of the 29 in-scope samples -- none of them lack body Item headings).

## Cases not handled (with sample file evidence)

- **A 10-Q misfiled/mis-indexed as a 10-K**: `0001696411_0001477932-26-005753.htm` (Crona
  Corp.) -- see failure #2 above. Out of scope for a 10-K prose parser; flagged for wave 1a/1b.
- **Two independent "financial statements are elsewhere" redirections landing on the same
  Item** in one document: VICI Properties L.P.'s original run (before this wave's F-page
  cluster fix) briefly exhibited Item 8 pointing at a stray, unrelated early "F-1" label; this
  is now fixed (see judgment calls), but the general class of "more than one page-label
  cluster claims the same prefix" is only handled by "pick the largest cluster," not by
  understanding the document semantically.
- **Front/back-matter proportion on very short filings** (Radiant Strategies, Antiaging
  Quantum Living): correctly parsed, but structurally cannot clear the 0.95 coverage floor
  because cover-page and signature content is a larger fraction of a short document. No code
  fix changes this; it is a property of the threshold vs. document length.
- **A filer-supplied "not required" disclaimer that exceeds the contract's 200-character
  auto-classification threshold** (Planet Green Holdings, Item 1A, 708 chars): left as
  `present`, fails the item-size floor, per literal reading of the contract's numeric rule.

## Judgment calls

1. **Comment-tail preservation in `clean.py`.** lxml's `Element.remove()` discards a removed
   node's `.tail` text. Some generators (CompSci Transform) place real page-number text as a
   comment's tail (`<!-- Field: PageNo -->i<!-- Field: /Sequence -->`); a naive comment-removal
   loop silently deleted that text. Fixed by preserving tail text on removal. This was the
   single highest-impact fix in this wave (it alone took the three CompSci Transform filings
   from ~2% page-label coverage to ~99%).
2. **Heading candidacy must be checked before the "inside a table" exclusion in
   `items.py`.** A table-of-contents row commonly puts "Item 1." in its own `<td>`, separate
   from the title and page-number cells. An earlier draft excluded all table-internal elements
   from heading consideration, which meant TOC rows built with `<table>` were invisible to
   both the heading detector and the TOC-skip logic -- functionally correct by accident (no
   heading, no skip needed) until it collided with the "F-page fixup" logic below. Fixed by
   checking heading candidacy first, regardless of table nesting, and only excluding
   non-heading table content from the general prose stream.
3. **Section text keeps its own heading line, not just its `title`.** The initial
   implementation moved a heading block's text into the section's `title` field and did not
   include it in `text`. Since headings are a normal part of a filing's content (for example
   dozens of "Note N. ..." headings in Item 8), stripping them out of `text` measurably lost
   body characters and was the single largest cause of `coverage` failures before the fix
   (several filings sat at 0.92-0.94 instead of ~0.99+). Decided: a section's `text` is
   self-contained for citation purposes and includes its own heading as the first paragraph;
   `title` is redundant metadata extracted from the same text, not a removal.
4. **The 30%-running-header threshold needs a minimum absolute occurrence count.** As a bare
   percentage it degenerates on short documents (a paragraph unique to one page of a 2-page
   document is already "50% of pages"). Added a `>= 3` distinct-page floor alongside the
   `> 30%` share. This does not change behavior on any real sample filing (all have >= 20
   pages) but was necessary for the synthetic unit tests and is a safer rule in general.
5. **Rejecting Form 8-K style decimal sub-item cross-references.** `ITEM_RE`/`COMBINED_RE`
   gained a `(?!\.\d)` guard so a body sentence like "as previously disclosed under
   Item 5.02 of a Current Report on Form 8-K" is not mistaken for a heading for Item 5 (seen
   in VICI Properties L.P.'s Item 9B).
6. **"Item 8 body is a stub, real financial statements are elsewhere" fixup.** Not in the
   contract's explicit rule list, but affected roughly a third of the sample (any filing where
   the financial statements are physically placed after Item 15/16 or on a separate F-page
   series). Implemented as: (a) prefer a distinct, clustered `F-`-page-label series if one
   exists (using the largest contiguous cluster of nearby F-labeled pages, to reject a lone
   stray early false match); (b) otherwise, search for a "CONSOLIDATED BALANCE SHEETS" /
   "REPORT OF INDEPENDENT REGISTERED PUBLIC ACCOUNTING FIRM" / "INDEX TO ... FINANCIAL
   STATEMENTS" heading later in the document. Both strategies back off entirely (touch
   nothing) if the candidate content turns out to already be the start of another Item's own
   detected heading, rather than partially cannibalizing that Item -- this was itself a bug
   found and fixed mid-wave (Donnelley Financial Solutions initially lost Item 15's real
   content this way; now it keeps it, at the cost of the `items_in_order` failure described
   above).
7. **`incorporated ... by reference` phrase tolerance.** Widened to accept "incorporated
   herein by *this* reference" (CorVel Corp's actual phrasing), which the literal contract
   regex (`incorporated (?:herein )?by reference`) would have missed by one word.
8. **Table join return shape.** The contract's own scope section describes calling
   `extract_tables(tree)` (one argument); wave 2b's actual, and wave 2c's contract's
   documented, signature is `extract_tables(tree, page_of) -> dict` (not a list). `join_tables`
   in `filing.py` calls with either arity (via `inspect.signature`) and only treats the result
   as populated tables if it is a `list`; wave 2b's dict result is therefore currently
   discarded and every table is unwrapped into prose for this wave's own runs (the stub
   behavior), which is what the 24/29 pass rate above reflects. This is a deliberate,
   conservative choice: wave 2b's table dicts do not yet carry an `id` (its own docstring says
   id/section_id/position assignment is wave 2c's job), so there is nothing to build a
   `[Table: id]` placeholder from that would not have to be redone by wave 2c anyway. The call
   site is exercised end-to-end (confirmed no exception, `extract_tables` does run) so wave 2c
   can wire in the real dict shape by changing only this one function.
9. **Integrated-report fallback and combined-Item logic are implemented per the contract but
   not exercised by any of the 29 in-scope sample filings** (none lack body Item headings, and
   none contain "Items N and NA" phrasing). They are covered by synthetic unit tests instead.

## Next step for whoever picks this up (wave 2c, or a continuation of this wave)

- Wire `join_tables` in `filing.py` to wave 2b's actual `extract_tables(tree, page_of) -> dict`
  return shape (`tables`, `layout_tables`, `xbrl_nonfraction_total`,
  `xbrl_nonfraction_matched`), assign table ids/`section_id`/`position`, and insert real
  `[Table: id]` placeholders instead of always unwrapping. This is explicitly wave 2c's scope
  per both contracts.
- Decide (with the human) whether `0001696411_0001477932-26-005753.htm` (Crona Corp.) should
  be dropped from the corpus/sample as a mis-typed filing, since its Item headings are a
  Form 10-Q's, not a Form 10-K's.
- No further work is needed on `clean.py`/`pages.py`/`items.py`/`sections.py`/`blocks.py` for
  this wave's own definition of done; 24/29 in-scope sample filings pass every prose check,
  and the 5 remaining failures are documented above with exact reasons rather than silently
  passing.

## Definition of done

- `uv run pytest tests/test_parse_prose.py` passes: **17 passed**.
- 24 of 29 in-scope sample filings (83%) pass every prose check; the report names the reason
  for each of the other 5, as the contract allows.
- This report is written.
