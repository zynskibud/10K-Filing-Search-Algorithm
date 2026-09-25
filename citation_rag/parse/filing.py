"""parse_filing(path, meta) -> dict (wave 2a contract, deliverables 5 + 6).

This is the wave-2a join: clean -> split pages -> extract/stub tables ->
detect Items -> split sections -> run the prose checks. Wave 2c later
extends this file with the real table-check wiring and the full-corpus
runner; the table extraction call is kept to one function (`join_tables`)
so that swap is contained.
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
import sys
from pathlib import Path

from .blocks import unwrap_table_to_text
from .clean import clean_document
from .items import STANDARD_ITEMS, detect_items
from .pages import assign_pages
from .sections import split_sections

REQUIRED_ITEMS = ["1", "1A", "2", "3", "5", "7", "7A", "8", "9A", "15"]
ITEM_SIZE_RANGES = {
    "1A": (3000, 400000),
    "7": (5000, 800000),
    "8": (5000, 3000000),
}
TOC_LINE_RE = re.compile(r"^item\s+\d", re.I | re.M)


def _resolve_table_extractor():
    try:
        from citation_rag.parse.tables import extract_tables  # type: ignore
    except Exception:
        return None
    return extract_tables


def join_tables(tree, page_for):
    """The single join point for wave 2b's table extractor.

    Returns (tables, table_ids): `tables` is the schema `tables[]` list
    (always [] until wave 2b's extractor is wired in and wave 2c assigns
    section_id/position); `table_ids` maps id(<table> element) -> table id
    for the tables that got a real id, so sections.py can place a
    "[Table: id]" placeholder instead of unwrapping that table into prose.
    """
    extract_tables = _resolve_table_extractor()
    if extract_tables is None:
        return [], {}
    try:
        n_params = len(inspect.signature(extract_tables).parameters)
    except (TypeError, ValueError):
        n_params = 1
    try:
        result = extract_tables(tree, page_for) if n_params >= 2 else extract_tables(tree)
    except Exception:
        return [], {}
    tables = result if isinstance(result, list) else []
    table_ids = {}
    for t in tables:
        if not isinstance(t, dict):
            continue
        el = t.get("element")
        tid = t.get("id")
        if el is not None and tid:
            table_ids[id(el)] = tid
    return tables, table_ids


def _label_series_value(label: str) -> tuple[str, int] | None:
    from .pages import classify_token

    series, value = classify_token(label)
    if series is None or value is None:
        return None
    return series, value


def _check_required_items_present(items, smaller_reporting) -> tuple[bool, str]:
    bad = []
    for it in items:
        if it["item"] not in REQUIRED_ITEMS:
            continue
        if it["status"] == "absent" and not smaller_reporting:
            bad.append(it["item"])
    if bad:
        return False, f"required Items absent: {', '.join(bad)}"
    return True, ""


def _check_items_in_order(items, integrated_report) -> tuple[bool, str]:
    if integrated_report:
        return True, "exempt (integrated report)"
    last = None
    for it in items:
        if it["status"] != "present" or it["page_start"] is None:
            continue
        if last is not None and it["page_start"] < last:
            return False, f"Item {it['item']} starts at page {it['page_start']}, before a prior present Item"
        last = it["page_start"]
    return True, ""


def _check_item_sizes(items) -> tuple[bool, str]:
    bad = []
    for it in items:
        if it["item"] not in ITEM_SIZE_RANGES or it["status"] != "present":
            continue
        lo, hi = ITEM_SIZE_RANGES[it["item"]]
        size = sum(len(s["text"]) for s in it["sections"])
        if not (lo <= size <= hi):
            bad.append(f"Item {it['item']}: {size} chars (expected {lo}-{hi})")
    if bad:
        return False, "; ".join(bad)
    return True, ""


def _check_coverage(items, body_chars) -> tuple[bool, str, float, int]:
    prose_chars = sum(len(s["text"]) for it in items for s in it["sections"])
    coverage = (prose_chars / body_chars) if body_chars else 1.0
    ok = 0.95 <= coverage <= 1.05
    detail = f"prose={prose_chars} body={body_chars} coverage={coverage:.3f}"
    return ok, detail, coverage, prose_chars


def _check_no_toc_in_sections(items) -> tuple[bool, str]:
    bad = []
    for it in items:
        for s in it["sections"]:
            if len(TOC_LINE_RE.findall(s["text"])) >= 5:
                bad.append(s["id"])
    if bad:
        return False, f"sections with TOC-like text: {', '.join(bad)}"
    return True, ""


def _check_page_labels_monotonic(pages) -> tuple[bool, str]:
    last = {}
    for p in pages:
        label = p.get("label")
        if not label:
            continue
        parsed = _label_series_value(label)
        if parsed is None:
            return False, f"page {p['index']}: unparseable label {label!r}"
        series, value = parsed
        if series in last and value != last[series] + 1:
            return False, f"page {p['index']}: label {label!r} breaks the {series} sequence"
        last[series] = value
    return True, ""


def run_checks(items, pages_result, content_blocks, smaller_reporting, integrated_report):
    failures = []

    ok, detail = _check_required_items_present(items, smaller_reporting)
    if not ok:
        failures.append(f"required_items_present: {detail}")

    ok, detail = _check_items_in_order(items, integrated_report)
    if not ok:
        failures.append(f"items_in_order: {detail}")

    ok, detail = _check_item_sizes(items)
    if not ok:
        failures.append(f"item_sizes: {detail}")

    body_chars = 0
    for b in content_blocks:
        if b.kind == "text":
            body_chars += len(b.text)
        else:
            body_chars += len(unwrap_table_to_text(b.element))
    ok, detail, coverage, prose_chars = _check_coverage(items, body_chars)
    if not ok:
        failures.append(f"coverage: {detail}")

    ok, detail = _check_no_toc_in_sections(items)
    if not ok:
        failures.append(f"no_toc_in_sections: {detail}")

    ok, detail = _check_page_labels_monotonic(pages_result.pages)
    if not ok:
        failures.append(f"page_labels_monotonic: {detail}")

    n_labeled = sum(1 for p in pages_result.pages if p.get("label"))
    stats = {
        "prose_chars": prose_chars,
        "body_chars": body_chars,
        "coverage": round(coverage, 4),
        "n_pages": pages_result.n_pages,
        "page_markers": pages_result.n_markers,
        "page_label_coverage": round(n_labeled / pages_result.n_pages, 4) if pages_result.n_pages else 0.0,
    }
    return {"passed": not failures, "failures": failures, "stats": stats}


def parse_filing(path, meta: dict) -> dict:
    tree, ix_records = clean_document(path)
    company = meta.get("company")
    pages_result = assign_pages(tree, company=company)

    tables, table_ids = join_tables(tree, pages_result.page_for)

    items, content_blocks, item_stats = detect_items(
        tree, pages_result.page_for, pages_result.pages, meta.get("filer_category")
    )

    accession_no = meta["accession_no"]
    for item in items:
        item["sections"] = split_sections(
            accession_no, item["item"], item, content_blocks, table_ids
        )
        for k in ("start_index", "end_index", "forced_sections", "extra_index_range"):
            item.pop(k, None)

    categories = meta.get("filer_category") or ""
    smaller_reporting = "smaller reporting" in categories.lower()

    checks = run_checks(
        items, pages_result, content_blocks, smaller_reporting, item_stats["integrated_report"]
    )
    checks["stats"].update(item_stats)
    checks["stats"]["data_tables"] = len(tables)
    checks["stats"]["xbrl_nonfraction_total"] = len(ix_records)

    doc = {
        "accession_no": accession_no,
        "cik": meta.get("cik"),
        "company": meta.get("company"),
        "ticker": meta.get("ticker"),
        "fiscal_year_end": meta.get("fiscal_year_end"),
        "fiscal_year": meta.get("fiscal_year"),
        "filed_date": meta.get("filed_date"),
        "filer_category": meta.get("filer_category"),
        "sic": meta.get("sic"),
        "source_url": meta.get("source_url"),
        "pages": pages_result.pages,
        "items": items,
        "tables": tables,
        "checks": checks,
    }
    return doc


def main(argv=None):
    parser = argparse.ArgumentParser(description="Parse one 10-K filing into normalized JSON.")
    parser.add_argument("path", help="Path to the filing .htm or .htm.gz")
    parser.add_argument("--meta", required=True, help="JSON string with filing metadata")
    parser.add_argument("--out", help="Output path; defaults to stdout")
    args = parser.parse_args(argv)

    meta = json.loads(args.meta)
    doc = parse_filing(args.path, meta)
    text = json.dumps(doc, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")


if __name__ == "__main__":
    main()
