"""Detect Form 10-K Item headings (wave 2a contract, deliverable 3).

Produces, for each of the 22 standard Items, a record with its status
(present / absent / not_required / incorporated_by_reference), its part,
its title, its page range, and (for present Items) the slice of the shared
content-block stream that holds its body -- which sections.py then splits
into sections.
"""

from __future__ import annotations

import re

from .blocks import (
    BLOCK_TAGS,
    HEADING_EXTRA_TAGS,
    Block,
    has_block_child,
    in_table,
    nearest_table_ancestor,
    norm_text,
    unwrap_table_to_text,
)

STANDARD_ITEMS = [
    "1", "1A", "1B", "1C", "2", "3", "4",
    "5", "6", "7", "7A", "8", "9", "9A", "9B", "9C",
    "10", "11", "12", "13", "14",
    "15", "16",
]

ITEM_PARTS = {
    "1": "I", "1A": "I", "1B": "I", "1C": "I", "2": "I", "3": "I", "4": "I",
    "5": "II", "6": "II", "7": "II", "7A": "II", "8": "II", "9": "II",
    "9A": "II", "9B": "II", "9C": "II",
    "10": "III", "11": "III", "12": "III", "13": "III", "14": "III",
    "15": "IV", "16": "IV",
}

ITEM_TITLES = {
    "1": "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "1C": "Cybersecurity",
    "2": "Properties",
    "3": "Legal Proceedings",
    "4": "Mine Safety Disclosures",
    "5": "Market for Registrant's Common Equity, Related Stockholder Matters and Issuer Purchases of Equity Securities",
    "6": "[Reserved]",
    "7": "Management's Discussion and Analysis of Financial Condition and Results of Operations",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements and Supplementary Data",
    "9": "Changes in and Disagreements with Accountants on Accounting and Financial Disclosure",
    "9A": "Controls and Procedures",
    "9B": "Other Information",
    "9C": "Disclosure Regarding Foreign Jurisdictions that Prevent Inspections",
    "10": "Directors, Executive Officers and Corporate Governance",
    "11": "Executive Compensation",
    "12": "Security Ownership of Certain Beneficial Owners and Management and Related Stockholder Matters",
    "13": "Certain Relationships and Related Transactions, and Director Independence",
    "14": "Principal Accountant Fees and Services",
    "15": "Exhibits, Financial Statement Schedules",
    "16": "Form 10-K Summary",
}

SMALLER_REPORTING_OMITTABLE = {"1A", "1B", "6", "7A"}

_SEP = r"[.:\-–—]"
_PART_PREFIX = rf"(?:PART\s+[IV]+\s*{_SEP}?\s*)?"

# (?!\.\d) rejects an EDGAR Form 8-K style decimal sub-item reference
# such as "Item 5.02." (a cross-reference inside a 10-K's own text), which
# would otherwise look like a heading for Item 5.
ITEM_RE = re.compile(
    rf"^\s*{_PART_PREFIX}ITEM\s+(\d{{1,2}}[A-C]?)(?!\.\d)\s*{_SEP}?\s*(.*)$", re.I
)
COMBINED_RE = re.compile(
    rf"^\s*{_PART_PREFIX}ITEMS?\s+(\d{{1,2}}[A-C]?)(?!\.\d)\.?\s+(?:AND|&)\s+"
    rf"(\d{{1,2}}[A-C]?)(?!\.\d)\.?\s*(.*)$",
    re.I,
)
NOT_REQUIRED_RE = re.compile(
    r"\b(not applicable|none|not required|reserved|omitted)\b", re.I
)
INCORPORATED_RE = re.compile(r"incorporated (?:herein )?by (?:this )?reference", re.I)
INDEX_ROW_RE = re.compile(
    r"^ITEM\s+(\d{1,2}[A-C]?)\.?\s+(.*?)\s+(\d{1,3})(?:\s*[-–—]\s*\d{1,3})?\s*$",
    re.I,
)
FIN_STMT_MARKER_RE = re.compile(
    r"(report of independent registered public accounting firm"
    r"|index to (?:the )?(?:consolidated )?financial statements"
    r"|consolidated balance sheets?"
    r"|consolidated statements? of (?:operations|income|financial position"
    r"|cash flows|stockholders|changes in))",
    re.I,
)
FIN_STMT_STUB_MAX_CHARS = 3000

