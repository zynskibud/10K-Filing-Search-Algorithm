"""Detect Form 10-K Item headings (wave 2a contract, deliverable 3).

Produces, for each of the 22 standard Items, a record with its status
(present / absent / not_required / incorporated_by_reference), its part,
its title, its page range, and (for present Items) the slice of the shared
content-block stream that holds its body -- which sections.py then splits
into sections.
"""

from __future__ import annotations

import re

from .blocks import (
    BLOCK_TAGS,
    HEADING_EXTRA_TAGS,
    Block,
    _is_inline_styled,
    has_block_child,
    in_table,
    nearest_table_ancestor,
    norm_text,
    unwrap_table_to_text,
)

STANDARD_ITEMS = [
    "1", "1A", "1B", "1C", "2", "3", "4",
    "5", "6", "7", "7A", "8", "9", "9A", "9B", "9C",
    "10", "11", "12", "13", "14",
    "15", "16",
]

ITEM_PARTS = {
    "1": "I", "1A": "I", "1B": "I", "1C": "I", "2": "I", "3": "I", "4": "I",
    "5": "II", "6": "II", "7": "II", "7A": "II", "8": "II", "9": "II",
    "9A": "II", "9B": "II", "9C": "II",
    "10": "III", "11": "III", "12": "III", "13": "III", "14": "III",
    "15": "IV", "16": "IV",
}

ITEM_TITLES = {
    "1": "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "1C": "Cybersecurity",
    "2": "Properties",
    "3": "Legal Proceedings",
    "4": "Mine Safety Disclosures",
    "5": "Market for Registrant's Common Equity, Related Stockholder Matters and Issuer Purchases of Equity Securities",
    "6": "[Reserved]",
    "7": "Management's Discussion and Analysis of Financial Condition and Results of Operations",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements and Supplementary Data",
    "9": "Changes in and Disagreements with Accountants on Accounting and Financial Disclosure",
    "9A": "Controls and Procedures",
    "9B": "Other Information",
    "9C": "Disclosure Regarding Foreign Jurisdictions that Prevent Inspections",
    "10": "Directors, Executive Officers and Corporate Governance",
    "11": "Executive Compensation",
    "12": "Security Ownership of Certain Beneficial Owners and Management and Related Stockholder Matters",
    "13": "Certain Relationships and Related Transactions, and Director Independence",
    "14": "Principal Accountant Fees and Services",
    "15": "Exhibits, Financial Statement Schedules",
    "16": "Form 10-K Summary",
}

SMALLER_REPORTING_OMITTABLE = {"1A", "1B", "6", "7A"}

# "," is accepted alongside the usual punctuation because some filers write
# the Part/Item prefix as "Part I, Item 1." (Equitable Holdings' real
# filing) instead of "PART I ITEM 1." or "PART I. ITEM 1." -- without it,
# the comma broke `_PART_PREFIX`'s optional separator, which made the whole
# anchored match fail for every Item heading in that filing (not just the
# Part prefix), since ITEM_RE requires "ITEM" to start right where the
# unmatched prefix left off.
_SEP = r"[.,:\-–—]"
_PART_PREFIX = rf"(?:PART\s+[IV]+\s*{_SEP}?\s*)?"

# Wave 2d: "[IL]TEM" (not just "ITEM") tolerates a leading-letter typo some
# filers' own source HTML actually contains -- a lowercase "l" in place of
# capital "I" (1-800-Flowers' real filing literally spells the word "ltem
# 1A." at its Item 1A heading). re.I already folds I/i; "L" does not fold
# to "I", so it must be listed explicitly. This is a real, if rare, typo
# class (an "I"/"l" mix-up), not a per-filer special case -- any filer
# that makes this exact typo is covered.
_ITEM_WORD = r"[IL]TEMS?"
# Wave 2d: the item-number group also accepts "I" (or "i") as a stand-in
# for the digit "1" specifically before a lettered sub-item -- NewLake
# Capital's real filing heads its Item 1A "ITEM IA. RISK FACTORS" (capital
# "I" typo'd for "1"). Restricted to the "1[A-C]" shape only, so it can
# never widen a two-digit Item number like "10" or "11".
_NUM_RE = rf"(?:\d{{1,2}}[A-C]?|I[A-C])"

# (?!\.\d) rejects an EDGAR Form 8-K style decimal sub-item reference
# such as "Item 5.02." (a cross-reference inside a 10-K's own text), which
# would otherwise look like a heading for Item 5.
ITEM_RE = re.compile(
    # "ITEMS?": some filers write "Items 15. Exhibits..." for a single
    # Item, not just for a genuine two-Item combination (which COMBINED_RE
    # matches first, so this tolerance never steals a real combined
    # heading -- COMBINED_RE is always tried before falling back to this
    # pattern). Found via Organon & Co.'s real filing, where "Items 15."
    # otherwise matched neither regex and Item 15 was never detected.
    rf"^\s*{_PART_PREFIX}{_ITEM_WORD}\s+({_NUM_RE})(?!\.\d)\s*{_SEP}?\s*(.*)$", re.I
)
COMBINED_RE = re.compile(
    rf"^\s*{_PART_PREFIX}{_ITEM_WORD}\s+({_NUM_RE})(?!\.\d)\.?\s+(?:AND|&)\s+"
    rf"({_NUM_RE})(?!\.\d)\.?\s*(.*)$",
    re.I,
)

# ---------------------------------------------------------------------------
# Wave 2e: a body heading that names the Item's number and canonical SEC
# title, but never uses the word "Item" at all -- Spruce Power Holding
# Corp's real filing heads its risk-factors section "1A. Risk Factors" as
# its own block; "Item 1A" (with the word) shows up only in the table of
# contents and in cross-references elsewhere in the document. ITEM_RE
# requires "Item" (or the I/l typo forms wave 2d added), so this heading
# was never detected at all, and the missing heading made Item 1A `absent`.
# General rule, not a per-filing special case: any block under 200
# characters shaped like "<number><sep><canonical title>", matched against
# the title's first 3 words so a longer real heading still counts, and
# gated on the title itself so an ordinary numbered sentence or a price
# ("5. $1,000,000 offering") cannot match.
# ---------------------------------------------------------------------------

