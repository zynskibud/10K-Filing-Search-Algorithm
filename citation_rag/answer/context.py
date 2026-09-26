"""Small-to-big context assembly (wave 7a contract, point 1).

Groups the reranked top-k results by `section_id`, fetches each section's
full text from `sections` (and, for table chunks, the full table text from
`tables_parsed`), and assembles `ContextBlock`s in best-rank order under a
20,000-token budget (the bge-small tokenizer, the same ruler used for
chunking). A section whose text exceeds 4,000 tokens is cut down to a window
of paragraphs around the matched chunks, with `[...]` marking what was
skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from citation_rag.chunk.tokens import count_tokens
from citation_rag.search import vector as _vector

DEFAULT_BUDGET_TOKENS = 20_000
DEFAULT_SECTION_WINDOW_TOKENS = 4_000

_TABLE_PLACEHOLDER_RE = re.compile(r"^\[Table: (?P<id>.+?)\]$", re.MULTILINE)
_SAFE_IDENT = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass
class ContextBlock:
    ref: int
    chunk_ids: list
    section_id: str
    accession_no: str
    company: str
    fiscal_year: int
    item: str
    section_title: str
    page_start: int
    page_end: int
    text: str


# -- DB access ---------------------------------------------------------------


def _qualify(table: str, schema: str | None) -> str:
    if schema is None:
        return table
    if not _SAFE_IDENT.fullmatch(schema):
        raise ValueError(f"unsafe schema: {schema!r}")
    return f"{schema}.{table}"


def _fetch_sections(section_ids: Sequence[str], pool=None, schema: str | None = None) -> dict[str, dict]:
    if not section_ids:
        return {}
    pool = pool or _vector.get_pool()
    table = _qualify("sections", schema)
    cols = ["id", "accession_no", "item", "title", "page_start", "page_end", "text"]
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE id = ANY(%s)", (list(section_ids),))
            rows = cur.fetchall()
        conn.commit()
    return {row[0]: dict(zip(cols, row)) for row in rows}


def _fetch_filings(accession_nos: Sequence[str], pool=None, schema: str | None = None) -> dict[str, dict]:
    if not accession_nos:
        return {}
    pool = pool or _vector.get_pool()
    table = _qualify("filings", schema)
    cols = ["accession_no", "company", "fiscal_year"]
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE accession_no = ANY(%s)", (list(accession_nos),))
            rows = cur.fetchall()
        conn.commit()
    return {row[0]: dict(zip(cols, row)) for row in rows}


def _fetch_tables(table_ids: Sequence[str], accession_no: str, pool=None, schema: str | None = None) -> dict[str, dict]:
    """Fetch `tables_parsed` rows for a section's table chunks.

    `tables_parsed.id` is stored as `{accession_no}:{table_id}` (see
    `citation_rag.index.load.load_filings`). A chunk's own `table_id` column
    (schemas.md section 4) may already carry that full form, or just the
    bare id (`t017`) -- the wave 4b/4c chunker that decides this has not
    landed as of this wave. Judgment call: try both forms, so this keeps
    working whichever convention the chunker ends up using.
    """
    if not table_ids:
        return {}
    pool = pool or _vector.get_pool()
    table = _qualify("tables_parsed", schema)
    cols = ["id", "text"]
    candidates: set[str] = set()
    for tid in table_ids:
        candidates.add(tid)
        if ":" not in tid:
            candidates.add(f"{accession_no}:{tid}")
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE id = ANY(%s)", (list(candidates),))
            rows = cur.fetchall()
        conn.commit()
    return {row[0]: dict(zip(cols, row)) for row in rows}


# -- table substitution --------------------------------------------------


def _substitute_tables(text: str, table_rows: dict[str, dict]) -> str:
    """Replace each `[Table: {id}]` placeholder line with itself followed by
    the table's full text, for any table row we found."""
    if not table_rows:
        return text

    by_bare: dict[str, str] = {}
    for row in table_rows.values():
        row_id = row["id"]
        bare = row_id.split(":", 1)[-1] if ":" in row_id else row_id
        by_bare[bare] = row["text"]

    def _sub(m: "re.Match[str]") -> str:
        table_text = by_bare.get(m.group("id"))
        if table_text is None:
            return m.group(0)
        return f"{m.group(0)}\n{table_text}"

    return _TABLE_PLACEHOLDER_RE.sub(_sub, text)


# -- window logic for oversized sections ----------------------------------


