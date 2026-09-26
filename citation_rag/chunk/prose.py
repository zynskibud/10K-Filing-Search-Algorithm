"""Prose chunking strategies (wave 4b, contract point 3; plan tab section 5).

Four strategies, all measured with the bge-small tokenizer (`tokens.py`):

- s1: fixed 400 tokens, 50-token overlap, over the whole filing's prose in
  document order, ignoring section and Item boundaries.
- s2: fixed 400 tokens, 50-token overlap, never across a section boundary.
- s3: whole paragraphs joined up to 400 tokens, never across a section or
  inside a paragraph; an over-long paragraph is split at sentence
  boundaries, then hard-split by tokens if still over 512.
- s4: whole section as one chunk, split at paragraph boundaries into
  under-8,000-token parts if the section itself is too big.

Every chunk's `text` is sliced by character offset from the exact prose
string it was cut from (never rebuilt from detokenized pieces), so it is
always an exact substring of that prose (contract's hard rule on chunk
text). Internal records also carry `_char_start`/`_char_end` (offsets into
the prose string the chunk came from) and `_scope` (which prose string
that is: `"filing"` for s1, a section id otherwise) purely for tests; the
JSONL writer in `run.py` drops both before writing a row.
"""

from __future__ import annotations

import re

from .prefix import make_embed_text, make_prefix
from .tables import resolve_section_prose
from .tokens import count_tokens, token_offsets

PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
# Contract point 3 (s3): "split at sentence boundaries (regex on '. ', '; ')".
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;])\s+")


# ---------------------------------------------------------------------------
# Splitting into paragraphs / sentences, offsets preserved
# ---------------------------------------------------------------------------


def split_paragraphs(text: str) -> list[tuple[str, int, int]]:
    """`(text, start, end)` for each blank-line-separated paragraph."""
    if not text:
        return []
    parts = []
    pos = 0
    for m in PARAGRAPH_SPLIT_RE.finditer(text):
        if m.start() > pos and text[pos : m.start()].strip():
            parts.append((text[pos : m.start()], pos, m.start()))
        pos = m.end()
    if pos < len(text) and text[pos:].strip():
        parts.append((text[pos:], pos, len(text)))
    return parts


def split_sentences(text: str) -> list[tuple[str, int, int]]:
    """`(text, start, end)` for each sentence-like piece of `text`."""
    if not text:
        return []
    pieces = []
    pos = 0
    for m in SENTENCE_SPLIT_RE.finditer(text):
        if text[pos : m.start()].strip():
            pieces.append((text[pos : m.start()], pos, m.start()))
        pos = m.end()
    if text[pos:].strip():
        pieces.append((text[pos:], pos, len(text)))
    return pieces


# ---------------------------------------------------------------------------
# Packing units (paragraphs or sentences) up to a token limit
# ---------------------------------------------------------------------------


def _pack(units: list[tuple[str, int, int]], limit: int) -> list[list[tuple[str, int, int, int]]]:
    """Group consecutive units so each group's summed token count is
    `<= limit` where possible. A unit whose own count already exceeds
    `limit` becomes a one-item group on its own (the caller splits it
    further)."""
    groups: list[list[tuple[str, int, int, int]]] = []
    current: list[tuple[str, int, int, int]] = []
    current_tokens = 0
    for text, start, end in units:
        t = count_tokens(text)
        if current and current_tokens + t > limit:
            groups.append(current)
            current = []
            current_tokens = 0
        current.append((text, start, end, t))
        current_tokens += t
    if current:
        groups.append(current)
    return groups


def _finalize_group_text(base_text: str, group: list[tuple[str, int, int, int]]) -> tuple[str, int, int]:
    start = group[0][1]
    end = group[-1][2]
    return base_text[start:end], start, end


def _hard_split(base_text: str, start: int, end: int, limit: int) -> list[tuple[str, int, int]]:
    """Cut `base_text[start:end]` into `<= limit`-token pieces by raw
    token offsets. Last resort for a run of text with no natural break."""
    sub = base_text[start:end]
    offsets = token_offsets(sub)
    pieces = []
    i = 0
    n = len(offsets)
    while i < n:
        j = min(i + limit, n)
        cs = start + offsets[i][0]
        ce = start + offsets[j - 1][1]
        pieces.append((base_text[cs:ce], cs, ce))
        i = j
    return pieces


