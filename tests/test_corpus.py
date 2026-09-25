"""Tests for Wave 1b corpus download and selection."""

import json
import gzip
from pathlib import Path
from collections import Counter

import pytest


MANIFEST_PATH = Path("data/raw/manifest.jsonl")
CORPUS_PATH = Path("data/raw/corpus.jsonl")


def load_jsonl(path):
    """Load all rows from a JSONL file."""
    rows = []
    if not path.exists():
        return rows
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def test_manifest_exists():
    """Manifest should exist and have rows."""
    assert MANIFEST_PATH.exists(), "manifest.jsonl does not exist"
    rows = load_jsonl(MANIFEST_PATH)
    assert len(rows) >= 1300, f"manifest.jsonl has only {len(rows)} rows, need >= 1300"


def test_manifest_no_duplicate_accession():
    """Manifest should have no duplicate accession_no."""
    rows = load_jsonl(MANIFEST_PATH)
    accessions = [r.get("accession_no") for r in rows]
    duplicates = [a for a, c in Counter(accessions).items() if c > 1]
    assert not duplicates, f"Manifest has duplicate accession_no: {duplicates}"


def test_manifest_no_duplicate_cik():
    """Manifest should have no duplicate cik."""
    rows = load_jsonl(MANIFEST_PATH)
    ciks = [r.get("cik") for r in rows]
    duplicates = [c for c, cnt in Counter(ciks).items() if cnt > 1]
    assert not duplicates, f"Manifest has duplicate cik: {duplicates}"


def test_corpus_exists():
    """Corpus should exist and have exactly 1,100 rows."""
    assert CORPUS_PATH.exists(), "corpus.jsonl does not exist"
    rows = load_jsonl(CORPUS_PATH)
    assert len(rows) == 1100, f"corpus.jsonl has {len(rows)} rows, expected 1100"


def test_corpus_no_duplicate_accession():
    """Corpus should have no duplicate accession_no."""
    rows = load_jsonl(CORPUS_PATH)
    accessions = [r.get("accession_no") for r in rows]
    duplicates = [a for a, c in Counter(accessions).items() if c > 1]
    assert not duplicates, f"Corpus has duplicate accession_no: {duplicates}"


def test_corpus_no_duplicate_cik():
    """Corpus should have no duplicate cik."""
    rows = load_jsonl(CORPUS_PATH)
    ciks = [r.get("cik") for r in rows]
    duplicates = [c for c, cnt in Counter(ciks).items() if cnt > 1]
    assert not duplicates, f"Corpus has duplicate cik: {duplicates}"


def test_corpus_all_have_page_markers():
    """All corpus rows should have has_page_markers = true."""
    rows = load_jsonl(CORPUS_PATH)
    for i, row in enumerate(rows):
        assert row.get("has_page_markers") is True, \
            f"Row {i} (accession {row.get('accession_no')}) has has_page_markers != true"


def test_corpus_files_exist_and_match_size():
    """Every corpus file should exist and match bytes_gz."""
    rows = load_jsonl(CORPUS_PATH)
    for i, row in enumerate(rows):
        path_str = row.get("path")
        bytes_gz = row.get("bytes_gz")
        assert path_str, f"Row {i} has no path"
        assert bytes_gz, f"Row {i} has no bytes_gz"

        path = Path(path_str)
        assert path.exists(), f"Row {i}: file {path} does not exist"
        actual_size = path.stat().st_size
        assert actual_size == bytes_gz, \
            f"Row {i}: {path} has size {actual_size}, expected {bytes_gz}"


def test_corpus_files_start_with_valid_header():
    """Every corpus file should start with <?xml, <html, or <!DOCTYPE."""
    rows = load_jsonl(CORPUS_PATH)
    valid_headers = (b"<?xml", b"<html", b"<!DOCTYPE")

    for i, row in enumerate(rows):
        path_str = row.get("path")
        path = Path(path_str)

        # Read first 500 bytes
        with gzip.open(path, "rb") as f:
            header = f.read(500)

        # Check if header starts with valid prefix (case-insensitive)
        header_lower = header.lower()
        valid = False
        for prefix in valid_headers:
            if header_lower.startswith(prefix.lower()):
                valid = True
                break

        assert valid, f"Row {i}: {path} does not start with <?xml, <html, or <!DOCTYPE"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
