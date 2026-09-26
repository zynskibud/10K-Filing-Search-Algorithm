"""Wave 2c contract, deliverable 5.

Parses the first 20 filings of `data/raw/corpus.jsonl`, by accession order,
and asserts: the schema shape from `reports/contracts/schemas.md` section 1,
22 Items in the standard SEC order, `tables[]`/placeholder consistency, and
that `checks` is present with the expected shape.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from citation_rag.parse.checks import check_placeholders_consistent
from citation_rag.parse.filing import parse_filing
from citation_rag.parse.items import STANDARD_ITEMS
from citation_rag.parse.run import _build_meta

ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "data" / "raw" / "corpus.jsonl"

TOP_LEVEL_SCHEMA_KEYS = {
    "accession_no",
    "cik",
    "company",
    "ticker",
    "fiscal_year_end",
    "fiscal_year",
    "filed_date",
    "filer_category",
    "sic",
    "source_url",
    "pages",
    "items",
    "tables",
    "checks",
}
ITEM_KEYS = {"item", "part", "title", "status", "page_start", "page_end", "sections"}
SECTION_KEYS = {"id", "seq", "title", "page_start", "page_end", "text", "tables"}
TABLE_KEYS = {
    "id",
    "item",
    "section_id",
    "title",
    "units",
    "headers",
    "rows",
    "text",
    "page_start",
    "page_end",
    "position",
    "xbrl_values_matched",
    "xbrl_values_total",
}
VALID_ITEM_STATUS = {"present", "absent", "not_required", "incorporated_by_reference"}
VALID_PARTS = {"I", "II", "III", "IV"}


def _load_first_20_rows():
    if not CORPUS_PATH.exists():
        return []
    rows = []
    with CORPUS_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: r["accession_no"])
    return rows[:20]


ROWS = _load_first_20_rows()


@pytest.mark.skipif(not ROWS, reason="data/raw/corpus.jsonl not present")
@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["accession_no"])
def test_corpus_filing_schema_shape(row):
    meta = _build_meta(row)
    doc = parse_filing(row["path"], meta)

    # --- top-level schema shape ---
    assert TOP_LEVEL_SCHEMA_KEYS <= set(doc.keys()), doc.keys()
    assert doc["accession_no"] == row["accession_no"]
    assert isinstance(doc["pages"], list)
    assert isinstance(doc["items"], list)
    assert isinstance(doc["tables"], list)

    # --- 22 Items, in the standard SEC order ---
    item_nums = [it["item"] for it in doc["items"]]
    assert item_nums == STANDARD_ITEMS, item_nums
    for it in doc["items"]:
        assert ITEM_KEYS <= set(it.keys())
        assert it["part"] in VALID_PARTS
        assert it["status"] in VALID_ITEM_STATUS
        for s in it["sections"]:
            assert SECTION_KEYS <= set(s.keys())
            assert s["id"].startswith(f"{row['accession_no']}:{it['item']}:")

    # --- tables[] shape ---
    for t in doc["tables"]:
        assert TABLE_KEYS <= set(t.keys())
        assert t["id"].startswith("t")
        assert t["item"] in STANDARD_ITEMS

    # --- placeholders consistent: every table id appears exactly once as a
    # placeholder in some section text, and vice versa ---
    ok, detail = check_placeholders_consistent(doc["items"], doc["tables"])
    assert ok, detail

    # --- checks present, expected shape ---
    checks = doc["checks"]
    assert set(checks.keys()) >= {"passed", "failures", "stats"}
    assert isinstance(checks["passed"], bool)
    assert isinstance(checks["failures"], list)
    assert isinstance(checks["stats"], dict)
    assert checks["passed"] == (len(checks["failures"]) == 0)