_NUMBERED_HEADING_SEP = r"[.:\-–—]"
_NUMBERED_HEADING_NUM_RE = re.compile(rf"^\s*(\d{{1,2}}[A-C]?)\s*{_NUMBERED_HEADING_SEP}\s*(.*)$")

# Item 6 has been "[Reserved]" since the SEC dropped "Selected Financial
# Data" as a required Item in 2021 (see ITEM_TITLES's own comment history);
# a numbered-only heading with no word "Item" can carry either title
# depending on the filing's vintage, so both are accepted as Item 6's
# canonical title here specifically.
_NUMBERED_HEADING_TITLE_OVERRIDES = {"6": ["Reserved", "Selected Financial Data"]}


def _numbered_heading_titles(item_num: str) -> list[str]:
    if item_num in _NUMBERED_HEADING_TITLE_OVERRIDES:
        return _NUMBERED_HEADING_TITLE_OVERRIDES[item_num]
    return [ITEM_TITLES.get(item_num, "").strip("[]")]


def _title_prefix_re(title: str, n_words: int = 3) -> re.Pattern | None:
    """A case-insensitive regex matching the title's first `n_words` words,
    anchored at the start of the candidate's trailing text."""
    words = [w for w in title.split()[:n_words] if w]
    if not words:
        return None
    pattern = r"\s+".join(re.escape(w) for w in words)
    return re.compile(rf"^\s*{pattern}\b", re.I)


_NUMBERED_HEADING_TITLE_RE = {
    item_num: [p for p in (_title_prefix_re(t) for t in _numbered_heading_titles(item_num)) if p]
    for item_num in STANDARD_ITEMS
}


def _match_numbered_title_heading(text: str):
    """Match "<number><sep><canonical title>" with no word "Item" anywhere.

    Returns a match object with the same two-group shape as ITEM_RE
    (group(1) = raw item number, group(2) = trailing text) so callers can
    treat it identically to an ITEM_RE match; returns None if `text` is not
    shaped like a number followed by a separator, or if the trailing text
    does not open with that item's own canonical title.
    """
    m = _NUMBERED_HEADING_NUM_RE.match(text)
    if not m:
        return None
    item_num = normalize_item_num(m.group(1))
    title_res = _NUMBERED_HEADING_TITLE_RE.get(item_num)
    if not title_res:
        return None
    trailing = m.group(2)
    if not any(tre.match(trailing) for tre in title_res):
        return None
    return m


# The contract's phrase list is "not required", "not applicable", "none",
# "reserved", "omitted"; "n/a" is added as the near-universal abbreviation
# of "not applicable" for exactly this disclaimer (Item 4 and Item 9C are
# both routinely answered with a bare "N/A." for a filer with no mining
# operations or foreign-jurisdiction inspection issues), which otherwise
# left a real "N/A." Item body classified "present" and failing
# no_empty_sections purely for being an abbreviation rather than the
# spelled-out phrase (found via Dream Homes & Development Corp. and
# Aditxt's real filings).
NOT_REQUIRED_RE = re.compile(
    r"\b(not applicable|n/a|none|not required|reserved|omitted)\b", re.I
)
INCORPORATED_RE = re.compile(r"incorporated (?:herein )?by (?:this )?reference", re.I)
# A short stub that points at another Item, or another page of this same
# document, rather than saying "incorporated by reference" in so many
# words -- for example Item 8's "See financial statements included in Item
# 15 'Exhibits, Financial Statement Schedules' of this Annual Report."
# (Rush Street Interactive's real filing puts its full financial statement
# package inside Item 15's own body and simply points Item 8 at it) or
# "See index to Consolidated Financial Statements on page 45." (International
# Flavors & Fragrances' real filing). Both are functionally the same as
# incorporation by reference -- the content lives elsewhere in this same
# document -- just phrased as a plain cross-reference.
SEE_OTHER_ITEM_RE = re.compile(
    r"\bsee\b.{0,80}\b(?:item\s+\d|page\s+[a-z]?-?\d|index to)", re.I | re.S
)
INDEX_ROW_RE = re.compile(
    r"^ITEM\s+(\d{1,2}[A-C]?)\.?\s+(.*?)\s+(\d{1,3})(?:\s*[-–—]\s*\d{1,3})?\s*$",
    re.I,
)
FIN_STMT_MARKER_RE = re.compile(
    r"(report of independent registered public accounting firm"
    r"|index to (?:the )?(?:consolidated )?financial statements"
    r"|consolidated balance sheets?"
    r"|consolidated statements? of (?:operations|income|financial position"
    r"|cash flows|stockholders|changes in))",
    re.I,
)
FIN_STMT_STUB_MAX_CHARS = 3000

_ALL_TAGS = BLOCK_TAGS | HEADING_EXTRA_TAGS

_QUOTE_CHARS = ('"', "'", "“", "‘", "”", "’")


def _is_cross_reference_quote(trailing: str | None) -> bool:
    """A real Item heading's title starts with the title's own words (for
    example "RISK FACTORS"). A plain paragraph that merely mentions the
    Item in passing sometimes quotes its title right back, and does so as
    the very first sentence of its own block element -- for example
    'Item 1A. "Risk Factors," as well as elsewhere in this Annual Report
    ...' -- which otherwise matches ITEM_RE / COMBINED_RE exactly like a
    real heading (found via a bank holding company sample filing, where
    this false heading's `found[...]` overwrite silently discarded the
    real, much larger Item 1A body and truncated Item 8's). Reject a match
    whose trailing text opens with a quotation mark.
    """
    if not trailing:
        return False
    return trailing.lstrip()[:1] in _QUOTE_CHARS


def normalize_item_num(raw: str) -> str:
    num = raw.upper().replace(" ", "")
    # A leading "I" typo'd for the digit "1" (see _NUM_RE above) still
    # needs to resolve to the real Item number ("IA" -> "1A") so it looks
    # up correctly everywhere else (ITEM_TITLES, ITEM_PARTS, STANDARD_ITEMS).
    if num[:1] == "I" and len(num) > 1 and num[1] in "ABC":
        num = "1" + num[1:]
    return num