_ALL_TAGS = BLOCK_TAGS | HEADING_EXTRA_TAGS


def normalize_item_num(raw: str) -> str:
    return raw.upper().replace(" ", "")


def clean_title(text: str) -> str:
    return text.strip(" .:-–— ").strip()


def split_filer_category(category: str | None) -> list[str]:
    """filer_category is a <br>-joined string; split before comparing."""
    if not category:
        return []
    parts = re.split(r"<br\s*/?>", category, flags=re.I)
    return [p.strip() for p in parts if p.strip()]


def is_smaller_reporting(categories: list[str]) -> bool:
    return any("smaller reporting" in c.lower() for c in categories)


# ---------------------------------------------------------------------------
# Building the content + heading streams
# ---------------------------------------------------------------------------


def build_streams(root, page_for):
    """One preorder walk producing (content_blocks, headings).

    content_blocks: list[Block], paragraphs and whole tables, document
    order, never descending into a table's own cells.
    headings: list[dict], each an Item-heading candidate (single or
    combined), with the content_blocks index where its body would start.
    """
    content_blocks: list[Block] = []
    headings: list[dict] = []
    for el in root.iter():
        tag = el.tag
        if not isinstance(tag, str):
            continue
        if tag == "table":
            if in_table(el):
                continue
            content_blocks.append(Block(el, "", "table", page_for(el)))
            continue
        if tag not in _ALL_TAGS:
            continue
        if has_block_child(el, _ALL_TAGS):
            continue
        text = norm_text(el)
        if not text:
            continue
        # Heading candidacy is checked before the in-table exclusion: a
        # table-of-contents row commonly puts "Item 1." inside a <td>
        # (or a <p> nested one level inside a <td>), so this must run
        # even for elements nested inside a <table>.
        if len(text) < 200:
            cm = COMBINED_RE.match(text)
            m = None if cm else ITEM_RE.match(text)
            if cm or m:
                headings.append(
                    {
                        "element": el,
                        "text": text,
                        "page": page_for(el),
                        "match": cm or m,
                        "combined": bool(cm),
                        "content_index": len(content_blocks),
                        "table_ancestor": nearest_table_ancestor(el),
                    }
                )
                continue
        if in_table(el):
            continue  # non-heading text inside a table: the table (its
            # own atomic content_blocks entry) owns this content instead.
        if tag in HEADING_EXTRA_TAGS:
            continue  # only used for heading scan above; prose comes via <tr>
        content_blocks.append(Block(el, text, "text", page_for(el)))
    return content_blocks, headings


def _block_text_len(block: Block) -> int:
    if block.kind == "text":
        return len(block.text)
    return len(unwrap_table_to_text(block.element))


def _following_text_len(headings, content_blocks, i):
    start = headings[i]["content_index"]
    end = headings[i + 1]["content_index"] if i + 1 < len(headings) else len(content_blocks)
    return sum(_block_text_len(b) for b in content_blocks[start:end])


def _table_has_many_item_rows(table_el, threshold=3) -> bool:
    count = 0
    for tr in table_el.iter("tr"):
        cells = [c for c in tr if isinstance(c.tag, str) and c.tag in ("td", "th")]
        row_text = " ".join(norm_text(c) for c in cells).strip()
        if re.match(r"^item\b", row_text, re.I):
            count += 1
            if count > threshold:
                return True
    return False


