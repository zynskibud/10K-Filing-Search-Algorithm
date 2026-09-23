"""Measure the structure of every downloaded 10-K. Informs the parser and chunker.

Usage: uv run python -m ingest.survey

Writes one JSON line per filing to data/survey.jsonl and prints a summary.
"""

import json
import re
import statistics
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

import lxml.html

MANIFEST = Path("data/raw/manifest.jsonl")
OUT = Path("data/survey.jsonl")

PAGE_BREAK = re.compile(
    r"<[^>]+page-break-(?:before|after)\s*:\s*always[^>]*>", re.IGNORECASE
)
GENERATOR = re.compile(r"<!--\s*(.{0,80}?(?:Workiva|Toppan|Donnelley|DFIN|Broadridge|"
                       r"GoFiler|EDGAR Online|Certent|Merrill|Novaworks)[^>]{0,40})-->", re.I)
# A page number at the end of the last line: "12", "F-3", "A-1", "ii", "Apple Inc. | 2025 Form 10-K | 12"
PAGE_NUM = re.compile(r"(?:^|[\s|])((?:[A-Z]{1,2}-)?\d{1,3}|[ivxlc]{1,6})$")
ITEM = re.compile(r"^\s*item\s*(\d{1,2}[a-c]?)\s*[\.:\-–—]?", re.IGNORECASE)
NUMERIC = re.compile(r"\d[\d,]*\.?\d*")


BLOCK_TAGS = {"div", "p", "tr", "br", "li", "table", "h1", "h2", "h3", "h4", "h5", "h6"}


def lines_of(el) -> list[str]:
    # text_content() joins block elements with no line break, so add one after each block.
    for b in el.iter(*BLOCK_TAGS):
        b.tail = "\n" + (b.tail or "")
    text = el.text_content().replace("\xa0", " ")
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def survey(record: dict) -> dict:
    raw = Path(record["path"]).read_text(encoding="utf-8", errors="replace")
    gen = GENERATOR.search(raw[:3000])
    raw_nohidden = re.sub(r"<\?xml[^>]*\?>|<ix:header>.*?</ix:header>", "", raw, flags=re.S | re.I)
    segments = PAGE_BREAK.split(raw_nohidden)

    page_numbers, footers, page_chars = [], [], []
    item_hits: list[tuple[str, int]] = []
    tables = numeric_tables = table_chars = 0
    total_chars = 0
    for page_idx, seg in enumerate(segments):
        if not seg.strip():
            continue
        try:
            root = lxml.html.fromstring(seg)
        except Exception:
            continue
        lines = lines_of(root)
        chars = sum(len(ln) for ln in lines)
        total_chars += chars
        page_chars.append(chars)
        if lines:
            last = lines[-1]
            footers.append(last[-60:])
            m = PAGE_NUM.search(last)
            page_numbers.append(m.group(1) if m else None)
        for ln in lines:
            if len(ln) < 200:
                m = ITEM.match(ln)
                if m:
                    item_hits.append((m.group(1).upper(), page_idx))
        for t in root.iter("table"):
            tables += 1
            txt = t.text_content()
            table_chars += len(txt.strip())
            if len(NUMERIC.findall(txt)) >= 6 and len(t.findall(".//tr")) >= 3:
                numeric_tables += 1

    items = Counter(i for i, _ in item_hits)
    return {
        "ticker": record["ticker"],
        "fiscal_year": record["fiscal_year"],
        "path": record["path"],
        "bytes": len(raw),
        "generator": gen.group(1).strip() if gen else None,
        "inline_xbrl": "<ix:" in raw[:200000] or "<ix:" in raw,
        "page_breaks": len(segments) - 1,
        "pages": len(page_chars),
        "pages_with_number": sum(p is not None for p in page_numbers),
        "page_number_sample": page_numbers[:3] + page_numbers[len(page_numbers) // 2:][:3],
        "footer_sample": footers[len(footers) // 2:][:2],
        "median_page_chars": statistics.median(page_chars) if page_chars else 0,
        "total_chars": total_chars,
        "items_found": sorted(items),
        "item_counts": dict(items),
        "tables": tables,
        "numeric_tables": numeric_tables,
        "table_char_share": round(table_chars / total_chars, 3) if total_chars else 0,
    }


def main() -> None:
    records = [json.loads(line) for line in MANIFEST.open()]
    with Pool() as pool:
        results = pool.map(survey, records, chunksize=4)
    with OUT.open("w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"surveyed {len(results)} filings -> {OUT}")


if __name__ == "__main__":
    main()