def clean_title(text: str) -> str:
    return text.strip(" .:-–— ").strip()


def split_filer_category(category: str | None) -> list[str]:
    """filer_category is a <br>-joined string; split before comparing."""
    if not category:
        return []
    parts = re.split(r"<br\s*/?>", category, flags=re.I)
    return [p.strip() for p in parts if p.strip()]


def is_smaller_reporting(categories: list[str]) -> bool:
    return any("smaller reporting" in c.lower() for c in categories)


# ---------------------------------------------------------------------------
# Building the content + heading streams
# ---------------------------------------------------------------------------


def build_streams(root, page_for):
    """One preorder walk producing (content_blocks, headings).

    content_blocks: list[Block], paragraphs and whole tables, document
    order, never descending into a table's own cells.
    headings: list[dict], each an Item-heading candidate (single or
    combined), with the content_blocks index where its body would start.
    """
    content_blocks: list[Block] = []
    headings: list[dict] = []
    table_block_index: dict[int, int] = {}
    for el in root.iter():
        tag = el.tag
        if not isinstance(tag, str):
            continue
        if tag == "table":
            if in_table(el):
                continue
            table_block_index[id(el)] = len(content_blocks)
            content_blocks.append(Block(el, "", "table", page_for(el)))
            continue
        if tag not in _ALL_TAGS:
            continue
        if _is_inline_styled(el):
            continue  # absorbed into its container's text (see
            # has_block_child's docstring in blocks.py); walking it as its
            # own leaf too would duplicate its text alongside the
            # container's now-complete norm_text().
        if has_block_child(el, _ALL_TAGS):
            continue
        text = norm_text(el)
        if not text:
            continue
        # Heading candidacy is checked before the in-table exclusion: a
        # table-of-contents row commonly puts "Item 1." inside a <td>
        # (or a <p> nested one level inside a <td>), so this must run
        # even for elements nested inside a <table>.
        if len(text) < 200:
            cm = COMBINED_RE.match(text)
            m = None if cm else ITEM_RE.match(text)
            if cm is None and m is None:
                # Wave 2e: no "Item" word anywhere in this block, but it may
                # still be a real heading naming the Item's number and its
                # canonical title (see _match_numbered_title_heading).
                m = _match_numbered_title_heading(text)
            match = cm or m
            if match and not _is_cross_reference_quote(match.group(3) if cm else match.group(2)):
                table_ancestor = nearest_table_ancestor(el)
                # A heading found inside a <td> is discovered only once
                # the walk has already descended into the table -- by
                # which point the table itself was already appended to
                # content_blocks as one atomic block (tables are never
                # descended into for ordinary content). Anchoring the
                # heading's own content_index there (its containing
                # table's index), rather than at the current, later
                # position in the walk, keeps a real Item heading that
                # happens to render as a table row (a common way an
                # Item 15 exhibit schedule's Item 601(b) sub-parts are
                # laid out) from having its own table's content
                # misattributed to the previous Item's last section
                # (found via Madrigal Pharmaceuticals/Organon, where
                # skipping this meant the exhibit table's "Item 15(a)",
                # "Item 15(b)", ... rows got unwrapped into Item 14's
                # prose and tripped no_toc_in_sections there instead).
                content_index = (
                    table_block_index[id(table_ancestor)]
                    if table_ancestor is not None and id(table_ancestor) in table_block_index
                    else len(content_blocks)
                )
                headings.append(
                    {
                        "element": el,
                        "text": text,
                        "page": page_for(el),
                        "match": match,
                        "combined": bool(cm),
                        "content_index": content_index,
                        "table_ancestor": table_ancestor,
                    }
                )
                continue
        if in_table(el):
            continue  # non-heading text inside a table: the table (its
            # own atomic content_blocks entry) owns this content instead.
        if tag in HEADING_EXTRA_TAGS:
            continue  # only used for heading scan above; prose comes via <tr>
        content_blocks.append(Block(el, text, "text", page_for(el)))
    return content_blocks, headings


def _block_text_len(block: Block) -> int:
    if block.kind == "text":
        return len(block.text)
    return len(unwrap_table_to_text(block.element))


def _following_text_len(headings, content_blocks, i):
    start = headings[i]["content_index"]
    end = headings[i + 1]["content_index"] if i + 1 < len(headings) else len(content_blocks)
    return sum(_block_text_len(b) for b in content_blocks[start:end])


_ROW_ITEM_NUM_RE = re.compile(r"^item\s+(\d{1,2}[a-c]?)\b", re.I)
_ROW_TRAILING_PAGE_RE = re.compile(r"\d{1,4}\s*$")


def _table_has_many_item_rows(table_el, threshold=3) -> bool:
    """True for a genuine table-of-contents-style index table: several rows,
    each for a *different* Item number, each ending in a page reference.

    A real Item 15 exhibit schedule also renders as a table whose rows all
    literally start with "Item" -- "Item 15.", "Item 15(a)",
    "Item 15(a)(1) and (2)", "Item 15(b)", "Item 15(c)" are the sub-parts
    Item 601 of Regulation S-K requires -- but every row names the *same*
    Item number, which is not a TOC. Counting distinct Item numbers
    instead of raw row count avoids misclassifying this table (seen in
    Madrigal Pharmaceuticals and Organon's real filings: the original
    raw-row-count rule flagged it as a TOC, which dropped the Item 15
    heading entirely and failed required_items_present).

    A large filer's Part III "incorporated by reference to our proxy
    statement" block is also commonly laid out as a two-column table --
    "Item 10. | Directors, Executive Officers ..." -- one row per Item,
    several distinct numbers, but with *no* page number (the content is
    not in this document at all, so there is nothing to point a page at).
    Requiring a trailing page reference is what tells a real TOC (whose
    whole purpose is pointing at pages of this same document) apart from
    this legitimate index (seen in Liberty Broadband's real filing:
    without this, Items 10-14 were never detected as headings at all and
    fell into Item 8's trailing section instead, tripping
    no_toc_in_sections there).
    """
    seen: set[str] = set()
    for tr in table_el.iter("tr"):
        cells = [c for c in tr if isinstance(c.tag, str) and c.tag in ("td", "th")]
        row_text = " ".join(norm_text(c) for c in cells).strip()
        m = _ROW_ITEM_NUM_RE.match(row_text)
        if not m or not _ROW_TRAILING_PAGE_RE.search(row_text):
            continue
        seen.add(normalize_item_num(m.group(1)))
        if len(seen) > threshold:
            return True
    return False


