"""Fetch EDGAR filer submissions metadata for every distinct CIK in the
10-K index, then build data/survey/candidates.jsonl: one row per filing
(one row per line of data/survey/index_10k.jsonl) with filer and filing
fields attached.

Resumable: each CIK's raw submissions JSON is cached at
data/survey/cache/submissions/{cik10}.json. A CIK already cached is not
re-fetched. The candidates.jsonl build step is cheap (no network) and is
always regenerated in full from the cache plus the index, so it never
needs its own resume logic.
"""

import json
import time
from pathlib import Path

import httpx

HEADERS = {
    "User-Agent": "Matthew Pisinski mattpisinski@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}

INDEX_PATH = Path("data/survey/index_10k.jsonl")
CACHE_DIR = Path("data/survey/cache/submissions")
FAILED_LOG = Path("data/survey/cache/submissions_failed.jsonl")
CANDIDATES_PATH = Path("data/survey/candidates.jsonl")

SLEEP_S = 0.12
MAX_RETRIES = 6


def fetch_with_retry(client: httpx.Client, url: str) -> httpx.Response | None:
    backoff = 1.0
    resp = None
    for attempt in range(MAX_RETRIES):
        resp = client.get(url, headers=HEADERS, timeout=60)
        if resp.status_code == 403:
            raise RuntimeError(f"SEC returned 403 for {url}. Stopping per instructions.")
        if resp.status_code == 404:
            return None
        if resp.status_code in (429, 503):
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)
            continue
        resp.raise_for_status()
        return resp
    print(f"WARN: exhausted retries for {url}, last status {resp.status_code if resp else 'n/a'}")
    return None


def load_index_rows():
    rows = []
    with INDEX_PATH.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def fetch_all_submissions(ciks: list[str]):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    already = {p.stem for p in CACHE_DIR.glob("*.json")}
    todo = [c for c in ciks if f"CIK{int(c):010d}" not in already]
    print(f"{len(ciks)} distinct CIKs, {len(already)} already cached, {len(todo)} to fetch")

    with httpx.Client() as client:
        for i, cik in enumerate(todo):
            cik10 = f"{int(cik):010d}"
            url = f"https://data.sec.gov/submissions/CIK{cik10}.json"
            resp = fetch_with_retry(client, url)
            time.sleep(SLEEP_S)
            if resp is None:
                with FAILED_LOG.open("a") as f:
                    f.write(json.dumps({"cik": cik, "url": url}) + "\n")
                continue
            (CACHE_DIR / f"CIK{cik10}.json").write_text(resp.text)
            if (i + 1) % 200 == 0:
                print(f"  fetched {i + 1}/{len(todo)}")
    print("Fetch phase done.")


def find_filing(submissions: dict, accession_no: str):
    """Look in filings.recent first, then in the paginated older files if needed."""
    recent = submissions["filings"]["recent"]
    if accession_no in recent["accessionNumber"]:
        idx = recent["accessionNumber"].index(accession_no)
        return {k: recent[k][idx] for k in recent}

    # Fall back to paginated older submission pages (rare for our 2025+ filings).
    for page in submissions["filings"].get("files", []):
        page_url = f"https://data.sec.gov/submissions/{page['name']}"
        cache_path = CACHE_DIR / page["name"]
        if cache_path.exists():
            page_data = json.loads(cache_path.read_text())
        else:
            with httpx.Client() as client:
                resp = fetch_with_retry(client, page_url)
                time.sleep(SLEEP_S)
            if resp is None:
                continue
            cache_path.write_text(resp.text)
            page_data = resp.json()
        if accession_no in page_data.get("accessionNumber", []):
            idx = page_data["accessionNumber"].index(accession_no)
            return {k: page_data[k][idx] for k in page_data}
    return None


def build_candidates(index_rows):
    n_ok = 0
    n_missing_cache = 0
    n_missing_filing = 0
    with CANDIDATES_PATH.open("w") as out:
        for row in index_rows:
            cik = row["cik"]
            cik10 = f"{int(cik):010d}"
            cache_path = CACHE_DIR / f"CIK{cik10}.json"
            if not cache_path.exists():
                n_missing_cache += 1
                continue
            submissions = json.loads(cache_path.read_text())

            filing = find_filing(submissions, row["accession_no"])
            if filing is None:
                n_missing_filing += 1
                continue

            accession_nodash = row["accession_no"].replace("-", "")
            primary_document = filing.get("primaryDocument", "")
            primary_doc_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{accession_nodash}/{primary_document}"
            )

            candidate = {
                "accession_no": row["accession_no"],
                "cik": cik10,
                "company_index": row["company"],
                "filed_date_index": row["filed_date"],
                "quarter": row["quarter"],
                "name": submissions.get("name"),
                "sic": submissions.get("sic"),
                "sicDescription": submissions.get("sicDescription"),
                "category": submissions.get("category"),
                "entityType": submissions.get("entityType"),
                "stateOfIncorporation": submissions.get("stateOfIncorporation"),
                "tickers": submissions.get("tickers"),
                "exchanges": submissions.get("exchanges"),
                "fiscalYearEnd": submissions.get("fiscalYearEnd"),
                "form": filing.get("form"),
                "filingDate": filing.get("filingDate"),
                "reportDate": filing.get("reportDate"),
                "isXBRL": filing.get("isXBRL"),
                "isInlineXBRL": filing.get("isInlineXBRL"),
                "primaryDocument": primary_document,
                "primaryDocDescription": filing.get("primaryDocDescription"),
                "primary_doc_url": primary_doc_url,
            }
            out.write(json.dumps(candidate) + "\n")
            n_ok += 1

    print(
        f"candidates.jsonl: {n_ok} rows written, "
        f"{n_missing_cache} skipped (no cache), {n_missing_filing} skipped (accession not found)"
    )


def main():
    index_rows = load_index_rows()
    ciks = sorted({row["cik"] for row in index_rows}, key=lambda c: int(c))
    fetch_all_submissions(ciks)
    build_candidates(index_rows)


if __name__ == "__main__":
    main()
