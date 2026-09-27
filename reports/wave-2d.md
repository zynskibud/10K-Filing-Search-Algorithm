# Wave 2d report: parser fixes and corpus top-up

Owner: Sonnet subagent (finishing a run stopped mid-wave; see
`reports/wave-2d-stop.md`). Implements `reports/contracts/wave-2d.md`.
Owns `citation_rag/parse/run.py` (the `os.nice(10)` line only),
`tests/test_parse_*.py` (stale expectations only), this report. The parser
fix itself (`citation_rag/parse/checks.py`, `citation_rag/parse/items.py`)
and the full 1,333-filing re-parse were done by the prior subagent before
the TIMING stop; this report picks up from there. No commits made. No
Docker. No model runs. No multi-process runs (the corpus was not
re-parsed). `df -h /` showed 59 GB free throughout, well above the 15 GB
floor.

## Pass counts, before and after

| Wave | Corpus size | Passing | Rate |
|---|---:|---:|---:|
| 2c | 1,100 | 979 | 89.0% |
| 2d | 1,333 | 1,202 | 90.2% |

1,202 >= the contract's 1,050-filing floor. `data/parsed/` has 1,333
files (one per `data/raw/corpus.jsonl` row); `data/parse_failures.jsonl`
has 131 rows.

## The 9 named filings

The contract names 9 of wave 2c's 53 passing-but-`not_required` Item 1A
filings as a "silent drop": the orchestrator's sec.gov spot check found
real risk factors behind a `not_required` classification the old rule
("smaller reporting company + no heading found = not_required") produced
without checking the raw text.

| Accession | Company | Item 1A status now | Sections | On failure list |
|---|---|---|---:|---|
| 0001628280-26-022506 | Spruce Power Holding Corp | `absent` | 0 | No -- see note below |
| 0001084869-26-000029 | 1-800-Flowers.com, Inc. | `present` | 5 | No |
| 0001854964-26-000006 | NewLake Capital Partners, Inc. | `present` | 60 | No |
| 0001493152-26-012916 | BTCS Inc. | `present` | 1 | Yes -- `item_sizes: Item 1A: 199 chars` |
| 0001829126-26-002303 | Mountain Crest Acquisition Corp. V | `not_required` | 1 | No -- see note below |
| 0002077096-25-000193 | Magyar Bancorp, Inc. | `not_required` | 1 | No -- see note below |
| 0001493152-26-023179 | OZOP Energy Solutions, Inc. | `not_required` | 1 | No -- see note below |
| 0001493152-26-015155 | Powerdyne International, Inc. | `not_required` | 1 | No -- see note below |
| 0001683168-26-002958 | Sativus Tech Corp. | `not_required` | 1 | No -- see note below |

Two outcomes need a flag, both found by reading the raw filing text
directly (`data/raw/.../*.htm.gz`), not just the parsed JSON:

**1. Five filings are still `not_required`, and that looks correct, not a
bug.** I read the raw HTML for Mountain Crest V, Magyar Bancorp, OZOP,
Powerdyne, and Sativus. All five have a real, on-line "ITEM 1A. RISK
FACTORS" (or "Item 1A - Risk Factors") heading, immediately followed by a
one-line disclaimer that matches the omitted-phrase list word for word,
for example:

- Mountain Crest V: "ITEM 1A. RISK FACTORS ... As a smaller reporting
  company, we are not required to make disclosures under this Item."
- Magyar Bancorp: "ITEM 1A. Risk Factors ... Not required for smaller
  reporting companies."
- OZOP: "ITEM 1A. RISK FACTORS ... We are a smaller reporting Company and
  are not required to include disclosures under this item."
- Powerdyne: "ITEM 1A. RISK FACTORS ... The Company qualifies as a smaller
  reporting company ... and is not required to provide the information
  required by this Item."
- Sativus: "Item 1A. Risk Factors. ... We are a smaller reporting company
  ... and are not required to provide the information required under this
  item."

