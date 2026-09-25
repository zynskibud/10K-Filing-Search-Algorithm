"""Split each Item's body into sections (wave 2a contract, deliverable 4).

A heading block is a leaf block element under 200 characters that is bold,
italic, underlined, or an all-caps line of 3+ words; in Item 8, a line
starting with "Note" followed by a number also counts. Sections under 200
characters are merged into the section that follows (or the previous one,
at the end of an Item).
"""

from __future__ import annotations

import re

from .blocks import unwrap_table_to_text

BOLD_STYLE_RE = re.compile(r"font-weight\s*:\s*(bold|[7-9]00)", re.I)
UNDERLINE_STYLE_RE = re.compile(r"text-decoration\s*:[^;]*underline", re.I)
ITALIC_STYLE_RE = re.compile(r"font-style\s*:\s*italic", re.I)
NOTE_RE = re.compile(r"^\s*note\s+\d+[a-z]?\b", re.I)

MIN_SECTION_LEN = 200


def _collect_styles(el) -> str:
    """Concatenate the block's own style with every descendant's style,
    plus a synthetic style for <b>/<strong>/<i>/<em>/<u> tags, so a heading
    line built from several styled spans is still recognized as one."""
    parts = [el.get("style") or ""]
    for child in el.iter():
        if child is el:
            continue
        tag = child.tag
        if not isinstance(tag, str):
            continue
        s = child.get("style")
        if s:
            parts.append(s)
        if tag in ("b", "strong"):
            parts.append("font-weight:bold")
        elif tag in ("i", "em"):
            parts.append("font-style:italic")
        elif tag == "u":
            parts.append("text-decoration:underline")
    return " ; ".join(parts)


def _is_bold_italic_or_underline(block) -> bool:
    combined = _collect_styles(block.element)
    return bool(
        BOLD_STYLE_RE.search(combined)
        or UNDERLINE_STYLE_RE.search(combined)
        or ITALIC_STYLE_RE.search(combined)
    )


def _is_all_caps_heading(text: str) -> bool:
    words = text.split()
    if len(words) < 3:
        return False
    letters = re.sub(r"[^A-Za-z]", "", text)
    if not letters:
        return False
    return letters == letters.upper()


def _is_heading_block(block, item_num: str) -> bool:
    if block.kind != "text":
        return False
    text = block.text
    if not text or len(text) >= 200:
        return False
    if item_num == "8" and NOTE_RE.match(text):
        return True
    if _is_bold_italic_or_underline(block):
        return True
    if _is_all_caps_heading(text):
        return True
    return False


def _render_group(group_blocks, table_ids):
    """Join a group's blocks into one text blob. table_ids maps
    id(table_element) -> table_id for tables wave 2b classified as data
    tables; anything not in that map is unwrapped into prose (the wave-2a
    stub behavior, and the schema's rule for layout tables)."""
    parts = []
    tbl_ids_used = []
    pages = []
    for b in group_blocks:
        if b.page:
            pages.append(b.page)
        if b.kind == "table":
            tid = table_ids.get(id(b.element))
            if tid:
                parts.append(f"[Table: {tid}]")
                tbl_ids_used.append(tid)
            else:
                unwrapped = unwrap_table_to_text(b.element)
                if unwrapped:
                    parts.append(unwrapped)
        else:
            parts.append(b.text)
    text = "\n\n".join(p for p in parts if p)
    pg_start = min(pages) if pages else None
    pg_end = max(pages) if pages else None
    return text, tbl_ids_used, pg_start, pg_end


def _first_not_none(a, b):
    return a if a is not None else b


def _merge_short_sections(rendered, min_len=MIN_SECTION_LEN):
    if len(rendered) <= 1:
        return rendered
    result = list(rendered)
    changed = True
    while changed and len(result) > 1:
        changed = False
        for i, r in enumerate(result):
            if len(r["text"]) >= min_len:
                continue
            if i < len(result) - 1:
                nxt = result[i + 1]
                merged = {
                    "title": nxt["title"] or r["title"],
                    "text": "\n\n".join(t for t in (r["text"], nxt["text"]) if t),
                    "page_start": _first_not_none(r["page_start"], nxt["page_start"]),
                    "page_end": _first_not_none(nxt["page_end"], r["page_end"]),
                    "tables": r["tables"] + nxt["tables"],
                }
                result[i + 1] = merged
                del result[i]
            else:
                prev = result[i - 1]
                merged = {
                    "title": prev["title"],
                    "text": "\n\n".join(t for t in (prev["text"], r["text"]) if t),
                    "page_start": _first_not_none(prev["page_start"], r["page_start"]),
                    "page_end": _first_not_none(r["page_end"], prev["page_end"]),
                    "tables": prev["tables"] + r["tables"],
                }
                result[i - 1] = merged
                del result[i]
            changed = True
            break
    return result


def split_sections(accession_no, item_num, item_rec, content_blocks, table_ids=None):
    """Build the `sections` list for one Item record.

    item_rec: dict with status/page_start/page_end/start_index/end_index/
    forced_sections, as produced by items.detect_items.
    table_ids: optional {id(table_element): table_id} for tables wave 2b
    classified as data tables; omitted tables are unwrapped into prose.
    """
    table_ids = table_ids or {}

    if item_rec.get("forced_sections") is not None:
        secs = []
        for i, text in enumerate(item_rec["forced_sections"], start=1):
            secs.append(
                {
                    "id": f"{accession_no}:{item_num}:{i:03d}",
                    "seq": i,
                    "title": "",
                    "page_start": item_rec["page_start"],
                    "page_end": item_rec["page_end"],
                    "text": text,
                    "tables": [],
                }
            )
        return secs

    if item_rec.get("start_index") is None:
        return []

    blocks = content_blocks[item_rec["start_index"] : item_rec["end_index"]]
    extra = item_rec.get("extra_index_range")
    if extra:
        blocks = blocks + content_blocks[extra[0] : extra[1]]
    if not blocks:
        return []

    groups = []  # list of [heading_text_or_None, [blocks]]
    current_heading = None
    current_blocks = []
    any_heading = False
    for b in blocks:
        if _is_heading_block(b, item_num):
            any_heading = True
            groups.append((current_heading, current_blocks))
            current_heading = b.text
            # The heading line stays in the body text too (as its first
            # paragraph), not just in the `title` field, so a citation to
            # this section is self-contained and no body text is lost.
            current_blocks = [b]
        else:
            current_blocks.append(b)
    groups.append((current_heading, current_blocks))

    if not any_heading:
        # Whole Item is one section.
        groups = [(None, blocks)]
    else:
        # Drop a leading empty pre-heading group (pure boundary artifact).
        if len(groups) > 1 and groups[0][0] is None and not groups[0][1]:
            groups = groups[1:]

    rendered = []
    for heading, group_blocks in groups:
        text, tbl_ids_used, pg_start, pg_end = _render_group(group_blocks, table_ids)
        rendered.append(
            {
                "title": heading or "",
                "text": text,
                "page_start": pg_start,
                "page_end": pg_end,
                "tables": tbl_ids_used,
            }
        )

    merged = _merge_short_sections(rendered)

    secs = []
    for i, r in enumerate(merged, start=1):
        secs.append(
            {
                "id": f"{accession_no}:{item_num}:{i:03d}",
                "seq": i,
                "title": r["title"],
                "page_start": r["page_start"] if r["page_start"] is not None else item_rec["page_start"],
                "page_end": r["page_end"] if r["page_end"] is not None else item_rec["page_end"],
                "text": r["text"],
                "tables": r["tables"],
            }
        )
    return secs