def filter_headings(headings, content_blocks):
    """Drop table-of-contents hits. Returns (kept, n_toc_skipped, first_real)."""
    follow_lens = [_following_text_len(headings, content_blocks, i) for i in range(len(headings))]
    first_real = None
    for i, flen in enumerate(follow_lens):
        if flen > 1500:
            first_real = i
            break

    table_cache: dict = {}

    def table_is_toc(table_el) -> bool:
        if table_el is None:
            return False
        key = id(table_el)
        if key not in table_cache:
            table_cache[key] = _table_has_many_item_rows(table_el)
        return table_cache[key]

    kept = []
    n_skipped = 0
    for i, h in enumerate(headings):
        if first_real is not None and i < first_real:
            n_skipped += 1
            continue
        if table_is_toc(h["table_ancestor"]):
            n_skipped += 1
            continue
        kept.append(h)

    if not kept and headings:
        # Fallback for filings where no heading has 1,500+ chars of
        # following text before the next heading (for example a very
        # short shell-company Item 1) -- without this, TOC-cutoff logic
        # alone would discard every heading and leave the filing with no
        # Items at all. Use the last occurrence of each item number
        # (TOC entries normally come first, the body heading last), still
        # excluding anything flagged as a TOC-style table.
        best: dict[str, dict] = {}
        combined_kept = []
        for h in headings:
            if table_is_toc(h["table_ancestor"]):
                continue
            if h["combined"]:
                combined_kept.append(h)
                continue
            item_num = normalize_item_num(h["match"].group(1))
            prev = best.get(item_num)
            if prev is None or h["content_index"] > prev["content_index"]:
                best[item_num] = h
        kept = sorted(list(best.values()) + combined_kept, key=lambda h: h["content_index"])

    return kept, n_skipped, first_real


def _page_end_for_range(content_blocks, start, end, default):
    pages = [b.page for b in content_blocks[start:end] if b.page]
    return max(pages) if pages else default


def _raw_text_for_range(content_blocks, start, end) -> str:
    parts = []
    for b in content_blocks[start:end]:
        parts.append(b.text if b.kind == "text" else unwrap_table_to_text(b.element))
    return "\n".join(p for p in parts if p)


def _classify_status(raw_text: str) -> str | None:
    """Return an override status for a body that turns out to be a stub,
    or None if the Item should stay 'present'."""
    stripped = raw_text.strip()
    if len(stripped) < 600 and (INCORPORATED_RE.search(stripped) or SEE_OTHER_ITEM_RE.search(stripped)):
        return "incorporated_by_reference"
    if len(stripped) < 1000 and NOT_REQUIRED_RE.search(stripped):
        return "not_required"
    return None


# ---------------------------------------------------------------------------
# Wave 2d: cross-checking a not_required classification against the raw
# document text, for the four Items a smaller reporting company may omit.
#
# The old rule ("smaller reporting company and no heading found means
# not_required") masked a real heading-detection miss: the orchestrator's
# spot check against sec.gov found 9 passing filings with Item 1A
# not_required that had real risk factors. The contract's fix: mark
# not_required only when (a) a heading exists and its body matches the
# omitted-item phrases, or (b) no heading exists AND the raw document text
# has no run longer than 2,000 characters between a mention of that Item
# and the next Item mention -- otherwise a missing heading is `absent`.
#
# Two separate mechanisms below implement this, deliberately kept narrow
# after an early, broader draft (checking the raw-text gap in both
# directions around every mention, regardless of whether a heading was
# found) turned out to be unsafe at corpus scale: Item 1A's own real body
# is almost always tens of thousands of characters, so *any* filing where
# Item 1B is legitimately not_required ("None.") would see a huge "gap"
# back to Item 1A's own heading and get flagged as suspicious -- that gap
# is Item 1A's own already-correctly-captured content, not missing Item 1B
# content, and the broad check could not tell the two apart. It turned a
# ~89% corpus pass rate into single digits in a live run and was reverted.
# ---------------------------------------------------------------------------

_OMITTABLE_NEXT = {"1A": "1B", "1B": "1C", "6": "7", "7A": "8"}
_LONG_RUN_LIMIT = 2000


def _mention_number_pattern(item_num: str) -> str:
    """Same "1"/"I" typo tolerance as _NUM_RE (see ITEM_RE), for scanning
    a bare mention rather than an anchored heading match."""
    if item_num[:1] == "1" and len(item_num) > 1:
        return f"[1I]{re.escape(item_num[1:])}"
    return re.escape(item_num)


def _mention_positions(item_num: str, full_text: str) -> list[int]:
    """Every place `item_num` is literally mentioned anywhere in the raw
    document text -- not just at a detected heading, so a cross-reference
    ("... under Item 1A, 'Risk Factors'") still counts as a mention."""
    pat = re.compile(rf"\b[IL]TEMS?\s+{_mention_number_pattern(item_num)}\b", re.I)
    return [m.start() for m in pat.finditer(full_text)]


def _has_forward_long_run(item_num: str, full_text: str) -> bool:
    """Rule (b), literally: no heading was found for `item_num` at all, so
    the only "mentions" of it left in the document are a TOC entry and any
    cross-reference. If one of those is followed by more than 2,000
    characters before the *next* Item is itself mentioned (or by
    end-of-document), a real heading was very likely missed (Spruce Power
    Holding Corp's real filing: a forward-looking-statements cross-
    reference -- "... described above and in Item 1A under the heading
    'Risk Factors'" -- sits deep inside Item 1's own body, more than 2,000
    characters before Item 1B is ever mentioned, because Item 1A's real
    heading itself is missing the word "Item" entirely in the source
    HTML). Only usable when a neighboring Item is mentioned somewhere in
    the document at all -- with no mention of it anywhere, there is no
    boundary to measure against, and defaulting to end-of-document would
    flag most Items in most short documents.
    """
    next_num = _OMITTABLE_NEXT[item_num]
    this_positions = _mention_positions(item_num, full_text)
    if not this_positions:
        return False
    next_positions = sorted(_mention_positions(next_num, full_text))
    if not next_positions:
        return False
    for pos in this_positions:
        fwd_candidates = [p for p in next_positions if p > pos]
        fwd_end = min(fwd_candidates) if fwd_candidates else len(full_text)
        if fwd_end - pos > _LONG_RUN_LIMIT:
            return True
    return False