def _window_section(full_text: str, chunk_texts: Sequence[str], budget_tokens: int) -> str:
    """A window of paragraphs around the matched chunks, up to `budget_tokens`.

    Paragraphs are split on the blank-line separator (schemas.md: "Paragraphs
    are separated by one blank line"). Seed paragraphs are the ones
    containing a matched chunk's text; the window then grows outward one
    paragraph at a time, breadth-first, stopping just before it would exceed
    the budget. `[...]` marks any gap: before the first included paragraph,
    between two non-adjacent included runs, and after the last one.

    Judgment call: this operates at paragraph granularity. If a single seed
    paragraph alone is already over budget, it is still included whole
    (splitting it further would risk cutting a quotable sentence in half).
    """
    paragraphs = full_text.split("\n\n")
    n = len(paragraphs)
    if n <= 1:
        return full_text

    offsets: list[tuple[int, int]] = []
    pos = 0
    for para in paragraphs:
        offsets.append((pos, pos + len(para)))
        pos += len(para) + 2  # "\n\n"

    seeds: set[int] = set()
    for ct in chunk_texts:
        start = full_text.find(ct)
        if start == -1:
            continue
        end = start + len(ct)
        for i, (p_start, p_end) in enumerate(offsets):
            if p_end >= start and p_start <= end:
                seeds.add(i)
    if not seeds:
        seeds = {0}

    def assemble(included: set[int]) -> str:
        idxs = sorted(included)
        pieces: list[str] = []
        if idxs[0] > 0:
            pieces.append("[...]")
        prev: int | None = None
        for i in idxs:
            if prev is not None and i != prev + 1:
                pieces.append("[...]")
            pieces.append(paragraphs[i])
            prev = i
        if idxs[-1] < n - 1:
            pieces.append("[...]")
        return "\n\n".join(pieces)

    included = set(seeds)
    text = assemble(included)

    while True:
        frontier: set[int] = set()
        for i in included:
            if i - 1 >= 0 and i - 1 not in included:
                frontier.add(i - 1)
            if i + 1 < n and i + 1 not in included:
                frontier.add(i + 1)
        if not frontier:
            break
        grew = False
        for c in sorted(frontier):
            trial = included | {c}
            trial_text = assemble(trial)
            if count_tokens(trial_text) <= budget_tokens:
                included = trial
                text = trial_text
                grew = True
        if not grew:
            break

    return text


# -- assembly ---------------------------------------------------------------


def build_context(
    results: Sequence[Any],
    *,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    section_window_tokens: int = DEFAULT_SECTION_WINDOW_TOKENS,
    pool: Any = None,
    schema: str | None = None,
    fetch_sections: "Callable[[Sequence[str]], dict[str, dict]] | None" = None,
    fetch_filings: "Callable[[Sequence[str]], dict[str, dict]] | None" = None,
    fetch_tables: "Callable[[Sequence[str], str], dict[str, dict]] | None" = None,
) -> list[ContextBlock]:
    """Assemble ContextBlocks from reranked results, best rank first.

    `fetch_sections`/`fetch_filings`/`fetch_tables` default to real Postgres
    lookups (via `pool`/`schema`); tests may inject fakes instead.
    """
    fetch_sections = fetch_sections or (lambda ids: _fetch_sections(ids, pool=pool, schema=schema))
    fetch_filings = fetch_filings or (lambda accs: _fetch_filings(accs, pool=pool, schema=schema))
    fetch_tables = fetch_tables or (lambda tids, acc: _fetch_tables(tids, acc, pool=pool, schema=schema))

    order: list[str] = []
    groups: dict[str, list] = {}
    for r in results:
        sid = getattr(r, "section_id", None)
        if not sid:
            continue
        if sid not in groups:
            groups[sid] = []
            order.append(sid)
        groups[sid].append(r)

    if not order:
        return []

    sections = fetch_sections(order)
    accession_nos = sorted({sections[sid]["accession_no"] for sid in order if sid in sections})
    filings = fetch_filings(accession_nos)

    blocks: list[ContextBlock] = []
    total = 0
    ref = 1
    for sid in order:
        section = sections.get(sid)
        if section is None:
            continue
        members = groups[sid]
        chunk_ids = [r.chunk_id for r in members]
        table_ids = [r.table_id for r in members if getattr(r, "table_id", None)]

        text = section["text"]
        if table_ids:
            table_rows = fetch_tables(table_ids, section["accession_no"])
            text = _substitute_tables(text, table_rows)

        tok = count_tokens(text)
        if tok > section_window_tokens:
            chunk_texts = [r.text for r in members]
            text = _window_section(text, chunk_texts, section_window_tokens)
            tok = count_tokens(text)

        if blocks and total + tok > budget_tokens:
            break

        filing = filings.get(section["accession_no"], {})
        blocks.append(
            ContextBlock(
                ref=ref,
                chunk_ids=chunk_ids,
                section_id=sid,
                accession_no=section["accession_no"],
                company=filing.get("company", ""),
                fiscal_year=filing.get("fiscal_year", 0),
                item=section["item"],
                section_title=section["title"],
                page_start=section["page_start"],
                page_end=section["page_end"],
                text=text,
            )
        )
        total += tok
        ref += 1

    return blocks
