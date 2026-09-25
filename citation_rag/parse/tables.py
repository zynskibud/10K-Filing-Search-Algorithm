"""Table extraction for EDGAR 10-K filings.

`extract_tables(tree, page_of)` walks every `<table>` in an lxml tree
(already parsed with `lxml.html`, ix:header already dropped), decides
whether each one is a data table or a layout table, and for data tables
builds the title, units, headers, rows, and text form that
`reports/contracts/schemas.md` section 1 (`tables[]`) describes.

`tree` is expected to come from an HTML parser (`lxml.html`), so tags such
as `<ix:nonFraction>` come through unprefixed and lower-cased, e.g.
`"ix:nonfraction"`. See "judgment calls" in `reports/wave-2b.md` for the
gaps the contract left open and how this module fills them.
"""

from __future__ import annotations

import re

from citation_rag.parse.numbers import merge_number_cells, parse_number

_WS_RE = re.compile(r"\s+")
_ZERO_WIDTH_RE = re.compile(r"[​‌‍﻿]")
_UNITS_RE = re.compile(
    r"\((?:in|dollars in|amounts in)?\s*(thousands|millions|billions)[^)]*\)",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_FONT_WEIGHT_RE = re.compile(r"font-weight\s*:\s*(\d+|bold)", re.IGNORECASE)
_INDENT_RE = re.compile(
    r"(?:padding-left|margin-left|text-indent)\s*:\s*(-?\d+(?:\.\d+)?)\s*(pt|px|em)?",
    re.IGNORECASE,
)

_SPACER_TEXTS = {"", "$", "(", ")", "%"}

_MAX_HEADER_ROWS = 3
_MAX_TITLE_BLOCKS = 6
_MAX_WALK_STEPS = 500

_ROW_XPATH = "./tr|./thead/tr|./tbody/tr|./tfoot/tr"
_CELL_XPATH = "./td|./th"


# ---------------------------------------------------------------------------
# small text/style helpers
# ---------------------------------------------------------------------------


def _norm(text: str | None) -> str:
    if not text:
        return ""
    text = _ZERO_WIDTH_RE.sub("", text)
    text = text.replace("\xa0", " ")
    return _WS_RE.sub(" ", text).strip()


def _cell_text(el) -> str:
    return _norm("".join(el.itertext()))


def _is_tag(el, name: str) -> bool:
    tag = getattr(el, "tag", None)
    return isinstance(tag, str) and tag.lower() == name


def _is_bold(el) -> bool:
    for node in el.iter():
        style = node.get("style", "") if hasattr(node, "get") else ""
        m = _FONT_WEIGHT_RE.search(style or "")
        if m:
            val = m.group(1).lower()
            if val == "bold":
                return True
            try:
                if int(val) >= 600:
                    return True
            except ValueError:
                pass
    return False


def _indent_level(el) -> int:
    style = el.get("style", "") or ""
    m = _INDENT_RE.search(style)
    if m:
        value = float(m.group(1))
        unit = (m.group(2) or "pt").lower()
        if unit == "em":
            pt = value * 12.0
        elif unit == "px":
            pt = value * 0.75
        else:
            pt = value
        if pt <= 2:
            return 0
        if pt <= 15:
            return 1
        return 2
    # Fallback: leading &nbsp; runs in the raw (unnormalized) text.
    raw = "".join(el.itertext())
    lead = raw[: len(raw) - len(raw.lstrip("\xa0 \t"))]
    nbsp_count = lead.count("\xa0")
    if nbsp_count == 0:
        return 0
    if nbsp_count <= 3:
        return 1
    return 2


def _looks_like_units(text: str) -> bool:
    if not text:
        return False
    if _UNITS_RE.search(text):
        return True
    return "except per share" in text.lower()


def _clean_units(text: str) -> str:
    t = _norm(text)
    if t.startswith("(") and t.endswith(")"):
        t = t[1:-1].strip()
    return t


def _is_numeric_for_header(text: str) -> bool:
    t = text.strip()
    if _YEAR_RE.match(t):
        return False
    return parse_number(t) is not None


# ---------------------------------------------------------------------------
# row/cell extraction
# ---------------------------------------------------------------------------


def _direct_rows(table) -> list:
    return table.xpath(_ROW_XPATH)


def _row_has_text(tr) -> bool:
    for cell in tr.xpath(_CELL_XPATH):
        if _cell_text(cell):
            return True
    return False


def _visible_rows(table) -> list:
    return [tr for tr in _direct_rows(table) if _row_has_text(tr)]


def _row_cells(tr) -> list[str]:
    """Raw cell texts for one row, colspan cells repeated across their span."""
    cells: list[str] = []
    for cell in tr.xpath(_CELL_XPATH):
        text = _cell_text(cell)
        span = 1
        raw_span = cell.get("colspan")
        if raw_span:
            try:
                span = max(int(raw_span), 1)
            except ValueError:
                span = 1
        cells.extend([text] * span)
    return cells


def _label_element(tr):
    """The first non-spacer <td>/<th> in a row, used for bold/indent checks."""
    for cell in tr.xpath(_CELL_XPATH):
        if _cell_text(cell) not in _SPACER_TEXTS:
            return cell
    return None


def _row_label_and_data(tr) -> tuple[str, list[str]]:
    """Split a row into its label column (raw index 0) and merged data cells.

    Column 0 is always the label slot, whether blank (a header row's
    row-name column) or filled, so header and body rows line up by
    position. `merge_number_cells` only runs on the data columns, because
    split-number artifacts ($, (, ), %) only ever occur in EDGAR's numeric
    cells, never in the label column itself.
    """
    raw = _row_cells(tr)
    if not raw:
        return "", []
    label = raw[0]
    data = merge_number_cells(raw[1:])
    return label, data


# ---------------------------------------------------------------------------
# title / units: walk backwards through preceding siblings and ancestors'
# preceding siblings, up to 6 non-empty blocks
# ---------------------------------------------------------------------------


def _preceding_blocks(table, limit: int = _MAX_TITLE_BLOCKS) -> list:
    blocks = []
    node = table
    steps = 0
    while node is not None and len(blocks) < limit and steps < _MAX_WALK_STEPS:
        prev = node.getprevious()
        while prev is not None and len(blocks) < limit and steps < _MAX_WALK_STEPS:
            steps += 1
            if isinstance(getattr(prev, "tag", None), str):
                text = _cell_text(prev)
                if text:
                    blocks.append(prev)
            prev = prev.getprevious()
        node = node.getparent()
        steps += 1
        if node is not None:
            tag = getattr(node, "tag", None)
            if isinstance(tag, str) and tag.lower() in ("body", "html"):
                break
    return blocks


def _title_and_units(table) -> tuple[str | None, str | None]:
    blocks = _preceding_blocks(table)

    units = None
    for block in blocks:
        text = _cell_text(block)
        m = _UNITS_RE.search(text)
        if m:
            units = _clean_units(m.group(0))
            break
        if "except per share" in text.lower():
            units = _clean_units(text)
            break
    if units is None:
        rows = _direct_rows(table)
        if rows:
            first_row_text = _cell_text(rows[0])
            m = _UNITS_RE.search(first_row_text)
            if m:
                units = _clean_units(m.group(0))
            elif "except per share" in first_row_text.lower():
                units = _clean_units(first_row_text)

    title = None
    for block in blocks:
        text = _cell_text(block)
        if not text or len(text) >= 160:
            continue
        if _looks_like_units(text):
            continue
        ends_without_period = not text.rstrip().endswith(".")
        is_allcaps = text.isupper() and any(c.isalpha() for c in text)
        if _is_bold(block) or is_allcaps or ends_without_period:
            title = text
            break

    return title, units


# ---------------------------------------------------------------------------
# xbrl coverage
# ---------------------------------------------------------------------------


def _has_ancestor_tag(el, name: str) -> bool:
    node = el.getparent()
    while node is not None:
        if _is_tag(node, name):
            return True
        node = node.getparent()
    return False


def _nearest_table_ancestor(el):
    node = el.getparent()
    while node is not None:
        if _is_tag(node, "table"):
            return node
        node = node.getparent()
    return None


def _collect_nonfraction(tree):
    """Return (total_count, {id(table_el): [nonfraction_el, ...]})."""
    total = 0
    by_table: dict[int, list] = {}
    for el in tree.iter():
        if not _is_tag(el, "ix:nonfraction"):
            continue
        if _has_ancestor_tag(el, "ix:header"):
            continue
        total += 1
        owner = _nearest_table_ancestor(el)
        if owner is not None:
            by_table.setdefault(id(owner), []).append(el)
    return total, by_table


def _count_xbrl_matches(records: list, headers: list[str], rows: list[dict]) -> int:
    if not records:
        return 0
    cell_texts = [c for c in headers if c]
    for row in rows:
        if row["label"]:
            cell_texts.append(row["label"])
        cell_texts.extend(c for c in row["cells"] if c)
    matched = 0
    for el in records:
        text = _cell_text(el)
        if not text:
            continue
        if any(text in cell for cell in cell_texts):
            matched += 1
    return matched


# ---------------------------------------------------------------------------
# main extraction
# ---------------------------------------------------------------------------


def _header_rows(visible_rows: list) -> list:
    """The contiguous top rows (up to 3) where most non-empty cells are text."""
    header_rows = []
    for tr in visible_rows[:_MAX_HEADER_ROWS]:
        merged = merge_number_cells(_row_cells(tr))
        non_empty = [c for c in merged if c.strip()]
        if not non_empty:
            break
        non_numeric = sum(1 for c in non_empty if not _is_numeric_for_header(c))
        # A row with only one non-empty cell reads as a row label, not a set
        # of column headers, even when that one cell is non-numeric text (a
        # group-header row like "Segment data:" mixed in near the top).
        if len(non_empty) >= 2 and non_numeric > len(non_empty) / 2:
            header_rows.append(tr)
        else:
            break
    return header_rows


def _build_headers(header_trs: list, body_rows: list) -> list[str]:
    header_ld = [_row_label_and_data(tr) for tr in header_trs]
    body_ld = [_row_label_and_data(tr) for tr in body_rows]
    lengths = [len(data) for _, data in header_ld] + [len(data) for _, data in body_ld]
    ncols_data = max(lengths, default=0)

    if not header_trs:
        return [""] + [f"col{i}" for i in range(1, ncols_data + 1)]

    col_texts = ["" for _ in range(ncols_data)]
    label_parts = []
    for label, data in header_ld:
        label = label.strip()
        if label and not _looks_like_units(label):
            label_parts.append(label)
        for i in range(ncols_data):
            cell_text = data[i] if i < len(data) else ""
            if _looks_like_units(cell_text):
                cell_text = ""
            cell_text = cell_text.strip()
            if cell_text:
                col_texts[i] = (col_texts[i] + " " + cell_text).strip() if col_texts[i] else cell_text
    label_header = " ".join(label_parts)
    return [label_header] + col_texts


def _build_rows(body_rows: list, ncols_data: int) -> tuple[list[dict], bool]:
    rows = []
    misaligned = False
    for tr in body_rows:
        label, data_cells = _row_label_and_data(tr)
        if not label.strip() and not any(c.strip() for c in data_cells):
            continue

        label_el = _label_element(tr)
        indent = _indent_level(label_el) if label_el is not None else 0
        bold = _is_bold(label_el) if label_el is not None else False
        has_numeric = any(parse_number(c) is not None for c in data_cells)

        if bold and not has_numeric:
            rows.append({"label": label, "indent": indent, "cells": [""] * ncols_data})
            continue

        if len(data_cells) < ncols_data:
            data_cells = data_cells + [""] * (ncols_data - len(data_cells))
            misaligned = True
        elif len(data_cells) > ncols_data:
            data_cells = data_cells[:ncols_data]
            misaligned = True

        rows.append({"label": label, "indent": indent, "cells": data_cells})
    return rows, misaligned


def _build_text(title: str | None, units: str | None, headers: list[str], rows: list[dict]) -> str:
    if title and units:
        first_line = f"{title} ({units})"
    elif title:
        first_line = title
    elif units:
        first_line = f"({units})"
    else:
        first_line = ""

    lines = [first_line] if first_line else []
    for row in rows:
        parts = []
        for i, cell in enumerate(row["cells"]):
            if not cell.strip():
                continue
            header_label = headers[i + 1] if i + 1 < len(headers) else ""
            parts.append(f"{header_label}: {cell}" if header_label else cell)
        if parts:
            lines.append(row["label"] + " | " + " | ".join(parts))
        else:
            lines.append(row["label"])
    return "\n".join(lines)


def extract_tables(tree, page_of) -> dict:
    """Extract every table in `tree`.

    `page_of(element) -> int` maps an element to its 1-based page index.

    Returns a dict:
      - "tables": data tables, each a dict with title, units, headers, rows,
        text, page_start, page_end, misaligned, xbrl_values_matched,
        xbrl_values_total, and "element" (the source <table>, for wave 2c to
        locate placement and assign id/item/section_id/position).
      - "layout_tables": the <table> elements that did not qualify as data
        tables, for wave 2a to unwrap into prose.
      - "xbrl_nonfraction_total": count of ix:nonFraction elements in the
        whole tree (outside ix:header), prose included.
      - "xbrl_nonfraction_matched": how many of those are inside some data
        table and were found in that table's parsed cells.
    """
    all_tables = [el for el in tree.iter() if _is_tag(el, "table")]
    total_nonfraction, nonfraction_by_table = _collect_nonfraction(tree)

    data_tables = []
    layout_tables = []
    matched_total = 0

    for table in all_tables:
        visible_rows = _visible_rows(table)
        rows_count = len(visible_rows)
        columns = 0
        numeric_count = 0
        row_merged_cache = []
        for tr in visible_rows:
            merged = merge_number_cells(_row_cells(tr))
            row_merged_cache.append(merged)
            columns = max(columns, len(merged))
            numeric_count += sum(1 for c in merged if parse_number(c) is not None)

        is_data = rows_count >= 3 and columns >= 2 and numeric_count >= 6
        if not is_data:
            layout_tables.append(table)
            continue

        header_rows = _header_rows(visible_rows)
        body_rows = visible_rows[len(header_rows) :]
        headers = _build_headers(header_rows, body_rows)
        ncols_data = max(len(headers) - 1, 0)
        rows, misaligned = _build_rows(body_rows, ncols_data)

        title, units = _title_and_units(table)
        text = _build_text(title, units, headers, rows)

        page = page_of(table)
        records = nonfraction_by_table.get(id(table), [])
        matched = _count_xbrl_matches(records, headers, rows)
        matched_total += matched

        data_tables.append(
            {
                "element": table,
                "title": title,
                "units": units,
                "headers": headers,
                "rows": rows,
                "text": text,
                "page_start": page,
                "page_end": page,
                "misaligned": misaligned,
                "xbrl_values_matched": matched,
                "xbrl_values_total": len(records),
            }
        )

    return {
        "tables": data_tables,
        "layout_tables": layout_tables,
        "xbrl_nonfraction_total": total_nonfraction,
        "xbrl_nonfraction_matched": matched_total,
    }
