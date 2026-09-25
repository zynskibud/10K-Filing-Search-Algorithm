"""Select 1,350 eligible filings for corpus download.

Deduplication rules:
1. One row per accession_no: keep the row with non-empty category,
   or the lowest CIK if both are empty (co-registrant joint filings).
2. One filing per CIK: keep the latest filed if a CIK has two.

Shuffle with random.Random(20260925) and write the first 1,350 rows
to data/raw/selection.jsonl with all metadata fields, plus:
- rank: position in the shuffle
- categories: category string split on '<br>' into a list
- fiscal_year: year extracted from reportDate

Print counts per quarter and per category.
"""

import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime

ELIGIBLE_PATH = Path("data/survey/eligible.jsonl")
SELECTION_PATH = Path("data/raw/selection.jsonl")

SEED = 20260925
SELECTION_N = 1350


def load_eligible():
    rows = []
    with ELIGIBLE_PATH.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def dedupe_by_accession(rows):
    """Dedupe by accession_no: keep row with non-empty category,
    or lowest CIK if both empty."""
    by_accession = defaultdict(list)
    for row in rows:
        accession = row["accession_no"]
        by_accession[accession].append(row)

    deduped = []
    for accession, group in by_accession.items():
        if len(group) == 1:
            deduped.append(group[0])
        else:
            # Multiple rows with same accession_no (co-registrants)
            # Keep row with non-empty category, or lowest CIK if both empty
            with_category = [r for r in group if r.get("category", "").strip()]
            if with_category:
                deduped.append(with_category[0])
            else:
                # Both have empty category, keep lowest CIK
                deduped.append(min(group, key=lambda r: int(r["cik"])))

    return deduped


def dedupe_by_cik(rows):
    """Dedupe by CIK: keep latest filed if CIK has multiple rows."""
    by_cik = defaultdict(list)
    for row in rows:
        cik = row["cik"]
        by_cik[cik].append(row)

    deduped = []
    for cik, group in by_cik.items():
        if len(group) == 1:
            deduped.append(group[0])
        else:
            # Multiple filings from same CIK, keep latest filed
            latest = max(group, key=lambda r: r.get("filingDate", ""))
            deduped.append(latest)

    return deduped


def extract_fiscal_year(report_date_str):
    """Extract year from reportDate string (YYYY-MM-DD format)."""
    if not report_date_str:
        return None
    try:
        return int(report_date_str[:4])
    except (ValueError, IndexError):
        return None


def extract_first_ticker(tickers):
    """Extract first ticker from tickers list, or None."""
    if isinstance(tickers, list) and tickers:
        return tickers[0]
    return None


def main():
    # Load and dedupe
    rows = load_eligible()
    print(f"Initial eligible rows: {len(rows)}")

    rows = dedupe_by_accession(rows)
    print(f"After dedupe by accession_no: {len(rows)}")

    rows = dedupe_by_cik(rows)
    print(f"After dedupe by CIK: {len(rows)}")

    # Shuffle
    rng = random.Random(SEED)
    rng.shuffle(rows)

    # Take first SELECTION_N
    selected = rows[:SELECTION_N]
    print(f"Selected for download: {len(selected)}")

    # Add rank and process
    selection_with_rank = []
    quarter_counts = Counter()
    category_counts = Counter()

    for rank, row in enumerate(selected):
        # Copy all fields from row
        output_row = dict(row)
        output_row["rank"] = rank

        # Split category on '<br>' into list
        category_str = row.get("category", "")
        if category_str:
            categories = [c.strip() for c in category_str.split("<br>")]
            output_row["categories"] = categories
            for cat in categories:
                category_counts[cat] += 1
        else:
            output_row["categories"] = []

        # Extract fiscal year
        report_date = row.get("reportDate")
        fiscal_year = extract_fiscal_year(report_date)
        if fiscal_year:
            output_row["fiscal_year"] = fiscal_year

        # Extract first ticker
        tickers = row.get("tickers")
        first_ticker = extract_first_ticker(tickers)
        if first_ticker:
            output_row["ticker"] = first_ticker

        # Track quarter
        quarter = row.get("quarter")
        if quarter:
            quarter_counts[quarter] += 1

        selection_with_rank.append(output_row)

    # Write selection.jsonl
    SELECTION_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SELECTION_PATH.open("w") as f:
        for row in selection_with_rank:
            f.write(json.dumps(row) + "\n")

    print(f"\nWrote {len(selection_with_rank)} rows to {SELECTION_PATH}")

    # Print counts per quarter
    print("\nCounts per quarter:")
    for quarter in sorted(quarter_counts.keys()):
        print(f"  {quarter}: {quarter_counts[quarter]}")

    # Print counts per category
    print("\nCounts per category:")
    for category in sorted(category_counts.keys()):
        print(f"  {category}: {category_counts[category]}")


if __name__ == "__main__":
    main()
