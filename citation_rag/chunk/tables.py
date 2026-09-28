"""Table handling for chunking (wave 4b, contract point 4; plan tab section 5).

Tables are a separate axis from prose chunking (plan tab section 5). This
module has two jobs:

1. Decide what a `[Table: id]` placeholder line becomes in the text that
   the prose chunkers see, per `table_option`:
   - 0 (no table chunks): the placeholder is removed. The table is not
     searchable at all, only reachable through its parent section.
   - 1 (back in the flow): the placeholder is replaced by the table's own
     row-text form, as a plain paragraph, so it is cut by the ordinary
     prose strategy along with everything else.
   - 2, 3 (own chunk): the placeholder is removed from the prose (same as
     0); the table gets its own chunk(s) instead, built by
     `make_table_chunks`.
2. Build the standalone table chunk(s) for options 2 and 3, splitting a
   table that is too big for one chunk by rows, each part repeating the
   table's title/header line.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .prefix import make_embed_text, make_prefix
from .tokens import count_tokens

TABLE_PLACEHOLDER_RE = re.compile(r"[ \t]*\[Table: (t\d+)\][ \t]*\n?")

# Judgment call: table chunk-splitting target. The contract gives 400
# tokens as the general prose target and does not repeat a table-specific
# number, so the same 400-token target is reused here.
TABLE_CHUNK_LIMIT = 400

TABLE_SUMMARIES_PATH = Path("data/table_summaries.jsonl")


def strip_tables(text: str) -> str:
    """Remove every `[Table: id]` placeholder line from `text`.

    A placeholder always sits on its own line (schema section 1), with a
    blank line on each side in the common case. Deleting the line and
    collapsing the newlines around it back to a single blank line keeps
    the "paragraphs separated by one blank line" convention intact, so
    the paragraph splitter in `prose.py` never sees a stray empty
    paragraph where a table used to be.
    """
    if not text:
        return text
    collapsed = re.sub(r"\n*\[Table: t\d+\]\n*", "\n\n", text)
    return collapsed.strip()


def inline_tables(text: str, tables_by_id: dict) -> str:
    """Replace each `[Table: id]` placeholder with the table's row text.

    Used for table_option 1: the table's row-text form (schema section 1,
    `tables[].text`) is dropped in as its own paragraph, in the table's
    original position, so it flows into the surrounding prose and gets cut
    by the ordinary chunking strategy like any other paragraph.
    """
    if not text:
        return text

    def _replace(match: re.Match) -> str:
        table = tables_by_id.get(match.group(1))
        body = table["text"] if table else ""
        return f"\n\n{body}\n\n"

    replaced = re.sub(r"\n*\[Table: (t\d+)\]\n*", _replace, text)
    return replaced.strip()


def resolve_section_prose(section: dict, tables_by_id: dict, table_option: int) -> str:
    """The text a prose chunker should cut, given how tables are handled.

    `table_option` 1 inlines every table's row text back into the prose;
    every other option (0, 2, 3) strips the placeholder, since those
    tables either get no chunk (0) or their own chunk built separately by
    `make_table_chunks` (2, 3).
    """
    text = section.get("text", "") or ""
    if table_option == 1:
        return inline_tables(text, tables_by_id)
    return strip_tables(text)


# ---------------------------------------------------------------------------
# Standalone table chunks (options 2 and 3)
# ---------------------------------------------------------------------------


def _split_rows(header_line: str, row_lines: list[str], rows: list[dict], limit: int):
    """Group `row_lines` (with `rows` its 1:1 structured parallel) into
    parts, each under `limit` tokens including a repeated `header_line`.
    A single row that alone (with the header) exceeds `limit` still forms
    its own part rather than being cut mid-row (the contract asks for a
    split "by rows", not inside a row).
    """
    header_tokens = count_tokens(header_line)
    parts: list[tuple[list[str], list[dict]]] = []
    cur_lines: list[str] = []
    cur_rows: list[dict] = []
    cur_tokens = header_tokens
    # A single row wider than the limit (some filers print one row per
    # dozens of columns) is split at its cell separators into several
    # lines that each start with the row label, so no chunk exceeds the
    # limit and every piece still says which row it belongs to.
    expanded: list[tuple[str, dict]] = []
    for line, row in zip(row_lines, rows):
        if header_tokens + count_tokens(line) <= limit:
            expanded.append((line, row))
            continue
        cells = line.split(" | ")
        label = cells[0]
        budget = max(50, limit - header_tokens)
        piece: list[str] = [label]
        piece_tokens = count_tokens(label)
        for cell in cells[1:]:
            ct = count_tokens(cell) + 1
            if piece_tokens + ct > budget and len(piece) > 1:
                expanded.append((" | ".join(piece), row))
                piece, piece_tokens = [label], count_tokens(label)
            piece.append(cell)
            piece_tokens += ct
        if len(piece) > 1 or not expanded or expanded[-1][1] is not row:
            expanded.append((" | ".join(piece), row))
    row_lines, rows = [e[0] for e in expanded], [e[1] for e in expanded]
    for line, row in zip(row_lines, rows):
        line_tokens = count_tokens(line)
        prospective = cur_tokens + line_tokens
        if cur_lines and prospective > limit:
            parts.append((cur_lines, cur_rows))
            cur_lines, cur_rows = [], []
            cur_tokens = header_tokens
        cur_lines.append(line)
        cur_rows.append(row)
        cur_tokens += line_tokens
    if cur_lines:
        parts.append((cur_lines, cur_rows))
    return parts


def _labels_embed_body(header_line: str, table: dict, rows_subset: list[dict]) -> str:
    """Option 2: prefix + title + units + "Columns: " + headers + "Rows: "
    + row labels. `header_line` already carries title and units (schema
    section 1: "title and units on the first line")."""
    parts = [header_line]
    headers = [h for h in (table.get("headers") or []) if h]
    if headers:
        parts.append("Columns: " + ", ".join(headers))
    row_labels = [r.get("label", "") for r in rows_subset if r.get("label")]
    if row_labels:
        parts.append("Rows: " + ", ".join(row_labels))
    return "\n".join(parts)


@lru_cache(maxsize=1)
def _load_table_summaries() -> dict:
    """`{table_id: summary_text}` from `data/table_summaries.jsonl`, if the
    file exists. Not generated here (contract point 4, option 3): this
    only reads what another step has already written. Expected row shape:
    `{"table_id": "t017", "summary": "..."}` (or `{"id": ..., "text": ...}`
    -- both keyed forms are accepted since no producer exists yet to fix
    the exact field names).
    """
    if not TABLE_SUMMARIES_PATH.exists():
        return {}
    summaries: dict[str, str] = {}
    with TABLE_SUMMARIES_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            tid = row.get("table_id") or row.get("id")
            summary = row.get("summary") or row.get("text")
            if tid and summary:
                summaries[tid] = summary
    return summaries


def load_table_summary(table_id: str) -> str | None:
    return _load_table_summaries().get(table_id)


def make_table_chunks(filing: dict, table: dict, table_option: int, section_title: str) -> list[dict]:
    """Build the standalone chunk(s) for one table (`table_option` 2 or 3).

    Returns rich dicts (schema columns plus internal bookkeeping); the
    caller assigns `id`/`seq` and strips non-schema keys before writing.
    """
    if table_option not in (2, 3):
        return []

    accession_no = filing["accession_no"]
    prefix = make_prefix(filing, table["item"], section_title)
    pages_by_index = {p["index"]: p.get("label") for p in filing.get("pages", [])}
    page_start = table.get("page_start")
    raw_text = table.get("text", "") or ""
    lines = raw_text.split("\n")
    header_line = lines[0] if lines else ""
    row_lines = lines[1:]
    rows = table.get("rows") or []

    if count_tokens(raw_text) <= TABLE_CHUNK_LIMIT or not row_lines:
        row_groups = [(row_lines, rows)]
    else:
        row_groups = _split_rows(header_line, row_lines, rows, TABLE_CHUNK_LIMIT)

    summary = load_table_summary(table["id"]) if table_option == 3 else None

    chunks = []
    for part_lines, part_rows in row_groups:
        text = "\n".join([header_line] + part_lines) if part_lines else header_line
        if table_option == 3 and summary:
            embed_body = summary
        else:
            embed_body = _labels_embed_body(header_line, table, part_rows)
        embed_text = make_embed_text(prefix, embed_body)
        chunks.append(
            {
                "accession_no": accession_no,
                "cik": filing.get("cik"),
                "item": table["item"],
                "section_id": table["section_id"],
                "table_id": table["id"],
                "page_start": page_start,
                "page_end": table.get("page_end"),
                "page_label": pages_by_index.get(page_start) if page_start is not None else None,
                "is_table": True,
                "text": text,
                "embed_text": embed_text,
                "token_count": count_tokens(text),
            }
        )
    return chunks
