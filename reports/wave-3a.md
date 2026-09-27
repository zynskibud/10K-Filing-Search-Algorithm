# Wave 3a report: golden set validation

Owner: Sonnet subagent (validator). Implements the "Validator task" section
of `reports/contracts/wave-3a.md`, plus the orchestrator's extensions for
this wave (per-evidence `section_ids`/`table_ids` on `general` and
`multi_part`, the `parts_in_one_section` flag, and the notes-parsing
clean-up). Owns `citation_rag/evals/golden.py`, `tests/test_evals.py`,
`evals/golden/` (drafts rewritten in place, plus `dev.jsonl`, `test.jsonl`,
`rejected.jsonl`), this report. No commits. No Docker, no model runs, no
multi-process job -- file reads and single-process Python throughout.
`df -h /` showed 37-54 GB free throughout, well above the 15 GB floor.

**The test set is sealed as of this report.** `evals/golden/test.jsonl` was
written once, by the split in step 4 below, and must not be looked at again
to tune anything upstream of it (writers, retrieval, prompts, or this
validator's own rules).

## 0. Code: per-evidence `section_ids`/`table_ids` for `general` and `multi_part`

`citation_rag/evals/golden.py`'s `GoldenCase` gained three fields:
`section_ids: list[str | None] | None`, `table_ids: list[str | None] |
None`, `parts_in_one_section: bool | None` (schemas.md section 2's update).

`validate()`:
- `general` now checks each evidence string against its **own**
  `section_ids[i]`/`table_ids[i]`, in filing `accession_nos[i]`, at
  `page[i]` -- the old code checked every evidence string against one
  shared `section_id`/`table_id`, which was wrong for any general case
  whose three filings don't happen to reuse the same section number.
  `section_ids` is now required and parallel to `evidence`; a missing or
  mismatched-length list is its own problem.
- `multi_part` now prefers per-index `section_ids`/`table_ids`/
  `accession_nos` when present, and falls back to the shared singular
  `section_id`/`table_id`/`accession_no` field when they are absent -- this
  keeps the older single-section shape (one `section_id`, one `accession_no`,
  evidence list) working unchanged, which is what the fixture's `f0005`
  case and 8 of the real draft cases (see step 0b) use. A new check flags
  `parts_in_one_section: true` whose resolved `section_ids` are not in fact
  all equal.
- Single-evidence types (`fact_lookup`, `number_from_table`, `paraphrased`,
  `exact_term`) are untouched.

Six new tests in `tests/test_evals.py` cover: a clean 3-filing `general`
case with parallel `section_ids`; a `general` case where one evidence
string is corrupted and the reported problem names the right filing; a
`multi_part` case with two different sections (both a prose section and a
table); `parts_in_one_section: true` on a matching case; the same flag
rejected when the sections actually differ; and the pre-existing
`f0005`-style fallback (shared `section_id`, int `page`, no `section_ids`)
still validating clean. All 36 previously-existing tests still pass
unchanged. Full suite: **42 passed in 0.13s** (`uv run pytest
tests/test_evals.py -q`).

## 0b. Draft clean-up: `notes`-encoded ids promoted to `section_ids`/`table_ids`

Before running the validator, every `general`/`multi_part` draft row needed
real `section_ids`/`table_ids` to check against. Writer 0 already wrote
them correctly. The other three writers did not -- and not only writers 1
and 2, as flagged going in; **writer 3 needed the same fix**:

- **Writer 1**, `general` (`w1-028..031`): per-evidence ids were in `notes`
  as `ev[i] Company section ACC:ITEM:SEQ pPAGE`. Parsed with a regex keyed
  on the `ev[i]` index, one match per evidence string, and written into
  `section_ids` (`table_ids` all null -- these are prose-only). Writer 1's
  four `multi_part` cases (`w1-037..040`) had one shared `section_id` and no
  per-part notes at all: these are genuinely single-section multi-part
  questions, so `section_ids` was set to that one id repeated, and
  `parts_in_one_section: true` was added.
- **Writer 2**: four `multi_part` cases (`w2-021,024,030,033`) are the same
  single-shared-section shape as writer 1's -- same treatment
  (`parts_in_one_section: true`). Its four `general` cases (`w2-037..040`)
  had **no** id information anywhere, not even in `notes` -- unlike every
  other writer's general/multi-part rows. For these, `section_ids`/
  `table_ids` were derived by searching each cited filing's parsed JSON for
  a section or table whose text contains the evidence string exactly,
  preferring the one whose page range matches the case's `page[i]`. All 12
  evidence strings across the 4 cases resolved to exactly one match each
  (no ambiguity, nothing left unresolved).
- **Writer 3**, `general` (`w3-028..031`) and `multi_part` (`w3-037..040`):
  both had per-evidence ids in `notes`, in two different sentence formats
  (`"ACC:ITEM:SEQ (Company, page N)"` for general, `"Evidence a: section
  ACC:ITEM:SEQ (page N, Item X)"` for multi-part). Parsed with two more
  regexes; all pages recovered from `notes` matched the pages already on
  the row, which cross-checks the parse.

This is a one-time data fix, run once against the current `data/parsed/`
and the drafts as handed off; it is not part of `golden.py`'s importable
API. All 8 rewritten `evals/golden/drafts/writer-{1,2,3}.jsonl` rows now
carry proper `section_ids`/`table_ids`, and `writer-1.jsonl`'s and
`writer-2.jsonl`'s single-section `multi_part` rows (8 total, 4 each) carry
`parts_in_one_section: true`.

## 1. `validate()` against each draft

Ran `citation_rag.evals.golden.validate` on all four rewritten drafts
against `data/parsed/`. Of 160 cases, **one problem**:

- `w1-030` (general, Adicet Bio / IN8BIO / Triller Group reverse-split
  question): its second evidence string used curly quotes
  (`"reverse stock split at a ratio of one-for-thirty (the "Reverse Stock
  Split")"`) but the current parsed text (re-parsed after the writers
  drafted) uses straight quotes at that spot. Confirmed by direct
  inspection: the un-curled string **is** an exact substring of the cited
  section; the curled one is not. Rejected with reason **"evidence changed
  after re-parse."**

## 2. Duplicate check

Normalized (lowercased, stop-word-stripped) word-set Jaccard similarity
>= 0.6 against every earlier-processed question, and an exact-evidence-string
check against every earlier case's evidence, processed in writer order
(w0 001-040, w1, w2, w3), skipping cases already rejected in step 1.
**Zero duplicates found** -- no two questions cleared the 0.6 threshold, and
no evidence string repeats (confirmed again at the end: 161 unique evidence
strings across the final 145 accepted cases, zero collisions).

## 3. Quality read

Read all 159 surviving cases against their evidence: question, evidence,
answer, and (for `paraphrased`/`exact_term`/`unanswerable`) the type rule.
**14 more rejected:**

**`exact_term` (6 of 20 remaining after step 1) -- rare term is the answer,
not in the question.** The rule requires the *question* to carry the rare
anchor (product name, place, legal case, quoted defined term, or Item
reference); in these six the question is generic ("under what trade name
does...", "what former name was...") and the distinctive string only shows
up in the evidence/answer, which makes them un-retrievable by the exact
term and functionally indistinguishable from `fact_lookup`:
`w0-023` (Rapiscan), `w0-024` (the Bitcoin Conference), `w0-026` (Monocle
Acquisition Corporation), `w2-025` (ticker MVXM), `w2-034` (SEEDO CORP.),
`w2-035` (Penny Stock Reform Act of 1990). The other 14 exact_term cases do
carry the anchor in the question (product codes like `ADI-270`/`INB-100`,
a docket number, a named subsidiary, a named facility, a program name) and
were kept.

**`general` (8 of 16, all from writers 0 and 3) -- ambiguous or
unsupported.** Writer 0's four cases (`w0-028..031`) ask about "these three
companies" / "these filers" with **no identifying description anywhere in
the question** -- there is no way, from the question text alone, to know
which three of 1,333 filings are meant, which breaks retrieval and makes
the question not self-contained. Writer 3's four cases (`w3-028..031`) have
the same "in this set" / "filers that..." problem, and three of them
additionally claim something is "most commonly named," "commonly flag," or
"commonly say" across filers based on only 3 example filings out of the
corpus -- a claim the evidence cannot support and that likely does not hold
if checked against the full corpus. All 8 rejected. By contrast, writers 1
and 2's general cases identify each filing by a distinguishing descriptor
("an insurance-technology platform provider," "a hepatitis and RSV-focused
biopharmaceutical company") rather than a bare pronoun, so they are
retrievable and were kept (all 7 remaining general cases, after step 1's
one rejection, are writer 1's and writer 2's).

**Everything else checked out.** All 32 `fact_lookup`, all 32
`number_from_table`, all 24 `paraphrased` (word overlap with evidence
checked; the highest was 0.20, well below a paraphrase-failure level), all
16 `multi_part`, and all 20 `unanswerable` cases were kept. For
`unanswerable`, each case's claimed-absent fact was spot-checked against
the full filing text (not just the cited section) with targeted keyword/
regex searches -- for example, Kilroy's and Ares's "S&P"/"Moody's" mentions
are all stock-performance-index references, not credit ratings, and TELA
Bio's filing never mentions institutional ownership percentages -- so the
fact really is absent in every case checked, not merely absent from the
cited section.

**Rejection reasons, with counts (15 total):**

| reason | count |
|---|---|
| exact_term without the term (rare term is the answer, not in the question) | 6 |
| general: question is ambiguous (no identifying description of the filings) | 5 |
| general: claim not supported by only 3 example filings | 3 |
| evidence changed after re-parse | 1 |

Full one-line reasons, per case, are in `evals/golden/rejected.jsonl`
alongside the full original row.

## 4. Split

`random.Random(7)`, stratified by type: for each type (in the order
`fact_lookup, number_from_table, paraphrased, exact_term, general,
unanswerable, multi_part`), the type's case ids (sorted for a stable
starting order) were shuffled with the shared `Random(7)` instance, and
`round(n/3)` went to test. Renumbered `g0001...` (dev block first, then
test block, in split order).

| type | total | dev | test |
|---|---|---|---|
| fact_lookup | 32 | 21 | 11 |
| number_from_table | 32 | 21 | 11 |
| paraphrased | 24 | 16 | 8 |
| exact_term | 14 | 9 | 5 |
| general | 7 | 5 | 2 |
| unanswerable | 20 | 13 | 7 |
| multi_part | 16 | 11 | 5 |
| **total** | **145** | **96** | **49** |

Both meet the definition of done (`dev >= 95`, `test >= 45`), and every
type is present in both splits.

**`multi_part` single-section count:** of the 16 accepted `multi_part`
cases, **8 have `parts_in_one_section: true`** -- exactly the 8 expected
from writers 1 and 2 (4 each: `w1-037..040`, `w2-021,024,030,033`).
Writer 0's and writer 3's 8 `multi_part` cases all draw their parts from
genuinely different sections, as the original contract intended, and carry
no flag.

## 5. Item and filer-category distribution (dev + test, 145 cases)

Item distribution (a case can touch more than one Item, e.g. `multi_part`
and `general` cases spanning sections in different Items; counted once per
Item touched):

| Item | count |
|---|---|
| 1 | 61 |
| 7 | 34 |
| 1A | 9 |
| 2 | 8 |
| 8 | 8 |
| 5 | 7 |
| 3 | 6 |
| 11 | 5 |
| 1C | 5 |
| 10 | 2 |
| 12 | 1 |
| 13 | 1 |
| 7A | 1 |
| 9A | 1 |

Filer-category distribution (counted once per filing cited; a `general`
case cites 3+ filings):

| filer category | count |
|---|---|
| Large accelerated filer | 64 |
| Non-accelerated filer / Smaller reporting company | 42 |
| Accelerated filer | 17 |
| Emerging growth company (no size tier stated) | 17 |
| Non-accelerated filer / Smaller reporting company / Emerging growth company | 9 |
| Accelerated filer / Smaller reporting company / Emerging growth company | 5 |
| Accelerated filer / Smaller reporting company | 3 |
| (unspecified) | 2 |

(The parsed corpus stores these as a single `<br>`-joined string per
filing; counts above are per distinct combination, unmodified.)

## Human spot-check: 15 dev cases, `random.Random(11)`

| id | question | answer | evidence | company |
|---|---|---|---|---|
| g0058 | What drove the rise in the company's bottom-line profit for the latest fiscal year versus the one before it? | Higher operating income, lower financing costs, and lower taxes in 2025. | Net income attributable to Ingredion for 2025 increased to $729 million compared to $647 million for 2024. The increase in net income was primarily due to higher operating income, lower financing cost[s]... | Ingredion Inc |
| g0072 | In the human-capital sections of their most recent 10-Ks, how many full-time employees did an insurance-technology platform provider, a wireless-transport equipment maker, and a digital-manufacturing company each report? | Insurance-technology platform: approximately 354; wireless-transport equipment maker: 898 full-time (900 total); digital-manufacturing company: 2,280 full-time | As of December 31, 2025, we employed approximately 354 full-time employees... \| As of July 3, 2026, we had 900 employees, of whom 898 were full-time... | Exzeo Group, Inc.; AVIAT NETWORKS, INC.; Proto Labs Inc |
| g0060 | What is the primary purpose of WNEB's subsidiary WFD Securities, Inc.? | Holding qualified securities. | WFD Securities, Inc. ("WFD"). WFD is a Massachusetts chartered security corporation, for the primary purpose of holding qualified securities. | Western New England Bancorp, Inc. |
| g0066 | What entity, together with its subsidiary Walden University, LLC, made up the "Walden Group" that Laureate sold under the Walden Purchase Agreement? | Walden e-Learning, LLC | the Company sold to the Walden Purchaser all of the issued and outstanding equity interest in Walden e-Learning, LLC... | LAUREATE EDUCATION, INC. |
| g0076 | What credit rating do Moody's and S&P Global Ratings currently assign to Ares Management's corporate debt? | The filing does not state this. | (none -- unanswerable) | Ares Management Corp |
| g0025 | What was Aerkomm's net cash used in operating activities for the year ended December 31, 2025? | $(5,635,927). | Net cash used in operating activities \| Years Ended December 31, 2025: $(5,635,927) \| ... | Aerkomm Inc. |
| g0024 | What was Theriva Biologics' total research and development expense for the year ended December 31, 2025? | $8,604 thousand. | Total research and development \| December 31, 2025: $8,604 \| ... | Theriva Biologics, Inc. |
| g0061 | From which company does Telomir Pharmaceuticals in-license its lead candidate Telomir-1? | MIRALOGX. | Within our present and future pipeline of treatments, Telomir-1 is in-licensed from MIRALOGX. | Telomir Pharmaceuticals, Inc. |
| g0081 | What was the total compensation paid to WNEB's chief executive officer for fiscal 2025? | The filing does not state this. | (none -- unanswerable) | Western New England Bancorp, Inc. |
| g0079 | During fiscal 2025, what was the highest and lowest reported trading price of MoveIX's common stock? | The filing does not state this. | (none -- unanswerable) | MOVEIX INC. |
| g0013 | By how much did mean body weight fall from baseline after 13 weeks of weekly VK2735 dosing, at most? | Up to 14.7% | Patients receiving weekly doses of VK2735 demonstrated statistically significant reductions in mean body weight after 13 weeks, ranging up to 14.7% from baseline. | Viking Therapeutics, Inc. |
| g0039 | What were Origin Materials' total revenues for the year ended December 31, 2025? | $18,922 thousand (about $18.9 million). | Total revenues \| Year Ended December 31, 2025: 18,922 \| ... | Origin Materials, Inc. |
| g0019 | On what date did Nakamoto Inc. complete its acquisition of BTC Inc and UTXO? | February 20, 2026 | On February 20, 2026, we acquired BTC Inc, the leading provider of Bitcoin-related media and events, and UTXO, an investment firm focused on private and public Bitcoin companies. | Nakamoto Inc. |
| g0012 | How large is Proto Labs' owned facility in Rosemount, Minnesota? | Approximately 130,000 square feet | We own a facility in Rosemount, Minnesota that encompasses approximately 130,000 square feet of manufacturing and office space. | Proto Labs Inc |
| g0069 | Among an offshore drilling contractor, a hepatitis and RSV-focused biopharmaceutical company, and a card-issuing payments platform, which one lists its common stock on the New York Stock Exchange rather than Nasdaq? | The offshore drilling contractor (Transocean), which trades on the NYSE under the symbol RIG; the other two trade on Nasdaq. | Our shares are listed on the New York Stock Exchange under the ticker symbol "RIG." \| Our common stock has been listed on The Nasdaq Global Select Market under the symbol "ENTA"... | Transocean Ltd.; ENANTA PHARMACEUTICALS INC; Marqeta, Inc. |

## Validation

```
$ uv run python -m citation_rag.evals.golden validate evals/golden/dev.jsonl --parsed-dir data/parsed
OK: 96 cases, 0 problems

$ uv run python -m citation_rag.evals.golden validate evals/golden/test.jsonl --parsed-dir data/parsed
OK: 49 cases, 0 problems
```

## Definition of done

- `dev.jsonl` has 96 cases (>= 95), `test.jsonl` has 49 (>= 45); every type
  present in both. Met.
- `validate` reports 0 problems for both splits. Met.
- No two cases share an evidence string (checked across dev + test
  combined: 161 unique evidence strings, zero collisions). Met.
