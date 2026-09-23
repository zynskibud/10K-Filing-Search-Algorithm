"""Download 10-K filings from SEC EDGAR.

Usage: uv run python -m ingest.download [--years 10] [--tickers AAPL,MSFT]

Writes raw HTML to data/raw/<TICKER>/<FY>_<ACCESSION>.htm and one JSON line
per filing to data/raw/manifest.jsonl. Files that exist are skipped, so the
script can be re-run after a failure.
"""

import argparse
import json
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

from ingest.companies import TICKERS

RAW_DIR = Path("data/raw")
MANIFEST = RAW_DIR / "manifest.jsonl"
MIN_INTERVAL = 0.12  # SEC limit is 10 requests per second


class Edgar:
    def __init__(self, user_agent: str):
        self.client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=60,
            follow_redirects=True,
        )
        self._last = 0.0

    def get(self, url: str) -> httpx.Response:
        for attempt in range(5):
            wait = MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            resp = self.client.get(url)
            if resp.status_code in (429, 503):
                time.sleep(2**attempt)
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp


def ticker_to_cik(edgar: Edgar) -> dict[str, tuple[str, str]]:
    data = edgar.get("https://www.sec.gov/files/company_tickers.json").json()
    return {row["ticker"]: (str(row["cik_str"]).zfill(10), row["title"]) for row in data.values()}


def ten_k_filings(edgar: Edgar, cik: str) -> list[dict]:
    """Return original 10-K filings (no amendments), newest first."""
    sub = edgar.get(f"https://data.sec.gov/submissions/CIK{cik}.json").json()
    blocks = [sub["filings"]["recent"]]
    for f in sub["filings"].get("files", []):
        blocks.append(edgar.get(f"https://data.sec.gov/submissions/{f['name']}").json())

    out = []
    for b in blocks:
        for i, form in enumerate(b["form"]):
            if form != "10-K" or not b["primaryDocument"][i]:
                continue
            out.append({
                "accession_no": b["accessionNumber"][i],
                "filing_date": b["filingDate"][i],
                "report_date": b["reportDate"][i],
                "primary_document": b["primaryDocument"][i],
            })
    out.sort(key=lambda f: f["filing_date"], reverse=True)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--tickers", type=str, default="")
    args = parser.parse_args()

    load_dotenv()
    user_agent = os.environ["SEC_USER_AGENT"]
    tickers = args.tickers.split(",") if args.tickers else TICKERS

    edgar = Edgar(user_agent)
    ciks = ticker_to_cik(edgar)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    done = set()
    if MANIFEST.exists():
        done = {json.loads(line)["accession_no"] for line in MANIFEST.open()}

    with MANIFEST.open("a") as manifest:
        for ticker in tickers:
            lookup = ticker.replace(".", "-")
            if lookup not in ciks:
                print(f"{ticker}: no CIK found, skipped")
                continue
            cik, company = ciks[lookup]
            filings = ten_k_filings(edgar, cik)[: args.years]
            new = 0
            for f in filings:
                if f["accession_no"] in done:
                    continue
                fiscal_year = int((f["report_date"] or f["filing_date"])[:4])
                acc = f["accession_no"].replace("-", "")
                url = (
                    f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/"
                    f"{f['primary_document']}"
                )
                path = RAW_DIR / ticker / f"{fiscal_year}_{f['accession_no']}.htm"
                path.parent.mkdir(exist_ok=True)
                try:
                    path.write_bytes(edgar.get(url).content)
                except httpx.HTTPError as e:
                    print(f"{ticker} {f['accession_no']}: download failed: {e}")
                    continue
                record = {
                    "ticker": ticker,
                    "cik": cik,
                    "company": company,
                    "fiscal_year": fiscal_year,
                    **f,
                    "source_url": url,
                    "path": str(path),
                }
                manifest.write(json.dumps(record) + "\n")
                manifest.flush()
                new += 1
            print(f"{ticker}: {len(filings)} filings, {new} new")


if __name__ == "__main__":
    main()