def _split_paragraph_over_limit(
    base_text: str, para_start: int, para_end: int, limit: int, hard_cap: int
) -> list[tuple[str, int, int]]:
    """`base_text[para_start:para_end]` is one paragraph over `limit`
    tokens. Split at sentence boundaries first (packed up to `limit`
    tokens per piece); any piece still over `hard_cap` gets hard-split."""
    para_text = base_text[para_start:para_end]
    sentences = split_sentences(para_text)
    if not sentences:
        sentences = [(para_text, 0, len(para_text))]
    sentences = [(t, para_start + s, para_start + e) for t, s, e in sentences]
    groups = _pack(sentences, limit)
    pieces = []
    for group in groups:
        text, start, end = _finalize_group_text(base_text, group)
        if count_tokens(text) <= hard_cap:
            pieces.append((text, start, end))
        else:
            pieces.extend(_hard_split(base_text, start, end, limit))
    return pieces


# ---------------------------------------------------------------------------
# Fixed-window packing (s1, s2)
# ---------------------------------------------------------------------------


def _sliding_windows(offsets: list[tuple[int, int]], window: int, overlap: int) -> list[tuple[int, int]]:
    """`(char_start, char_end)` per fixed-token window over `offsets`
    (token char offsets of one contiguous text), `overlap` tokens shared
    between consecutive windows."""
    n = len(offsets)
    if n == 0:
        return []
    windows = []
    i = 0
    while True:
        j = min(i + window, n)
        windows.append((offsets[i][0], offsets[j - 1][1]))
        if j >= n:
            break
        i = j - overlap
    return windows


# ---------------------------------------------------------------------------
# Chunk records
# ---------------------------------------------------------------------------


def _record(filing: dict, item: str, section: dict, text: str, embed_text: str, pages: dict, cs: int, ce: int) -> dict:
    page_start = section.get("page_start")
    return {
        "accession_no": filing["accession_no"],
        "cik": filing.get("cik"),
        "item": item,
        "section_id": section.get("id"),
        "table_id": None,
        "page_start": page_start,
        "page_end": section.get("page_end"),
        "page_label": pages.get(page_start) if page_start is not None else None,
        "is_table": False,
        "text": text,
        "embed_text": embed_text,
        "token_count": count_tokens(text),
        "_char_start": cs,
        "_char_end": ce,
        "_scope": section.get("id"),
    }


# ---------------------------------------------------------------------------
# s1: whole filing, ignoring section/Item boundaries
# ---------------------------------------------------------------------------


def build_filing_prose(filing: dict, tables_by_id: dict, table_option: int) -> tuple[str, list[dict]]:
    """The whole filing's prose, sections joined in document order (Item
    order, then section `seq` within an Item), plus `spans`: for each
    contributing section, the `(start, end)` character range it occupies
    in the joined text."""
    parts = []
    spans = []
    pos = 0
    for item in filing.get("items", []):
        for section in item.get("sections", []):
            prose = resolve_section_prose(section, tables_by_id, table_option)
            if not prose:
                continue
            if parts:
                parts.append("\n\n")
                pos += 2
            start = pos
            parts.append(prose)
            pos += len(prose)
            spans.append({"start": start, "end": pos, "section": section, "item": item["item"]})
    return "".join(parts), spans


def _span_at(spans: list[dict], offset: int) -> dict | None:
    for s in spans:
        if s["start"] <= offset < s["end"]:
            return s
    return spans[-1] if spans else None


def _spans_overlapping(spans: list[dict], start: int, end: int) -> list[dict]:
    return [s for s in spans if s["start"] < end and s["end"] > start]


def chunk_filing_s1(
    filing: dict, tables_by_id: dict, table_option: int, pages: dict, window: int = 400, overlap: int = 50
) -> list[dict]:
    full_prose, spans = build_filing_prose(filing, tables_by_id, table_option)
    if not full_prose or not spans:
        return []
    offsets = token_offsets(full_prose)
    windows = _sliding_windows(offsets, window, overlap)
    chunks = []
    for cs, ce in windows:
        text = full_prose[cs:ce]
        start_span = _span_at(spans, cs)
        overlapping = _spans_overlapping(spans, cs, ce) or ([start_span] if start_span else [])
        page_starts = [
            s["section"].get("page_start") for s in overlapping if s["section"].get("page_start") is not None
        ]
        page_ends = [s["section"].get("page_end") for s in overlapping if s["section"].get("page_end") is not None]
        page_start = min(page_starts) if page_starts else None
        page_end = max(page_ends) if page_ends else None
        section = start_span["section"] if start_span else {}
        item = start_span["item"] if start_span else None
        prefix = make_prefix(filing, item or "", section.get("title", ""))
        embed_text = make_embed_text(prefix, text)
        chunks.append(
            {
                "accession_no": filing["accession_no"],
                "cik": filing.get("cik"),
                "item": item,
                "section_id": section.get("id"),
                "table_id": None,
                "page_start": page_start,
                "page_end": page_end,
                "page_label": pages.get(page_start) if page_start is not None else None,
                "is_table": False,
                "text": text,
                "embed_text": embed_text,
                "token_count": count_tokens(text),
                "_char_start": cs,
                "_char_end": ce,
                "_scope": "filing",
            }
        )
    return chunks


