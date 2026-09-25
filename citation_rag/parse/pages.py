"""Split a cleaned filing tree into pages (wave 2a contract, deliverable 2).

- Detects page breaks in both the legacy CSS syntax
  (``page-break-(before|after): always``) and the modern syntax
  (``break-(before|after): page``), including on ``<hr>`` elements.
- Assigns every element a 1-based page index.
- Finds the printed page label for each page (arabic, roman, or an
  ``XX-###`` series such as ``F-3``), enforces that labels increase by one
  within each series, and nulls out any label that breaks the sequence.
- Removes the label line, and any line that repeats on more than 30% of
  pages (running headers/footers), from the tree so downstream text is
  clean.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from lxml import etree

# ---------------------------------------------------------------------------
# Page-break detection
# ---------------------------------------------------------------------------

LEGACY_BREAK_RE = re.compile(r"page-break-(before|after)\s*:\s*always", re.I)
MODERN_BREAK_RE = re.compile(r"break-(before|after)\s*:\s*page", re.I)


def _break_sides(style: str) -> set[str]:
    sides: set[str] = set()
    if not style:
        return sides
    for m in LEGACY_BREAK_RE.finditer(style):
        sides.add(m.group(1).lower())
    for m in MODERN_BREAK_RE.finditer(style):
        sides.add(m.group(1).lower())
    return sides


def compute_page_of(root) -> tuple[dict, int, int]:
    """Walk ``root`` and assign every element a 1-based page index.

    Returns (page_of, n_markers, n_pages). ``page_of`` maps element ->
    page index. A break-before marker bumps the page for the element that
    carries it (and, by document-order construction, everything after it).
    A break-after marker bumps the page for everything that follows the
    element's own subtree, without affecting the element's descendants.
    """
    page_of: dict = {}
    current = 1
    n_markers = 0
    for event, el in etree.iterwalk(root, events=("start", "end")):
        style = el.get("style") if hasattr(el, "get") else None
        sides = _break_sides(style or "")
        if event == "start":
            if "before" in sides:
                current += 1
                n_markers += 1
            page_of[el] = current
        else:
            if "after" in sides:
                current += 1
                n_markers += 1
    n_pages = max(page_of.values()) if page_of else 1
    return page_of, n_markers, n_pages


def make_page_for(page_of: dict) -> Callable:
    """Build a page_of(element) -> int lookup with ancestor fallback."""

    def page_for(element) -> int:
        el = element
        while el is not None:
            if el in page_of:
                return page_of[el]
            el = el.getparent()
        return 1

    return page_for


# ---------------------------------------------------------------------------
# Leaf "lines" for label / running-header detection
# ---------------------------------------------------------------------------

BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"}


def _norm_text(el) -> str:
    return " ".join(el.text_content().split())


def _leaf_text(el) -> str | None:
    """Return the "line" text for a leaf block element or a table row.

    A table row's cells are joined with " | " so a footer laid out as a
    single-row table (company name | "Form 10-K" | page number) reads as
    one line, matching the pipe hint in the label-detection rule.
    """
    tag = el.tag
    if not isinstance(tag, str):
        return None
    if tag == "tr":
        cells = [c for c in el if isinstance(c.tag, str) and c.tag in ("td", "th")]
        parts = [_norm_text(c) for c in cells]
        parts = [p for p in parts if p]
        text = " | ".join(parts)
        return text or None
    if tag in BLOCK_TAGS:
        for child in el:
            ctag = child.tag
            if isinstance(ctag, str) and ctag in BLOCK_TAGS:
                return None  # not a leaf, it contains block children
        text = _norm_text(el)
        return text or None
    return None


def iter_page_lines(root):
    """Yield (element, text) for every leaf line, in document order."""
    for el in root.iter():
        text = _leaf_text(el)
        if text:
            yield el, text


# ---------------------------------------------------------------------------
# Label token matching
# ---------------------------------------------------------------------------

# The "F-1" series sometimes renders with spaces around the hyphen (each
# character in its own span), e.g. "F - 1"; tolerate that when matching,
# then normalize it away before the token is stored or classified.
TOKEN_PATTERN = r"(?:\d{1,3}|[A-Za-z]{1,2}\s*-\s*\d{1,3}|[ivxlcIVXLC]{1,6})"
STANDALONE_RE = re.compile(rf"^{TOKEN_PATTERN}$")
START_RE = re.compile(rf"^\s*({TOKEN_PATTERN})\b")
END_RE = re.compile(rf"\b({TOKEN_PATTERN})\s*$")

ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}


def roman_to_int(s: str) -> int | None:
    s = s.lower()
    if not s or any(ch not in ROMAN_VALUES for ch in s):
        return None
    total = 0
    prev = 0
    for ch in reversed(s):
        v = ROMAN_VALUES[ch]
        if v < prev:
            total -= v
        else:
            total += v
            prev = v
    return total if total > 0 else None


def _normalize_token(tok: str) -> str:
    """Collapse whitespace inside a matched token, e.g. "F - 2" -> "F-2"."""
    return re.sub(r"\s+", "", tok)


def match_label_token(text: str, company: str | None) -> str | None:
    """Find a page-label token in a candidate line of text."""
    stripped = text.strip()
    if STANDALONE_RE.match(stripped):
        return _normalize_token(stripped)
    if len(text) < 80:
        lower = text.lower()
        has_context = (
            "form 10-k" in lower
            or "|" in text
            or (company and company.lower() in lower)
        )
        if has_context:
            m = START_RE.match(text)
            if m:
                return _normalize_token(m.group(1))
            m = END_RE.search(text)
            if m:
                return _normalize_token(m.group(1))
    return None


def classify_token(tok: str) -> tuple[str | None, int | None]:
    """Return (series, value) for a matched label token."""
    if re.fullmatch(r"\d{1,3}", tok):
        return "arabic", int(tok)
    if re.fullmatch(r"[ivxlcIVXLC]{1,6}", tok):
        v = roman_to_int(tok)
        return ("roman", v) if v else (None, None)
    m = re.fullmatch(r"([A-Za-z]{1,2})-(\d{1,3})", tok)
    if m:
        return m.group(1).upper(), int(m.group(2))
    return None, None


def _normalize_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _remove_element_keep_tail(el) -> None:
    parent = el.getparent()
    if parent is None:
        return
    tail = el.tail
    prev = el.getprevious()
    parent.remove(el)
    if tail and tail.strip():
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail


# ---------------------------------------------------------------------------
# Public result
# ---------------------------------------------------------------------------


@dataclass
class PagesResult:
    pages: list[dict]
    n_pages: int
    n_markers: int
    page_for: Callable = field(repr=False)


def assign_pages(root, company: str | None = None) -> PagesResult:
    page_of, n_markers, n_pages = compute_page_of(root)
    page_for = make_page_for(page_of)

    lines_by_page: dict[int, list[tuple]] = {i: [] for i in range(1, n_pages + 1)}
    for el, text in iter_page_lines(root):
        pg = page_for(el)
        lines_by_page.setdefault(pg, []).append((el, text))

    # 1. Raw label candidate per page: check the last 3 lines first (page
    #    numbers are usually printed in the footer), then the first 2.
    raw: dict[int, tuple | None] = {}
    for pg in range(1, n_pages + 1):
        lines = lines_by_page.get(pg, [])
        candidates = list(reversed(lines[-3:])) + lines[:2]
        found = None
        for el, text in candidates:
            tok = match_label_token(text, company)
            if tok:
                found = (el, tok)
                break
        raw[pg] = found

    # 2. Enforce sequence per series (arabic, roman, F-, etc.); a label
    #    that does not continue its series by exactly 1 is rejected.
    last_value: dict[str, int] = {}
    labels: dict[int, str | None] = {}
    for pg in range(1, n_pages + 1):
        found = raw.get(pg)
        if not found:
            labels[pg] = None
            continue
        _el, tok = found
        series, value = classify_token(tok)
        if series is None or value is None:
            labels[pg] = None
            continue
        prev = last_value.get(series)
        if prev is None or value == prev + 1:
            labels[pg] = tok
            last_value[series] = value
        else:
            labels[pg] = None

    # 3. Running headers/footers: any line whose normalized text appears
    #    on more than 30% of pages.
    page_sets: dict[str, set[int]] = {}
    for pg, lines in lines_by_page.items():
        seen_this_page = set()
        for _el, text in lines:
            norm = _normalize_line(text)
            if norm:
                seen_this_page.add(norm)
        for norm in seen_this_page:
            page_sets.setdefault(norm, set()).add(pg)
    # A minimum absolute occurrence count (not just the 30% share) avoids
    # a degenerate result on a short document: a line unique to one page
    # of a 2-page document is already "50% of pages" by the share alone,
    # which would wrongly treat ordinary paragraphs as running headers.
    running = {
        norm
        for norm, pages in page_sets.items()
        if len(pages) >= 3 and len(pages) / n_pages > 0.3
    }

    # 4. Remove the accepted label line on each page, and every occurrence
    #    of a running line, from the tree.
    to_remove: list = []
    for pg, found in raw.items():
        if found and labels[pg] is not None:
            to_remove.append(found[0])
    for lines in lines_by_page.values():
        for el, text in lines:
            if _normalize_line(text) in running:
                to_remove.append(el)
    seen_ids = set()
    for el in to_remove:
        if id(el) in seen_ids:
            continue
        seen_ids.add(id(el))
        _remove_element_keep_tail(el)

    pages = [{"index": pg, "label": labels[pg]} for pg in range(1, n_pages + 1)]
    return PagesResult(pages=pages, n_pages=n_pages, n_markers=n_markers, page_for=page_for)
