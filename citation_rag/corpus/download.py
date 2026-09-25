r"""Download primary documents for selected 10-K filings.

For each selected filing in rank order:
1. Download the primary document from SEC
2. Write it gzip-compressed to data/raw/{cik}/{accession_no}.htm.gz
3. Count page markers (CSS breaks and <hr> elements)
4. Append one line to data/raw/manifest.jsonl with all fields
5. Skip filings already in the manifest (resumable)

After all batches:
- Write data/raw/corpus.jsonl with rows having has_page_markers=true
- Keep only first 1,100 rows in rank order
- Report how many were excluded for lacking page markers

Usage:
  uv run python -m citation_rag.corpus.download [--batch N] [--batch-size B]
"""

import json
import time
import gzip
import re
import hashlib
import shutil
import subprocess
from pathlib import Path
from collections import Counter
from datetime import datetime

import httpx
from lxml import html as lxml_html
from citation_rag.settings import Settings

SELECTION_PATH = Path("data/raw/selection.jsonl")
MANIFEST_PATH = Path("data/raw/manifest.jsonl")
CORPUS_PATH = Path("data/raw/corpus.jsonl")

SLEEP_S = 0.12
MAX_RETRIES = 6
DEFAULT_BATCH_SIZE = 450
MIN_DISK_GB = 15

# Page marker regexes from contract
PAGE_BREAK_BEFORE_AFTER = re.compile(
    r"page-break-(?:before|after)\s*:\s*always", re.IGNORECASE
)
BREAK_BEFORE_AFTER_PAGE = re.compile(
    r"break-(?:before|after)\s*:\s*page", re.IGNORECASE
)


def get_settings():
    """Load settings including SEC_USER_AGENT."""
    try:
        settings = Settings()
        return settings
    except Exception as e:
        print(f"Warning: could not load settings: {e}")
        return None


def fetch_with_retry(client: httpx.Client, url: str, user_agent: str) -> httpx.Response | None:
    """Fetch URL with retries on 429/503, stop on 403."""
    headers = {
        "User-Agent": user_agent,
        "Accept-Encoding": "gzip, deflate",
    }
    backoff = 1.0
    resp = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.get(url, headers=headers, timeout=60)
        except httpx.RequestError as e:
            print(f"Network error fetching {url}: {e}")
            return None

        if resp.status_code == 403:
            print(f"ERROR: SEC returned 403 for {url}. Stopping per SEC etiquette.")
            raise RuntimeError(f"SEC 403 error for {url}")
        if resp.status_code == 404:
            print(f"WARN: SEC returned 404 for {url}")
            return None
        if resp.status_code in (429, 503):
            print(f"Got {resp.status_code} for {url}, retrying with backoff")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)
            continue

        if resp.status_code >= 400:
            print(f"WARN: got status {resp.status_code} for {url}")
            return None

        return resp

    print(f"WARN: exhausted retries for {url}")
    return None


def load_selection():
    """Load selected rows in rank order."""
    rows = []
    with SELECTION_PATH.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    # Sort by rank to ensure order
    rows.sort(key=lambda r: r.get("rank", 0))
    return rows


def get_manifest_accessions():
    """Load set of accession_no already in manifest."""
    done = set()
    if not MANIFEST_PATH.exists():
        return done
    with MANIFEST_PATH.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    row = json.loads(line)
                    done.add(row.get("accession_no"))
                except json.JSONDecodeError:
                    pass
    return done


def count_page_markers(content: bytes) -> int:
    r"""Count page markers using both regexes from contract.

    Count elements whose style matches:
    - page-break-(?:before|after)\s*:\s*always (case-insensitive)
    - break-(?:before|after)\s*:\s*page (case-insensitive)
    Also count <hr> elements with either style.
    """
    try:
        tree = lxml_html.fromstring(content)
    except Exception as e:
        print(f"WARN: could not parse HTML: {e}")
        return 0

    count = 0

    # Count all elements with matching styles
    for el in tree.iter():
        style = el.get("style") or ""
        if PAGE_BREAK_BEFORE_AFTER.search(style):
            count += 1
        elif BREAK_BEFORE_AFTER_PAGE.search(style):
            count += 1

    # Count <hr> elements with either style
    for hr in tree.findall(".//hr"):
        style = hr.get("style") or ""
        if PAGE_BREAK_BEFORE_AFTER.search(style) or BREAK_BEFORE_AFTER_PAGE.search(style):
            count += 1

    return count


