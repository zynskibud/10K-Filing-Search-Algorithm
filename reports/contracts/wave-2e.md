# Wave 2e contract: heading rule without "Item", absent-Item check, re-parse

LIGHT build now (Sonnet): code and one-core tests. The full re-parse is CPU-BULK and runs after the timing window through `scripts/parse.sh --force`. Owns `citation_rag/parse/items.py`, `citation_rag/parse/checks.py`, `tests/test_parse_prose.py`, `reports/wave-2e.md`.

## Why
Spruce Power (`0001628280-26-022506`) prints its risk factors body heading as `1A. Risk Factors`, with no word "Item". The detector requires "Item", so the Item was `absent`. The required-Items check then exempted it because the filer is a smaller reporting company, so the filing passed with its risk factors missing. Wave 2d's raw reads found that the other 5 "still not_required" filings do carry the omitted-phrase boilerplate under a real heading; those are correct.

## Items
1. **Heading rule.** In `items.py`, accept as an Item heading a block under 200 characters that matches `^\s*(\d{1,2}[A-C]?)\s*[.:\-–—]\s*(<canonical title>)` case-insensitive, where the canonical title is the SEC title for that number (1 Business, 1A Risk Factors, 1B Unresolved Staff Comments, 1C Cybersecurity, 2 Properties, 3 Legal Proceedings, 4 Mine Safety Disclosures, 5 Market for Registrant's Common Equity..., 6 Reserved or Selected Financial Data, 7 Management's Discussion and Analysis..., 7A Quantitative and Qualitative Disclosures About Market Risk, 8 Financial Statements and Supplementary Data, 9 Changes in and Disagreements..., 9A Controls and Procedures, 9B Other Information, 9C Disclosure Regarding Foreign Jurisdictions..., 10 Directors, Executive Officers..., 11 Executive Compensation, 12 Security Ownership..., 13 Certain Relationships..., 14 Principal Accountant Fees..., 15 Exhibits..., 16 Form 10-K Summary). Match on the first 3 words of the canonical title. Table-of-contents skipping and duplicate-heading resolution apply to these headings the same as to "Item N" headings.
2. **Check.** In `checks.py`, `required_items_present` fails on any `absent` required Item, whatever the filer category. `not_required` stays a pass.
3. **Tests.** Synthetic HTML with `1A. Risk Factors` and `7. Management's Discussion and Analysis` headings and no "Item" word; a smaller reporting company with an `absent` Item 1A must fail the check; the 30 sample filings still parse with 22 Items in order. Run tests single-process.
4. **Impact estimate without a re-parse.** Over the current `data/parsed/`, count filings with any required Item `absent` (these will move to the failure list unless the new rule rescues them) and, by scanning the raw text of those filings for the numbered-title pattern, estimate how many the rule rescues. Put both numbers in `reports/wave-2e.md`.
5. **Re-parse command** for later: `scripts/parse.sh --force`, written in the report. Do not run it.

## Definition of done
- `uv run pytest tests/test_parse_prose.py tests/test_parse_corpus.py -q` passes single-process.
- Spruce Power parses with Item 1A `present` when run on that single file (one file is allowed: `uv run python -m citation_rag.parse.filing <path> --meta ...`).
- `reports/wave-2e.md` has the impact estimate.
