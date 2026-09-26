"""Tests for wave 4b (chunking). Contract: reports/contracts/wave-4.md
section 4b, bullet 6.

Runs on `tests/fixtures/parsed/0001111111-25-000001.json` (hand-built, small)
plus the 3 real parsed filings under `tests/fixtures/parsed_samples/`
(produced from `data/survey/sample/` with `citation_rag.parse.filing`, per
the 4b contract's development instructions, since `data/parsed/` was still
being written by another wave when this was built).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from citation_rag.chunk import prose, tables
from citation_rag.chunk import run as chunk_run
from citation_rag.chunk.prefix import make_prefix
from citation_rag.chunk.tokens import count_tokens

FIXTURE_DIR = Path(__file__).parent / "fixtures"
PARSED_FIXTURE = FIXTURE_DIR / "parsed" / "0001111111-25-000001.json"
SAMPLE_DIR = FIXTURE_DIR / "parsed_samples"

ALL_PATHS = [PARSED_FIXTURE] + sorted(SAMPLE_DIR.glob("*.json"))
DEFAULT_TABLE_OPTION = 2


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def filings() -> list[dict]:
    return [_load(p) for p in ALL_PATHS]


def _find_section(filing: dict, section_id: str | None) -> dict | None:
    if section_id is None:
        return None
    for item in filing.get("items", []):
        for section in item.get("sections", []):
            if section["id"] == section_id:
                return section
    return None


def _pages_map(filing: dict) -> dict:
    return {p["index"]: p.get("label") for p in filing.get("pages", [])}


def _all_sections(filing: dict):
    for item in filing.get("items", []):
        for section in item.get("sections", []):
            yield item["item"], section


# ---------------------------------------------------------------------------
# 1. No chunk over its limit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy,limit", [("s1", 512), ("s2", 512), ("s3", 512), ("s4", 8000)])
def test_no_chunk_over_limit(filings, strategy, limit):
    for filing in filings:
        chunks = prose.chunk_filing(filing, strategy, DEFAULT_TABLE_OPTION)
        assert chunks, f"{filing['accession_no']}/{strategy}: expected at least one chunk"
        for c in chunks:
            assert c["token_count"] <= limit, (filing["accession_no"], strategy, c["token_count"])


def test_table_chunks_within_default_target(filings):
    """Table chunks (option 2) are split by rows so a single part rarely
    needs to exceed the 400-token target; not a hard contract cap, but a
    sanity check that splitting is actually shrinking parts."""
    for filing in filings:
        for table in filing.get("tables", []):
            section = _find_section(filing, table["section_id"])
            title = (section or {}).get("title") or table.get("title") or ""
            parts = tables.make_table_chunks(filing, table, 2, title)
            if count_tokens(table["text"]) > 400:
                assert len(parts) > 1


# ---------------------------------------------------------------------------
# 2. s2, s3, s4 never cross a section
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fn_name", ["chunk_section_s2", "chunk_section_s3", "chunk_section_s4"]
)
def test_never_crosses_section(filings, fn_name):
    fn = getattr(prose, fn_name)
    for filing in filings:
        tables_by_id = {t["id"]: t for t in filing.get("tables", [])}
        pages = _pages_map(filing)
        for item_num, section in _all_sections(filing):
            section_prose = tables.resolve_section_prose(section, tables_by_id, DEFAULT_TABLE_OPTION)
            if not section_prose:
                continue
            chunks = fn(filing, item_num, section, tables_by_id, DEFAULT_TABLE_OPTION, pages)
            for c in chunks:
                assert c["section_id"] == section["id"]
                assert 0 <= c["_char_start"] <= c["_char_end"] <= len(section_prose)
                assert c["text"] == section_prose[c["_char_start"] : c["_char_end"]]


# ---------------------------------------------------------------------------
# 3. s3 never splits a paragraph unless it alone exceeds 400 tokens
# ---------------------------------------------------------------------------


def test_s3_only_splits_over_limit_paragraphs(filings):
    """A chunk's bounds either match one packed group of whole paragraphs
    (never split), or fall inside a single paragraph that alone exceeds
    400 tokens (the only case the contract allows a paragraph to be cut)."""
    for filing in filings:
        tables_by_id = {t["id"]: t for t in filing.get("tables", [])}
        pages = _pages_map(filing)
        for item_num, section in _all_sections(filing):
            section_prose = tables.resolve_section_prose(section, tables_by_id, DEFAULT_TABLE_OPTION)
            if not section_prose:
                continue
            paragraphs = prose.split_paragraphs(section_prose)
            groups = prose._pack(paragraphs, 400)
            group_bounds = {prose._finalize_group_text(section_prose, g)[1:] for g in groups}
            oversized_paragraphs = [p for p in paragraphs if count_tokens(p[0]) > 400]
            chunks = prose.chunk_section_s3(filing, item_num, section, tables_by_id, DEFAULT_TABLE_OPTION, pages)
            for c in chunks:
                bounds = (c["_char_start"], c["_char_end"])
                if bounds in group_bounds:
                    continue  # one whole paragraph, or several joined -- never split
                overlapping = [
                    p for p in oversized_paragraphs if p[1] <= c["_char_start"] and c["_char_end"] <= p[2]
                ]
                assert len(overlapping) == 1, (filing["accession_no"], section["id"], c)


# ---------------------------------------------------------------------------
# 4. Full prose coverage, s2/s3/s4 (overlap excluded for s2), and s1
# ---------------------------------------------------------------------------


def _assert_covers(chunks_sorted, prose_text, allow_overlap):
    """Chunks partition `prose_text` end to end. For a strategy with no
    overlap, a small gap between two consecutive chunks is allowed only
    when it is pure whitespace -- the blank line between two paragraphs
    that landed in different chunks, which belongs to neither (a
    judgment call: paragraph separators are structural, not content, so
    "every character of prose" is checked up to that whitespace)."""
    assert chunks_sorted[0]["_char_start"] == 0
    assert chunks_sorted[-1]["_char_end"] == len(prose_text)
    for a, b in zip(chunks_sorted, chunks_sorted[1:]):
        if allow_overlap:
            assert b["_char_start"] <= a["_char_end"], "gap between chunks"
        else:
            assert b["_char_start"] >= a["_char_end"], "unexpected overlap between chunks"
            gap = prose_text[a["_char_end"] : b["_char_start"]]
            assert gap.strip() == "", f"non-whitespace content dropped between chunks: {gap!r}"
        assert b["_char_start"] > a["_char_start"]
        assert b["_char_end"] > a["_char_end"]


@pytest.mark.parametrize(
    "fn_name,allow_overlap", [("chunk_section_s2", True), ("chunk_section_s3", False), ("chunk_section_s4", False)]
)
def test_prose_coverage_per_section(filings, fn_name, allow_overlap):
    fn = getattr(prose, fn_name)
    for filing in filings:
        tables_by_id = {t["id"]: t for t in filing.get("tables", [])}
        pages = _pages_map(filing)
        for item_num, section in _all_sections(filing):
            section_prose = tables.resolve_section_prose(section, tables_by_id, DEFAULT_TABLE_OPTION)
            if not section_prose:
                continue
            chunks = fn(filing, item_num, section, tables_by_id, DEFAULT_TABLE_OPTION, pages)
            chunks_sorted = sorted(chunks, key=lambda c: c["_char_start"])
            _assert_covers(chunks_sorted, section_prose, allow_overlap)
            for c in chunks_sorted:
                assert c["text"] == section_prose[c["_char_start"] : c["_char_end"]]


def test_s1_covers_all_prose(filings):
    for filing in filings:
        tables_by_id = {t["id"]: t for t in filing.get("tables", [])}
        pages = _pages_map(filing)
        full_prose, _spans = prose.build_filing_prose(filing, tables_by_id, DEFAULT_TABLE_OPTION)
        if not full_prose:
            continue
        chunks = prose.chunk_filing_s1(filing, tables_by_id, DEFAULT_TABLE_OPTION, pages)
        chunks_sorted = sorted(chunks, key=lambda c: c["_char_start"])
        _assert_covers(chunks_sorted, full_prose, allow_overlap=True)
        for c in chunks_sorted:
            assert c["text"] == full_prose[c["_char_start"] : c["_char_end"]]


# ---------------------------------------------------------------------------
# 5. Table split parts each start with the header line
# ---------------------------------------------------------------------------


def test_table_parts_start_with_header_line(filings):
    saw_a_split = False
    for filing in filings:
        for table in filing.get("tables", []):
            section = _find_section(filing, table["section_id"])
            title = (section or {}).get("title") or table.get("title") or ""
            header_line = table["text"].split("\n")[0]
            parts = tables.make_table_chunks(filing, table, 2, title)
            if len(parts) > 1:
                saw_a_split = True
            for part in parts:
                assert part["text"].split("\n")[0] == header_line
                assert part["table_id"] == table["id"]
    assert saw_a_split, "no table in the fixtures was large enough to exercise splitting"


# ---------------------------------------------------------------------------
# 6. The prefix is in embed_text and not in text
# ---------------------------------------------------------------------------


def test_prefix_in_embed_text_not_in_text_prose(filings):
    for filing in filings:
        for strategy in prose.STRATEGIES:
            chunks = prose.chunk_filing(filing, strategy, DEFAULT_TABLE_OPTION)
            for c in chunks:
                section = _find_section(filing, c["section_id"])
                title = section["title"] if section else ""
                expected_prefix = make_prefix(filing, c["item"] or "", title)
                assert c["embed_text"] == expected_prefix + "\n" + c["text"]
                assert expected_prefix not in c["text"]


def test_prefix_in_embed_text_not_in_text_tables(filings):
    for filing in filings:
        for table in filing.get("tables", []):
            section = _find_section(filing, table["section_id"])
            title = (section or {}).get("title") or table.get("title") or ""
            expected_prefix = make_prefix(filing, table["item"], title)
            for c in tables.make_table_chunks(filing, table, 2, title):
                assert c["embed_text"].startswith(expected_prefix + "\n")
                assert expected_prefix not in c["text"]


# ---------------------------------------------------------------------------
# 7. Determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", ["s1", "s2", "s3", "s4"])
def test_determinism(tmp_path, strategy):
    paths = [str(p) for p in ALL_PATHS]
    out1 = tmp_path / "a.jsonl"
    out2 = tmp_path / "b.jsonl"
    chunk_run.run(strategy, DEFAULT_TABLE_OPTION, str(out1), parsed_dir=".", filings=paths)
    chunk_run.run(strategy, DEFAULT_TABLE_OPTION, str(out2), parsed_dir=".", filings=paths)
    assert out1.read_text(encoding="utf-8") == out2.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


def test_cli_writes_valid_jsonl(tmp_path):
    out = tmp_path / "cli.jsonl"
    chunk_run.main(
        [
            "--strategy",
            "s3",
            "--table-option",
            "2",
            "--out",
            str(out),
            "--filings",
            *[str(p) for p in ALL_PATHS],
        ]
    )
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines
    row_keys = set(chunk_run.SCHEMA_ROW_ORDER)
    for line in lines:
        row = json.loads(line)
        assert set(row.keys()) == row_keys
        assert "_char_start" not in row and "_char_end" not in row and "_scope" not in row
