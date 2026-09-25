"""Download EDGAR quarterly form indexes and keep 10-K rows.

The file is nominally fixed-width (Form Type, Company Name, CIK,
Date Filed, File Name) but a long Company Name overflows its column
and shifts everything after it, so fixed-column slicing silently
misparses those rows (seen for CIK 1353226, a long trust name). We
parse with a regex anchored on the two fields that have a fixed
shape (CIK is all digits, Date Filed is YYYY-MM-DD) and let Company
Name be whatever sits between Form Type and CIK.

We keep rows whose Form Type, after stripping, is exactly "10-K"
(not 10-K/A, not 10-KT, not 10-K405).

Writes data/survey/index_10k.jsonl, one JSON object per row:
    {"company": ..., "cik": ..., "filed_date": ..., "filename": ...,
     "accession_no": ..., "quarter": "2025QTR3"}

Resumable: if the output file already has rows for a quarter, that
quarter is skipped on a re-run (looked up by the "quarter" field).
"""

import json
import re
import time
from pathlib import Path

import httpx

ROW_RE = re.compile(
    r"^(?P<form>\S+)\s+(?P<company>.*?)\s+(?P<cik>\d+)\s+"
    r"(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<filename>\S+)\s*$"
)

HEADERS = {
    "User-Agent": "Matthew Pisinski mattpisinski@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}

QUARTERS = [
    (2025, 3),
    (2025, 4),
    (2026, 1),
    (2026, 2),
    (2026, 3),
]

OUT_PATH = Path("data/survey/index_10k.jsonl")
SLEEP_S = 0.12
MAX_RETRIES = 6


def fetch_with_retry(client: httpx.Client, url: str) -> httpx.Response:
    backoff = 1.0
    for attempt in range(MAX_RETRIES):
        resp = client.get(url, headers=HEADERS, timeout=60)
        if resp.status_code == 403:
            raise RuntimeError(f"SEC returned 403 for {url}. Stopping per instructions.")
        if resp.status_code in (429, 503):
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError(f"Exhausted retries for {url}, last status {resp.status_code}")


def parse_form_idx(text: str, quarter_label: str):
    lines = text.splitlines()
    # Find the dashed separator line; data starts right after it.
    start = None
    for i, line in enumerate(lines):
        if line.startswith("---"):
            start = i + 1
            break
    if start is None:
        raise RuntimeError(f"Could not find header separator in form.idx for {quarter_label}")

    rows = []
    n_unparsed = 0
    for line in lines[start:]:
        if not line.strip():
            continue
        m = ROW_RE.match(line)
        if not m:
            n_unparsed += 1
            continue
        if m.group("form") != "10-K":
            continue
        company = m.group("company").strip()
        cik = m.group("cik").strip()
        filed_date = m.group("date").strip()
        filename = m.group("filename").strip()
        accession_no = Path(filename).stem  # e.g. 0001084869-25-000017
        rows.append(
            {
                "company": company,
                "cik": cik,
                "filed_date": filed_date,
                "filename": filename,
                "accession_no": accession_no,
                "quarter": quarter_label,
            }
        )
    if n_unparsed:
        print(f"  WARNING: {n_unparsed} lines in {quarter_label} did not match the row pattern")
    return rows


def already_done_quarters(path: Path) -> set[str]:
    done = set()
    if not path.exists():
        return done
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            done.add(row.get("quarter"))
    return done


def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    done = already_done_quarters(OUT_PATH)
    print(f"Quarters already present: {sorted(done)}")

    counts = {}
    with httpx.Client() as client, OUT_PATH.open("a") as out:
        for year, qtr in QUARTERS:
            quarter_label = f"{year}QTR{qtr}"
            if quarter_label in done:
                print(f"Skip {quarter_label} (already fetched)")
                continue
            url = f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{qtr}/form.idx"
            print(f"Fetching {url}")
            resp = fetch_with_retry(client, url)
            time.sleep(SLEEP_S)
            rows = parse_form_idx(resp.text, quarter_label)
            for row in rows:
                out.write(json.dumps(row) + "\n")
            out.flush()
            counts[quarter_label] = len(rows)
            print(f"{quarter_label}: {len(rows)} 10-K rows")

    print("Done.", counts)


if __name__ == "__main__":
    main()