def _candidate_body_lengths(kept_before_dedup, content_blocks) -> dict:
    """Map id(heading) -> length of the text between it and the next
    heading in document order (any Item number), mirroring
    `_following_text_len` but keyed by heading identity so a later lookup
    can ask "how much real content followed *this specific* occurrence."
    """
    ordered = sorted(kept_before_dedup, key=lambda h: h["content_index"])
    lens = {}
    for i, h in enumerate(ordered):
        start = h["content_index"]
        end = ordered[i + 1]["content_index"] if i + 1 < len(ordered) else len(content_blocks)
        lens[id(h)] = sum(_block_text_len(b) for b in content_blocks[start:end])
    return lens


def _has_larger_duplicate_elsewhere(item_num, winner, candidates_by_item, candidate_lens) -> bool:
    """True if some *other* raw heading candidate for the same Item number
    (one `_resolve_heading_candidates` did not pick) has substantially more
    following text than the 2,000-character not_required threshold.

    Targets BTCS Inc.'s real filing: a genuine, full "ITEM 1A. RISK
    FACTORS" section (with real content) exists later in the document, but
    `_resolve_heading_candidates`'s ceiling rule -- which exists to stop an
    embedded exhibit's own, unrelated Item numbering from hijacking a
    well-formed heading -- correctly keeps the earlier, short "Not
    applicable ... described under Item 7" stub as Item 1A's own range,
    since the later occurrence sits physically inside what became Item 7's
    range. Rather than reopen that ceiling rule (risking exactly the
    hijack it was written to prevent), this only asks whether the loser
    heading itself looks real (substantial content followed it) -- if so,
    the winner's own short "not required" reading should not be trusted
    over it, even though nothing here changes which occurrence still wins
    the position.
    """
    others = [h for h in candidates_by_item.get(item_num, []) if winner is None or id(h) != id(winner)]
    return any(candidate_lens.get(id(h), 0) > _LONG_RUN_LIMIT for h in others)


# ---------------------------------------------------------------------------
# Integrated-report fallback
# ---------------------------------------------------------------------------


def _find_cross_reference_table(root):
    """Find a table whose rows look like 'Item 1A. Risk Factors ... 50-64'."""
    best = None
    best_rows = []
    for table in root.iter("table"):
        if in_table(table):
            continue
        rows = []
        for tr in table.iter("tr"):
            cells = [c for c in tr if isinstance(c.tag, str) and c.tag in ("td", "th")]
            row_text = " ".join(norm_text(c) for c in cells).strip()
            row_text = re.sub(r"\s+", " ", row_text)
            m = INDEX_ROW_RE.match(row_text)
            if m:
                rows.append(m)
        if len(rows) > best_rows.__len__():
            best, best_rows = table, rows
    if best is not None and len(best_rows) >= 5:
        return best, best_rows
    return None, []


def _label_to_page_index(pages: list[dict], label: str) -> int | None:
    for p in pages:
        if p.get("label") == label:
            return p["index"]
    return None


def _integrated_report_items(root, content_blocks, pages):
    table, rows = _find_cross_reference_table(root)
    if table is None:
        return None
    entries = []
    for m in rows:
        item_num = normalize_item_num(m.group(1))
        title = clean_title(m.group(2))
        label = m.group(3)
        page_idx = _label_to_page_index(pages, label)
        entries.append({"item": item_num, "title": title, "page_index": page_idx})
    entries = [e for e in entries if e["page_index"] is not None]
    if len(entries) < 5:
        return None
    entries.sort(key=lambda e: e["page_index"])
    records = {}
    for i, e in enumerate(entries):
        page_start = e["page_index"]
        page_end = entries[i + 1]["page_index"] if i + 1 < len(entries) else pages[-1]["index"] if pages else page_start
        start_index = next(
            (j for j, b in enumerate(content_blocks) if b.page >= page_start), len(content_blocks)
        )
        end_index = next(
            (j for j, b in enumerate(content_blocks) if b.page > page_end), len(content_blocks)
        )
        records[e["item"]] = {
            "item": e["item"],
            "title": e["title"] or ITEM_TITLES.get(e["item"], ""),
            "status": "present",
            "page_start": page_start,
            "page_end": page_end,
            "start_index": start_index,
            "end_index": end_index,
            "forced_sections": None,
        }
    return records


def _reassign_range_to_item8(found, item8, xlo, xhi, new_page_end):
    for other_num, rec in found.items():
        if other_num == "8" or rec.get("start_index") is None:
            continue
        s, e = rec["start_index"], rec["end_index"]
        if e <= xlo or s >= xhi:
            continue  # no overlap
        if s < xlo:
            rec["end_index"] = xlo
        else:
            # this item's range starts inside (or before) the reassigned
            # span; there is nothing left to attribute to it
            rec["end_index"] = rec["start_index"]
    item8["extra_index_range"] = (xlo, xhi)
    if item8["page_end"] is None or new_page_end > item8["page_end"]:
        item8["page_end"] = new_page_end


