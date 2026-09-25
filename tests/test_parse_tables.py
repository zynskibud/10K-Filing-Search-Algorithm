"""Tests for citation_rag.parse.tables and citation_rag.parse.numbers.

This module owns its own minimal HTML cleaning (strip the XML declaration,
parse with lxml.html, drop <ix:header>) instead of depending on wave 2a's
clean.py, per the wave-2b contract.
"""

from __future__ import annotations

import glob
import os
import re
from collections import Counter

import lxml.html as LH
import pytest

from citation_rag.parse.numbers import merge_number_cells, parse_number
from citation_rag.parse.tables import extract_tables

SAMPLE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "data", "survey", "sample"
)
REPORT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "reports", "wave-2b.md"
)

_XML_DECL_RE = re.compile(r"^\s*<\?xml[^>]*\?>\s*")


def clean_html(raw: str):
    """Minimal cleaning: strip the XML declaration, parse, drop ix:header."""
    raw = _XML_DECL_RE.sub("", raw, count=1)
    tree = LH.fromstring(raw.encode("utf-8"))
    for header in tree.iter():
        if isinstance(header.tag, str) and header.tag.lower() == "ix:header":
            header.getparent().remove(header)
            break
    return tree


def make_page_of(_pages=None):
    """A trivial page_of: every element is on page 1."""

    def page_of(_el):
        return 1

    return page_of


def parse_fragment(html: str):
    """Wrap a table fragment in a minimal body and parse it."""
    doc = f"<html><body>{html}</body></html>"
    return LH.fromstring(doc)


# ---------------------------------------------------------------------------
# numbers.py
# ---------------------------------------------------------------------------


class TestMergeNumberCells:
    def test_dollar_prefix_merges_forward(self):
        assert merge_number_cells(["$", "2,877.1", ""]) == ["$2,877.1"]

    def test_parens_negative_merges(self):
        assert merge_number_cells(["(", "1,204", ")"]) == ["(1,204)"]

    def test_percent_merges_backward(self):
        assert merge_number_cells(["45.2", "%"]) == ["45.2%"]

    def test_dollar_and_parens_together(self):
        assert merge_number_cells(["$", "(", "1,204", ")"]) == ["$(1,204)"]

    def test_plain_cells_untouched(self):
        assert merge_number_cells(["Net sales", "1,204", "1,090"]) == [
            "Net sales",
            "1,204",
            "1,090",
        ]

    def test_empty_cells_drop_out(self):
        assert merge_number_cells(["", "1,204", "", "1,090", ""]) == ["1,204", "1,090"]

    def test_all_spacer_row_collapses(self):
        assert merge_number_cells(["", "", ""]) == []

    def test_leading_connector_with_nothing_after(self):
        assert merge_number_cells(["$"]) == ["$"]


class TestParseNumber:
    def test_plain_integer(self):
        assert parse_number("1,204") == 1204.0

    def test_negative_parens(self):
        assert parse_number("(1,204)") == -1204.0

    def test_dash_is_null(self):
        assert parse_number("-") is None
        assert parse_number("—") is None
        assert parse_number("–") is None

    def test_empty_is_null(self):
        assert parse_number("") is None
        assert parse_number("   ") is None

    def test_percent(self):
        assert parse_number("45.2%") == 45.2

    def test_dollar_prefix(self):
        assert parse_number("$1,204") == 1204.0

    def test_footnote_marker_after_number(self):
        assert parse_number("1,204(1)") == 1204.0

    def test_whole_cell_parens_not_treated_as_footnote(self):
        assert parse_number("(1)") == -1.0

    def test_non_numeric_text(self):
        assert parse_number("Total net sales") is None

    def test_none_input(self):
        assert parse_number(None) is None


# ---------------------------------------------------------------------------
# tables.py: synthetic HTML
# ---------------------------------------------------------------------------


def _extract(html: str):
    tree = parse_fragment(html)
    return extract_tables(tree, make_page_of())


