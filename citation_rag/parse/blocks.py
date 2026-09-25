"""Shared document-order content stream used by items.py and sections.py.

Both modules need to walk the cleaned, page-split tree as a flat sequence
of paragraph-like text blocks and whole `<table>` elements, in document
order, without descending into a table's own internals (a table is always
an atomic unit here -- either it gets unwrapped into prose text, or it is
replaced by a `[Table: id]` placeholder; either decision is made by the
caller, not by this module).
"""

from __future__ import annotations

from dataclasses import dataclass, field

BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"}
# Extra tags only consulted when looking for Item headings: a
# table-of-contents row commonly puts "Item 1." in its own <td>, separate
# from the title and page-number cells.
HEADING_EXTRA_TAGS = {"td", "th"}


def norm_text(el) -> str:
    return " ".join(el.text_content().split())


def in_table(el) -> bool:
    p = el.getparent()
    while p is not None:
        if isinstance(p.tag, str) and p.tag == "table":
            return True
        p = p.getparent()
    return False


def has_block_child(el, tags) -> bool:
    for child in el:
        ctag = child.tag
        if isinstance(ctag, str) and (ctag in tags or ctag == "table"):
            return True
    return False


@dataclass
class Block:
    element: object
    text: str
    kind: str  # "text" | "table"
    page: int = 0
    style: str = ""
    tag: str = ""


def iter_content_blocks(root, page_for):
    """Yield Block objects in document order: leaf text blocks and tables.

    Elements inside a `<table>` are never yielded individually; the table
    itself is yielded once, as a "table" block.
    """
    for el in root.iter():
        tag = el.tag
        if not isinstance(tag, str):
            continue
        if tag == "table":
            if in_table(el):
                continue  # a nested table; the outer table owns it
            yield Block(el, "", "table", page_for(el), el.get("style") or "", tag)
            continue
        if tag in BLOCK_TAGS:
            if in_table(el):
                continue
            if has_block_child(el, BLOCK_TAGS):
                continue
            text = norm_text(el)
            if text:
                yield Block(el, text, "text", page_for(el), el.get("style") or "", tag)


def iter_heading_candidates(root, page_for, max_len=200):
    """Yield Block objects for elements that could be an Item heading.

    Unlike iter_content_blocks, this also looks at <td>/<th> (a
    table-of-contents row often puts "Item 1." alone in one cell) and does
    not filter by regex here -- callers decide what counts as a heading.
    Text length is capped at max_len as the contract specifies.
    """
    tags = BLOCK_TAGS | HEADING_EXTRA_TAGS
    for el in root.iter():
        tag = el.tag
        if not isinstance(tag, str) or tag not in tags:
            continue
        if has_block_child(el, tags):
            continue
        text = norm_text(el)
        if not text or len(text) >= max_len:
            continue
        yield Block(el, text, "text", page_for(el), el.get("style") or "", tag)


def unwrap_table_to_text(table_el) -> str:
    """Flatten a table's rows into plain prose lines: label | cell | cell."""
    lines = []
    for tr in table_el.iter("tr"):
        cells = [c for c in tr if isinstance(c.tag, str) and c.tag in ("td", "th")]
        parts = [norm_text(c) for c in cells]
        parts = [p for p in parts if p]
        if parts:
            lines.append(" | ".join(parts))
    return "\n".join(lines)


def nearest_table_ancestor(el):
    p = el.getparent()
    while p is not None:
        if isinstance(p.tag, str) and p.tag == "table":
            return p
        p = p.getparent()
    return None