def _find_missing_item8(found, content_blocks) -> None:
    """A small filer occasionally omits the "Item 8." heading entirely and
    launches straight into the audit report right after Item 7A's body
    (Summit Networks' real filing: "Not applicable." for Item 7A, then the
    very next page opens with "Report of Independent Registered Public
    Accounting Firm" with no Item 8 heading anywhere in between). Since
    `_apply_financial_statements_fixup` only ever *extends* an Item 8 that
    was already found, it never fires here at all -- Item 8 must be
    synthesized first. Search the gap between the nearest preceding found
    Item and the nearest following one for a financial-statement marker
    line, and if found, treat everything from there to the next Item as
    Item 8's body. A no-op if Item 8 already has a heading of its own.
    """
    if found.get("8") is not None:
        return
    idx8 = STANDARD_ITEMS.index("8")
    prev_rec = None
    prev_end = 0
    for num in reversed(STANDARD_ITEMS[:idx8]):
        rec = found.get(num)
        if rec and rec.get("end_index") is not None:
            prev_rec, prev_end = rec, rec["end_index"]
            break
    next_start = len(content_blocks)
    for num in STANDARD_ITEMS[idx8 + 1 :]:
        rec = found.get(num)
        if rec and rec.get("start_index") is not None:
            next_start = rec["start_index"]
            break
    for i in range(prev_end, next_start):
        b = content_blocks[i]
        if b.kind != "text" or len(b.text) >= 120:
            continue
        if FIN_STMT_MARKER_RE.search(b.text):
            page_start = b.page
            page_end = _page_end_for_range(content_blocks, i, next_start, page_start)
            found["8"] = {
                "item": "8",
                "title": ITEM_TITLES.get("8", ""),
                "status": "present",
                "page_start": page_start,
                "page_end": page_end,
                "start_index": i,
                "end_index": next_start,
                "forced_sections": None,
            }
            if prev_rec is not None and prev_rec["end_index"] > i:
                # The preceding Item's own range (built before Item 8 was
                # known to exist at all) currently reaches all the way to
                # the next Item, swallowing this same span; trim it back
                # to where Item 8 now begins.
                prev_rec["end_index"] = i
                if prev_rec.get("page_end") is not None and prev_rec["page_end"] > page_start:
                    prev_rec["page_end"] = _page_end_for_range(
                        content_blocks, prev_rec["start_index"], i, prev_rec["page_start"]
                    )
            return


def _apply_financial_statements_fixup(found, content_blocks, pages):
    """Many 10-Ks print Item 8's real body as a short stub ("The required
    financial statements commence on page F-1." or "... appear in this
    Report beginning on page 33.") and place the actual audited financial
    statements later in the document -- physically inside whatever later
    Item's range happens to run to the end of the document (usually 15 or
    16), sometimes on a separate "F-1, F-2, ..." page series, sometimes on
    the filing's own continuing page numbers. Move that range's content
    blocks over to Item 8 and trim it out of whichever other Item
    currently claims it, so Item 8's size and the coverage check both
    reflect where the content really is.
    """
    item8 = found.get("8")
    if item8 is None or item8.get("start_index") is None:
        return
    current_size = len(_raw_text_for_range(content_blocks, item8["start_index"], item8["end_index"]))
    if current_size >= FIN_STMT_STUB_MAX_CHARS:
        return  # Item 8 already has a real body

    # Never reach into content that is already the legitimate body of
    # another Item's own detected heading (for example a filing that
    # genuinely headed its financial-statement index "Item 15."): treat a
    # candidate as owned by that other Item only when it sits at (or right
    # after) that Item's own heading -- not merely somewhere inside a
    # trailing range that extends to end-of-document by default (which is
    # true of whichever Item happens to be last, and is exactly the
    # normal, liftable "back matter" case this fixup targets).
    other_starts_by_item = {
        num: rec["start_index"]
        for num, rec in found.items()
        if num != "8" and rec.get("start_index") is not None
    }

    def _owning_item_at(i, window=3) -> str | None:
        for num, s in other_starts_by_item.items():
            if s <= i < s + window:
                return num
        return None

    def _owned_by_real_fin_stmt_heading(i, window=3):
        """True only when another Item's heading sits within `window`
        blocks of position `i` *and* that other Item is Item 15
        ("Exhibits, Financial Statement Schedules"), which is
        the one Item that can legitimately claim a financial-statement
        index right at its own heading (Donnelley Financial Solutions'
        real filing: its Item 15 heading literally *is* the index), so it
        stays protected whenever a marker heading sits nearby. Item 16
        ("Form 10-K Summary") has no such claim: a minimal "None." stub
        immediately followed -- purely by physical page order, with no
        semantic connection, since Item 16 has no closing boundary of its
        own -- by an unrelated F-1..F-N financial statement index would
        otherwise be permanently protected as "already Item 16's," leaving
        Item 8 a permanent one-line stub (found via USA Opportunity Income
        One's real filing, where "None." is followed two blocks later by
        "INDEX TO FINANCIAL STATEMENTS").
        """
        owner = _owning_item_at(i, window)
        if owner is None or owner == "16":
            return False
        return any(
            b.kind == "text" and FIN_STMT_MARKER_RE.search(b.text)
            for b in content_blocks[i : i + window]
        )

    # Strategy 1: a distinct "F-1, F-2, ..." page-label series. If that
    # range's start looks like it is really owned by another Item's own
    # heading, trust that heading instead and do not touch anything.
    f_pages = [p["index"] for p in pages if p.get("label") and str(p["label"]).upper().startswith("F-")]
    if f_pages:
        # A lone early "F-1" is sometimes a stray false match (e.g. an
        # exhibit numbered "F-1" in a list near the front of the filing);
        # a real F-page series is long and its pages are close together,
        # so use the largest cluster of nearby F-labeled pages, not the
        # raw min/max.
        f_pages_sorted = sorted(f_pages)
        clusters = [[f_pages_sorted[0]]]
        for p in f_pages_sorted[1:]:
            if p - clusters[-1][-1] <= 5:
                clusters[-1].append(p)
            else:
                clusters.append([p])
        best_cluster = max(clusters, key=len)
        lo, hi = min(best_cluster), max(best_cluster)
        if item8["page_end"] is None or item8["page_end"] < lo:
            extra_indices = [
                i for i, b in enumerate(content_blocks) if i >= item8["end_index"] and b.page and lo <= b.page <= hi
            ]
            if extra_indices and not _owned_by_real_fin_stmt_heading(min(extra_indices)):
                _reassign_range_to_item8(found, item8, min(extra_indices), max(extra_indices) + 1, hi)
                return

    # Strategy 2: no separate F-page series (continuing arabic page
    # numbers) -- find the first "CONSOLIDATED BALANCE SHEETS" / "Report
    # of Independent Registered Public Accounting Firm" / financial
    # statement index heading after Item 8's own (stub) body, and claim
    # everything from there to the end of document. If that heading is
    # really the start of another Item's own body (for example a filing
    # that genuinely headed its financial-statement index "Item 15."),
    # trust that heading instead and give up rather than partially
    # cannibalizing it.
    for i in range(item8["end_index"], len(content_blocks)):
        b = content_blocks[i]
        if b.kind != "text" or len(b.text) >= 120:
            continue
        if FIN_STMT_MARKER_RE.search(b.text):
            if _owned_by_real_fin_stmt_heading(i):
                return
            end_of_doc = len(content_blocks)
            new_page_end = content_blocks[end_of_doc - 1].page if end_of_doc else item8["page_end"]
            _reassign_range_to_item8(found, item8, i, end_of_doc, new_page_end or item8["page_end"])
            return


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