def extract_fiscal_year(report_date_str):
    """Extract year from reportDate string (YYYY-MM-DD format)."""
    if not report_date_str:
        return None
    try:
        return int(report_date_str[:4])
    except (ValueError, IndexError):
        return None


def get_first_ticker(row):
    """Get first ticker from tickers list or ticker field."""
    if "ticker" in row:
        return row["ticker"]
    tickers = row.get("tickers")
    if isinstance(tickers, list) and tickers:
        return tickers[0]
    return None


def download_and_process(row: dict, user_agent: str, client: httpx.Client) -> dict | None:
    """Download filing, compress, and create manifest row."""
    accession_no = row["accession_no"]
    cik = row["cik"]
    url = row["primary_doc_url"]

    # Fetch with retry
    resp = fetch_with_retry(client, url, user_agent)
    if resp is None:
        return None

    content = resp.content

    # Create directory
    cik_dir = Path("data/raw") / cik
    cik_dir.mkdir(parents=True, exist_ok=True)

    # Write gzip-compressed file
    gz_path = cik_dir / f"{accession_no}.htm.gz"
    with gzip.open(gz_path, "wb", compresslevel=6) as f:
        f.write(content)

    # Compute SHA256
    sha256_hash = hashlib.sha256(content).hexdigest()

    # Count page markers
    page_markers = count_page_markers(content)
    has_page_markers = page_markers >= 15

    # Build manifest row
    manifest_row = {
        "cik": cik,
        "company": row.get("name"),
        "ticker": get_first_ticker(row),
        "accession_no": accession_no,
        "filed_date": row.get("filingDate"),
        "report_date": row.get("reportDate"),
        "fiscal_year": extract_fiscal_year(row.get("reportDate")),
        "filer_category": row.get("category"),
        "sic": row.get("sic"),
        "source_url": url,
        "path": str(gz_path),
        "bytes": len(content),
        "bytes_gz": gz_path.stat().st_size,
        "sha256": sha256_hash,
        "page_markers": page_markers,
        "has_page_markers": has_page_markers,
        "rank": row.get("rank"),
    }

    return manifest_row


def check_disk_space():
    """Check free disk space. Return GB free."""
    result = subprocess.run(
        ["df", "-h", "/"],
        capture_output=True,
        text=True
    )
    lines = result.stdout.strip().split("\n")
    if len(lines) >= 2:
        # Parse the second line (actual filesystem)
        parts = lines[1].split()
        if len(parts) >= 4:
            # parts[3] is "Available" column
            avail_str = parts[3]
            # Remove trailing 'G', 'T', etc. and convert
            try:
                if avail_str.endswith("G"):
                    return float(avail_str[:-1])
                elif avail_str.endswith("T"):
                    return float(avail_str[:-1]) * 1024
                elif avail_str.endswith("M"):
                    return float(avail_str[:-1]) / 1024
            except ValueError:
                pass
    return None


