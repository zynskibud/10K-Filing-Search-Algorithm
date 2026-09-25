"""The Wave 1a filer rule: which candidate 10-K filings enter the corpus.

Background (plan.html, Plan tab, section 2): the corpus keeps filers
whose 10-K follows the standard SEC Item list, including smaller
reporting companies, and drops filers whose 10-K uses a different
Item list (for example asset-backed securities issuers).

Checks are applied in a fixed priority order. `is_eligible` returns the
first reason that fails, so the exclusion table is a clean partition
(a filing counts under one reason, even if more than one would apply).

Judgment calls (see reports/wave-1a.md, "open questions" for the ones
left to the human):
- Asset-backed issuers are matched by SIC 6189 OR EDGAR's own
  `entityType == "asset-backed"`, whichever fires. The contract also
  mentions name patterns ("Trust 20", "Receivables", "Funding LLC")
  "with SIC 6189" -- read literally that adds no filing beyond the SIC
  check, so it is not implemented as a separate exclusion; it is only
  used to sanity-check the SIC 6189 rows the report shows as evidence.
- Royalty trusts are matched by SIC 6792 (SEC's "Oil Royalty Traders"
  code, the standard code for oil and gas royalty trusts).
- Blank-check / shell SPACs are matched by SIC 6770 or the
  sicDescription "Blank Checks".
- "Grantor trusts" and "ETF/commodity trusts" (mentioned in the
  contract as excluded) have NO reliable field-based signal: a probe
  of SPDR Gold Trust (CIK 1222333), a commodity trust that files a
  10-K with a reduced, non-standard Item list, shows SIC 6221 and
  entityType "operating" -- the same SIC the contract says must NOT be
  excluded (SIC 6221 commodity pools) and the same entityType as a
  normal operating company. No available field tells these apart from
  a real operating company without a name-pattern guess that would
  also catch legitimate operating companies with "Trust" in the name
  (many REITs are named "... Trust", and REITs must stay included).
  This rule therefore does NOT exclude grantor trusts or ETF/commodity
  trusts. This is flagged as an open question, not decided here.
"""

from __future__ import annotations

SIC_ASSET_BACKED = "6189"
SIC_ROYALTY_TRUST = "6792"  # "Oil Royalty Traders"
SIC_BLANK_CHECK = "6770"
SIC_DESC_BLANK_CHECK = "blank checks"

HTML_EXTENSIONS = (".htm", ".html")


def is_eligible(row: dict) -> tuple[bool, str]:
    """Return (True, "eligible") or (False, reason).

    `row` is one line of data/survey/candidates.jsonl.
    """
    sic = (row.get("sic") or "").strip()
    sic_description = (row.get("sicDescription") or "").strip().lower()
    entity_type = (row.get("entityType") or "").strip().lower()
    primary_document = (row.get("primaryDocument") or "").strip().lower()
    is_inline_xbrl = row.get("isInlineXBRL")

    if entity_type == "asset-backed":
        return False, "entity_type_asset_backed"

    if sic == SIC_ASSET_BACKED:
        return False, "sic_6189_asset_backed"

    if sic == SIC_ROYALTY_TRUST:
        return False, "sic_6792_royalty_trust"

    if sic == SIC_BLANK_CHECK or sic_description == SIC_DESC_BLANK_CHECK:
        return False, "sic_6770_blank_check"

    # isInlineXBRL can arrive as bool, int (0/1), or None depending on the
    # submissions JSON; treat anything falsy as "not inline XBRL".
    if not is_inline_xbrl:
        return False, "not_inline_xbrl"

    if not primary_document.endswith(HTML_EXTENSIONS):
        return False, "primary_doc_not_html"

    return True, "eligible"