_ORDER_EXEMPT_ITEMS = {"15", "16"}  # filers commonly print these two out of
# order (the Form 10-K Summary before the exhibit index or vice versa; see
# reports/wave-2a.md's Donnelley Financial Solutions case, where a real,
# content-bearing "Item 15" heading legitimately starts after "Item 16").


def _resolve_heading_candidates(candidates: dict[str, list[dict]]) -> dict[str, dict]:
    """Pick which heading occurrence is real for each Item number that has
    more than one non-combined match in `kept`.

    A duplicate heading has two very different causes in real filings:
    a genuinely repeated heading, where the later one is the real,
    content-bearing occurrence (Donnelley Financial Solutions' double
    "Item 15."); or unrelated content deep in the document that merely
    starts with "Item N." -- most often an appended exhibit's own
    financial statements, formatted with a *different* SEC form's Item
    numbering (a target company's "Item 1. Financial Statements" from its
    own 10-Q, filed as an exhibit, seen in both Aditxt's and iSpecimen's
    real filings). Blindly preferring the latest occurrence (this
    module's original rule) silently let the second case overwrite the
    real Item 1, which then failed `items_in_order` (Item 1's page ended
    up after every other Item's); merely picking a different *winner*
    without also removing the loser from `kept` was not enough either --
    the loser still cut its neighbor's real body short at its own
    position, orphaning everything between it and the next real heading
    (iSpecimen's real Item 8 lost its own financial statements this way).
    So the loser must be dropped from `kept` entirely, not just from this
    Item's own attribution -- see `detect_items`, which uses this
    function's result to filter `kept` before computing any body ranges.

    The rule: walk Items in their standard order and require each chosen
    occurrence to sit, in document position, before the *next* Item's own
    earliest occurrence -- an Item's real heading cannot physically come
    after the next Item's. Within that bound, still prefer the latest
    occurrence (preserving the original, deliberate behavior for a
    genuine repeated heading). Items 15 and 16 are excluded from this
    bound in both directions, matching `items_in_order`'s own exemption.
    """
    resolved: dict[str, dict] = {}
    numbers_with_candidates = [n for n in STANDARD_ITEMS if n in candidates]
    for i, item_num in enumerate(numbers_with_candidates):
        hs = candidates[item_num]
        if item_num in _ORDER_EXEMPT_ITEMS:
            resolved[item_num] = hs[-1]
            continue
        ceiling = None
        for later_num in numbers_with_candidates[i + 1 :]:
            if later_num in _ORDER_EXEMPT_ITEMS:
                continue
            ceiling = candidates[later_num][0]["content_index"]
            break
        in_bounds = [h for h in hs if ceiling is None or h["content_index"] < ceiling]
        resolved[item_num] = _pick_heading(in_bounds or [hs[0]])
    return resolved


def _heading_title_len(h: dict) -> int:
    m = h["match"]
    text = m.group(3) if h["combined"] else m.group(2)
    return len(clean_title(text or ""))


def _pick_heading(candidates: list[dict]) -> dict:
    """Among same-Item heading matches that survived the ceiling bound,
    prefer one with real title text over a bare "Item N" match with none.

    A large filer's page layout commonly prints the current Item's number
    as a running header or footer on *every one of that Item's own pages*
    (Microsoft's real 10-K prints a bare "Item 7" on all 15 of Item 7's
    pages) -- this evades `pages.py`'s running-line filter because the
    printed text changes with the current Item, so no single line repeats
    on more than 30% of pages even though the *pattern* does. Such a line
    never carries the Item's title (only the real heading, printed once,
    does), so among in-bounds candidates: prefer a titled one, latest
    first (preserving the original "later occurrence wins" rule for a
    genuine repeated heading, such as Donnelley Financial Solutions'
    double "Item 15."); with no titled candidate at all, take the first
    occurrence -- the actual start of the Item's own range, not a later
    running-footer echo of it.
    """
    titled = [h for h in candidates if _heading_title_len(h) > 0]
    if titled:
        return titled[-1]
    return candidates[0]


def _filter_spurious_headings(kept: list[dict]) -> list[dict]:
    """Drop every heading that lost the `_resolve_heading_candidates`
    selection for its own (first, for a combined heading) Item number, so
    a spurious duplicate no longer defines a body-range boundary at all
    (see that function's docstring).

    A combined heading ("Items 7 and 7A.") competes for its first Item
    number's slot exactly like a single heading -- a cross-reference deep
    in an exhibit list can read as one just as easily as a single heading
    can (Goodyear Tire & Rubber's real filing has "ITEMS 8 AND 15(a)(2) OF
    FORM 10-K" on its own line, well past Item 9's real heading, which
    otherwise overwrote the real Item 8 with an empty range starting after
    Item 16). Its second Item number never independently competes, since
    that Item's assignment is always synthetic, derived from whichever
    heading wins the first number's slot.
    """
    candidates: dict[str, list[dict]] = {}
    for h in kept:
        item_num = normalize_item_num(h["match"].group(1))
        candidates.setdefault(item_num, []).append(h)
    if not candidates:
        return kept
    chosen_ids = {id(h) for h in _resolve_heading_candidates(candidates).values()}
    return [h for h in kept if id(h) in chosen_ids]