That is rule (a) from the contract: heading found, body matches the
omitted phrases, so `not_required` is the right call. I searched each
document for a second, larger risk-factors block elsewhere (in case the
real content sits behind a different heading and the winning heading is
only a stub, the way BTCS's is) and found none -- just scattered, ordinary
uses of the word "risk" in unrelated boilerplate. So either these 5 of the
9 do not actually carry the "real risk factors" the orchestrator's spot
check found, or the spot check was checking something other than the
current text of this exact accession number. I cannot resolve that
from the parser or the downloaded HTML alone; flagging it rather than
guessing. The contract's Definition of Done ("none is `not_required`")
does not hold for these 5, as written.

**2. Spruce Power is `absent` but is not on the failure list -- a gap in
`check_required_items_present`, not in wave 2d's fix.** The fix correctly
stopped calling this filing's Item 1A `not_required` (no heading exists,
and the forward-long-run check confirms it: a cross-reference sits deep
inside Item 1's own body, 2,000+ characters before Item 1B is next
mentioned, so a real heading was likely missed). `items.py` now marks it
`absent`, exactly as the contract's item 1 says it should. But
`checks.py`'s `check_required_items_present` (unchanged by this wave, not
in the diff) reads:

```python
if it["status"] == "absent" and not smaller_reporting:
    bad.append(it["item"])
```

Spruce Power is a smaller reporting company, so this check never flags
its absent item at all -- for any required item, not just the four
omittable ones. The filing's `checks.passed` is `True` and it never
reaches `data/parse_failures.jsonl`. So the fix moved the Item 1A
classification from a wrong `not_required` to a correct `absent`, but the
existing check that is supposed to turn `absent` into a visible failure
exempts exactly the population (smaller reporting companies) that
`SMALLER_REPORTING_OMITTABLE` targets. Per the contract's "if a check
seems wrong, write it in the report; do not change it," this is reported,
not changed. Effect: the contract's Definition of Done ("each is
`present` or on the failure list") does not hold for Spruce Power either
-- it is `absent` and passing.

Net: of the 9, 3 are cleanly fixed as intended (1-800-Flowers, NewLake
Capital as `present`; BTCS as `present`-but-failing on `item_sizes`,
which the contract's "or on the failure list" clause does cover). The
other 6 (Spruce Power, Mountain Crest V, Magyar Bancorp, OZOP, Powerdyne,
Sativus) do not fit the DoD's two allowed outcomes for the reasons above.

## Failures grouped by check

131 failing filings produce 151 check-failures across 8 checks (a filing
can fail more than one check).

| Check | Count | Example accession numbers |
|---|---:|---|
| `item_sizes` | 46 | 0001683168-26-004650 (Natics Corp.), 0001803498-26-000014 (Blackstone Private Credit Fund), 0001493152-25-015950 (Netsol Technologies Inc) |
| `coverage` | 43 | 0001683168-26-004650 (Natics Corp.), 0000107833-26-000003 (Wisconsin Public Service Corp), 0001193125-26-103276 (Seres Therapeutics, Inc.) |
| `required_items_present` | 35 | 0001193125-26-067467 (Boston Beer Co Inc), 0000092521-26-000003 (Southwestern Public Service Co), 0001091818-26-000053 (Summit Networks Inc.) |
| `no_empty_sections` | 8 | 0001193125-26-126385 (Rein Therapeutics, Inc.), 0001493152-26-020424 (Celularity Inc), 0000004281-26-000012 (Howmet Aerospace Inc.) |
| `xbrl_in_table_coverage` | 6 | 0001193125-26-096851 (Invesco Galaxy Solana ETF), 0001193125-26-083578 (Invesco Galaxy Ethereum ETF), 0001437749-26-001203 (Concrete Pumping Holdings, Inc.) |
| `no_toc_in_sections` | 5 | 0001104659-26-010397 (Liberty Broadband Corp), 0001712184-26-000023 (Liberty Latin America Ltd.), 0001437402-26-000013 (Ardelyx, Inc.) |
| `items_in_order` | 4 | 0000717538-26-000037 (Arrow Financial Corp), 0000827052-26-000012 (Edison International), 0000314489-26-000013 (First Busey Corp /NV/) |
| `table_count_range` | 4 | 0001562762-26-000080 (Cal-Maine Foods Inc), 0000072971-26-000133 (Wells Fargo & Company/MN), 0001562762-26-000104 (Lesaka Technologies Inc) |

`no_empty_sections` dropped from wave 2c's 31 to 8 now that the floor is
20 characters instead of 50 (contract item 2): most of wave 2c's 31 were a
genuine, complete one-sentence answer under the old floor, not an empty
section. The 8 that remain are shorter still (well under 20 characters
after placeholders are excluded) and look like real gaps, not terse
answers.

`item_sizes` and `coverage` both rose slightly in count (35 -> 46, 36 ->
43) because 233 new filings entered the corpus in the top-up; the same
two distribution-tail patterns wave 2c documented -- a genuinely tiny
Item under the size floor for a shell/micro-cap filer, and a genuinely
huge Item 1A for a BDC-type filer over the ceiling -- recur at the same
rate in the larger set. Not a regression from this wave's fixes.

## Parser changes, and the reasoning behind each

Full diff: `git diff 88f24e3 HEAD -- citation_rag/parse/`. Two files
changed, both with the reasoning already written as code comments by the
prior subagent; summarized here.

**`citation_rag/parse/checks.py`**
- `NO_EMPTY_SECTION_MIN`: 50 -> 20 characters (contract item 2). Comment
  cites a concrete wave 2c failure as the reason: "We are not a party to
  any material lawsuits." is 46 characters -- a genuine, terse, complete
  answer, not an empty section. 20 still catches a truly empty
  placeholder-only section. Confirmed above: this took `no_empty_sections`
  failures from 31 to 8.

**`citation_rag/parse/items.py`** -- all four changes below serve the
contract's item 1 (fix the silent Item-1A drop) and were, per the code
comments, each traced back to one of the 9 named filings:
- `_ITEM_WORD = r"[IL]TEMS?"` and `_NUM_RE` widened to accept `I` in place
  of the digit `1` before a lettered sub-item. Reason given: 1-800-Flowers'
  real filing literally spells its heading "ltem 1A." (lowercase L for
  capital I), and NewLake Capital's spells its heading "ITEM IA." (capital
  I for digit 1). `re.I` already folds I/i case, but not L-for-I or I-for-1,
  so both had to be added explicitly. The comment is explicit that this
  is a general typo class (an I/l mix-up any filer could make), not a
  per-filing rule, and the digit-substitution is restricted to the
  `1[A-C]` shape only so it can't widen a two-digit Item number like `10`.
  `normalize_item_num` was updated to fold a leading `I` back to `1` so
  the corrected match still resolves through `ITEM_TITLES`/`ITEM_PARTS`.
