# Wave 1a contract: EDGAR survey and filer rule

Owner: orchestrator session citation-rag-41. Implementer: Sonnet subagent.
Project root: /Users/matthewpisinski/work/work/side-projects/AI_Engineering/Citation_Rag
Plan reference: plan.html, Plan tab, section 2 (Corpus) and section 3 (parser cases).

## Rules
- LIGHT work only: HTTP metadata requests and a download of 30 sample filings (about 150 MB). No model runs. No Docker.
- Wave 0 runs in parallel and owns `pyproject.toml`, `.venv`, `.env`, `.gitignore`, `docker-compose.yml`. Do NOT edit those files. Run your scripts with `uv run --with httpx,lxml python <script>` so you need no project venv. Put scripts under `citation_rag/corpus/` (they become project code) and data under `data/survey/`.
- SEC rules: header `User-Agent: Matthew Pisinski mattpisinski@gmail.com`, at most 10 requests per second (sleep 0.12 s between requests), retry on 429/503 with backoff. Use `Accept-Encoding: gzip, deflate`.
- Never write outside the project folder. Stop if free disk < 15 GB.
- Do not commit. The orchestrator commits after review.

## Goal
Produce the list of candidate 10-K filings for the corpus, a concrete filer rule, and a survey of 30 sample filings that tells the parser what it will face.

## Steps
1. **Index.** Download the EDGAR quarterly form indexes `https://www.sec.gov/Archives/edgar/full-index/{YYYY}/QTR{n}/form.idx` for 2025 QTR3, 2025 QTR4, 2026 QTR1, 2026 QTR2, 2026 QTR3. Keep rows with form type exactly `10-K` (not 10-K/A, not 10-KT, not 10-K405). Fields: company name, CIK, filed date, filename path. Write `data/survey/index_10k.jsonl`.
2. **Filer metadata.** For each distinct CIK, fetch `https://data.sec.gov/submissions/CIK{cik:010d}.json` and record: `name`, `sic`, `sicDescription`, `category` (filer category string), `entityType`, `stateOfIncorporation`, `tickers`, `exchanges`, `fiscalYearEnd`. Also, from the `filings.recent` block, find the matching accession and its `primaryDocument`, `reportDate`, `isXBRL`, `isInlineXBRL`. Build the primary document URL: `https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_no_dashes}/{primaryDocument}`. Write `data/survey/candidates.jsonl`, one row per filing with all fields. This is about 7,000 requests at 10/s, about 12 minutes. Make it resumable (skip CIKs already fetched).
3. **Filer rule.** Draft the rule from plan section 2 as concrete, testable conditions. Include (at least) these exclusions, and justify each with counts from the data:
   - Asset-backed securities issuers (SIC 6189, or entityType/name patterns such as "Trust 20", "Receivables", "Funding LLC" with SIC 6189).
   - Non-operating trusts and funds (SIC 6221 commodity pools, SIC 6798 REITs are NOT excluded; grantor trusts, royalty trusts SIC 6792 ARE excluded, ETF/commodity trusts excluded).
   - Filings with `isInlineXBRL` false.
   - Filings whose primary document is not `.htm`/`.html`.
   - Shell companies / blank-check SPACs: decide from `sicDescription` = "Blank Checks" (SIC 6770). Exclude.
   Smaller reporting companies are INCLUDED (the plan says so). Record the filer `category` so the parser can expect omitted Items.
   Implement the rule as `citation_rag/corpus/filer_rule.py` with a function `is_eligible(row) -> tuple[bool, str]` returning the reason when not eligible. Apply it and write `data/survey/eligible.jsonl` and a table of exclusion reasons with counts.
4. **Sample.** Pick 30 eligible filings at random with `random.seed(20260925)`. Download the primary document of each into `data/survey/sample/{cik}_{accession}.htm`. For each, measure and record in `data/survey/sample_survey.jsonl`:
   - bytes; `<ix:` tag present; generator comment (Workiva, DFIN, Toppan, other) from the first 3000 bytes.
   - number of page breaks: count of elements whose style contains `page-break-before:always` or `page-break-after:always` (case-insensitive, allow spaces), plus `<hr` tags with such style.
   - printed page numbers: for the last 3 non-empty text lines and the first 2 of each page, does a line match `^\s*(\d{1,3}|[A-Z]-\d{1,3}|[ivx]{1,5})\s*$` or end with such a token. Report the share of pages with a number found.
   - Item headings: lines under 150 chars matching `^\s*ITEM\s+(\d{1,2}[A-C]?)\b` case-insensitive; report which Items were found and how many times each (2 means TOC + body).
   - `<table>` count, and count of tables with >= 3 rows and >= 2 columns and >= 6 numeric cells.
   - `ix:nonFraction` count.
   - filer `category` from step 2.
5. **Report** `reports/wave-1a.md` with:
   - counts: filings per quarter; distinct CIKs; eligible count; exclusion table with reasons and counts.
   - the concrete filer rule as written in code, and any judgment calls you made.
   - the sample survey summary: distribution of page breaks, page-number coverage, Items found, tables, generators, categories. Name the specific cases the parser must handle that you saw (with the sample filename as evidence).
   - open questions for the human, if any (for example a filer type you were unsure about). Do not decide them; list them.

## Definition of done
- `data/survey/index_10k.jsonl`, `candidates.jsonl`, `eligible.jsonl`, `sample_survey.jsonl`, and `sample/` exist.
- `citation_rag/corpus/filer_rule.py` exists with `is_eligible`.
- Eligible count >= 3,000 (so a random 1,100 is easy). If it is lower, say why.
- `reports/wave-1a.md` written.