# ---------------------------------------------------------------------------
# s2: fixed window, never across a section
# ---------------------------------------------------------------------------


def chunk_section_s2(
    filing: dict,
    item: str,
    section: dict,
    tables_by_id: dict,
    table_option: int,
    pages: dict,
    window: int = 400,
    overlap: int = 50,
) -> list[dict]:
    prose = resolve_section_prose(section, tables_by_id, table_option)
    if not prose:
        return []
    offsets = token_offsets(prose)
    windows = _sliding_windows(offsets, window, overlap)
    prefix = make_prefix(filing, item, section.get("title", ""))
    chunks = []
    for cs, ce in windows:
        text = prose[cs:ce]
        embed_text = make_embed_text(prefix, text)
        chunks.append(_record(filing, item, section, text, embed_text, pages, cs, ce))
    return chunks


# ---------------------------------------------------------------------------
# s3: paragraphs joined up to 400 tokens, never across a section
# ---------------------------------------------------------------------------


def chunk_section_s3(
    filing: dict,
    item: str,
    section: dict,
    tables_by_id: dict,
    table_option: int,
    pages: dict,
    limit: int = 400,
    hard_cap: int = 512,
) -> list[dict]:
    prose = resolve_section_prose(section, tables_by_id, table_option)
    if not prose:
        return []
    paragraphs = split_paragraphs(prose)
    if not paragraphs:
        return []
    groups = _pack(paragraphs, limit)
    prefix = make_prefix(filing, item, section.get("title", ""))
    pieces: list[tuple[str, int, int]] = []
    for group in groups:
        text, start, end = _finalize_group_text(prose, group)
        if len(group) > 1 or count_tokens(text) <= limit:
            pieces.append((text, start, end))
        else:
            pieces.extend(_split_paragraph_over_limit(prose, start, end, limit, hard_cap))
    chunks = []
    for text, cs, ce in pieces:
        embed_text = make_embed_text(prefix, text)
        chunks.append(_record(filing, item, section, text, embed_text, pages, cs, ce))
    return chunks


# ---------------------------------------------------------------------------
# s4: whole section as one chunk (bge-m3 only, 8,000-token parts)
# ---------------------------------------------------------------------------


def chunk_section_s4(
    filing: dict,
    item: str,
    section: dict,
    tables_by_id: dict,
    table_option: int,
    pages: dict,
    limit: int = 8000,
) -> list[dict]:
    prose = resolve_section_prose(section, tables_by_id, table_option)
    if not prose:
        return []
    prefix = make_prefix(filing, item, section.get("title", ""))
    if count_tokens(prose) <= limit:
        pieces = [(prose, 0, len(prose))]
    else:
        paragraphs = split_paragraphs(prose) or [(prose, 0, len(prose))]
        groups = _pack(paragraphs, limit)
        pieces = []
        for group in groups:
            text, start, end = _finalize_group_text(prose, group)
            if len(group) > 1 or count_tokens(text) <= limit:
                pieces.append((text, start, end))
            else:
                # Judgment call: the contract does not describe a
                # fallback for a single paragraph over 8,000 tokens (not
                # observed in the sample filings). Reuse s3's
                # sentence-then-hard-split fallback so the 8,000-token
                # cap always holds.
                pieces.extend(_split_paragraph_over_limit(prose, start, end, limit, limit))
    chunks = []
    for text, cs, ce in pieces:
        embed_text = make_embed_text(prefix, text)
        chunks.append(_record(filing, item, section, text, embed_text, pages, cs, ce))
    return chunks


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

STRATEGIES = ("s1", "s2", "s3", "s4")


def chunk_filing(filing: dict, strategy: str, table_option: int) -> list[dict]:
    """All prose chunks for one filing under one strategy. Table chunks
    (table_option 2 or 3) are built separately by `tables.make_table_chunks`
    and appended by the caller (`run.py`)."""
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy: {strategy!r}")
    tables_by_id = {t["id"]: t for t in filing.get("tables", [])}
    pages = {p["index"]: p.get("label") for p in filing.get("pages", [])}
    if strategy == "s1":
        return chunk_filing_s1(filing, tables_by_id, table_option, pages)
    chunks = []
    for item in filing.get("items", []):
        for section in item.get("sections", []):
            if strategy == "s2":
                chunks.extend(chunk_section_s2(filing, item["item"], section, tables_by_id, table_option, pages))
            elif strategy == "s3":
                chunks.extend(chunk_section_s3(filing, item["item"], section, tables_by_id, table_option, pages))
            elif strategy == "s4":
                chunks.extend(chunk_section_s4(filing, item["item"], section, tables_by_id, table_option, pages))
    return chunks
