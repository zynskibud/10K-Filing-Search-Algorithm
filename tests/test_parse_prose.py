"""Unit tests for the wave-2a prose parser.

Most tests parse small synthetic HTML strings through the same lxml.html
parser `clean.py` uses, so they exercise pages.py/items.py/sections.py the
same way the CLI does. The last test parses all 30 wave-1a sample filings
and checks the shape the contract's definition of done asks for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lxml import html as lxml_html

from citation_rag.parse.filing import parse_filing
from citation_rag.parse.items import STANDARD_ITEMS, detect_items
from citation_rag.parse.pages import assign_pages
from citation_rag.parse.sections import split_sections

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "survey" / "sample"
SAMPLE_SURVEY = ROOT / "data" / "survey" / "sample_survey.jsonl"


def parse(html: str):
    return lxml_html.fromstring(html)


# ---------------------------------------------------------------------------
# pages.py: page splitting
# ---------------------------------------------------------------------------


def test_page_split_legacy_syntax():
    html = (
        "<html><body>"
        "<div>page one text</div>"
        '<hr style="page-break-after: always"/>'
        "<div>page two text</div>"
        "</body></html>"
    )
    tree = parse(html)
    result = assign_pages(tree)
    assert result.n_pages == 2
    assert result.n_markers == 1


def test_page_split_modern_syntax():
    html = (
        "<html><body>"
        "<div>page one text</div>"
        '<div style="break-before: page">page two text</div>'
        '<div style="break-before: page">page three text</div>'
        "</body></html>"
    )
    tree = parse(html)
    result = assign_pages(tree)
    assert result.n_pages == 3
    assert result.n_markers == 2


def test_page_split_mixed_legacy_and_modern():
    html = (
        "<html><body>"
        "<div>page one</div>"
        '<hr style="page-break-after:always"/>'
        "<div>page two</div>"
        '<div style="break-before: page;">page three</div>'
        "</body></html>"
    )
    tree = parse(html)
    result = assign_pages(tree)
    assert result.n_pages == 3


# ---------------------------------------------------------------------------
# pages.py: page labels
# ---------------------------------------------------------------------------


def test_page_labels_standalone_arabic_sequence():
    pages_html = []
    for i in range(1, 5):
        pages_html.append(f"<div>Body text for page {i}.</div><div>{i}</div>")
    html = "<html><body>" + '<hr style="page-break-after: always"/>'.join(pages_html) + "</body></html>"
    tree = parse(html)
    result = assign_pages(tree)
    labels = [p["label"] for p in result.pages]
    assert labels == ["1", "2", "3", "4"]


def test_page_label_sequence_break_is_nulled():
    # Page 3's footer says "7", which breaks the 1,2,3 sequence -> null.
    html = (
        "<html><body>"
        "<div>text</div><div>1</div>"
        '<hr style="page-break-after: always"/>'
        "<div>text</div><div>2</div>"
        '<hr style="page-break-after: always"/>'
        "<div>text</div><div>7</div>"
        "</body></html>"
    )
    tree = parse(html)
    result = assign_pages(tree)
    labels = [p["label"] for p in result.pages]
    assert labels == ["1", "2", None]


def test_page_label_f_series_with_spaced_hyphen():
    # EDGAR sometimes renders "F-1" as "F - 1" (each character its own
    # span); the label detector should still recognize the series.
    html = (
        "<html><body>"
        "<div>financial statement text</div><div>F - 1</div>"
        '<hr style="page-break-after: always"/>'
        "<div>more financial statement text</div><div>F - 2</div>"
        "</body></html>"
    )
    tree = parse(html)
    result = assign_pages(tree)
    labels = [p["label"] for p in result.pages]
    assert labels == ["F-1", "F-2"]


# ---------------------------------------------------------------------------
# items.py: TOC skipping, combined Items, incorporated by reference
# ---------------------------------------------------------------------------


def _item_heading(item, title, extra_style=""):
    return f'<div style="{extra_style}">Item {item}. {title}</div>'


def _build_10k_body(long_item1_text: str, extra_items_html: str = "") -> str:
    toc_rows = "".join(
        f"<tr><td>Item {n}.</td><td>{t}</td><td>{n}</td></tr>"
        for n, t in [("1", "Business"), ("1A", "Risk Factors"), ("2", "Properties")]
    )
    toc = f"<table>{toc_rows}</table>"
    body = (
        "<html><body>"
        f"{toc}"
        f'<div style="break-before: page">' + _item_heading("1", "Business") + "</div>"
        f"<div>{long_item1_text}</div>"
        + _item_heading("1A", "Risk Factors")
        + "<div>Some risk factor text that is reasonably long so it is not merged away by the short section rule and stands as its own body of the item for the purposes of this synthetic fixture.</div>"
        + extra_items_html
        + "</body></html>"
    )
    return body


def test_toc_entries_are_skipped_not_counted_as_items():
    long_text = "Business description. " * 200  # > 1500 chars
    html = _build_10k_body(long_text)
    tree = parse(html)
    pages_result = assign_pages(tree)
    items, content_blocks, stats = detect_items(tree, pages_result.page_for, pages_result.pages, None)
    assert stats["toc_headings_skipped"] >= 2
    item1 = next(it for it in items if it["item"] == "1")
    assert item1["status"] == "present"


def test_combined_items_create_two_entries():
    long_text = "Business description. " * 200
    combined_heading = _item_heading("7 and 7A", "")
    # Use the combined-item phrasing the contract calls out explicitly.
    combined_heading = "<div>Items 7 and 7A.</div>"
    html = (
        "<html><body>"
        f'<div style="break-before: page">' + _item_heading("1", "Business") + "</div>"
        f"<div>{long_text}</div>"
        + combined_heading
        + "<div>Quantitative and qualitative market risk discussion text that is long enough to not be merged into a neighboring section by the short-section rule in sections.py.</div>"
        + "</body></html>"
    )
    tree = parse(html)
    pages_result = assign_pages(tree)
    items, content_blocks, stats = detect_items(tree, pages_result.page_for, pages_result.pages, None)
    assert stats["combined_items"] == 1
    item7 = next(it for it in items if it["item"] == "7")
    item7a = next(it for it in items if it["item"] == "7A")
    assert item7["status"] == "present"
    assert item7a["status"] == "present"
    accession_no = "0000000000-00-000000"
    sections7a = split_sections(accession_no, "7A", item7a, content_blocks)
    assert len(sections7a) == 1
    assert sections7a[0]["text"] == "See Item 7."


def test_incorporated_by_reference_short_body():
    long_text = "Business description. " * 200
    html = (
        "<html><body>"
        f'<div style="break-before: page">' + _item_heading("1", "Business") + "</div>"
        f"<div>{long_text}</div>"
        + _item_heading("11", "Executive Compensation")
        + "<div>The information required by this Item is incorporated herein by reference to our definitive proxy statement.</div>"
        + _item_heading("12", "Security Ownership")
        + "<div>Placeholder.</div>"
        + "</body></html>"
    )
    tree = parse(html)
    pages_result = assign_pages(tree)
    items, content_blocks, stats = detect_items(tree, pages_result.page_for, pages_result.pages, None)
    item11 = next(it for it in items if it["item"] == "11")
    assert item11["status"] == "incorporated_by_reference"


def test_not_required_omission_short_body():
    long_text = "Business description. " * 200
    html = (
        "<html><body>"
        f'<div style="break-before: page">' + _item_heading("1", "Business") + "</div>"
        f"<div>{long_text}</div>"
        + _item_heading("1B", "Unresolved Staff Comments")
        + "<div>None.</div>"
        + _item_heading("2", "Properties")
        + "<div>Placeholder property description that is not short.</div>"
        + "</body></html>"
    )
    tree = parse(html)
    pages_result = assign_pages(tree)
    items, content_blocks, stats = detect_items(tree, pages_result.page_for, pages_result.pages, None)
    item1b = next(it for it in items if it["item"] == "1B")
    assert item1b["status"] == "not_required"


def test_smaller_reporting_company_omits_1a_without_heading():
    long_text = "Business description. " * 200
    html = (
        "<html><body>"
        f'<div style="break-before: page">' + _item_heading("1", "Business") + "</div>"
        f"<div>{long_text}</div>"
        + _item_heading("2", "Properties")
        + "<div>Placeholder property description that is not short.</div>"
        + "</body></html>"
    )
    tree = parse(html)
    pages_result = assign_pages(tree)
    items, content_blocks, stats = detect_items(
        tree, pages_result.page_for, pages_result.pages, "Non-accelerated filer<br>Smaller reporting company"
    )
    item1a = next(it for it in items if it["item"] == "1A")
    assert item1a["status"] == "not_required"


def test_filer_category_br_joined_split():
    from citation_rag.parse.items import split_filer_category

    assert split_filer_category("Non-accelerated filer<br>Smaller reporting company<br>Emerging growth company") == [
        "Non-accelerated filer",
        "Smaller reporting company",
        "Emerging growth company",
    ]
    assert split_filer_category(None) == []
    assert split_filer_category("") == []


# ---------------------------------------------------------------------------
# sections.py: splitting on headings, merging short sections, tables unwrap
# ---------------------------------------------------------------------------


def test_section_splitting_on_bold_headings():
    long_text = "Business description. " * 200
    html = (
        "<html><body>"
        f'<div style="break-before: page">' + _item_heading("1", "Business") + "</div>"
        f"<div>{long_text}</div>"
        + _item_heading("1A", "Risk Factors")
        + '<div style="font-weight:bold">We face significant competition in our markets.</div>'
        + "<div>" + "Competition text. " * 30 + "</div>"
        + '<div style="font-weight:bold">Our supply chain depends on a small number of vendors.</div>'
        + "<div>" + "Supply chain text. " * 30 + "</div>"
        + _item_heading("1B", "Unresolved Staff Comments")
        + "<div>None.</div>"
        + "</body></html>"
    )
    tree = parse(html)
    pages_result = assign_pages(tree)
    items, content_blocks, stats = detect_items(tree, pages_result.page_for, pages_result.pages, None)
    item1a = next(it for it in items if it["item"] == "1A")
    accession_no = "0000000000-00-000000"
    sections = split_sections(accession_no, "1A", item1a, content_blocks)
    assert len(sections) == 2
    assert sections[0]["title"].startswith("We face significant competition")
    assert sections[0]["text"].startswith("We face significant competition")
    assert sections[1]["title"].startswith("Our supply chain")


def test_short_sections_are_merged_forward():
    long_text = "Business description. " * 200
    html = (
        "<html><body>"
        f'<div style="break-before: page">' + _item_heading("1", "Business") + "</div>"
        f"<div>{long_text}</div>"
        + _item_heading("1A", "Risk Factors")
        + '<div style="font-weight:bold">Short heading one</div>'
        + "<div>Tiny.</div>"
        + '<div style="font-weight:bold">Short heading two</div>'
        + "<div>" + "Real content that is long enough to stand on its own as a section. " * 10 + "</div>"
        + _item_heading("1B", "Unresolved Staff Comments")
        + "<div>None.</div>"
        + "</body></html>"
    )
    tree = parse(html)
    pages_result = assign_pages(tree)
    items, content_blocks, stats = detect_items(tree, pages_result.page_for, pages_result.pages, None)
    item1a = next(it for it in items if it["item"] == "1A")
    accession_no = "0000000000-00-000000"
    sections = split_sections(accession_no, "1A", item1a, content_blocks)
    # The short first section merges into the one that follows.
    assert len(sections) == 1
    assert "Short heading one" in sections[0]["text"]
    assert "Real content" in sections[0]["text"]


def test_item_with_no_headings_is_one_section():
    long_text = "Business description. " * 200
    html = (
        "<html><body>"
        f'<div style="break-before: page">' + _item_heading("1", "Business") + "</div>"
        f"<div>{long_text}</div>"
        + _item_heading("2", "Properties")
        + "<div>" + "Our properties are located in several states. " * 20 + "</div>"
        + "</body></html>"
    )
    tree = parse(html)
    pages_result = assign_pages(tree)
    items, content_blocks, stats = detect_items(tree, pages_result.page_for, pages_result.pages, None)
    item2 = next(it for it in items if it["item"] == "2")
    accession_no = "0000000000-00-000000"
    sections = split_sections(accession_no, "2", item2, content_blocks)
    assert len(sections) == 1
    assert sections[0]["seq"] == 1
    assert sections[0]["id"] == f"{accession_no}:2:001"


# ---------------------------------------------------------------------------
# clean.py: ix:* handling, comment tail preservation
# ---------------------------------------------------------------------------


def test_clean_document_unwraps_ix_and_keeps_comment_tail(tmp_path):
    from citation_rag.parse.clean import clean_document

    html = (
        "<html><body>"
        "<div>Some intro text.</div>"
        '<div><ix:nonFraction name="us-gaap:Assets" contextref="c1" scale="3" sign="-">1,234</ix:nonFraction></div>'
        "<ix:header><div>hidden header stuff</div></ix:header>"
        "<!-- Field: PageNo -->7<!-- Field: /Sequence -->"
        "</body></html>"
    )
    path = tmp_path / "filing.htm"
    path.write_text(html, encoding="utf-8")
    tree, records = clean_document(path)
    assert len(records) == 1
    assert records[0]["name"] == "us-gaap:Assets"
    assert records[0]["scale"] == "3"
    text = tree.text_content()
    assert "1,234" in text  # ix:nonFraction unwrapped, text kept
    assert "hidden header stuff" not in text  # ix:header dropped entirely
    assert "7" in text  # comment's tail text survives comment removal


# ---------------------------------------------------------------------------
# Full sample: all 30 wave-1a filings
# ---------------------------------------------------------------------------


def _load_sample_rows():
    if not SAMPLE_SURVEY.exists():
        return []
    return [json.loads(line) for line in SAMPLE_SURVEY.open(encoding="utf-8")]


@pytest.mark.skipif(not SAMPLE_DIR.exists(), reason="sample filings not present")
def test_all_sample_filings_produce_22_items_in_order():
    rows = _load_sample_rows()
    assert rows, "sample_survey.jsonl should have 30 rows"
    failures = []
    for row in rows:
        cik = row["cik"]
        matches = list(SAMPLE_DIR.glob(f"{cik}_*.htm"))
        if not matches:
            failures.append((row["accession_no"], "file not found"))
            continue
        meta = {
            "accession_no": row["accession_no"],
            "cik": cik,
            "company": row["company"],
            "ticker": None,
            "fiscal_year_end": None,
            "fiscal_year": None,
            "filed_date": None,
            "filer_category": row.get("category"),
            "sic": row.get("sic"),
            "source_url": row.get("primary_doc_url"),
        }
        try:
            doc = parse_filing(matches[0], meta)
        except Exception as e:  # pragma: no cover - reported, not raised
            failures.append((row["accession_no"], f"{type(e).__name__}: {e}"))
            continue
        item_nums = [it["item"] for it in doc["items"]]
        if item_nums != STANDARD_ITEMS:
            failures.append((row["accession_no"], f"items not in standard order: {item_nums}"))
    assert not failures, f"{len(failures)} sample filing(s) failed to produce 22 ordered Items: {failures}"