def filter_headings(headings, content_blocks):
    """Drop table-of-contents hits. Returns (kept, n_toc_skipped, first_real)."""
    follow_lens = [_following_text_len(headings, content_blocks, i) for i in range(len(headings))]
    first_real = None
    for i, flen in enumerate(follow_lens):
        if flen > 1500:
            first_real = i
            break

    table_cache: dict = {}

    def table_is_toc(table_el) -> bool:
        if table_el is None:
            return False
        key = id(table_el)
        if key not in table_cache:
            table_cache[key] = _table_has_many_item_rows(table_el)
        return table_cache[key]

    kept = []
    n_skipped = 0
    for i, h in enumerate(headings):
        if first_real is not None and i < first_real:
            n_skipped += 1
            continue
        if table_is_toc(h["table_ancestor"]):
            n_skipped += 1
            continue
        kept.append(h)

    if not kept and headings:
        # Fallback for filings where no heading has 1,500+ chars of
        # following text before the next heading (for example a very
        # short shell-company Item 1) -- without this, TOC-cutoff logic
        # alone would discard every heading and leave the filing with no
        # Items at all. Use the last occurrence of each item number
        # (TOC entries normally come first, the body heading last), still
        # excluding anything flagged as a TOC-style table.
        best: dict[str, dict] = {}
        combined_kept = []
        for h in headings:
            if table_is_toc(h["table_ancestor"]):
                continue
            if h["combined"]:
                combined_kept.append(h)
                continue
            item_num = normalize_item_num(h["match"].group(1))
            prev = best.get(item_num)
            if prev is None or h["content_index"] > prev["content_index"]:
                best[item_num] = h
        kept = sorted(list(best.values()) + combined_kept, key=lambda h: h["content_index"])

    return kept, n_skipped, first_real


def _page_end_for_range(content_blocks, start, end, default):
    pages = [b.page for b in content_blocks[start:end] if b.page]
    return max(pages) if pages else default


def _raw_text_for_range(content_blocks, start, end) -> str:
    parts = []
    for b in content_blocks[start:end]:
        parts.append(b.text if b.kind == "text" else unwrap_table_to_text(b.element))
    return "\n".join(p for p in parts if p)


def _classify_status(raw_text: str) -> str | None:
    """Return an override status for a body that turns out to be a stub,
    or None if the Item should stay 'present'."""
    stripped = raw_text.strip()
    if len(stripped) < 600 and INCORPORATED_RE.search(stripped):
        return "incorporated_by_reference"
    if len(stripped) < 200 and NOT_REQUIRED_RE.search(stripped):
        return "not_required"
    return None


# ---------------------------------------------------------------------------
# Integrated-report fallback
# ---------------------------------------------------------------------------


def _find_cross_reference_table(root):
    """Find a table whose rows look like 'Item 1A. Risk Factors ... 50-64'."""
    best = None
    best_rows = []
    for table in root.iter("table"):
        if in_table(table):
            continue
        rows = []
        for tr in table.iter("tr"):
            cells = [c for c in tr if isinstance(c.tag, str) and c.tag in ("td", "th")]
            row_text = " ".join(norm_text(c) for c in cells).strip()
            row_text = re.sub(r"\s+", " ", row_text)
            m = INDEX_ROW_RE.match(row_text)
            if m:
                rows.append(m)
        if len(rows) > best_rows.__len__():
            best, best_rows = table, rows
    if best is not None and len(best_rows) >= 5:
        return best, best_rows
    return None, []


def _label_to_page_index(pages: list[dict], label: str) -> int | None:
    for p in pages:
        if p.get("label") == label:
            return p["index"]
    return None


def _integrated_report_items(root, content_blocks, pages):
    table, rows = _find_cross_reference_table(root)
    if table is None:
        return None
    entries = []
    for m in rows:
        item_num = normalize_item_num(m.group(1))
        title = clean_title(m.group(2))
        label = m.group(3)
        page_idx = _label_to_page_index(pages, label)
        entries.append({"item": item_num, "title": title, "page_index": page_idx})
    entries = [e for e in entries if e["page_index"] is not None]
    if len(entries) < 5:
        return None
    entries.sort(key=lambda e: e["page_index"])
    records = {}
    for i, e in enumerate(entries):
        page_start = e["page_index"]
        page_end = entries[i + 1]["page_index"] if i + 1 < len(entries) else pages[-1]["index"] if pages else page_start
        start_index = next(
            (j for j, b in enumerate(content_blocks) if b.page >= page_start), len(content_blocks)
        )
        end_index = next(
            (j for j, b in enumerate(content_blocks) if b.page > page_end), len(content_blocks)
        )
        records[e["item"]] = {
            "item": e["item"],
            "title": e["title"] or ITEM_TITLES.get(e["item"], ""),
            "status": "present",
            "page_start": page_start,
            "page_end": page_end,
            "start_index": start_index,
            "end_index": end_index,
            "forced_sections": None,
        }
    return records