def detect_items(root, page_for, pages: list[dict], filer_category: str | None):
    content_blocks, headings = build_streams(root, page_for)
    kept, n_toc_skipped, first_real = filter_headings(headings, content_blocks)
    kept_before_dedup = kept
    kept = _filter_spurious_headings(kept)

    categories = split_filer_category(filer_category)
    smaller_reporting = is_smaller_reporting(categories)
    # Whole-document text for the wave 2d not_required cross-check (see
    # _has_forward_long_run); computed once, independent of the
    # content_blocks/heading split, since it needs to see mentions
    # (headings and cross-references alike) everywhere.
    full_text = norm_text(root)
    candidates_by_item: dict[str, list[dict]] = {}
    for h in kept_before_dedup:
        if h["combined"]:
            continue
        candidates_by_item.setdefault(normalize_item_num(h["match"].group(1)), []).append(h)
    candidate_lens = _candidate_body_lengths(kept_before_dedup, content_blocks)
    winner_by_item: dict[str, dict] = {
        normalize_item_num(h["match"].group(1)): h for h in kept if not h["combined"]
    }

    stats = {
        "combined_items": 0,
        "integrated_report": False,
        "toc_headings_skipped": n_toc_skipped,
        "total_heading_candidates": len(headings),
    }

    found: dict[str, dict] = {}
    n = len(kept)
    idx = 0
    while idx < n:
        h = kept[idx]
        start_index = h["content_index"]
        end_index = kept[idx + 1]["content_index"] if idx + 1 < n else len(content_blocks)
        page_start = h["page"]
        page_end = _page_end_for_range(content_blocks, start_index, end_index, page_start)
        if h["combined"]:
            first_num = normalize_item_num(h["match"].group(1))
            second_num = normalize_item_num(h["match"].group(2))
            title_text = clean_title(h["match"].group(3))
            rec = {
                "item": first_num,
                "title": title_text or ITEM_TITLES.get(first_num, ""),
                "status": "present",
                "page_start": page_start,
                "page_end": page_end,
                "start_index": start_index,
                "end_index": end_index,
                "forced_sections": None,
            }
            # Every non-combined duplicate was already resolved by
            # `_filter_spurious_headings`, so the surviving `kept` list has
            # at most one occurrence per Item number here; assign directly.
            found[first_num] = rec
            found[second_num] = {
                "item": second_num,
                "title": ITEM_TITLES.get(second_num, ""),
                "status": "present",
                "page_start": page_end,
                "page_end": page_end,
                "start_index": None,
                "end_index": None,
                "forced_sections": [f"See Item {first_num}."],
            }
            stats["combined_items"] += 1
        else:
            item_num = normalize_item_num(h["match"].group(1))
            title_text = clean_title(h["match"].group(2))
            rec = {
                "item": item_num,
                "title": title_text or ITEM_TITLES.get(item_num, ""),
                "status": "present",
                "page_start": page_start,
                "page_end": page_end,
                "start_index": start_index,
                "end_index": end_index,
                "forced_sections": None,
            }
            found[item_num] = rec
        idx += 1

    _find_missing_item8(found, content_blocks)
    _apply_financial_statements_fixup(found, content_blocks, pages)

    # Resolve status overrides (incorporated by reference / not required)
    # for items whose body has real content.
    for item_num, rec in found.items():
        if rec["start_index"] is None:
            continue
        # The heading line itself is not a content_blocks entry (build_streams
        # tracks it separately, in `headings`), so a heading whose own text
        # carries the whole disclosure -- "Item 6. [Reserved]" is the single
        # most common case, since Item 6 has been permanently reserved by the
        # SEC since 2021 and almost every current 10-K's Item 6 body is empty
        # -- would otherwise never see the word "reserved" and stay
        # "present" with an empty body. Prepending the title includes it.
        raw_text = (rec.get("title") or "") + "\n" + _raw_text_for_range(
            content_blocks, rec["start_index"], rec["end_index"]
        )
        extra = rec.get("extra_index_range")
        if extra:
            raw_text += "\n" + _raw_text_for_range(content_blocks, extra[0], extra[1])
        override = _classify_status(raw_text)
        if override == "not_required" and item_num in SMALLER_REPORTING_OMITTABLE:
            # Wave 2d: a heading exists and its own body reads like a
            # not_required disclaimer, but don't trust that in isolation
            # when another raw candidate for this exact Item number has
            # substantial content of its own (see
            # _has_larger_duplicate_elsewhere's docstring -- BTCS Inc.'s
            # real filing).
            if not _has_larger_duplicate_elsewhere(item_num, winner_by_item.get(item_num), candidates_by_item, candidate_lens):
                rec["status"] = override
        elif override:
            rec["status"] = override

    # Integrated-report fallback: no body headings survived at all, but a
    # cross-reference index table maps Items to printed page labels.
    if not found:
        integrated = _integrated_report_items(root, content_blocks, pages)
        if integrated:
            found = integrated
            stats["integrated_report"] = True

    items = []
    for item_num in STANDARD_ITEMS:
        rec = found.get(item_num)
        if rec is not None:
            items.append(
                {
                    "item": item_num,
                    "part": ITEM_PARTS[item_num],
                    "title": rec["title"],
                    "status": rec["status"],
                    "page_start": rec["page_start"],
                    "page_end": rec["page_end"],
                    "start_index": rec["start_index"],
                    "end_index": rec["end_index"],
                    "forced_sections": rec["forced_sections"],
                    "extra_index_range": rec.get("extra_index_range"),
                }
            )
        else:
            status = "absent"
            if smaller_reporting and item_num in SMALLER_REPORTING_OMITTABLE:
                # Wave 2d: no heading at all was found for this Item --
                # mark it not_required only when the raw document text
                # backs that up (rule (b) of the contract); otherwise a
                # heading was very likely missed, so it is left `absent`
                # and goes to required_items_present's failure list
                # instead of being silently swallowed.
                if not _has_forward_long_run(item_num, full_text):
                    status = "not_required"
            items.append(
                {
                    "item": item_num,
                    "part": ITEM_PARTS[item_num],
                    "title": ITEM_TITLES.get(item_num, ""),
                    "status": status,
                    "page_start": None,
                    "page_end": None,
                    "start_index": None,
                    "end_index": None,
                    "forced_sections": None,
                }
            )

    return items, content_blocks, stats
