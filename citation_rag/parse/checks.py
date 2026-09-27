"""Checks for a parsed filing (wave 2c contract, deliverable 2).

Every check function returns ``(passed: bool, detail: str)``. ``run_checks``
runs all of them and returns the ``checks`` object the schema describes:
``{"passed": bool, "failures": [str, ...], "stats": {...}}``.

Prose checks carried over from wave 2a's own report/checks, with the
adjustments wave 2c's contract asks for:
- ``items_in_order``: Items 15 and 16 are exempt (filers place the Form
  10-K Summary before the exhibit index).
- ``coverage``: floor 0.90 (was 0.95; short shell filings have
  proportionally more front/back matter).
- ``required_items_present``: a filing whose Item headings match the Form
  10-Q structure (Part I Items 1-4, Part II Items 1-6, nothing past Item 6
  ever detected) fails with reason ``not_a_10k_structure`` instead of the
  generic "required Items absent" message.

New table checks (wave 2c): ``xbrl_in_table_coverage``, ``table_count_range``,
``placeholders_consistent``, ``no_empty_sections``.
"""

from __future__ import annotations

import re

from .blocks import unwrap_table_to_text

REQUIRED_ITEMS = ["1", "1A", "2", "3", "5", "7", "7A", "8", "9A", "15"]
ITEM_SIZE_RANGES = {
    "1A": (3000, 400000),
    "7": (5000, 800000),
    "8": (5000, 3000000),
}
TOC_LINE_RE = re.compile(r"^item\s+(\d{1,2}[a-c]?)\b", re.I | re.M)
TABLE_PLACEHOLDER_RE = re.compile(r"\[Table: (t\d+)\]")
CROSS_REF_SECTION_RE = re.compile(r"^\s*See Item [0-9A-C]+\.\s*$")

# Items past Item 6 that a genuine Form 10-K almost always detects *some*
# status for (present, not_required, incorporated_by_reference -- even
# "absent" is rare because the heading itself is usually written even when
# the body is a one-line disclaimer). A Form 10-Q never has these headings
# at all, so all of them being fully undetected, together with some of
# Items 1-4 being detected, is a reliable 10-Q signature.
_BEYOND_10Q_ITEMS = {
    "7", "7A", "8", "9", "9A", "9B", "9C",
    "10", "11", "12", "13", "14", "15", "16",
}

COVERAGE_FLOOR = 0.90
COVERAGE_CEILING = 1.15  # generous: table.text repeats header labels per
# cell ("2025: 167,045"), which can run longer than a naive cell-join.
XBRL_IN_TABLE_FLOOR = 0.98
TABLE_COUNT_MIN = 5
TABLE_COUNT_MAX = 900
# Wave 2d: lowered from 50 to 20 (contract item 2). 31 wave 2c failures were
# a complete, correctly-parsed one-sentence Item body under the old 50-char
# floor ("We are not a party to any material lawsuits." is 46 characters) --
# a genuine, terse answer, not an empty section. 20 characters still catches
# a truly empty placeholder-only section.
NO_EMPTY_SECTION_MIN = 20


def _is_10q_structure(items) -> bool:
    # "present"/"incorporated_by_reference" mean a real heading was found
    # for that Item; "not_required" is excluded on purpose, since a
    # smaller-reporting filer auto-omits Items 1A/1B/6/7A whenever no real
    # heading is found at all (see items.py's SMALLER_REPORTING_OMITTABLE),
    # which would otherwise make 6/7A look "present" for every filer, real
    # 10-K or not, and mask the very signature this check looks for.
    detected = {
        it["item"] for it in items if it["status"] in ("present", "incorporated_by_reference")
    }
    if not (detected & {"1", "2", "3", "4"}):
        return False
    return not (detected & _BEYOND_10Q_ITEMS)


def check_required_items_present(items, smaller_reporting) -> tuple[bool, str]:
    if _is_10q_structure(items):
        return False, "not_a_10k_structure"
    bad = []
    for it in items:
        if it["item"] not in REQUIRED_ITEMS:
            continue
        if it["status"] == "absent" and not smaller_reporting:
            bad.append(it["item"])
    if bad:
        return False, f"required Items absent: {', '.join(bad)}"
    return True, ""


def _is_combined_cross_reference(it) -> bool:
    secs = it.get("sections") or []
    return len(secs) == 1 and bool(CROSS_REF_SECTION_RE.match(secs[0]["text"]))


def check_items_in_order(items, integrated_report) -> tuple[bool, str]:
    if integrated_report:
        return True, "exempt (integrated report)"
    last = None
    for it in items:
        if it["item"] in ("15", "16"):
            continue  # filers commonly place the Form 10-K Summary (16)
            # before the exhibit index (15), or vice versa; exempt per
            # contract.
        if _is_combined_cross_reference(it):
            continue  # a combined-Item stub's "page_start" is the tail end
            # of the *other* Item's own range (for example "ITEMS 1 and 2.
            # BUSINESS AND PROPERTIES" gives Item 2 a page_start that sits
            # before Item 1B/1C's, which are not adjacent to Item 1 in
            # STANDARD_ITEMS order); it is a cross-reference, not an
            # independent position in the document, so it is not checked.
        if it["status"] != "present" or it["page_start"] is None:
            continue
        if last is not None and it["page_start"] < last:
            return False, f"Item {it['item']} starts at page {it['page_start']}, before a prior present Item"
        last = it["page_start"]
    return True, ""