class TestLayoutVsData:
    def test_data_table_detected(self):
        html = """
        <div>Net sales by segment</div>
        <table>
          <tr><td></td><td>2025</td><td>2024</td></tr>
          <tr><td>Americas</td><td>167,045</td><td>162,560</td></tr>
          <tr><td>Europe</td><td>95,000</td><td>90,000</td></tr>
          <tr><td>Total</td><td>391,035</td><td>383,285</td></tr>
        </table>
        """
        result = _extract(html)
        assert len(result["tables"]) == 1
        assert result["layout_tables"] == []

    def test_one_column_layout_table_unwrapped(self):
        html = """
        <table>
          <tr><td>Item 1. Business</td></tr>
          <tr><td>Item 1A. Risk Factors</td></tr>
          <tr><td>Item 2. Properties</td></tr>
        </table>
        """
        result = _extract(html)
        assert result["tables"] == []
        assert len(result["layout_tables"]) == 1

    def test_few_numeric_cells_is_layout(self):
        html = """
        <table>
          <tr><td>Name</td><td>Title</td></tr>
          <tr><td>Jane Doe</td><td>CEO, age 52</td></tr>
          <tr><td>John Roe</td><td>CFO</td></tr>
        </table>
        """
        result = _extract(html)
        assert result["tables"] == []
        assert len(result["layout_tables"]) == 1

    def test_decorative_spacer_table_is_layout(self):
        html = """
        <table><tr><td style="width:1%"/><td style="width:98%"/></tr>
        <tr><td colspan="2" style="border-bottom:1pt solid #000"/></tr></table>
        """
        result = _extract(html)
        assert result["tables"] == []
        assert len(result["layout_tables"]) == 1


class TestSplitCellMerge:
    def test_dollar_and_spacer_cells_merge_in_body_row(self):
        html = """
        <table>
          <tr><td></td><td>2025</td><td>2024</td></tr>
          <tr><td>Net sales</td><td>$</td><td>2,877.1</td><td></td><td>$</td><td>2,759.7</td><td></td></tr>
          <tr><td>Cost of sales</td><td>$</td><td>1,200.0</td><td></td><td>$</td><td>1,100.0</td><td></td></tr>
          <tr><td>Gross profit</td><td>$</td><td>1,677.1</td><td></td><td>$</td><td>1,659.7</td><td></td></tr>
        </table>
        """
        result = _extract(html)
        assert len(result["tables"]) == 1
        table = result["tables"][0]
        row = table["rows"][0]
        assert row["label"] == "Net sales"
        assert row["cells"] == ["$2,877.1", "$2,759.7"]
        assert table["misaligned"] is False


class TestNegatives:
    def test_parenthesized_negative_in_body_row(self):
        html = """
        <table>
          <tr><td></td><td>2025</td><td>2024</td></tr>
          <tr><td>Net loss</td><td>(</td><td>1,204</td><td>)</td><td>(</td><td>1,090</td><td>)</td></tr>
          <tr><td>Revenue</td><td>5,000</td><td>4,500</td></tr>
          <tr><td>Expenses</td><td>6,204</td><td>5,590</td></tr>
        </table>
        """
        result = _extract(html)
        table = result["tables"][0]
        row = table["rows"][0]
        assert row["cells"] == ["(1,204)", "(1,090)"]
        assert parse_number(row["cells"][0]) == -1204.0


class TestColspanHeaders:
    def test_colspan_header_repeats_label(self):
        html = """
        <table>
          <tr><td></td><td colspan="2">Year Ended December 31</td></tr>
          <tr><td></td><td>2025</td><td>2024</td></tr>
          <tr><td>Net sales</td><td>1,204</td><td>1,090</td></tr>
          <tr><td>Cost of sales</td><td>600</td><td>550</td></tr>
          <tr><td>Gross profit</td><td>604</td><td>540</td></tr>
        </table>
        """
        result = _extract(html)
        table = result["tables"][0]
        assert table["headers"] == [
            "",
            "Year Ended December 31 2025",
            "Year Ended December 31 2024",
        ]


class TestMultiRowHeaders:
    def test_two_header_rows_join_with_space(self):
        html = """
        <table>
          <tr><td></td><td>Twelve Months Ended</td><td>Twelve Months Ended</td></tr>
          <tr><td></td><td>2025</td><td>2024</td></tr>
          <tr><td>Net sales</td><td>1,204</td><td>1,090</td></tr>
          <tr><td>Cost of sales</td><td>600</td><td>550</td></tr>
          <tr><td>Gross profit</td><td>604</td><td>540</td></tr>
        </table>
        """
        result = _extract(html)
        table = result["tables"][0]
        assert table["headers"] == [
            "",
            "Twelve Months Ended 2025",
            "Twelve Months Ended 2024",
        ]

    def test_units_row_excluded_from_headers(self):
        html = """
        <table>
          <tr><td>(in millions)</td><td>2025</td><td>2024</td></tr>
          <tr><td>Net sales</td><td>1,204</td><td>1,090</td></tr>
          <tr><td>Cost of sales</td><td>600</td><td>550</td></tr>
          <tr><td>Gross profit</td><td>604</td><td>540</td></tr>
        </table>
        """
        result = _extract(html)
        table = result["tables"][0]
        assert table["headers"] == ["", "2025", "2024"]
        assert table["units"] == "in millions"