- `_has_forward_long_run` (new): when no heading at all is found for an
  omittable Item, this checks whether the raw document text has any run
  longer than 2,000 characters between a mention of that Item and the
  next Item's mention -- the contract's rule (b), used to decide `absent`
  vs. `not_required` when there's no heading to read a disclaimer from.
  Traced to Spruce Power: a forward-looking-statements cross-reference to
  Item 1A sits deep inside Item 1's own body, more than 2,000 characters
  before Item 1B is next mentioned, because Item 1A's real heading is
  missing the word "Item" entirely in that filing's source HTML. The
  docstring is explicit that an earlier, broader version of this idea
  (checking the gap in both directions, for every Item, regardless of
  whether a heading was found) was tried and reverted: it turned the
  corpus pass rate to "single digits" in a live run, because Item 1A's own
  real body is itself tens of thousands of characters, so any filing where
  Item 1B is legitimately `not_required` would see a huge apparent "gap"
  back to Item 1A's own already-correct heading and get wrongly flagged.
- `_has_larger_duplicate_elsewhere` (new): when a heading *is* found and
  its own body reads as a `not_required` disclaimer, this checks whether
  some other raw heading candidate for the same Item number -- one that
  lost to the winner under the existing "ceiling" dedup rule -- has more
  than 2,000 characters of its own following content. If so, the
  winner's own short disclaimer is not trusted, and the Item is left
  `present` at the winner's (short) range rather than being marked
  `not_required`. Traced to BTCS: BTCS's real filing has both a short
  "Not applicable ... see Item 7" stub early on and a genuine, full "ITEM
  1A. RISK FACTORS" section with real content later in the document; the
  existing ceiling rule (which exists to stop an embedded exhibit's own
  Item numbering from hijacking a well-formed heading) correctly keeps
  the earlier stub as Item 1A's position. The comment explains the
  deliberate choice not to reopen that ceiling rule, to avoid
  reintroducing the hijack it prevents; the result (present but too
  short, at 199 characters) is why BTCS lands on the failure list under
  `item_sizes` rather than becoming a clean `present`.
- The two call sites in `detect_items` were changed to route through
  these two helpers instead of unconditionally trusting a `not_required`
  read: an existing heading's disclaimer classification is now gated by
  `_has_larger_duplicate_elsewhere`, and a missing heading's fallback to
  `not_required` is now gated by `_has_forward_long_run`.