def check_item_sizes(items) -> tuple[bool, str]:
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


def compute_body_chars(content_blocks, raw_tables) -> int:
    """Total raw content across the whole document, as a coverage baseline.

    A data table's contribution uses its own wave-2b row-form `text`
    (title/units + "label | header: cell" lines), the same text that ends
    up counted on the numerator side (either inline via its placeholder
    substitution, or omitted entirely if the table never got placed, which
    correctly dings coverage). Using the old naive `unwrap_table_to_text`
    (a plain "cell | cell | cell" join) here instead would systematically
    under-count the denominator relative to the richer, header-repeating
    row-form text and could push coverage past 1.8 on tables-heavy
    filings -- this was measured on the sample and is why this function
    exists instead of reusing `unwrap_table_to_text` for every table block.
    A layout table (never classified as a data table) still uses the naive
    unwrap, since that is exactly how it is rendered into prose.
    """
    table_text_by_element_id = {
        id(t["element"]): t.get("text", "") for t in raw_tables if t.get("element") is not None
    }
    total = 0
    for b in content_blocks:
        if b.kind == "text":
            total += len(b.text)
        else:
            data_text = table_text_by_element_id.get(id(b.element))
            total += len(data_text) if data_text is not None else len(unwrap_table_to_text(b.element))
    return total


def compute_coverage(items, tables, body_chars) -> tuple[float, int, int]:
    """coverage = (prose text + placed table text) / raw body text.

    `items[].sections[].text` already contains a short `[Table: id]`
    placeholder in place of each placed data table's original HTML, so
    that text alone undercounts a tables-heavy filing; the table's own
    row-form `text` is added back in to measure the real content coverage,
    per the contract ("computes coverage over prose + table text").
    """
    prose_chars = sum(len(s["text"]) for it in items for s in it["sections"])
    table_chars = sum(len(t["text"]) for t in tables)
    numerator = prose_chars + table_chars
    coverage = (numerator / body_chars) if body_chars else 1.0
    return coverage, prose_chars, table_chars


def check_coverage(coverage: float, prose_chars: int, table_chars: int, body_chars: int) -> tuple[bool, str]:
    ok = COVERAGE_FLOOR <= coverage <= COVERAGE_CEILING
    detail = (
        f"prose={prose_chars} table_text={table_chars} body={body_chars} "
        f"coverage={coverage:.3f}"
    )
    return ok, detail


def check_no_toc_in_sections(items) -> tuple[bool, str]:
    """A real table-of-contents leak has a line for several *different*
    Items (1, 1A, 2, 3, ...). An Item 15 exhibit schedule legitimately
    lists several rows for its *own* number's Item 601(b) sub-parts
    ("Item 15.", "Item 15(a)", "Item 15(b)", ...), which the raw-line-count
    version of this check could not tell apart from a real TOC (seen in
    Madrigal Pharmaceuticals' real Item 15 section); counting distinct
    Item numbers instead fixes that without weakening the real check.
    """
    bad = []
    for it in items:
        for s in it["sections"]:
            nums = {m.group(1).upper() for m in TOC_LINE_RE.finditer(s["text"])}
            if len(nums) >= 5:
                bad.append(s["id"])
    if bad:
        return False, f"sections with TOC-like text: {', '.join(bad)}"
    return True, ""


def _label_series_value(label: str):
    from .pages import classify_token

    series, value = classify_token(label)
    if series is None or value is None:
        return None
    return series, value


def check_page_labels_monotonic(pages) -> tuple[bool, str]:
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


def check_xbrl_in_table_coverage(tables) -> tuple[bool, str, int, int]:
    """Among ix:nonFraction values that sit inside a data table, the share
    matched to a parsed cell. Uses the tables actually extracted as data
    tables (whether or not they ended up placed in a section), since this
    checks wave 2b's extraction quality, not wave 2c's placement.
    """
    total = sum(t.get("xbrl_values_total", 0) for t in tables)
    matched = sum(t.get("xbrl_values_matched", 0) for t in tables)
    coverage = (matched / total) if total else 1.0
    ok = coverage >= XBRL_IN_TABLE_FLOOR
    detail = f"matched={matched} total={total} coverage={coverage:.5f}"
    return ok, detail, matched, total


def check_table_count_range(n_data_tables: int) -> tuple[bool, str]:
    ok = TABLE_COUNT_MIN <= n_data_tables <= TABLE_COUNT_MAX
    detail = f"{n_data_tables} data tables (expected {TABLE_COUNT_MIN}-{TABLE_COUNT_MAX})"
    return ok, detail


