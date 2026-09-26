"""parse_filing(path, meta) -> dict (wave 2a/2c contract).

The join: clean -> split pages -> extract tables (2b) -> assign table ids in
document order -> detect Items (2a) -> split sections, placing a
`[Table: id]` placeholder for every table wave 2b classified as a data
table (a layout table is unwrapped into prose instead) -> assign each
placed table its `item`/`section_id`/`position` -> run all checks (2c,
`checks.py`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .checks import run_checks
from .clean import clean_document
from .items import STANDARD_ITEMS, detect_items
from .pages import assign_pages
from .sections import split_sections
from .tables import extract_tables


def _resolve_table_extractor():
    try:
        from citation_rag.parse.tables import extract_tables as fn  # type: ignore
    except Exception:
        return None
    return fn


def join_tables(tree, page_for) -> dict:
    """Call wave 2b's `extract_tables(tree, page_for)` and assign table ids.

    Returns the dict `extract_tables` returns (tables/layout_tables/
    xbrl_nonfraction_total/xbrl_nonfraction_matched), with every data table
    dict in `tables` given an `id` = `t{index:03d}` in document order
    (before placement), per the contract's deliverable 0. Degrades to an
    empty result if the extractor cannot be imported or raises, so a bug in
    tables.py does not take down the whole parse.
    """
    extract_tables_fn = _resolve_table_extractor()
    if extract_tables_fn is None:
        return {"tables": [], "layout_tables": [], "xbrl_nonfraction_total": 0, "xbrl_nonfraction_matched": 0}
    try:
        result = extract_tables_fn(tree, page_for)
    except Exception:
        return {"tables": [], "layout_tables": [], "xbrl_nonfraction_total": 0, "xbrl_nonfraction_matched": 0}
    if not isinstance(result, dict):
        return {"tables": [], "layout_tables": [], "xbrl_nonfraction_total": 0, "xbrl_nonfraction_matched": 0}
    tables = result.get("tables") or []
    for i, t in enumerate(tables, start=1):
        t["id"] = f"t{i:03d}"
    result["tables"] = tables
    result.setdefault("layout_tables", [])
    result.setdefault("xbrl_nonfraction_total", 0)
    result.setdefault("xbrl_nonfraction_matched", 0)
    return result


def _table_ids_by_element(tables: list[dict]) -> dict:
    ids = {}
    for t in tables:
        el = t.get("element")
        tid = t.get("id")
        if el is not None and tid:
            ids[id(el)] = tid
    return ids


def _place_tables(items, raw_tables) -> tuple[list[dict], list[str]]:
    """Build the final schema `tables[]` list.

    A table only appears in the final list if its `[Table: id]` placeholder
    actually landed in some section's text -- content that sorts before a
    filing's first detected Item heading (a cover page, a TOC) is never
    part of any Item's range, so a data table sitting there (rare, but a
    table-form TOC's page-number column can trip wave 2b's numeric-cell
    threshold) would otherwise have no `item`/`section_id` to report, which
    the schema requires. Filtering to placed tables only is what keeps
    `placeholders_consistent` true by construction; the unplaced ones are
    counted in stats instead of silently listed with fake placement.
    """
    placement: dict[str, dict] = {}
    for item in items:
        for s in item["sections"]:
            for tid in s["tables"]:
                pos = s["text"].find(f"[Table: {tid}]")
                placement[tid] = {"item": item["item"], "section_id": s["id"], "position": pos}

    final_tables = []
    unplaced = []
    for t in raw_tables:
        tid = t["id"]
        p = placement.get(tid)
        if p is None:
            unplaced.append(tid)
            continue
        final_tables.append(
            {
                "id": tid,
                "item": p["item"],
                "section_id": p["section_id"],
                "title": t.get("title"),
                "units": t.get("units"),
                "headers": t.get("headers", []),
                "rows": t.get("rows", []),
                "text": t.get("text", ""),
                "page_start": t.get("page_start"),
                "page_end": t.get("page_end"),
                "position": p["position"],
                "misaligned": t.get("misaligned", False),
                "xbrl_values_matched": t.get("xbrl_values_matched", 0),
                "xbrl_values_total": t.get("xbrl_values_total", 0),
            }
        )
    return final_tables, unplaced


def parse_filing(path, meta: dict) -> dict:
    tree, ix_records = clean_document(path)
    company = meta.get("company")
    pages_result = assign_pages(tree, company=company)

    table_extract = join_tables(tree, pages_result.page_for)
    raw_tables = table_extract["tables"]
    table_ids = _table_ids_by_element(raw_tables)

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

    final_tables, unplaced_table_ids = _place_tables(items, raw_tables)

    categories = meta.get("filer_category") or ""
    smaller_reporting = "smaller reporting" in categories.lower()

    checks = run_checks(
        items,
        pages_result,
        content_blocks,
        final_tables,
        table_extract,
        smaller_reporting,
        item_stats["integrated_report"],
    )
    checks["stats"].update(item_stats)
    checks["stats"]["xbrl_nonfraction_doc_total"] = len(ix_records)
    if unplaced_table_ids:
        checks["stats"]["tables_unplaced"] = len(unplaced_table_ids)

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
        "tables": final_tables,
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
