"""Apply the filer rule to candidates.jsonl, then survey 30 random
eligible filings.

Steps:
1. Read data/survey/candidates.jsonl, apply filer_rule.is_eligible to
   each row, write data/survey/eligible.jsonl (eligible rows only) and
   print an exclusion-reason count table.
2. Pick 30 eligible filings at random with random.seed(20260925).
   Download each primary document into
   data/survey/sample/{cik}_{accession}.htm (resumable: skip a file
   already on disk).
3. For each sample file, measure the fields the contract asks for and
   write one row per filing to data/survey/sample_survey.jsonl
   (resumable: skip an accession already in that file).
"""

import json
import random
import re
import time
from collections import Counter
from pathlib import Path

import httpx
from lxml import html as lxml_html

from citation_rag.corpus.filer_rule import is_eligible

HEADERS = {
    "User-Agent": "Matthew Pisinski mattpisinski@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}

CANDIDATES_PATH = Path("data/survey/candidates.jsonl")
ELIGIBLE_PATH = Path("data/survey/eligible.jsonl")
SAMPLE_DIR = Path("data/survey/sample")
SAMPLE_SURVEY_PATH = Path("data/survey/sample_survey.jsonl")

SLEEP_S = 0.12
MAX_RETRIES = 6
SAMPLE_N = 30
SEED = 20260925

PAGE_BREAK_RE = re.compile(r"page-break-(?:before|after)\s*:\s*always", re.I)
PAGE_NUM_FULL_RE = re.compile(r"^\s*(\d{1,3}|[A-Z]-\d{1,3}|[ivx]{1,5})\s*$")
PAGE_NUM_END_RE = re.compile(r"(\d{1,3}|[A-Z]-\d{1,3}|[ivx]{1,5})\s*$")
ITEM_HEADING_RE = re.compile(r"^\s*ITEM\s+(\d{1,2}[A-C]?)\b", re.I)
NUMERIC_CELL_RE = re.compile(r"^[\s\$\(\)\-\+\d,.%]*\d[\s\$\(\)\-\+\d,.%]*$")

GENERATOR_KEYWORDS = [
    ("workiva", "Workiva"),
    ("dfin", "DFIN"),
    ("donnelley", "DFIN"),
    ("toppan", "Toppan"),
    ("merrill", "Toppan"),  # Toppan Merrill
]


def fetch_with_retry(client: httpx.Client, url: str) -> httpx.Response:
    backoff = 1.0
    resp = None
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
    raise RuntimeError(f"Exhausted retries for {url}, last status {resp.status_code if resp else 'n/a'}")


def build_eligible():
    reasons = Counter()
    n_total = 0
    with CANDIDATES_PATH.open() as f, ELIGIBLE_PATH.open("w") as out:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            n_total += 1
            ok, reason = is_eligible(row)
            reasons[reason] += 1
            if ok:
                out.write(json.dumps(row) + "\n")
    print(f"Total candidates: {n_total}")
    print("Exclusion / eligibility counts:")
    for reason, count in reasons.most_common():
        print(f"  {reason}: {count}")
    return reasons


def load_eligible():
    rows = []
    with ELIGIBLE_PATH.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def sample_path_for(row: dict) -> Path:
    return SAMPLE_DIR / f"{row['cik']}_{row['accession_no']}.htm"


def download_sample_files(rows: list[dict]):
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client() as client:
        for row in rows:
            path = sample_path_for(row)
            if path.exists() and path.stat().st_size > 0:
                continue
            url = row["primary_doc_url"]
            print(f"Downloading {url}")
            resp = fetch_with_retry(client, url)
            time.sleep(SLEEP_S)
            path.write_bytes(resp.content)


def detect_generator(head_bytes: bytes) -> str:
    text = head_bytes.decode("utf-8", errors="ignore").lower()
    for keyword, label in GENERATOR_KEYWORDS:
        if keyword in text:
            return label
    return "other"


def split_into_pages(tree, all_text_elements):
    """Return a list of page segments, each a list of stripped, non-empty
    text lines, split at elements whose style requests a page break.
    """
    pages = []
    current_lines: list[str] = []
    for el in tree.iter():
        style = el.get("style") or ""
        is_break = bool(PAGE_BREAK_RE.search(style))
        # Collect this element's own direct text/tail as line material.
        for piece in (el.text, el.tail):
            if piece and piece.strip():
                current_lines.append(piece.strip())
        if is_break and current_lines:
            pages.append(current_lines)
            current_lines = []
    if current_lines:
        pages.append(current_lines)
    return pages


def count_page_breaks(tree) -> int:
    count = 0
    for el in tree.iter():
        style = el.get("style") or ""
        if PAGE_BREAK_RE.search(style):
            count += 1
    return count


def page_number_coverage(pages: list[list[str]]) -> tuple[int, int]:
    found = 0
    total = 0
    for lines in pages:
        if not lines:
            continue
        total += 1
        candidates = lines[:2] + lines[-3:]
        hit = any(
            PAGE_NUM_FULL_RE.match(line) or PAGE_NUM_END_RE.search(line)
            for line in candidates
        )
        if hit:
            found += 1
    return found, total


def item_headings(pages: list[list[str]]) -> Counter:
    counts = Counter()
    for lines in pages:
        for line in lines:
            if len(line) >= 150:
                continue
            m = ITEM_HEADING_RE.match(line)
            if m:
                counts[m.group(1).upper()] += 1
    return counts


def table_stats(tree):
    tables = tree.findall(".//table")
    n_tables = len(tables)
    n_data_tables = 0
    for table in tables:
        rows = table.findall(".//tr")
        if len(rows) < 3:
            continue
        max_cols = 0
        numeric_cells = 0
        for row in rows:
            cells = row.findall("./td") + row.findall("./th")
            max_cols = max(max_cols, len(cells))
            for cell in cells:
                text = "".join(cell.itertext()).strip()
                if text and NUMERIC_CELL_RE.match(text):
                    numeric_cells += 1
        if max_cols >= 2 and numeric_cells >= 6:
            n_data_tables += 1
    return n_tables, n_data_tables


def count_ix_nonfraction(raw_text: str) -> int:
    return len(re.findall(r"<ix:nonfraction\b", raw_text, re.I))


def already_surveyed() -> set[str]:
    done = set()
    if not SAMPLE_SURVEY_PATH.exists():
        return done
    with SAMPLE_SURVEY_PATH.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                done.add(row.get("accession_no"))
    return done


def survey_sample(rows: list[dict]):
    done = already_surveyed()
    with SAMPLE_SURVEY_PATH.open("a") as out:
        for row in rows:
            if row["accession_no"] in done:
                continue
            path = sample_path_for(row)
            raw_bytes = path.read_bytes()
            raw_text = raw_bytes.decode("utf-8", errors="ignore")

            has_ix = "<ix:" in raw_text.lower()
            generator = detect_generator(raw_bytes[:3000])

            tree = lxml_html.fromstring(raw_bytes)
            n_page_breaks = count_page_breaks(tree)
            pages = split_into_pages(tree, None)
            n_pages_found, n_pages_total = page_number_coverage(pages)
            item_counts = item_headings(pages)
            n_tables, n_data_tables = table_stats(tree)
            n_ix_nonfraction = count_ix_nonfraction(raw_text)

            record = {
                "accession_no": row["accession_no"],
                "cik": row["cik"],
                "company": row.get("name"),
                "category": row.get("category"),
                "sic": row.get("sic"),
                "sicDescription": row.get("sicDescription"),
                "primary_doc_url": row["primary_doc_url"],
                "bytes": len(raw_bytes),
                "has_ix_tag": has_ix,
                "generator": generator,
                "n_page_breaks": n_page_breaks,
                "n_pages_segmented": n_pages_total,
                "n_pages_with_number_found": n_pages_found,
                "page_number_coverage": (
                    round(n_pages_found / n_pages_total, 3) if n_pages_total else None
                ),
                "item_headings_found": dict(item_counts),
                "n_tables_total": n_tables,
                "n_tables_data": n_data_tables,
                "n_ix_nonfraction": n_ix_nonfraction,
            }
            out.write(json.dumps(record) + "\n")
            out.flush()
            print(f"Surveyed {row['accession_no']}: {record['bytes']} bytes, "
                  f"{n_tables} tables ({n_data_tables} data), "
                  f"{n_page_breaks} page breaks, generator={generator}")


def main():
    build_eligible()
    eligible_rows = load_eligible()
    print(f"Eligible: {len(eligible_rows)}")

    rng = random.Random(SEED)
    sample_rows = rng.sample(eligible_rows, min(SAMPLE_N, len(eligible_rows)))

    download_sample_files(sample_rows)
    survey_sample(sample_rows)


if __name__ == "__main__":
    main()
