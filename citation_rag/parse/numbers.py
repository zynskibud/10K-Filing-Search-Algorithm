"""Number-cell helpers for EDGAR HTML tables.

EDGAR generators split one printed number into several `<td>` cells: the
`$`, the digits, and a trailing `(` or `)` or `%` each get their own cell so
that columns line up visually. `merge_number_cells` puts those cells back
together. `parse_number` turns a printed cell into a float, or `None` when
the cell is not a number (a dash, a blank, "N/A").
"""

from __future__ import annotations

import re

# Cells that carry no number of their own and must attach to a neighbor.
_FORWARD_SPACERS = {"$", "("}  # attach to the cell that follows
_BACKWARD_SPACERS = {")", "%"}  # attach to the cell that precedes

_FOOTNOTE_RE = re.compile(r"\(\d+\)$")
_NULL_TOKENS = {"-", "‐", "‑", "‒", "–", "—", "―", "n/a", "na", "nm"}


_ZERO_WIDTH_RE = re.compile(r"[​‌‍﻿]")


def _clean(text: str) -> str:
    text = _ZERO_WIDTH_RE.sub("", text)
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def merge_number_cells(cells: list[str]) -> list[str]:
    """Join EDGAR's split number cells back into one cell each.

    A cell that is only `$`, `(`, `)`, `%`, or empty (after whitespace
    normalization) carries no content of its own. `$` and `(` attach to the
    cell that follows. `)` and `%` attach to the cell that precedes. An
    empty cell merges away and adds nothing.
    """
    result: list[str] = []
    pending_prefix = ""
    for raw in cells:
        text = raw if raw is not None else ""
        stripped = _clean(text)
        if stripped in _FORWARD_SPACERS:
            pending_prefix += stripped
            continue
        if stripped in _BACKWARD_SPACERS:
            if result:
                result[-1] = result[-1] + stripped
            else:
                pending_prefix += stripped
            continue
        if stripped == "":
            continue
        result.append(pending_prefix + _clean(text))
        pending_prefix = ""
    if pending_prefix:
        # A leading connector with nothing after it: keep it as its own cell
        # rather than drop information silently.
        result.append(pending_prefix)
    return result


def parse_number(s: str | None) -> float | None:
    """Parse a printed table cell into a float, or None if it is not a number.

    Handles thousands separators ("1,204"), parenthesized negatives
    ("(1,204)"), dash/blank nulls ("-", "—", ""), percent signs, dollar
    signs, and a trailing footnote marker after a number ("1,204(1)").
    """
    if s is None:
        return None
    text = _clean(s)
    if text == "":
        return None

    # A footnote marker follows a number, e.g. "1,204(1)". Do not strip it
    # when the whole cell is itself a parenthesized negative, e.g. "(1)".
    m = _FOOTNOTE_RE.search(text)
    if m and m.start() > 0:
        text = text[: m.start()].strip()

    if text.lower() in _NULL_TOKENS:
        return None

    # A dollar sign can precede a parenthesized negative, e.g. "$(9,741)"
    # (merge_number_cells produces this when "$" and "(1,204)" are separate
    # EDGAR cells). Strip it before checking for the parens.
    text = re.sub(r"^\$\s*", "", text)

    negative = False
    if text.startswith("(") and text.endswith(")") and len(text) > 1:
        negative = True
        text = text[1:-1].strip()
        text = re.sub(r"^\$\s*", "", text)  # a $ can also sit inside the parens

    text = text.replace("$", "").replace("%", "").replace(",", "").strip()
    if text == "" or text.lower() in _NULL_TOKENS:
        return None

    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value
