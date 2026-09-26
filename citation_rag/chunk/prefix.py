"""Context prefix for a chunk (wave 4b, contract point 2; plan tab section 5).

`make_prefix` builds the metadata line: "Company | FY2025 | Item 1A |
<section title>". `make_embed_text` puts it in front of the chunk's own
text with a newline. The stored `text` never carries the prefix -- only
`embed_text` does -- so citations quote the filing's own words only.
"""

from __future__ import annotations


def _fiscal_year_label(filing: dict) -> str:
    fy = filing.get("fiscal_year")
    if fy:
        return f"FY{fy}"
    # Judgment call: no `fiscal_year` on record. Fall back to the year in
    # `fiscal_year_end` (for example "2025-12-31" -> "FY2025"), else give
    # up with a plain placeholder rather than crashing the whole run.
    fy_end = filing.get("fiscal_year_end") or ""
    year = fy_end.split("-")[0] if fy_end else ""
    return f"FY{year}" if year else "FY?"


def make_prefix(filing: dict, item: str, section_title: str) -> str:
    company = filing.get("company") or "Unknown company"
    fy_label = _fiscal_year_label(filing)
    title = (section_title or "").strip()
    return f"{company} | {fy_label} | Item {item} | {title}"


def make_embed_text(prefix: str, text: str) -> str:
    return f"{prefix}\n{text}"
