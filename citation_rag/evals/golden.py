"""Load and validate the golden question set against schemas.md section 2.

CLI:
    uv run python -m citation_rag.evals.golden validate evals/golden/dev.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

VALID_TYPES = {
    "fact_lookup",
    "number_from_table",
    "paraphrased",
    "exact_term",
    "general",
    "unanswerable",
    "multi_part",
}


class GoldenCase(BaseModel):
    """One row of evals/golden/{split}.jsonl, per schemas.md section 2."""

    model_config = ConfigDict(extra="allow")

    id: str
    question: str
    type: Literal[
        "fact_lookup",
        "number_from_table",
        "paraphrased",
        "exact_term",
        "general",
        "unanswerable",
        "multi_part",
    ]
    companies: list[str] | Literal["general"]
    accession_no: str | None = None
    accession_nos: list[str] | None = None
    item: str | None = None
    section_id: str | None = None
    table_id: str | None = None
    evidence: str | list[str] | None = None
    page: int | list[int] | None = None
    answer: str
    answer_kind: Literal["number", "text", "none"]
    answer_value: float | None = None
    answer_unit: str | None = None
    notes: str | None = None


@dataclass
class Problem:
    case_id: str
    reason: str

    def __str__(self) -> str:
        return f"{self.case_id}: {self.reason}"


def load_golden(path: str | Path) -> list[GoldenCase]:
    """Parse a golden JSONL file. Raises ValueError with the line number on a bad row."""
    cases: list[GoldenCase] = []
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {lineno}: invalid JSON: {exc}") from exc
            try:
                cases.append(GoldenCase.model_validate(raw))
            except Exception as exc:  # pydantic ValidationError
                raise ValueError(f"line {lineno}: schema error: {exc}") from exc
    return cases


def _load_parsed(parsed_dir: Path, accession_no: str) -> dict | None:
    fp = parsed_dir / f"{accession_no}.json"
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _find_section(parsed: dict, section_id: str) -> dict | None:
    for item in parsed.get("items", []):
        for sec in item.get("sections", []):
            if sec.get("id") == section_id:
                return sec
    return None


def _find_table(parsed: dict, table_id: str) -> dict | None:
    for table in parsed.get("tables", []):
        if table.get("id") == table_id:
            return table
    return None


def _table_contains_whole_lines(table_text: str, evidence: str) -> bool:
    """Evidence must be one or more whole, contiguous lines of the table text."""
    table_lines = table_text.split("\n")
    ev_lines = evidence.split("\n")
    if not ev_lines:
        return False
    n = len(ev_lines)
    for i in range(len(table_lines) - n + 1):
        if table_lines[i : i + n] == ev_lines:
            return True
    return False


def _check_single_evidence(
    parsed: dict,
    section_id: str | None,
    table_id: str | None,
    evidence: str,
    page: int | None,
) -> str | None:
    """Returns a reason string on failure, or None if the evidence checks out."""
    if table_id:
        table = _find_table(parsed, table_id)
        if table is None:
            return f"table_id {table_id!r} not found in parsed filing"
        if not _table_contains_whole_lines(table.get("text", ""), evidence):
            return f"evidence is not a contiguous set of whole lines in table {table_id!r}"
        if page is not None and not (table.get("page_start") <= page <= table.get("page_end")):
            return f"page {page} is outside table {table_id!r} page range"
        return None
    if section_id:
        section = _find_section(parsed, section_id)
        if section is None:
            return f"section_id {section_id!r} not found in parsed filing"
        if evidence not in section.get("text", ""):
            return f"evidence is not an exact substring of section {section_id!r}"
        if page is not None and not (section.get("page_start") <= page <= section.get("page_end")):
            return f"page {page} is outside section {section_id!r} page range"
        return None
    return "case has neither section_id nor table_id to check evidence against"


def validate(golden_path: str | Path, parsed_dir: str | Path) -> list[Problem]:
    """Validate a golden JSONL file against the parsed filings in parsed_dir.

    Checks: evidence is an exact substring of the named section/table text,
    the page is inside that section/table's page range, companies CIKs match
    the filing, type is valid, and ids are unique. Unanswerable cases skip
    the evidence check.
    """
    problems: list[Problem] = []
    parsed_dir = Path(parsed_dir)
    path = Path(golden_path)

    seen_ids: set[str] = set()
    raw_lines: list[tuple[int, dict]] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                problems.append(Problem(case_id=f"line{lineno}", reason=f"invalid JSON: {exc}"))
                continue
            raw_lines.append((lineno, raw))

    for lineno, raw in raw_lines:
        case_id = raw.get("id", f"line{lineno}")
        try:
            case = GoldenCase.model_validate(raw)
        except Exception as exc:
            problems.append(Problem(case_id=case_id, reason=f"schema error: {exc}"))
            continue

        if case.type not in VALID_TYPES:
            problems.append(Problem(case_id=case.id, reason=f"invalid type {case.type!r}"))

        if case.id in seen_ids:
            problems.append(Problem(case_id=case.id, reason="duplicate id"))
        seen_ids.add(case.id)

        if case.type == "unanswerable":
            if case.evidence is not None:
                problems.append(Problem(case_id=case.id, reason="unanswerable case must have evidence = null"))
            if case.answer != "The filing does not state this.":
                problems.append(Problem(case_id=case.id, reason="unanswerable case must have the fixed answer text"))
            if case.answer_kind != "none":
                problems.append(Problem(case_id=case.id, reason="unanswerable case must have answer_kind = none"))
            if not case.notes:
                problems.append(Problem(case_id=case.id, reason="unanswerable case must have notes on nearby content"))
            # Evidence check is skipped per contract, but companies/CIK is still checked below.

        if case.type == "general":
            if case.companies != "general":
                problems.append(Problem(case_id=case.id, reason="general case must have companies == 'general'"))
            if not isinstance(case.evidence, list) or len(case.evidence) < 3:
                problems.append(Problem(case_id=case.id, reason="general case must have >= 3 evidence strings"))
            elif not case.accession_nos or len(case.accession_nos) != len(case.evidence):
                problems.append(Problem(case_id=case.id, reason="general case needs accession_nos parallel to evidence"))
            else:
                distinct = set(case.accession_nos)
                if len(distinct) < 3:
                    problems.append(Problem(case_id=case.id, reason="general case must draw evidence from >= 3 distinct filings"))
                pages = case.page if isinstance(case.page, list) else [None] * len(case.evidence)
                if isinstance(case.page, list) and len(pages) != len(case.evidence):
                    problems.append(Problem(case_id=case.id, reason="general case page list must be parallel to evidence"))
                    pages = [None] * len(case.evidence)
                for ev, acc, pg in zip(case.evidence, case.accession_nos, pages):
                    parsed = _load_parsed(parsed_dir, acc)
                    if parsed is None:
                        problems.append(Problem(case_id=case.id, reason=f"parsed file not found for {acc!r}"))
                        continue
                    reason = _check_single_evidence(parsed, case.section_id, case.table_id, ev, pg)
                    if reason:
                        problems.append(Problem(case_id=case.id, reason=f"[{acc}] {reason}"))
            continue  # general cases have no single accession_no / companies CIK check

        # Non-general cases: companies must be a list of CIKs matching the filing.
        if not isinstance(case.companies, list):
            problems.append(Problem(case_id=case.id, reason="non-general case must have a list of company CIKs"))
        if not case.accession_no:
            problems.append(Problem(case_id=case.id, reason="non-general case must have an accession_no"))
            continue

        parsed = _load_parsed(parsed_dir, case.accession_no)
        if parsed is None:
            problems.append(Problem(case_id=case.id, reason=f"parsed file not found for {case.accession_no!r}"))
            continue

        if isinstance(case.companies, list):
            filing_cik = parsed.get("cik")
            for cik in case.companies:
                if cik != filing_cik:
                    problems.append(Problem(case_id=case.id, reason=f"company CIK {cik!r} does not match filing CIK {filing_cik!r}"))

        if case.item is not None and case.section_id:
            # section_id is "{accession_no}:{item}:{seq}"
            parts = case.section_id.split(":")
            if len(parts) >= 2 and parts[-2] != case.item:
                problems.append(Problem(case_id=case.id, reason=f"item {case.item!r} does not match section_id {case.section_id!r}"))

        if case.type == "unanswerable":
            continue

        if case.type == "multi_part":
            if not isinstance(case.evidence, list) or not (2 <= len(case.evidence) <= 3):
                problems.append(Problem(case_id=case.id, reason="multi_part case must have 2 to 3 evidence strings"))
                continue
            pages = case.page if isinstance(case.page, list) else [case.page] * len(case.evidence)
            if len(pages) != len(case.evidence):
                problems.append(Problem(case_id=case.id, reason="multi_part page list must be parallel to evidence"))
                pages = [case.page if not isinstance(case.page, list) else None] * len(case.evidence)
            for ev, pg in zip(case.evidence, pages):
                reason = _check_single_evidence(parsed, case.section_id, case.table_id, ev, pg)
                if reason:
                    problems.append(Problem(case_id=case.id, reason=reason))
            continue

        # Single-evidence types: fact_lookup, number_from_table, paraphrased, exact_term.
        if not isinstance(case.evidence, str):
            problems.append(Problem(case_id=case.id, reason="this case type requires a single evidence string"))
            continue
        page = case.page if isinstance(case.page, int) else None
        reason = _check_single_evidence(parsed, case.section_id, case.table_id, case.evidence, page)
        if reason:
            problems.append(Problem(case_id=case.id, reason=reason))

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="citation_rag.evals.golden")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="validate a golden JSONL file")
    p_validate.add_argument("golden_path")
    p_validate.add_argument("--parsed-dir", default="data/parsed")

    args = parser.parse_args(argv)

    if args.command == "validate":
        problems = validate(args.golden_path, args.parsed_dir)
        if not problems:
            n = len(load_golden(args.golden_path))
            print(f"OK: {n} cases, 0 problems")
            return 0
        for p in problems:
            print(p)
        print(f"FAILED: {len(problems)} problem(s)")
        return 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
