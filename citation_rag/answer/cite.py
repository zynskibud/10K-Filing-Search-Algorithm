"""Citation check in code (wave 7a contract, point 4).

For each citation the model gave: the `ref` must exist in the sent blocks,
and the `quote` (whitespace-normalized) must be a substring of that block's
text. Every `[n]` marker in the answer prose must have a matching citation
entry. An `answerable=false` answer must carry zero citations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

_MARKER_RE = re.compile(r"\[(\d+)\]")


def _normalize_ws(s: str) -> str:
    return " ".join(s.split())


@dataclass
class CitationCheck:
    valid: bool
    invalid_refs: list
    unquoted_markers: list
    quotes_not_found: list
    refused_with_citations: bool
    citation_map: dict = field(default_factory=dict)


def check_citations(
    answer: str,
    citations: Sequence[dict],
    answerable: bool,
    blocks: Sequence[Any],
) -> CitationCheck:
    """Check an answer's citations against the blocks that were sent to the LLM.

    `blocks` are `ContextBlock`s (or anything with the same `ref`,
    `chunk_ids`, `accession_no`, `company`, `fiscal_year`, `item`,
    `section_title`, `page_start`, `page_end`, `text` attributes).
    """
    block_by_ref = {b.ref: b for b in blocks}
    markers = {int(m) for m in _MARKER_RE.findall(answer)}

    invalid_refs: list = []
    quotes_not_found: list = []
    citation_map: dict = {}
    cited_refs: set = set()

    for c in citations:
        ref = c.get("ref")
        quote = c.get("quote", "") or ""
        cited_refs.add(ref)

        block = block_by_ref.get(ref)
        if block is None:
            invalid_refs.append(ref)
            continue

        if _normalize_ws(quote) and _normalize_ws(quote) in _normalize_ws(block.text):
            citation_map[ref] = {
                "accession_no": block.accession_no,
                "company": block.company,
                "fiscal_year": block.fiscal_year,
                "item": block.item,
                "section_title": block.section_title,
                "page_start": block.page_start,
                "page_end": block.page_end,
                "chunk_ids": block.chunk_ids,
            }
        else:
            quotes_not_found.append(ref)

    unquoted_markers = sorted(m for m in markers if m not in cited_refs)
    refused_with_citations = (not answerable) and bool(citations)

    valid = (
        not invalid_refs
        and not quotes_not_found
        and not unquoted_markers
        and not refused_with_citations
    )

    return CitationCheck(
        valid=valid,
        invalid_refs=invalid_refs,
        unquoted_markers=unquoted_markers,
        quotes_not_found=quotes_not_found,
        refused_with_citations=refused_with_citations,
        citation_map=citation_map,
    )