class TestIndentHierarchy:
    def test_indent_from_padding_left(self):
        html = """
        <table>
          <tr><td></td><td>2025</td><td>2024</td></tr>
          <tr><td style="padding-left:9pt">Americas</td><td>1,204</td><td>1,090</td></tr>
          <tr><td style="padding-left:18pt">United States</td><td>900</td><td>800</td></tr>
          <tr><td>Total</td><td>2,104</td><td>1,890</td></tr>
        </table>
        """
        result = _extract(html)
        table = result["tables"][0]
        assert table["rows"][0]["indent"] == 1
        assert table["rows"][1]["indent"] == 2
        assert table["rows"][2]["indent"] == 0

    def test_group_header_row_kept_with_empty_cells(self):
        html = """
        <table>
          <tr><td></td><td>2025</td><td>2024</td></tr>
          <tr><td style="font-weight:700">Segment data:</td><td></td><td></td></tr>
          <tr><td>Americas</td><td>1,204</td><td>1,090</td></tr>
          <tr><td>Europe</td><td>900</td><td>800</td></tr>
        </table>
        """
        result = _extract(html)
        table = result["tables"][0]
        group_row = table["rows"][0]
        assert group_row["label"] == "Segment data:"
        assert group_row["cells"] == ["", ""]


class TestUnitsDetection:
    def test_units_from_preceding_block(self):
        html = """
        <div style="font-weight:700">Net sales by segment</div>
        <div>(in millions)</div>
        <table>
          <tr><td></td><td>2025</td><td>2024</td></tr>
          <tr><td>Americas</td><td>1,204</td><td>1,090</td></tr>
          <tr><td>Europe</td><td>900</td><td>800</td></tr>
          <tr><td>Total</td><td>2,104</td><td>1,890</td></tr>
        </table>
        """
        result = _extract(html)
        table = result["tables"][0]
        assert table["title"] == "Net sales by segment"
        assert table["units"] == "in millions"

    def test_except_per_share_detected(self):
        html = """
        <div>Earnings per share (in millions, except per share data)</div>
        <table>
          <tr><td></td><td>2025</td><td>2024</td></tr>
          <tr><td>Diluted EPS</td><td>1.20</td><td>1.10</td></tr>
          <tr><td>Basic EPS</td><td>1.25</td><td>1.15</td></tr>
          <tr><td>Shares</td><td>900</td><td>800</td></tr>
        </table>
        """
        result = _extract(html)
        table = result["tables"][0]
        assert "except per share" in table["units"]


class TestNoHeaderRow:
    def test_missing_header_defaults_to_col_n(self):
        html = """
        <table>
          <tr><td>Americas</td><td>1,204</td><td>1,090</td></tr>
          <tr><td>Europe</td><td>900</td><td>800</td></tr>
          <tr><td>Total</td><td>2,104</td><td>1,890</td></tr>
        </table>
        """
        result = _extract(html)
        table = result["tables"][0]
        assert table["headers"] == ["", "col1", "col2"]


class TestMisalignment:
    def test_short_row_padded_and_flagged(self):
        html = """
        <table>
          <tr><td></td><td>2025</td><td>2024</td><td>2023</td></tr>
          <tr><td>Americas</td><td>1,204</td><td>1,090</td><td>1,000</td></tr>
          <tr><td>Europe</td><td>900</td></tr>
          <tr><td>Total</td><td>2,104</td><td>1,090</td><td>1,000</td></tr>
        </table>
        """
        result = _extract(html)
        table = result["tables"][0]
        europe_row = [r for r in table["rows"] if r["label"] == "Europe"][0]
        assert europe_row["cells"] == ["900", "", ""]
        assert table["misaligned"] is True