def finalize_corpus():
    """After all batches: write corpus.jsonl with rows having has_page_markers=true,
    keeping only first 1,100 in rank order."""
    if not MANIFEST_PATH.exists():
        print("ERROR: manifest.jsonl does not exist")
        return

    # Load manifest rows
    manifest_rows = []
    with MANIFEST_PATH.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    row = json.loads(line)
                    manifest_rows.append(row)
                except json.JSONDecodeError:
                    pass

    print(f"Manifest has {len(manifest_rows)} rows")

    # Filter to rows with page markers
    with_markers = [r for r in manifest_rows if r.get("has_page_markers")]
    without_markers = len(manifest_rows) - len(with_markers)

    print(f"  With page markers: {len(with_markers)}")
    print(f"  Without page markers: {without_markers}")

    # Sort by rank to ensure order
    with_markers.sort(key=lambda r: r.get("rank", 0))

    # Take first 1,100
    corpus = with_markers[:1100]
    print(f"  Corpus (first 1,100): {len(corpus)}")

    # Write corpus.jsonl
    with CORPUS_PATH.open("w") as f:
        for row in corpus:
            f.write(json.dumps(row) + "\n")

    print(f"\nWrote {len(corpus)} rows to {CORPUS_PATH}")

    # Summary
    if without_markers > 0:
        print(f"Note: {without_markers} filings in manifest lacked >= 15 page markers and were excluded from corpus")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, default=1, help="Batch number (1-based)")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help="Filings per batch")
    parser.add_argument("--finalize", action="store_true", help="Finalize corpus after all batches")
    args = parser.parse_args()

    # Handle finalize mode
    if args.finalize:
        finalize_corpus()
        return

    # Load settings
    settings = get_settings()
    user_agent = settings.sec_user_agent if settings else "Matthew Pisinski mattpisinski@gmail.com"

    # Load selection and manifest
    selected = load_selection()
    done_accessions = get_manifest_accessions()

    print(f"Selected filings: {len(selected)}")
    print(f"Already in manifest: {len(done_accessions)}")

    # Filter to filings not yet downloaded
    todo = [r for r in selected if r["accession_no"] not in done_accessions]
    print(f"Remaining to download: {len(todo)}")

    # Determine batch range
    batch_num = args.batch
    batch_size = args.batch_size
    start_idx = (batch_num - 1) * batch_size
    end_idx = start_idx + batch_size

    # Find which ranks are in this batch by looking at selected filings
    batch_selected = selected[start_idx:end_idx]
    batch_accessions = {r["accession_no"] for r in batch_selected}
    batch_rows = [r for r in todo if r["accession_no"] in batch_accessions]

    print(f"\nBatch {batch_num}: downloading {len(batch_rows)} filings "
          f"(ranks {start_idx}-{end_idx - 1}, {len(batch_selected)} in batch, "
          f"{len(batch_rows)} not yet downloaded)")

    # Check disk before batch
    disk_gb = check_disk_space()
    if disk_gb is not None:
        print(f"Free disk before batch: {disk_gb:.1f} GB")
        if disk_gb < MIN_DISK_GB:
            print(f"ERROR: free disk {disk_gb:.1f} GB < {MIN_DISK_GB} GB minimum")
            return

    # Download batch
    start_time = time.time()
    failed = []
    success_count = 0

    with httpx.Client() as client:
        for i, row in enumerate(batch_rows):
            accession_no = row["accession_no"]
            print(f"[{i+1}/{len(batch_rows)}] Downloading {accession_no}...", end=" ")

            try:
                manifest_row = download_and_process(row, user_agent, client)
                if manifest_row:
                    # Append to manifest
                    with MANIFEST_PATH.open("a") as f:
                        f.write(json.dumps(manifest_row) + "\n")
                    success_count += 1
                    page_markers = manifest_row.get("page_markers", 0)
                    has_markers = "✓" if manifest_row.get("has_page_markers") else "✗"
                    print(f"OK ({page_markers} markers {has_markers})")
                else:
                    failed.append((accession_no, "download_failed"))
                    print("FAILED")
            except RuntimeError as e:
                # 403 error - stop immediately
                print(f"STOPPED: {e}")
                raise
            except Exception as e:
                failed.append((accession_no, str(e)))
                print(f"ERROR: {e}")

            # SEC rate limit: max 10 requests per second = 0.12s min between requests
            time.sleep(SLEEP_S)

    elapsed = time.time() - start_time

    # Check disk after batch
    disk_gb_after = check_disk_space()
    if disk_gb_after is not None:
        print(f"Free disk after batch: {disk_gb_after:.1f} GB")

    print(f"\nBatch {batch_num} complete in {elapsed:.1f}s")
    print(f"  Downloaded: {success_count}")
    print(f"  Failed: {len(failed)}")
    if failed:
        print("  Failures:")
        for accession, reason in failed[:10]:  # Show first 10
            print(f"    {accession}: {reason}")
        if len(failed) > 10:
            print(f"    ... and {len(failed) - 10} more")


if __name__ == "__main__":
    main()