**Where the fix's effect was unclear from the code alone**, I verified by
reading the raw filing text directly rather than relying on the JSON
output: see the "9 named filings" section above for the 6 filings (Spruce
Power plus 5 `not_required` outcomes) where the fix's intended effect and
the actual outcome diverge, and why.

## `tests/test_parse_tables.py` (contract item 4)

Already done before this wave's stop: `TestSample`'s table-count assertion
reads `5 <= n_data <= 900` with a comment dated "Wave 2d," matching
`checks.py`'s own `TABLE_COUNT_MIN`/`MAX`. No further stale-expectation
fix was needed; xbrl print-instead-of-fail behavior is unchanged.

## `os.nice(10)` (this subagent's change)

Added at the top of `citation_rag/parse/run.py`'s `main()`:

```python
def main(argv=None):
    os.nice(10)  # protocol: host parse workers run at low priority
    parser = argparse.ArgumentParser(...)
```

Per `.coord/PROTOCOL.md`'s table ("Citation ... parse workers max 4, under
`nice -n 10`"), matching the same rule already applied to the launcher
script in a prior wave's commit.

## Tests

```
uv run pytest tests/test_parse_prose.py tests/test_parse_tables.py tests/test_parse_corpus.py -q
104 passed in 25.18s
```

Run single-process (no `-n`/xdist flag), per the TIMING-window machine
rule. No failures, so no stale-expectation fixes were needed in any of the
three files. `test_parse_corpus.py` parses 20 filings in-process, which is
allowed under the machine rule.

## Fresh 10-filing spot-check list

`random.Random(3)` over the sorted list of 1,202 passing accession
numbers:

| Accession | Company | URL |
|---|---|---|
| 0001193125-26-123930 | Fidelity Solana Fund | https://www.sec.gov/Archives/edgar/data/2063380/000119312526123930/fsol-20251231.htm |
| 0001743881-26-000009 | BridgeBio Pharma, Inc. | https://www.sec.gov/Archives/edgar/data/1743881/000174388126000009/bbio-20251231.htm |
| 0001104659-26-032802 | BGO Industrial Real Estate Income Trust, Inc. | https://www.sec.gov/Archives/edgar/data/1942722/000110465926032802/bgo-20251231x10k.htm |
| 0001477932-26-005668 | UPEXI, INC. | https://www.sec.gov/Archives/edgar/data/1775194/000147793226005668/upxi_10k.htm |
| 0001628280-26-012252 | TANGER INC. | https://www.sec.gov/Archives/edgar/data/899715/000162828026012252/skt-20251231.htm |
| 0001999371-26-004880 | abrdn Platinum ETF Trust | https://www.sec.gov/Archives/edgar/data/1460235/000199937126004880/pplt-10k_123125.htm |
| 0000886206-25-000085 | Franklin Covey Co | https://www.sec.gov/Archives/edgar/data/886206/000088620625000085/fc-20250831x10k.htm |
| 0000048287-26-000084 | HNI Corp | https://www.sec.gov/Archives/edgar/data/48287/000004828726000084/hni-20260103.htm |
| 0001628280-26-011696 | Global Net Lease, Inc. | https://www.sec.gov/Archives/edgar/data/1526113/000162828026011696/gnl-20251231.htm |
| 0001193805-26-001105 | Gulf Resources, Inc. | https://www.sec.gov/Archives/edgar/data/885462/000119380526001105/e665350_10k-gulf.htm |

## Definition of done, checked against the contract

- **Passing filings >= 1,050.** Holds: 1,202.
- **Of the 9 named filings, none is `not_required` for Item 1A; each is
  `present` or on the failure list.** Does not fully hold: 5 of 9
  (Mountain Crest V, Magyar Bancorp, OZOP, Powerdyne, Sativus) are still
  `not_required` -- and reading their raw filing text, that looks like the
  correct call, not a bug, which raises a question about the original
  spot-check finding for those 5 rather than about the fix. A 6th (Spruce
  Power) is `absent`, which the contract expected to appear on the
  failure list, but `check_required_items_present`'s existing
  `smaller_reporting` exemption keeps it off that list. See the "9 named
  filings" section above for the filing-by-filing evidence.
- **`uv run pytest tests/test_parse_prose.py tests/test_parse_tables.py
  tests/test_parse_corpus.py -q` passes.** Holds: 104 passed, 0 failed.