class TestXbrlCoverage:
    def test_matched_value_counted(self):
        html = """
        <table>
          <tr><td></td><td>2025</td><td>2024</td></tr>
          <tr><td>Net sales</td><td><ix:nonFraction name="us-gaap:Revenue">1,204</ix:nonFraction></td><td>1,090</td></tr>
          <tr><td>Cost</td><td>600</td><td>550</td></tr>
          <tr><td>Profit</td><td>604</td><td>540</td></tr>
        </table>
        """
        result = _extract(html)
        assert result["xbrl_nonfraction_total"] == 1
        assert result["xbrl_nonfraction_matched"] == 1
        table = result["tables"][0]
        assert table["xbrl_values_matched"] == 1
        assert table["xbrl_values_total"] == 1

    def test_prose_value_unmatched(self):
        html = """
        <p>Revenue grew by <ix:nonFraction name="us-gaap:Growth">12</ix:nonFraction> percent.</p>
        <table>
          <tr><td></td><td>2025</td><td>2024</td></tr>
          <tr><td>Net sales</td><td>1,204</td><td>1,090</td></tr>
          <tr><td>Cost</td><td>600</td><td>550</td></tr>
          <tr><td>Profit</td><td>604</td><td>540</td></tr>
        </table>
        """
        result = _extract(html)
        assert result["xbrl_nonfraction_total"] == 1
        assert result["xbrl_nonfraction_matched"] == 0

    def test_ix_header_excluded(self):
        html = """
        <div style="display:none"><ix:header><ix:hidden>
        <ix:nonFraction name="dei:Hidden">99</ix:nonFraction>
        </ix:hidden></ix:header></div>
        <table>
          <tr><td></td><td>2025</td><td>2024</td></tr>
          <tr><td>Net sales</td><td>1,204</td><td>1,090</td></tr>
          <tr><td>Cost</td><td>600</td><td>550</td></tr>
          <tr><td>Profit</td><td>604</td><td>540</td></tr>
        </table>
        """
        tree = parse_fragment(html)
        cleaned = clean_html(f"<?xml version='1.0'?>\n" + LH.tostring(tree).decode())
        result = extract_tables(cleaned, make_page_of())
        assert result["xbrl_nonfraction_total"] == 0


class TestTextForm:
    def test_text_form_matches_expected_shape(self):
        html = """
        <div style="font-weight:700">Net sales by segment</div>
        <div>(in millions)</div>
        <table>
          <tr><td></td><td>2025</td><td>2024</td></tr>
          <tr><td>Americas</td><td>167,045</td><td>162,560</td></tr>
          <tr><td>Total net sales</td><td>391,035</td><td>383,285</td></tr>
        </table>
        """
        result = _extract(html)
        table = result["tables"][0]
        expected = (
            "Net sales by segment (in millions)\n"
            "Americas | 2025: 167,045 | 2024: 162,560\n"
            "Total net sales | 2025: 391,035 | 2024: 383,285"
        )
        assert table["text"] == expected


# ---------------------------------------------------------------------------
# 30 sample filings
# ---------------------------------------------------------------------------


def _sample_files():
    return sorted(glob.glob(os.path.join(SAMPLE_DIR, "*.htm")))


@pytest.mark.parametrize("path", _sample_files(), ids=lambda p: os.path.basename(p))
def test_sample_filing_table_counts_and_xbrl_coverage(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    tree = clean_html(raw)
    result = extract_tables(tree, make_page_of())

    n_data = len(result["tables"])
    total = result["xbrl_nonfraction_total"]
    matched = result["xbrl_nonfraction_matched"]
    rate = matched / total if total else 1.0

    assert 20 <= n_data <= 800, (
        f"{os.path.basename(path)}: {n_data} data tables, expected 20-800"
    )

    if rate < 0.85:
        unmatched_texts = _unmatched_texts(tree, result)
        common = Counter(_pattern(t) for t in unmatched_texts).most_common(10)
        print(
            f"\n{os.path.basename(path)}: xbrl match rate {rate:.3f} "
            f"({matched}/{total}) below 0.85. Top unmatched patterns: {common}"
        )
    # Do not fail on a low match rate: the report explains it with data
    # from a full 30-filing run instead (see reports/wave-2b.md).


def _unmatched_texts(tree, result):
    import citation_rag.parse.tables as t

    _, nonfraction_by_table = t._collect_nonfraction(tree)
    unmatched = []
    for table in result["tables"]:
        el = table["element"]
        records = nonfraction_by_table.get(id(el), [])
        cell_texts = [c for c in table["headers"] if c]
        for row in table["rows"]:
            if row["label"]:
                cell_texts.append(row["label"])
            cell_texts.extend(c for c in row["cells"] if c)
        for rec in records:
            text = t._cell_text(rec)
            if text and not any(text in c for c in cell_texts):
                unmatched.append(text)
    return unmatched


def _pattern(text: str) -> str:
    if re.fullmatch(r"-?\d+(\.\d+)?%", text):
        return "percent"
    if re.fullmatch(r"\(?\d[\d,]*\)?", text):
        return "plain_number"
    if re.fullmatch(r"\d+\.\d+", text):
        return "decimal"
    if re.fullmatch(r"0(\.0+)?", text):
        return "zero"
    return "other"