def check_placeholders_consistent(items, tables) -> tuple[bool, str]:
    """Every table id in `tables[]` appears exactly once as a placeholder
    in some section text, and vice versa."""
    placeholder_counts: dict[str, int] = {}
    for it in items:
        for s in it["sections"]:
            for m in TABLE_PLACEHOLDER_RE.finditer(s["text"]):
                tid = m.group(1)
                placeholder_counts[tid] = placeholder_counts.get(tid, 0) + 1

    table_ids = {t["id"] for t in tables}
    placeholder_ids = set(placeholder_counts)

    problems = []
    missing_placeholder = table_ids - placeholder_ids
    if missing_placeholder:
        problems.append(f"tables with no placeholder: {sorted(missing_placeholder)}")
    orphan_placeholder = placeholder_ids - table_ids
    if orphan_placeholder:
        problems.append(f"placeholders with no table: {sorted(orphan_placeholder)}")
    duplicated = [tid for tid, n in placeholder_counts.items() if n > 1 and tid in table_ids]
    if duplicated:
        problems.append(f"tables placed more than once: {sorted(duplicated)}")

    if problems:
        return False, "; ".join(problems)
    return True, ""


def check_no_empty_sections(items) -> tuple[bool, str]:
    bad = []
    for it in items:
        if it["status"] != "present":
            continue
        for s in it["sections"]:
            if CROSS_REF_SECTION_RE.match(s["text"]):
                continue  # a combined-Item cross-reference stub ("See Item
                # 7."), not a real empty section.
            if s["tables"]:
                continue  # this section's substantive content is captured
                # in tables[], not in this text field -- Item 15's exhibit
                # index is the common case: several exhibit-list tables
                # plus a one-line "* Filed herewith" footnote, which reads
                # as near-empty only because its real content lives
                # elsewhere in the schema, not because anything was lost.
            stripped = TABLE_PLACEHOLDER_RE.sub("", s["text"]).strip()
            if len(stripped) < NO_EMPTY_SECTION_MIN:
                bad.append(s["id"])
    if bad:
        return False, f"sections under {NO_EMPTY_SECTION_MIN} chars: {', '.join(bad)}"
    return True, ""


def run_checks(
    items,
    pages_result,
    content_blocks,
    tables,
    table_extract,
    smaller_reporting,
    integrated_report,
) -> dict:
    failures = []

    ok, detail = check_required_items_present(items, smaller_reporting)
    if not ok:
        failures.append(f"required_items_present: {detail}")

    ok, detail = check_items_in_order(items, integrated_report)
    if not ok:
        failures.append(f"items_in_order: {detail}")

    ok, detail = check_item_sizes(items)
    if not ok:
        failures.append(f"item_sizes: {detail}")

    body_chars = compute_body_chars(content_blocks, table_extract.get("tables", []))
    coverage, prose_chars, table_chars = compute_coverage(items, tables, body_chars)
    ok, detail = check_coverage(coverage, prose_chars, table_chars, body_chars)
    if not ok:
        failures.append(f"coverage: {detail}")

    ok, detail = check_no_toc_in_sections(items)
    if not ok:
        failures.append(f"no_toc_in_sections: {detail}")

    ok, detail = check_page_labels_monotonic(pages_result.pages)
    if not ok:
        failures.append(f"page_labels_monotonic: {detail}")

    all_extracted_tables = table_extract.get("tables", [])
    ok, detail, in_table_matched, in_table_total = check_xbrl_in_table_coverage(all_extracted_tables)
    if not ok:
        failures.append(f"xbrl_in_table_coverage: {detail}")

    n_data_tables = len(all_extracted_tables)
    ok, detail = check_table_count_range(n_data_tables)
    if not ok:
        failures.append(f"table_count_range: {detail}")

    ok, detail = check_placeholders_consistent(items, tables)
    if not ok:
        failures.append(f"placeholders_consistent: {detail}")

    ok, detail = check_no_empty_sections(items)
    if not ok:
        failures.append(f"no_empty_sections: {detail}")

    n_labeled = sum(1 for p in pages_result.pages if p.get("label"))
    doc_total = table_extract.get("xbrl_nonfraction_total", 0)
    doc_coverage = (in_table_matched / doc_total) if doc_total else 1.0
    in_table_coverage = (in_table_matched / in_table_total) if in_table_total else 1.0

    stats = {
        "prose_chars": prose_chars,
        "body_chars": body_chars,
        "table_chars": table_chars,
        "coverage": round(coverage, 4),
        "n_pages": pages_result.n_pages,
        "page_markers": pages_result.n_markers,
        "page_label_coverage": round(n_labeled / pages_result.n_pages, 4) if pages_result.n_pages else 0.0,
        "data_tables": n_data_tables,
        "layout_tables": len(table_extract.get("layout_tables", [])),
        "xbrl_nonfraction_total": doc_total,
        "xbrl_nonfraction_matched": in_table_matched,
        "xbrl_in_table_total": in_table_total,
        "xbrl_in_table_coverage": round(in_table_coverage, 5),
        "xbrl_document_coverage": round(doc_coverage, 5),
    }
    return {"passed": not failures, "failures": failures, "stats": stats}