def _reassign_range_to_item8(found, item8, xlo, xhi, new_page_end):
    for other_num, rec in found.items():
        if other_num == "8" or rec.get("start_index") is None:
            continue
        s, e = rec["start_index"], rec["end_index"]
        if e <= xlo or s >= xhi:
            continue  # no overlap
        if s < xlo:
            rec["end_index"] = xlo
        else:
            # this item's range starts inside (or before) the reassigned
            # span; there is nothing left to attribute to it
            rec["end_index"] = rec["start_index"]
    item8["extra_index_range"] = (xlo, xhi)
    if item8["page_end"] is None or new_page_end > item8["page_end"]:
        item8["page_end"] = new_page_end


def _apply_financial_statements_fixup(found, content_blocks, pages):
    """Many 10-Ks print Item 8's real body as a short stub ("The required
    financial statements commence on page F-1." or "... appear in this
    Report beginning on page 33.") and place the actual audited financial
    statements later in the document -- physically inside whatever later
    Item's range happens to run to the end of the document (usually 15 or
    16), sometimes on a separate "F-1, F-2, ..." page series, sometimes on
    the filing's own continuing page numbers. Move that range's content
    blocks over to Item 8 and trim it out of whichever other Item
    currently claims it, so Item 8's size and the coverage check both
    reflect where the content really is.
    """
    item8 = found.get("8")
    if item8 is None or item8.get("start_index") is None:
        return
    current_size = len(_raw_text_for_range(content_blocks, item8["start_index"], item8["end_index"]))
    if current_size >= FIN_STMT_STUB_MAX_CHARS:
        return  # Item 8 already has a real body

    # Never reach into content that is already the legitimate body of
    # another Item's own detected heading (for example a filing that
    # genuinely headed its financial-statement index "Item 15."): treat a
    # candidate as owned by that other Item only when it sits at (or right
    # after) that Item's own heading -- not merely somewhere inside a
    # trailing range that extends to end-of-document by default (which is
    # true of whichever Item happens to be last, and is exactly the
    # normal, liftable "back matter" case this fixup targets).
    other_starts = [
        rec["start_index"]
        for num, rec in found.items()
        if num != "8" and rec.get("start_index") is not None
    ]

    def _owned_by_another_item(i, window=3):
        return any(s <= i < s + window for s in other_starts)

    # Strategy 1: a distinct "F-1, F-2, ..." page-label series. If that
    # range's start looks like it is really owned by another Item's own
    # heading, trust that heading instead and do not touch anything.
    f_pages = [p["index"] for p in pages if p.get("label") and str(p["label"]).upper().startswith("F-")]
    if f_pages:
        # A lone early "F-1" is sometimes a stray false match (e.g. an
        # exhibit numbered "F-1" in a list near the front of the filing);
        # a real F-page series is long and its pages are close together,
        # so use the largest cluster of nearby F-labeled pages, not the
        # raw min/max.
        f_pages_sorted = sorted(f_pages)
        clusters = [[f_pages_sorted[0]]]
        for p in f_pages_sorted[1:]:
            if p - clusters[-1][-1] <= 5:
                clusters[-1].append(p)
            else:
                clusters.append([p])
        best_cluster = max(clusters, key=len)
        lo, hi = min(best_cluster), max(best_cluster)
        if item8["page_end"] is None or item8["page_end"] < lo:
            extra_indices = [
                i for i, b in enumerate(content_blocks) if i >= item8["end_index"] and b.page and lo <= b.page <= hi
            ]
            if extra_indices and not _owned_by_another_item(min(extra_indices)):
                _reassign_range_to_item8(found, item8, min(extra_indices), max(extra_indices) + 1, hi)
                return

    # Strategy 2: no separate F-page series (continuing arabic page
    # numbers) -- find the first "CONSOLIDATED BALANCE SHEETS" / "Report
    # of Independent Registered Public Accounting Firm" / financial
    # statement index heading after Item 8's own (stub) body, and claim
    # everything from there to the end of document. If that heading is
    # really the start of another Item's own body (for example a filing
    # that genuinely headed its financial-statement index "Item 15."),
    # trust that heading instead and give up rather than partially
    # cannibalizing it.
    for i in range(item8["end_index"], len(content_blocks)):
        b = content_blocks[i]
        if b.kind != "text" or len(b.text) >= 120:
            continue
        if FIN_STMT_MARKER_RE.search(b.text):
            if _owned_by_another_item(i):
                return
            end_of_doc = len(content_blocks)
            new_page_end = content_blocks[end_of_doc - 1].page if end_of_doc else item8["page_end"]
            _reassign_range_to_item8(found, item8, i, end_of_doc, new_page_end or item8["page_end"])
            return


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def detect_items(root, page_for, pages: list[dict], filer_category: str | None):
    content_blocks, headings = build_streams(root, page_for)
    kept, n_toc_skipped, first_real = filter_headings(headings, content_blocks)

    categories = split_filer_category(filer_category)
    smaller_reporting = is_smaller_reporting(categories)

    stats = {
        "combined_items": 0,
        "integrated_report": False,
        "toc_headings_skipped": n_toc_skipped,
        "total_heading_candidates": len(headings),
    }

    found: dict[str, dict] = {}
    n = len(kept)
    idx = 0
    while idx < n:
        h = kept[idx]
        start_index = h["content_index"]
        end_index = kept[idx + 1]["content_index"] if idx + 1 < n else len(content_blocks)
        page_start = h["page"]
        page_end = _page_end_for_range(content_blocks, start_index, end_index, page_start)
        if h["combined"]:
            first_num = normalize_item_num(h["match"].group(1))
            second_num = normalize_item_num(h["match"].group(2))
            title_text = clean_title(h["match"].group(3))
            rec = {
                "item": first_num,
                "title": title_text or ITEM_TITLES.get(first_num, ""),
                "status": "present",
                "page_start": page_start,
                "page_end": page_end,
                "start_index": start_index,
                "end_index": end_index,
                "forced_sections": None,
            }
            found[first_num] = rec
            found[second_num] = {
                "item": second_num,
                "title": ITEM_TITLES.get(second_num, ""),
                "status": "present",
                "page_start": page_end,
                "page_end": page_end,
                "start_index": None,
                "end_index": None,
                "forced_sections": [f"See Item {first_num}."],
            }
            stats["combined_items"] += 1
        else:
            item_num = normalize_item_num(h["match"].group(1))
            title_text = clean_title(h["match"].group(2))
            rec = {
                "item": item_num,
                "title": title_text or ITEM_TITLES.get(item_num, ""),
                "status": "present",
                "page_start": page_start,
                "page_end": page_end,
                "start_index": start_index,
                "end_index": end_index,
                "forced_sections": None,
            }
            # a later occurrence of the same item overrides an earlier one
            found[item_num] = rec
        idx += 1

    _apply_financial_statements_fixup(found, content_blocks, pages)

    # Resolve status overrides (incorporated by reference / not required)
    # for items whose body has real content.
    for item_num, rec in found.items():
        if rec["start_index"] is None:
            continue
        raw_text = _raw_text_for_range(content_blocks, rec["start_index"], rec["end_index"])
        extra = rec.get("extra_index_range")
        if extra:
            raw_text += "\n" + _raw_text_for_range(content_blocks, extra[0], extra[1])
        override = _classify_status(raw_text)
        if override:
            rec["status"] = override

    # Integrated-report fallback: no body headings survived at all, but a
    # cross-reference index table maps Items to printed page labels.
    if not found:
        integrated = _integrated_report_items(root, content_blocks, pages)
        if integrated:
            found = integrated
            stats["integrated_report"] = True

    items = []
    for item_num in STANDARD_ITEMS:
        rec = found.get(item_num)
        if rec is not None:
            items.append(
                {
                    "item": item_num,
                    "part": ITEM_PARTS[item_num],
                    "title": rec["title"],
                    "status": rec["status"],
                    "page_start": rec["page_start"],
                    "page_end": rec["page_end"],
                    "start_index": rec["start_index"],
                    "end_index": rec["end_index"],
                    "forced_sections": rec["forced_sections"],
                    "extra_index_range": rec.get("extra_index_range"),
                }
            )
        else:
            status = "absent"
            if smaller_reporting and item_num in SMALLER_REPORTING_OMITTABLE:
                status = "not_required"
            items.append(
                {
                    "item": item_num,
                    "part": ITEM_PARTS[item_num],
                    "title": ITEM_TITLES.get(item_num, ""),
                    "status": status,
                    "page_start": None,
                    "page_end": None,
                    "start_index": None,
                    "end_index": None,
                    "forced_sections": None,
                }
            )

    return items, content_blocks, stats
